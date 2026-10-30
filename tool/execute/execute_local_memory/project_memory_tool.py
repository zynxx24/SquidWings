"""Tool executors for the smart memory layer."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from brain.memory.smart_memory import SmartMemory
from config.settings import get_settings
from tool.execute.execute_local_ops.path_guard import PathGuardError, resolve_workspace
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


def _workspace(workspace_path: str | None) -> Path:
    return resolve_workspace(workspace_path, get_settings().project_root)


def _recall_sync(
    workspace: Path, query: str, limit: int, token_budget: int
) -> dict[str, Any]:
    sm = SmartMemory(workspace)
    files = sm.score_files(query, limit=limit)
    summary = sm.build_summary(token_budget=token_budget)
    return _ok(
        summary=summary,
        files=[
            {
                "path": f.rel_path,
                "score": f.score,
                "reason": f.reason,
                "status": f.status,
            }
            for f in files
        ],
        keywords=sorted(query.lower().split())[:20] if query else [],
    )


async def memory_recall(
    query: str = "",
    limit: int = 15,
    token_budget: int = 400,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(
        _recall_sync, workspace, query, max(1, min(50, limit)), max(100, min(2000, token_budget))
    )


def _remember_sync(
    workspace: Path, kind: str, text: str, why: str
) -> dict[str, Any]:
    sm = SmartMemory(workspace)
    kind = kind.strip().lower()
    if kind == "decision":
        added = sm._add_decision(text)
    elif kind == "fact":
        added = sm._add_fact(text)
    elif kind == "error":
        added = sm._add_error(text)
    elif kind == "progress":
        sm.raw.record_progress(text, status="done", detail=why)
        added = True
    else:
        return _error(f"unknown kind: {kind}")
    return _ok(kind=kind, added=added, deduped=not added)


async def memory_remember(
    kind: str,
    text: str,
    why: str = "",
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(_remember_sync, workspace, kind, text, why)


def _capture_sync(
    workspace: Path, user_input: str, assistant_output: str, model_id: str
) -> dict[str, Any]:
    sm = SmartMemory(workspace)
    captured = sm.capture_from_turn(user_input, assistant_output, model_id=model_id)
    return _ok(**captured)


async def memory_capture(
    user_input: str,
    assistant_output: str,
    model_id: str = "",
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(
        _capture_sync, workspace, user_input, assistant_output, model_id
    )


def _rank_sync(
    workspace: Path, query: str, limit: int, prefer_ext: list[str] | None
) -> dict[str, Any]:
    sm = SmartMemory(workspace)
    files = sm.score_files(query, limit=limit, prefer_ext=prefer_ext)
    return _ok(
        count=len(files),
        files=[
            {
                "path": f.rel_path,
                "score": f.score,
                "reason": f.reason,
                "status": f.status,
                "size": f.size,
            }
            for f in files
        ],
    )


async def memory_rank_files(
    query: str = "",
    limit: int = 20,
    prefer_ext: list[str] | None = None,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(
        _rank_sync, workspace, query, max(1, min(100, limit)), prefer_ext
    )


__all__ = [
    "memory_recall",
    "memory_remember",
    "memory_capture",
    "memory_rank_files",
]
