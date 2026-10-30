"""Skills: short playbooks written by people, loaded when a task calls for one.

Each ``*.md`` file here starts with a small header::

    ---
    name: fix-bug
    description: One line shown in the skill index.
    triggers: bug, error, perbaiki
    ---

The most relevant skills are placed in the prompt automatically, because small
models rarely ask for help on their own. The rest stay available through the
``load_skill`` tool, so they cost one line of prompt until needed. Set SKILLS_DIR
to add a folder of your own; a skill there replaces a built-in of the same name.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

_BUILTIN_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    triggers: tuple[str, ...]
    body: str


def _parse(path: Path) -> Skill | None:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not match:
        logger.warning(f"[skills] {path.name} has no header; skipped")
        return None
    fields = dict(
        (key.strip(), value.strip())
        for key, _, value in (line.partition(":") for line in match.group(1).splitlines())
    )
    if not fields.get("name"):
        return None
    return Skill(
        name=fields["name"],
        description=fields.get("description", ""),
        triggers=tuple(t.strip().lower() for t in fields.get("triggers", "").split(",") if t.strip()),
        body=match.group(2).strip(),
    )


@lru_cache(maxsize=1)
def load_all() -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    custom = os.getenv("SKILLS_DIR", "").strip()
    for directory in (_BUILTIN_DIR, Path(custom).expanduser() if custom else None):
        if directory is None or not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                skill = _parse(path)
            except OSError as exc:
                logger.warning(f"[skills] could not read {path}: {exc}")
                continue
            if skill:
                skills[skill.name] = skill
    return skills


def select(user_input: str, limit: int = 2) -> list[Skill]:
    """Skills whose triggers appear in the request, most matches first."""
    text = user_input.lower()
    scored: list[tuple[int, Skill]] = []
    for skill in load_all().values():
        score = sum(
            1 for trigger in skill.triggers
            if re.search(rf"(?<![a-z0-9]){re.escape(trigger)}(?![a-z0-9])", text)
        )
        if score:
            scored.append((score, skill))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [skill for _, skill in scored[:limit]]


def index_text() -> str:
    """One line per skill, so the model knows what it can ask for."""
    skills = load_all()
    if not skills:
        return ""
    lines = "\n".join(f"- {s.name}: {s.description}" for s in skills.values())
    return f"[SKILLS: call load_skill(name) for the full guide]\n{lines}"


__all__ = ["Skill", "index_text", "load_all", "select"]
