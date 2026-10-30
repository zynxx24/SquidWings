"""Session distillation: turn the chat log into a vault note.

The conversation store keeps every message, but a raw transcript is useless
as long-term memory: it is long, repetitive, and unsearchable by concept. L1
boot only ever lists sessions that exist as notes, and ``10-sessions/`` stayed
empty because nothing wrote there. This module closes that loop.

Design rules:

* **Code only, no model call.** Distillation must cost zero tokens and work
  when every provider is down. It is deterministic extraction, not a summary
  written by an LLM.
* **Idempotent per session.** The note path is derived from the session id, so
  the tenth turn rewrites the first turn's note instead of creating ten.
* **Cheap on repeat.** A signature over the rendered body skips the write (and
  the index churn that would bust the L1 cache) when nothing changed.
* **Never raises.** Memory enrichment must not be able to fail a turn.
"""

from __future__ import annotations

import re
import time
from typing import Any

from tool.execute.execute_local_memory import vault_tool
from tool.execute.execute_local_memory.vault import constants as C
from utils.logger import get_logger

logger = get_logger(__name__)

# Below this many messages a session is still a question, not a history.
MIN_MESSAGES = 4
MAX_USER_LINES = 12
MAX_DECISIONS = 8
MAX_FILES = 12
_HEAD_CHARS = 400

_SIGNATURES: dict[str, str] = {}

_FILE_RE = re.compile(
    r"(?:[\w.\-]+/)*[\w.\-]+\.(?:py|ts|tsx|js|jsx|md|json|toml|yaml|yml|css|html)"
)
_DECISION_PREFIXES = ("decision:", "keputusan:", "kita akan", "we will", "diputuskan")
_NEXT_PREFIXES = ("next step", "selanjutnya", "berikutnya", "todo", "belum selesai")


def _clean(text: str, limit: int = _HEAD_CHARS) -> str:
    """One-line, whitespace-collapsed, length-capped copy of a message."""
    flat = " ".join((text or "").split())
    return flat[:limit].rstrip()


def _slug(text: str, max_len: int = 48) -> str:
    flat = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (flat[:max_len].rstrip("-")) or "session"


def _note_path(session_id: str, title: str, created: float) -> str:
    """Stable per-session note path inside ``10-sessions/``.

    The session id suffix is what makes rewrites land on the same file even
    when the title changes after the first few turns.
    """
    stamp = time.strftime("%Y-%m-%d", time.localtime(created or time.time()))
    suffix = re.sub(r"[^A-Za-z0-9]", "", session_id)[-8:] or "s"
    return f"{C.FOLDER_SESSIONS}/{stamp}-{_slug(title)}-{suffix}.md"


def _collect(messages: list[Any]) -> dict[str, Any]:
    """Extract structure from a raw message list."""
    users: list[str] = []
    decisions: list[str] = []
    next_steps: list[str] = []
    files: list[str] = []
    models: list[str] = []
    first_ts = 0.0

    for msg in messages:
        role = getattr(msg, "role", "")
        content = getattr(msg, "content", "") or ""
        ts = float(getattr(msg, "timestamp", 0.0) or 0.0)
        if ts and not first_ts:
            first_ts = ts

        if role == "user":
            line = _clean(content)
            if line:
                users.append(line)
            continue

        if role != "assistant":
            continue

        model = getattr(msg, "model_used", "") or ""
        if model and model not in models:
            models.append(model)

        for raw_line in content.splitlines():
            line = _clean(raw_line.strip(" -*\t"), limit=200)
            if not line:
                continue
            low = line.lower()
            if low.startswith(_DECISION_PREFIXES) and len(decisions) < MAX_DECISIONS:
                decisions.append(line)
            elif low.startswith(_NEXT_PREFIXES) and len(next_steps) < MAX_DECISIONS:
                next_steps.append(line)
            if len(files) < MAX_FILES:
                for match in _FILE_RE.finditer(line):
                    candidate = match.group(0)
                    if candidate not in files:
                        files.append(candidate)

    return {
        "users": users,
        "decisions": decisions,
        "next_steps": next_steps,
        "files": files,
        "models": models,
        "created": first_ts,
        "title": users[0] if users else "Session",
    }


def _render(session_id: str, data: dict[str, Any], turns: int) -> str:
    """Markdown body for the session note."""
    lines = [f"# {data['title'][:120]}", ""]
    lines.append(f"Session `{session_id}` - {turns} messages.")
    if data["models"]:
        lines.append(f"Models: {', '.join(data['models'][:5])}")
    lines.append("")

    recent = data["users"][-MAX_USER_LINES:]
    if recent:
        lines.append("## Requests")
        for item in recent:
            lines.append(f"- {item}")
        lines.append("")

    if data["decisions"]:
        lines.append("## Decisions")
        for item in data["decisions"]:
            lines.append(f"- {item}")
        lines.append("")

    if data["files"]:
        lines.append("## Files touched")
        for item in data["files"]:
            lines.append(f"- {item}")
        lines.append("")

    if data["next_steps"]:
        lines.append("## Next steps")
        for item in data["next_steps"]:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


async def distill_session(
    store: Any,
    session_id: str,
    *,
    workspace_path: str | None = None,
    force: bool = False,
    min_messages: int = MIN_MESSAGES,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Write or refresh the vault note for one session. Never raises."""
    try:
        messages = list(store.get_history(session_id) or [])
    except Exception as exc:  # noqa: BLE001 - memory must never break a turn
        logger.warning("[session_distill] history unavailable: %s", exc)
        return {"ok": False, "written": False, "reason": f"history: {exc}"}

    if len(messages) < max(2, min_messages):
        return {"ok": True, "written": False, "reason": "too-short"}

    try:
        data = _collect(messages)
        body = _render(session_id, data, len(messages))
        rel = _note_path(session_id, data["title"], data["created"])

        key = f"{rel}"
        signature = f"{len(messages)}:{hash(body)}"
        if not force and _SIGNATURES.get(key) == signature:
            return {"ok": True, "written": False, "reason": "unchanged", "path": rel}

        result = await vault_tool.vault_write(
            workspace_path=workspace_path,
            path=rel,
            body=body,
            note_type=C.TYPE_SESSION,
            status=C.STATUS_ACTIVE,
            confidence=C.CONFIDENCE_HIGH,
            tags=["session", *(tags or [])],
        )

        if not result.get("ok"):
            logger.warning("[session_distill] write failed: %s", result.get("error"))
            return {"ok": False, "written": False, "reason": result.get("error", "")}

        _SIGNATURES[key] = signature
        # The vault changed outside vault_tool's own caches; L1 must rebuild.
        try:
            from tool.execute.execute_local_memory import vault_context

            vault_context.clear_cache()
        except Exception:  # noqa: BLE001
            pass

        return {
            "ok": True,
            "written": True,
            "path": rel,
            "bytes": result.get("bytes", len(body)),
            "turns": len(messages),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[session_distill] failed: %s", exc)
        return {"ok": False, "written": False, "reason": str(exc)}


def clear_cache() -> None:
    _SIGNATURES.clear()


__all__ = ["distill_session", "clear_cache", "MIN_MESSAGES"]
