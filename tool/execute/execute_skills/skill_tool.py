"""Tool access to the skill library in ``brain/skills``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.skills import install, load_all, runnable, stored, visible
from utils import session_settings
from utils.workspace_context import get_active_session, get_active_workspace


def _search(query: str) -> dict[str, Any]:
    """Skills for a need, active and stored in one ranking."""
    from brain.skills import sources

    found = [{k: v for k, v in item.items() if k != "path"} for item in sources.find(query)]
    if not found:
        return {"ok": False, "error": f"No skill matches '{query}'."}
    return {"ok": True, "query": query, "skills": found,
            "note": "Load one with load_skill(name=...). Stored skills open the same way."}


async def load_skill(name: str = "", file: str = "", install_files: bool = False, search: str = "") -> dict[str, Any]:
    """Return a skill's guide, one of its companion guides, or install its files into the project.

    ``search`` lists the skills that fit a need, stored ones included. A name written the way
    Claude Code skills refer to each other (``superpowers:writing-plans``) is accepted as is.
    """
    if search.strip() or not name.strip():
        return _search(search or name) if (search or name).strip() else {
            "ok": False, "error": "Give a skill name, or search=\"what you need\"."}
    skills = load_all()
    key = (name or "").strip().lower().removesuffix(".md").replace("_", "-").replace(" ", "-")
    key = key.rsplit(":", 1)[-1].lstrip("/")
    skill = skills.get(key) or stored(key)
    if skill is None:
        # Name only what the model may see: dormant skills stay out until their context wakes them.
        workspace = get_active_workspace()
        active = session_settings.load(workspace).skills if workspace else []
        shown = sorted(s.name for s in visible(active))
        error = f"Unknown skill '{name}'. Available: {', '.join(shown)}"
        # Stored skills (cloned from GitHub, kept out of the index) are offered when the name is close.
        from brain.skills import sources

        if similar := sources.search_stored(name.replace("-", " ")):
            error += ". Stored skills that may fit (load one by its exact name): " + "; ".join(
                f"{n}: {d}" for n, d in similar)
        return {"ok": False, "error": error}
    result: dict[str, Any] = {"ok": True, "name": skill.name}
    if skill.scope == "stored":
        result["note"] = "Stored skill from GitHub: opened on request only, it stays out of the index."
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
    workspace = get_active_workspace()
    if file:
        text = skill.guide(file.strip())
        if text is None:
            return {"ok": False, "error": f"'{file}' is not a guide of {skill.name}. Guides: {', '.join(skill.guides) or 'none'}"}
        text, installed_at = runnable(skill, text, workspace, get_active_session())
        result.update(file=file.strip(), guide=text)
        if installed_at:
            result["installed_at"] = installed_at
        return result
    guide, installed_at = runnable(skill, None, workspace, get_active_session())
    result["guide"] = guide
    if installed_at:
        result["installed_at"] = installed_at
    if skill.guides:
        result["more_guides"] = list(skill.guides)
    return result
