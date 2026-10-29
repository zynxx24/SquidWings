"""Path validation helpers for local operations.

All local tools must resolve paths inside the active workspace. This module
provides a single, auditable place to enforce that rule.
"""

from __future__ import annotations

from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)


class PathGuardError(ValueError):
    """Raised when a path escapes the workspace or is otherwise unsafe."""


def resolve_workspace(workspace_path: str | None, fallback: Path) -> Path:
    """Resolve the workspace root, validating it exists and is a directory."""
    if workspace_path:
        candidate = Path(workspace_path).expanduser()
        try:
            candidate = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PathGuardError(f"workspace not accessible: {workspace_path}") from exc
        if not candidate.is_dir():
            raise PathGuardError(f"workspace is not a directory: {workspace_path}")
        return candidate
    return fallback.resolve()


def resolve_inside(workspace: Path, raw_path: str, *, must_exist: bool = False) -> Path:
    """Resolve ``raw_path`` relative to ``workspace`` and ensure it stays inside.

    Rejects:
      * absolute paths outside the workspace
      * ``..`` traversal that escapes the workspace
      * symlinks whose real target escapes the workspace
    """
    if not raw_path or not raw_path.strip():
        raise PathGuardError("path must not be empty")

    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate

    try:
        resolved = candidate.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise PathGuardError(f"path does not exist: {raw_path}") from exc
    except (OSError, RuntimeError) as exc:
        raise PathGuardError(f"path not accessible: {raw_path}") from exc

    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise PathGuardError(
            f"path escapes workspace: {raw_path} (resolved to {resolved})"
        ) from exc

    return resolved


def is_binary_file(path: Path, probe_bytes: int = 8192) -> bool:
    """Heuristic: a file is binary if it contains a NUL byte in the first chunk."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(probe_bytes)
    except OSError:
        return True
    return b"\x00" in chunk


__all__ = ["PathGuardError", "resolve_workspace", "resolve_inside", "is_binary_file"]
