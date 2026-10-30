"""Tool access to the skill library in ``brain/skills``."""

from __future__ import annotations

from typing import Any

from brain.skills import load_all


async def load_skill(name: str) -> dict[str, Any]:
    """Return the full text of a skill."""
    skills = load_all()
    skill = skills.get(name.strip().lower())
    if skill is None:
        return {"ok": False, "error": f"Unknown skill '{name}'. Available: {', '.join(sorted(skills))}"}
    return {"ok": True, "name": skill.name, "guide": skill.body}
