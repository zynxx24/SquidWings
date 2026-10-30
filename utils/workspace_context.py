"""Per-session workspace context shared by the web UI and the agent loop.

``workspace/`` is where the model works, kept apart from the agent's own code:

* ``workspace/sessions/<session_id>/``: one folder per chat or team run.
* ``workspace/projects/<name>/``: projects opened from the CLI with ``/tree``.

The agent loop reads
the active path from a ContextVar, which stays isolated between concurrent
sessions, and falls back to the SQUIDWINGS_WORKSPACE env var.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = _PROJECT_ROOT / "workspace"
SESSIONS_ROOT = WORKSPACE_ROOT / "sessions"
PROJECTS_ROOT = WORKSPACE_ROOT / "projects"

_active_workspace: ContextVar[str] = ContextVar("active_workspace", default="")
_active_session: ContextVar[str] = ContextVar("active_session", default="")
_active_ponytail: ContextVar[str] = ContextVar("active_ponytail", default="")


def session_workspace(session_id: str, create: bool = True) -> Path:
    """Return (and create) workspace/sessions/<session_id>, sanitising the name."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "").strip("._") or "default"
    path = SESSIONS_ROOT / safe[:64]
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def set_active_workspace(path: str | Path) -> None:
    _active_workspace.set(str(path))


def get_active_workspace() -> str:
    return _active_workspace.get()


def set_active_session(session_id: str) -> None:
    _active_session.set(session_id)


def get_active_session() -> str:
    return _active_session.get()


def set_active_ponytail(mode: str) -> None:
    """This session's Ponytail mode. Empty means the PONYTAIL_MODE default."""
    _active_ponytail.set(mode)


def get_active_ponytail() -> str:
    return _active_ponytail.get()
