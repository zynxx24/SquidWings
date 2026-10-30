"""Per-session workspace context shared by the web UI and the agent loop.

The web chat has no active workspace of its own, so each session gets
``workspace/<session_id>/`` (same layout the CLI uses). The agent loop reads
the active path from a ContextVar, which stays isolated between concurrent
sessions, and falls back to the SQUIDWINGS_WORKSPACE env var.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = _PROJECT_ROOT / "workspace"

_active_workspace: ContextVar[str] = ContextVar("active_workspace", default="")


def session_workspace(session_id: str) -> Path:
    """Return (and create) workspace/<session_id>, sanitising the name."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "").strip("._") or "default"
    path = WORKSPACE_ROOT / safe[:64]
    path.mkdir(parents=True, exist_ok=True)
    return path


def set_active_workspace(path: str | Path) -> None:
    _active_workspace.set(str(path))


def get_active_workspace() -> str:
    return _active_workspace.get()
