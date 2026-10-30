"""Local memory tools — Obsidian vault + Graphify endpoint.

All executors accept keyword arguments only, matching ToolRegistry.execute().
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from config.settings import get_settings
from tool.execute.execute_local_memory.graphify_store import GraphifyStore
from tool.execute.execute_local_ops.path_guard import PathGuardError
from utils.logger import get_logger

logger = get_logger(__name__)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": message}
    payload.update(extra)
    return payload


def _ok(**payload: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"ok": True}
    base.update(payload)
    return base


def _graphify_store() -> GraphifyStore:
    s = get_settings()
    endpoint = str(getattr(s, "graphify_memory_endpoint", "") or "").strip()
    timeout = float(getattr(s, "graphify_memory_timeout_seconds", 5.0))
    if not endpoint:
        raise PathGuardError("GRAPHIFY_MEMORY_ENDPOINT is not configured")
    return GraphifyStore(endpoint, timeout)


def _graphify_endpoint_configured() -> bool:
    s = get_settings()
    return bool(str(getattr(s, "graphify_memory_endpoint", "") or "").strip())


def _payload_to_markdown(kind: str, payload: dict[str, Any]) -> tuple[str, str]:
    """Render an event payload as (title, markdown body) for the vault."""
    title = str(
        payload.get("title") or payload.get("query") or payload.get("name") or kind
    ).strip()[:80] or kind
    lines = [f"# {title}", ""]
    for key, value in payload.items():
        if key in {"title"}:
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, indent=2)[:4000]
            lines += [f"## {key}", "", "```json", rendered, "```", ""]
        else:
            lines.append(f"- **{key}**: {value}")
    return title, "\n".join(lines).rstrip() + "\n"


async def _vault_ingest(
    kind: str,
    payload: dict[str, Any],
    workspace: str | None,
    tags: list[str] | None,
) -> dict[str, Any]:
    """Fallback sink: store the event as a note in the built-in vault."""
    from tool.execute.execute_local_memory.vault_tool import vault_write

    title, body = _payload_to_markdown(kind, payload)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-")[:60] or kind
    stamp = time.strftime("%Y%m%d-%H%M%S")
    result = await vault_write(
        workspace_path=workspace,
        path=f"00-inbox/{kind}-{stamp}-{slug}",
        body=body,
        note_type="note",
        tags=sorted({kind, "graphify", *(tags or [])}),
    )
    result["backend"] = "vault"
    return result


async def _vault_query(
    kind: str | None,
    workspace: str | None,
    limit: int,
    text: str | None,
) -> dict[str, Any]:
    from tool.execute.execute_local_memory.vault_tool import vault_search

    result = await vault_search(
        workspace_path=workspace,
        query=text or "",
        tag=kind or None,
        limit=limit,
    )
    result["backend"] = "vault"
    return result


async def graphify_ingest(
    kind: str,
    payload: dict[str, Any],
    workspace: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Send an event to Graphify, falling back to the built-in vault.

    With no endpoint configured, or when the endpoint is unreachable, the
    event is still persisted locally so memory is never silently lost.
    """
    kind = (kind or "event").strip() or "event"
    payload = payload if isinstance(payload, dict) else {"value": payload}
    try:
        if not _graphify_endpoint_configured():
            return await _vault_ingest(kind, payload, workspace, tags)
        store = _graphify_store()
        result = await store.ingest(kind, payload, workspace, tags)
        if not result.get("ok"):
            fallback = await _vault_ingest(kind, payload, workspace, tags)
            fallback["remote_error"] = result.get("error")
            return fallback
        result["backend"] = "graphify"
        return result
    except PathGuardError as exc:
        return _error(str(exc))
    except Exception as exc:  # pragma: no cover
        logger.warning("graphify_ingest failed: %s", exc)
        return _error(f"graphify_ingest failed: {exc}")


async def graphify_query(
    kind: str | None = None,
    workspace: str | None = None,
    limit: int = 20,
    text: str | None = None,
) -> dict[str, Any]:
    """Query Graphify, falling back to the built-in vault index."""
    limit = max(1, min(200, int(limit)))
    try:
        if not _graphify_endpoint_configured():
            return await _vault_query(kind, workspace, limit, text)
        store = _graphify_store()
        result = await store.query(kind, workspace, limit, text)
        if not result.get("ok"):
            fallback = await _vault_query(kind, workspace, limit, text)
            fallback["remote_error"] = result.get("error")
            return fallback
        result["backend"] = "graphify"
        return result
    except PathGuardError as exc:
        return _error(str(exc))
    except Exception as exc:  # pragma: no cover
        logger.warning("graphify_query failed: %s", exc)
        return _error(f"graphify_query failed: {exc}")


__all__ = ["graphify_ingest", "graphify_query"]
