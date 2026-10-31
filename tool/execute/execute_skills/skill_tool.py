"""Tool access to the skill library in ``brain/skills``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.skills import install, load_all
from utils import session_settings
from utils.workspace_context import get_active_session, get_active_workspace


async def load_skill(name: str, file: str = "", install_files: bool = False) -> dict[str, Any]:
    """Return a skill's guide, one of its companion guides, or install its files into the project."""
    skills = load_all()
    skill = skills.get(name.strip().lower())
    if skill is None:
        return {"ok": False, "error": f"Unknown skill '{name}'. Available: {', '.join(sorted(skills))}"}
    result: dict[str, Any] = {"ok": True, "name": skill.name}
    # Asking for a dormant skill by name wakes it, for this session only.
    if skill.scope == "contextual" and (workspace := get_active_workspace()):
        session_settings.activate_skills(workspace, [skill.name])
    if missing := skill.missing():
        result["missing_programs"] = missing
    if install_files:
        workspace = get_active_workspace()
        if not workspace:
            return {"ok": False, "error": "No project folder is open, so there is nowhere to install the skill."}
        try:
            target = install(skill, Path(workspace), get_active_session())
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        result["installed_at"] = str(target.relative_to(workspace))
        result["note"] = "This copy belongs to this session only. Run its tools from installed_at."
        return result
    if file:
        text = skill.guide(file.strip())
        if text is None:
            return {"ok": False, "error": f"'{file}' is not a guide of {skill.name}. Guides: {', '.join(skill.guides) or 'none'}"}
        result.update(file=file.strip(), guide=text)
        return result
    result["guide"] = skill.body
    if skill.guides:
        result["more_guides"] = list(skill.guides)
    return result
