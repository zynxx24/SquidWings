"""Redacted, append-only JSON Lines audit records for tool execution."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from utils.helpers import truncate_string
from utils.logger import get_logger

logger = get_logger(__name__)
SENSITIVE_KEYS = {"api_key", "authorization", "password", "secret", "token"}


def _redact(value: Any, depth: int = 0) -> Any:
    if depth >= 4:
        return "<max-depth>"
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if str(key).lower() in SENSITIVE_KEYS else _redact(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in value[:30]]
    if isinstance(value, str):
        return truncate_string(value, 1000)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return truncate_string(str(value), 1000)


class AuditLogger:
    """Persist tool metadata without storing arbitrary result content.

    Audit failures are logged and never mask the actual tool result. Reads
    are bounded and skip malformed/truncated lines instead of failing.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        success: bool,
        latency_ms: float,
        session_id: str = "",
        model_id: str = "",
        error: str = "",
    ) -> None:
        entry = {
            "timestamp": time.time(),
            "tool_name": tool_name,
            "arguments": _redact(arguments),
            "success": success,
            "latency_ms": round(latency_ms, 2),
            "session_id": session_id,
            "model_id": model_id,
            "error": truncate_string(error, 1000) if error else "",
        }
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error("[audit] Could not persist tool event: %s", exc)

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        try:
            with self.path.open(encoding="utf-8") as stream:
                lines = stream.readlines()[-limit:]
        except FileNotFoundError:
            return []
        except OSError as exc:
            logger.error("[audit] Could not read tool events: %s", exc)
            return []
        events = []
        for line in lines:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    events.append(value)
            except json.JSONDecodeError:
                continue
        return events
