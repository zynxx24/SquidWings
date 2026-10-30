"""Tool access to project memory."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from brain.memory.project_memory import ProjectMemory
from config.settings import get_settings
from tool.execute.execute_local_memory import user_profile
from tool.execute.execute_local_ops.path_guard import PathGuardError, resolve_workspace


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def _workspace(workspace_path: str | None) -> Path:
    return resolve_workspace(workspace_path, get_settings().project_root)


def _file_rows(memory: ProjectMemory, query: str, limit: int, prefer_ext: list[str] | None) -> list[dict[str, Any]]:
    return [
        {"path": f.rel_path, "score": f.score, "reason": f.reason, "status": f.status, "size": f.size}
        for f in memory.score_files(query, limit=limit, prefer_ext=prefer_ext)
    ]


def _recall_sync(workspace: Path, query: str, limit: int, token_budget: int) -> dict[str, Any]:
    memory = ProjectMemory(workspace)
    return {
        "ok": True,
        "summary": memory.summary(token_budget=token_budget),
        "files": _file_rows(memory, query, limit, None),
    }


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


def _remember_sync(workspace: Path, kind: str, text: str, why: str) -> dict[str, Any]:
    normalized = kind.strip().lower()
    if normalized in {"user", "preference"}:
        added: bool | None = user_profile.remember(text)
    else:
        added = ProjectMemory(workspace).remember(normalized, text, why)
    if added is None:
        return _error(f"unknown kind: {kind}. Use user, decision, fact, error, or progress.")
    return {"ok": True, "kind": kind, "added": added, "deduped": not added}


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

    def rank() -> dict[str, Any]:
        files = _file_rows(ProjectMemory(workspace), query, max(1, min(100, limit)), prefer_ext)
        return {"ok": True, "count": len(files), "files": files}

    return await asyncio.to_thread(rank)


def _forget_sync(workspace: Path, text: str) -> dict[str, Any]:
    from_project = ProjectMemory(workspace).forget(text)
    from_profile = user_profile.forget(text)
    return {"ok": True, "removed": from_project + from_profile}


async def memory_forget(text: str, workspace_path: str | None = None) -> dict[str, Any]:
    """Remove remembered items that match, from the project and the user profile."""
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(_forget_sync, workspace, text)


__all__ = ["memory_forget", "memory_rank_files", "memory_recall", "memory_remember"]
