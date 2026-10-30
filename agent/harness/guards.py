"""Argument repair, tool-result trimming, and stuck detection."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

# Names small models commonly use instead of the declared parameter.
_ALIASES: dict[str, tuple[str, ...]] = {
    "file_path": ("path", "filepath", "filename", "file"),
    "path": ("file_path", "filepath", "filename", "file", "rel_path"),
    "dir_path": ("path", "directory", "dir", "folder"),
    "query": ("q", "search", "search_query", "keyword"),
    "pattern": ("query", "q", "search", "regex", "keyword"),
    "dotted_path": ("query", "key", "json_path", "jsonpath"),
    "text": ("query", "content", "q"),
    "body": ("content", "text", "contents"),
    "rel_path": ("path", "file_path", "filename"),
    "content": ("text", "body", "data", "contents"),
    "command": ("cmd", "shell", "script"),
    "search": ("old", "old_text", "old_string", "find"),
    "replace": ("new", "new_text", "new_string", "replacement"),
}

_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def repair_json_arguments(raw: Any) -> dict[str, Any] | None:
    """Best-effort parse of malformed tool arguments. None if beyond repair."""
    if isinstance(raw, dict):
        if set(raw) in ({"_raw"}, {"raw"}):  # markers the parsers leave on bad JSON
            return repair_json_arguments(next(iter(raw.values())))
        return raw
    if not isinstance(raw, str):
        return None

    text = _FENCE.sub("", raw.strip()).strip()
    if not text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    text = _TRAILING_COMMA.sub(r"\1", text[start : end + 1])
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(text)  # single quotes, True/None
        except (ValueError, SyntaxError):
            return None
    return parsed if isinstance(parsed, dict) else None


def _coerce(value: Any, expected: str) -> Any:
    """Fix the scalar type confusions small models make ("5" for 5)."""
    if not isinstance(value, str):
        return value
    lowered = value.strip().lower()
    if expected == "integer" and re.fullmatch(r"-?\d+", lowered):
        return int(lowered)
    if expected == "number" and re.fullmatch(r"-?\d+(\.\d+)?", lowered):
        return float(lowered)
    if expected == "boolean" and lowered in {"true", "false"}:
        return lowered == "true"
    return value


def normalize_arguments(
    arguments: dict[str, Any], parameters: dict[str, Any]
) -> tuple[dict[str, Any], str | None, bool]:
    """Fit arguments to a JSON schema.

    Returns:
        ``(arguments, error, repaired)``. ``error`` describes what is still
        wrong, in terms the model can act on, instead of a Python TypeError.
    """
    properties = parameters.get("properties") or {}
    if not properties:
        return arguments, None, False

    fixed = dict(arguments)
    repaired = False
    for name in properties:
        if name in fixed:
            continue
        for alias in _ALIASES.get(name, ()):
            if alias in fixed and alias not in properties:
                fixed[name] = fixed.pop(alias)
                repaired = True
                break

    unknown = [k for k in fixed if k not in properties]
    for key in unknown:
        del fixed[key]
        repaired = True

    for name, spec in properties.items():
        if name in fixed:
            coerced = _coerce(fixed[name], str(spec.get("type", "")))
            if coerced is not fixed[name]:
                fixed[name] = coerced
                repaired = True

    missing = [n for n in parameters.get("required") or [] if n not in fixed]
    if missing:
        expected = ", ".join(
            f"{n} ({properties[n].get('type', 'any')})" for n in properties
        )
        return fixed, f"Missing required argument(s): {', '.join(missing)}. Expected: {expected}.", repaired
    return fixed, None, repaired


def clip_text(text: str, limit: int) -> str:
    """Keep the head and tail of long output; errors usually sit at the end."""
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    omitted = len(text) - limit
    return (
        f"{text[:head]}\n...[{omitted} characters omitted; re-read a narrower range if needed]...\n"
        f"{text[-tail:]}"
    )


def compact_messages(messages: list[dict[str, Any]], max_chars: int, keep_recent: int = 6) -> int:
    """Blank the oldest tool results once the conversation is too large.

    The tool call stays in place so the model still knows what it did and can
    repeat it. Returns the number of results dropped.
    """
    total = sum(len(str(m.get("content") or "")) for m in messages)
    if total <= max_chars:
        return 0

    tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    dropped = 0
    for index in tool_indexes[:-keep_recent] if keep_recent else tool_indexes:
        if total <= max_chars:
            break
        size = len(str(messages[index].get("content") or ""))
        if size <= 200:
            continue
        messages[index] = {
            **messages[index],
            "content": "[old result removed to save space; call the tool again if you still need it]",
        }
        total -= size
        dropped += 1
    return dropped


def tool_failed(tool_result: dict[str, Any]) -> str | None:
    """Return the error text when a tool failed, including soft ``ok: false`` results."""
    if not isinstance(tool_result, dict):
        return None
    if not tool_result.get("success"):
        return str(tool_result.get("error") or "unknown error")
    payload = tool_result.get("result")
    if isinstance(payload, dict):
        if payload.get("ok") is False or payload.get("success") is False:
            return str(payload.get("error") or payload.get("stderr") or "tool reported failure")
        if payload.get("timed_out"):
            return "command timed out"
    return None


def tool_trace(history: list[dict[str, Any]], limit: int = 500) -> str:
    """One line on what a run's tools did, kept with the answer for later turns."""
    parts: list[str] = []
    for entry in history:
        call = entry.get("tool_call") or {}
        args = call.get("arguments")
        gist = ""
        if isinstance(args, dict):
            gist = next(
                (str(args[k]) for k in ("path", "file_path", "dir_path", "query", "command") if args.get(k)),
                "",
            )
        failed = " (failed)" if tool_failed(entry.get("result") or {}) else ""
        parts.append(f"{call.get('name')} {' '.join(gist.split())[:60]}".strip() + failed)
    return "; ".join(dict.fromkeys(parts))[:limit]


class LoopGuard:
    """Spots a run that is going in circles."""

    def __init__(self, stuck_after: int) -> None:
        self._stuck_after = max(1, stuck_after)
        self._last_signature = ""
        self._repeats = 0
        self._failures = 0
        self.needs_help = False
        self.give_up = False

    def observe(self, name: str, arguments: Any, error: str | None) -> str | None:
        """Record one tool call. Returns a nudge for the model when it is stuck."""
        signature = f"{name}:{json.dumps(arguments, sort_keys=True, default=str)}"
        self._repeats = self._repeats + 1 if signature == self._last_signature else 0
        self._last_signature = signature
        self._failures = self._failures + 1 if error else 0

        if self._repeats >= self._stuck_after + 3:
            self.give_up = True
        if self._repeats >= self._stuck_after:
            self.needs_help = True
            return (
                f"Kamu sudah memanggil {name} dengan argumen yang sama {self._repeats + 1} kali "
                "dan hasilnya tidak akan berubah. Gunakan hasil yang sudah ada, atau coba "
                "pendekatan lain."
            )
        if self._failures >= self._stuck_after:
            self.needs_help = True
            return (
                f"{self._failures} tool call terakhir gagal (terakhir: {error}). Berhenti "
                "sejenak: baca pesan errornya, periksa asumsimu (path, nama argumen, keberadaan "
                "berkas), lalu ubah pendekatan."
            )
        return None
