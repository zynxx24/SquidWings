"""
model.connection.graphify_client — 9Router HTTP client (urllib-based).

Lightweight HTTP client yang pakai urllib (bukan httpx) untuk
komunikasi langsung ke 9Router. Mendukung SSE parsing dan
standard JSON response.

Usage:
    client = GraphifyClient(router_url, api_key)
    text, pt, ct = client.llm(messages, max_tokens=8192)
"""

from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.request
from typing import Any, cast

from utils.logger import get_logger

logger = get_logger(__name__)

# Rough token estimator
def est_tokens(text: str) -> int:
    """Estimate token count (~4 chars per token)."""
    return max(1, len(text) // 4)


def _parse_sse(raw: str) -> dict[str, Any]:
    """Gabungkan stream SSE (data: {...}) menjadi satu objek chat completion."""
    parts, usage, finish = [], {}, "stop"
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("usage"):
            usage = obj["usage"]
        for ch in obj.get("choices") or []:
            piece = (ch.get("delta") or ch.get("message") or {}).get("content")
            if piece:
                parts.append(piece)
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": "".join(parts)},
                "finish_reason": finish,
            }
        ],
        "usage": usage,
    }


def _parse_body(raw: str) -> dict[str, Any]:
    """Parse response body — detect SSE vs JSON automatically."""
    raw = raw.strip()
    if not raw:
        raise RuntimeError("9Router membalas dengan body kosong")
    if raw.startswith("data:") or "\ndata:" in raw:
        return _parse_sse(raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return cast(dict[str, Any], data)
        raise RuntimeError(f"Balasan 9Router bukan dict JSON: {raw[:300]!r}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Balasan 9Router bukan JSON: {raw[:300]!r}"
        ) from exc


class GraphifyClient:
    """HTTP client for 9Router using urllib.

    Lightweight alternative to httpx-based APIClient.
    """

    def __init__(
        self,
        router_url: str,
        api_key: str,
        default_model: str = "",
        timeout: int = 120,
    ) -> None:
        self._router_url = router_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._timeout = timeout

    def http_json(
        self,
        method: str,
        path: str,
        body: Any = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Send HTTP request to 9Router.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (e.g. /v1/chat/completions)
            body: Request body (will be JSON-encoded)
            timeout: Request timeout

        Returns:
            Parsed response dict.
        """
        url = self._router_url + path
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        effective_timeout = timeout or self._timeout

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as r:
                return _parse_body(r.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            body_text = ""
            with contextlib.suppress(OSError, UnicodeError):
                body_text = e.read().decode("utf-8", "ignore")
            raise RuntimeError(
                f"9Router HTTP {e.code}: {body_text[:300]}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"9Router connection error: {e.reason}"
            ) from e

    def llm(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.2,
        stream: bool = False,
    ) -> tuple[str, int, int]:
        """Call LLM via 9Router.

        Args:
            messages: Chat messages.
            model: Model ID (uses default if not specified).
            max_tokens: Max output tokens.
            temperature: Sampling temperature.
            stream: Whether to request streaming.

        Returns:
            Tuple of (text, prompt_tokens, completion_tokens).
        """
        effective_model = model or self._default_model

        data = self.http_json("POST", "/v1/chat/completions", {
            "model": effective_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        })

        if data.get("error"):
            raise RuntimeError(f"9Router error: {data['error']}")

        text = (
            (data.get("choices") or [{}])[0]
            .get("message", {})
            .get("content") or ""
        ).strip()

        if not text:
            raise RuntimeError(
                f"Jawaban kosong dari model. Respons mentah: "
                f"{json.dumps(data)[:300]}"
            )

        usage = data.get("usage") or {}
        pt = usage.get("prompt_tokens") or est_tokens(
            " ".join(m.get("content", "") for m in messages)
        )
        ct = usage.get("completion_tokens") or est_tokens(text)

        logger.info(
            f"[graphify] {effective_model} | "
            f"in={pt} out={ct} total={pt + ct}"
        )

        return text, pt, ct
