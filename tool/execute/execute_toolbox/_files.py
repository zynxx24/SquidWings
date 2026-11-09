"""Workspace file access for toolbox tools: always inside the session workspace."""

from __future__ import annotations

from pathlib import Path

from config.settings import get_settings
from tool.execute.execute_local_ops.path_guard import resolve_inside, resolve_workspace
from utils.workspace_context import get_active_workspace

MAX_FILE_BYTES = 50 * 1024 * 1024
_PROTECTED = {".git", ".agent_memory", ".vault"}


def workspace(workspace_path: str | None) -> Path:
    return resolve_workspace(workspace_path or get_active_workspace() or None, get_settings().project_root)


def source(path: str, workspace_path: str | None) -> Path:
    """An existing file inside the workspace, not too large to load."""
    root = workspace(workspace_path)
    target = resolve_inside(root, path, must_exist=True)
    if not target.is_file():
        raise ValueError(f"{path} is not a file")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{path} is larger than 50 MB")
    return target


def destination(path: str, workspace_path: str | None) -> Path:
    """A file to write inside the workspace, never in .git or memory folders."""
    root = workspace(workspace_path)
    target = resolve_inside(root, path)
    parts = target.relative_to(root).parts
    if any(p in _PROTECTED for p in parts):
        raise ValueError(f"writing inside {parts[0]} is not allowed")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def relative(path: Path, workspace_path: str | None) -> str:
    root = workspace(workspace_path)
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
