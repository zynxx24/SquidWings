"""Claude's Messages API spoken in the OpenAI chat format the agent already uses.

``to_request`` turns an OpenAI chat payload into a ``POST /v1/messages`` body: system messages
become ``system``, tool calls become ``tool_use`` blocks, tool results become ``tool_result``
blocks in a user turn, images become image blocks, and turns of the same role are merged
because Claude wants user and assistant to alternate.

``to_response`` turns a Messages reply into a chat completion, and ``StreamTranslator`` turns
Claude's server-sent events into OpenAI-style chunks, tool-call deltas included, so the existing
stream handler needs no change.

With ``auto_cache`` the system prompt and the newest turn get ``cache_control``, which is how
Claude caches a long conversation; the next request then reads it as cached tokens.
"""

from __future__ import annotations

import json
import re
from typing import Any

STOP_REASONS = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "tool_use": "tool_calls",
                "pause_turn": "stop", "refusal": "content_filter"}
_PASS_THROUGH = ("top_p", "top_k", "metadata", "thinking", "service_tier")
_DATA_URL = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)
_CACHE = {"type": "ephemeral"}


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") in ("text", None))
    return "" if content is None else str(content)


def _blocks(content: Any) -> list[dict[str, Any]]:
    """OpenAI message content as Claude content blocks. Empty text is left out: Claude refuses it."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    out: list[dict[str, Any]] = []
    for part in content if isinstance(content, list) else [content]:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text" and str(part.get("text", "")).strip():
            block: dict[str, Any] = {"type": "text", "text": str(part["text"])}
            if part.get("cache_control"):
                block["cache_control"] = part["cache_control"]
            out.append(block)
        elif kind == "image_url":
            url = part.get("image_url", {}).get("url", "") if isinstance(part.get("image_url"), dict) else str(part.get("image_url", ""))
            match = _DATA_URL.match(url)
            if match:
                out.append({"type": "image", "source": {"type": "base64", "media_type": match.group(1).lower(), "data": match.group(2)}})
            elif url.startswith(("http://", "https://")):
                out.append({"type": "image", "source": {"type": "url", "url": url}})
        elif kind in ("image", "document", "tool_use", "tool_result"):   # already Claude-shaped
            out.append(part)
    return out


def _args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {"_raw": str(raw)}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for tool in tools:
        fn = tool.get("function", tool) if isinstance(tool, dict) else {}
        if not fn.get("name"):
            continue
        out.append({"name": fn["name"], "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return out


def _tool_choice(choice: Any) -> dict[str, Any] | None:
    if choice in (None, "auto"):
        return {"type": "auto"}
    if choice == "required":
        return {"type": "any"}
    if choice == "none":
        return None
    if isinstance(choice, dict):
        name = (choice.get("function") or {}).get("name") or choice.get("name")
        if name:
            return {"type": "tool", "name": name}
    return {"type": "auto"}


def to_request(payload: dict[str, Any], model: str, *, auto_cache: bool = True) -> dict[str, Any]:
    system_parts: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": list(blocks)})

    for message in payload.get("messages") or []:
        role = message.get("role")
        content = message.get("content")
        if role in ("system", "developer"):
            system_parts.extend(b for b in _blocks(content) if b["type"] == "text")
        elif role == "assistant":
            blocks = _blocks(content)
            for call in message.get("tool_calls") or []:
                fn = call.get("function") or {}
                blocks.append({"type": "tool_use", "id": call.get("id") or f"call_{len(blocks)}",
                               "name": fn.get("name", ""), "input": _args(fn.get("arguments"))})
            add("assistant", blocks)
        elif role == "tool":
            result = _text_of(content) if not isinstance(content, list) else [b for b in _blocks(content)]
            add("user", [{"type": "tool_result", "tool_use_id": message.get("tool_call_id", ""), "content": result or "(empty)"}])
        else:
            add("user", _blocks(content))

    if not turns or turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": [{"type": "text", "text": "(lanjutkan)"}]})

    body: dict[str, Any] = {"model": model, "messages": turns, "max_tokens": int(payload.get("max_tokens") or 4096)}
    if system_parts:
        body["system"] = system_parts
    thinking = payload.get("thinking")
    if payload.get("temperature") is not None and not thinking:   # extended thinking requires the default temperature
        body["temperature"] = max(0.0, min(1.0, float(payload["temperature"])))
    for name in _PASS_THROUGH:
        if payload.get(name) is not None:
            body[name] = payload[name]
    stop = payload.get("stop")
    if stop:
        body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    if payload.get("tools"):
        choice = _tool_choice(payload.get("tool_choice"))
        if choice is not None:
            body["tools"] = _tools(payload["tools"])
            body["tool_choice"] = choice
    if payload.get("stream"):
        body["stream"] = True
    if auto_cache:
        _mark_cache(body)
    return body


def _mark_cache(body: dict[str, Any]) -> None:
    """Cache the system prompt and everything up to the newest turn. Claude allows four marks."""
    marks = sum(1 for b in body.get("system", []) if b.get("cache_control"))
    marks += sum(1 for t in body["messages"] for b in t["content"] if isinstance(b, dict) and b.get("cache_control"))
    if body.get("system") and marks < 4 and not body["system"][-1].get("cache_control"):
        body["system"][-1]["cache_control"] = dict(_CACHE)
        marks += 1
    if marks < 4:
        last = body["messages"][-1]["content"]
        if last and isinstance(last[-1], dict) and not last[-1].get("cache_control"):
            last[-1]["cache_control"] = dict(_CACHE)


def usage_of(usage: dict[str, Any] | None) -> dict[str, Any]:
    """Claude usage in the OpenAI shape, with the Claude fields kept for the usage log."""
    u = usage or {}
    read = int(u.get("cache_read_input_tokens") or 0)
    write = int(u.get("cache_creation_input_tokens") or 0)
    prompt = int(u.get("input_tokens") or 0) + read + write
    output = int(u.get("output_tokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": output, "total_tokens": prompt + output,
            "prompt_tokens_details": {"cached_tokens": read},
            "cache_read_input_tokens": read, "cache_creation_input_tokens": write}


def to_response(data: dict[str, Any], model: str) -> dict[str, Any]:
    text, thinking, calls = [], [], []
    for block in data.get("content") or []:
        kind = block.get("type")
        if kind == "text":
            text.append(block.get("text", ""))
        elif kind == "thinking":
            thinking.append(block.get("thinking", ""))
        elif kind == "tool_use":
            calls.append({"id": block.get("id", ""), "type": "function",
                          "function": {"name": block.get("name", ""), "arguments": json.dumps(block.get("input") or {})}})
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
    if calls:
        message["tool_calls"] = calls
    if thinking:
        message["reasoning_content"] = "".join(thinking)
    return {"id": data.get("id", ""), "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": STOP_REASONS.get(data.get("stop_reason") or "", "stop")}],
            "usage": usage_of(data.get("usage"))}


class StreamTranslator:
    """Feed it Claude stream events one by one; it returns the OpenAI chunks to yield."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.usage: dict[str, Any] = {}
        self._tool_index: dict[int, int] = {}   # Claude block index -> OpenAI tool_call index
        self._id = ""

    def _chunk(self, delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {"id": self._id, "object": "chat.completion.chunk", "model": self.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    def feed(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind = event.get("type")
        if kind == "message_start":
            message = event.get("message") or {}
            self._id = message.get("id", "")
            self.usage.update(message.get("usage") or {})
            return [self._chunk({"role": "assistant", "content": ""})]
        if kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                idx = self._tool_index.setdefault(int(event.get("index", 0)), len(self._tool_index))
                return [self._chunk({"tool_calls": [{"index": idx, "id": block.get("id", ""), "type": "function",
                                                      "function": {"name": block.get("name", ""), "arguments": ""}}]})]
            if block.get("type") == "text" and block.get("text"):
                return [self._chunk({"content": block["text"]})]
            return []
        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                return [self._chunk({"content": delta.get("text", "")})]
            if dtype == "thinking_delta":
                return [self._chunk({"reasoning_content": delta.get("thinking", "")})]
            if dtype == "input_json_delta":
                found = self._tool_index.get(int(event.get("index", 0)))
                if found is None:
                    return []
                return [self._chunk({"tool_calls": [{"index": found, "function": {"arguments": delta.get("partial_json", "")}}]})]
            return []
        if kind == "message_delta":
            self.usage.update({k: v for k, v in (event.get("usage") or {}).items() if v is not None})
            reason = (event.get("delta") or {}).get("stop_reason")
            return [self._chunk({}, STOP_REASONS.get(reason or "", "stop"))] if reason else []
        if kind == "message_stop":
            return [{"id": self._id, "object": "chat.completion.chunk", "model": self.model, "choices": [],
                     "usage": usage_of(self.usage)}]
        if kind == "error":
            error = event.get("error") or {}
            raise StreamError(str(error.get("message") or error), str(error.get("type") or ""))
        return []


class StreamError(RuntimeError):
    def __init__(self, message: str, kind: str = "") -> None:
        super().__init__(message)
        self.kind = kind


__all__ = ["to_request", "to_response", "usage_of", "StreamTranslator", "StreamError", "STOP_REASONS"]
