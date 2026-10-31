"""Per-session choices, stored with the session so they survive restarts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

PONYTAIL_MODES = ("off", "lite", "full", "ultra")


@dataclass
class SessionSettings:
    """What this chat uses. An empty ponytail means the PONYTAIL_MODE default."""

    ponytail: str = ""
    graphify: bool = True
    aislop: bool = True
    # Contextual skills woken in this session. They stay active until the session ends.
    skills: list[str] = field(default_factory=list)


def _path(workspace: str | Path) -> Path:
    return Path(workspace) / ".agent_memory" / "session.json"


def load(workspace: str | Path) -> SessionSettings:
    try:
        data = json.loads(_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SessionSettings()
    known = {key: data[key] for key in SessionSettings.__dataclass_fields__ if key in data}
    return SessionSettings(**known)


def activate_skills(workspace: str | Path, names: list[str]) -> list[str]:
    """Remember contextual skills as active in this session. Returns all active ones."""
    settings = load(workspace)
    added = [name for name in names if name not in settings.skills]
    if added:
        settings.skills = [*settings.skills, *added]
        save(workspace, settings)
    return settings.skills


def save(workspace: str | Path, settings: SessionSettings) -> None:
    path = _path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(asdict(settings)), encoding="utf-8")
    temporary.replace(path)
