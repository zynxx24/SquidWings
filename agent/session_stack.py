"""Everything one chat owns, in one place.

Each chat gets its own folder holding its project memory, Obsidian vault, code
graph, and aislop score, and its own Ponytail mode. Two chats never mix: what the
agent learns while working on one does not leak into the other. Your profile and
the playbooks stay global, because those are about you and about how to work.

    workspace/sessions/<session>/
      .agent_memory/   project memory, Ponytail mode, aislop score
      .vault/          this chat's Obsidian vault
      graphify-out/    this chat's code graph
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from brain.memory.project_memory import ProjectMemory
from security import sandbox
from tool.execute.execute_code_intel import quality_tools
from tool.execute.execute_local_memory import user_profile
from tool.execute.execute_local_memory.vault import constants as C
from utils import session_settings
from utils.session_settings import PONYTAIL_MODES, SessionSettings
from utils.workspace_context import (
    get_active_session,
    get_active_workspace,
    session_workspace,
    set_active_ponytail,
    set_active_session,
    set_active_workspace,
)


def enter(session_id: str, workspace: str | Path | None = None) -> SessionStack:
    """Make a session current for this task. Call once at the start of a turn.

    The state is held in context variables, so sessions running side by side do
    not disturb each other.
    """
    explicit = workspace or os.getenv("SQUIDWINGS_WORKSPACE", "").strip()
    folder = Path(explicit) if explicit else session_workspace(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    stack = SessionStack(session_id, folder)
    set_active_session(session_id)
    set_active_workspace(folder)
    set_active_ponytail(stack.settings.ponytail)
    return stack


def current() -> SessionStack | None:
    """The session entered for this task, if any."""
    workspace = get_active_workspace()
    return SessionStack(get_active_session(), Path(workspace)) if workspace else None


class SessionStack:
    def __init__(self, session_id: str, workspace: Path) -> None:
        self.session_id = session_id
        self.workspace = workspace

    @property
    def settings(self) -> SessionSettings:
        return session_settings.load(self.workspace)

    def update(self, **changes: Any) -> SessionSettings:
        """Change settings, ignoring unknown keys and invalid Ponytail modes."""
        settings = self.settings
        mode = changes.get("ponytail")
        if isinstance(mode, str) and mode in PONYTAIL_MODES:
            settings.ponytail = mode
        for key in ("graphify", "aislop"):
            if isinstance(changes.get(key), bool):
                setattr(settings, key, changes[key])
        session_settings.save(self.workspace, settings)
        return settings

    def status(self) -> dict[str, Any]:
        """What this session has right now, for the UI and the CLI."""
        settings = self.settings
        state = ProjectMemory(self.workspace).state if self.workspace.is_dir() else {}
        vault = self.workspace / ".vault"
        notes = sum(1 for _ in vault.rglob("*.md")) if vault.is_dir() else 0
        policy = sandbox.load_policy()
        try:
            backend, sandbox_error = sandbox.active_backend(policy), ""
        except sandbox.SandboxError as exc:
            backend, sandbox_error = "", str(exc)
        return {
            "session_id": self.session_id,
            "folder": str(self.workspace),
            "ponytail": {
                "mode": settings.ponytail or os.getenv("PONYTAIL_MODE", "full").strip().lower(),
                "custom": bool(settings.ponytail),
                "modes": list(PONYTAIL_MODES),
            },
            "graphify": {
                "installed": quality_tools.has_graphify(),
                "enabled": settings.graphify,
                **quality_tools.graph_stats(self.workspace),
            },
            "aislop": {
                "installed": quality_tools.has_aislop(),
                "enabled": settings.aislop,
                "last": quality_tools.last_score(self.workspace),
            },
            "vault": {"notes": notes, "path": str(vault), "folders": list(C.STANDARD_FOLDERS)},
            "memory": {
                "decisions": len(state.get("decisions", [])),
                "facts": len(state.get("facts", [])),
                "errors": len(state.get("errors", [])),
                "files": len(state.get("files", {})),
                "turns": state.get("turn_count", 0),
            },
            "profile": {"entries": len(user_profile.entries())},
            "sandbox": {
                "backend": backend,
                "network": policy.network,
                "mode": policy.mode,
                "error": sandbox_error,
            },
        }
