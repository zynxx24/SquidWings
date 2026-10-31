"""Skills: short playbooks written by people, loaded when a task calls for one.

Each ``*.md`` file here starts with a small header::

    ---
    name: fix-bug
    description: One line shown in the skill index.
    triggers: bug, error, perbaiki
    ---

A bigger skill is a folder with a ``SKILL.md`` and companion files (more ``.md``
guides, code the skill runs). Its header may add ``requires: node, ffmpeg`` to
name the programs it needs; the index says which are missing.

Skills come in two scopes. ``core`` (the default) is the everyday kit: always in
the index, and its guide is added when a trigger matches. ``scope: contextual``
is for skills only some work needs, such as animation. It stays dormant, out of
the index and out of the prompt, until a request names one of its triggers. An
optional ``context:`` list must match as well, so "animasi hover di tombol" (CSS)
does not wake a video skill but "video animasi" does.
From then on it stays active for that session only, so "lanjutkan" does not
lose it halfway, and other chats never see it.

The most relevant skills are placed in the prompt automatically, because small
models rarely ask for help on their own. The rest stay available through the
``load_skill`` tool, so they cost one line of prompt until needed. Set SKILLS_DIR
to add a folder of your own; a skill there replaces a built-in of the same name.
SKILLS_DISABLED takes a comma-separated list of skills to leave out.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
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
    requires: tuple[str, ...] = ()
    scope: str = "core"
    context: tuple[str, ...] = ()
    # Set for folder skills: where the companion files live, and which guides there are.
    root: Path | None = None
    guides: tuple[str, ...] = field(default=())

    def missing(self) -> list[str]:
        """Programs the skill needs that are not on PATH."""
        return [program for program in self.requires if shutil.which(program) is None]

    def guide(self, name: str) -> str | None:
        """A companion guide by its listed name. Only listed names, so no path escapes the folder."""
        if self.root is None or name not in self.guides:
            return None
        return (self.root / name).read_text(encoding="utf-8")


def _guides(root: Path) -> tuple[str, ...]:
    return tuple(sorted(
        str(path.relative_to(root)) for path in root.rglob("*.md")
        if path.name != "SKILL.md" and not any(part.startswith(".") for part in path.relative_to(root).parts)
    ))


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
    folder = path.name == "SKILL.md"
    return Skill(
        name=fields["name"],
        description=fields.get("description", ""),
        triggers=tuple(t.strip().lower() for t in fields.get("triggers", "").split(",") if t.strip()),
        body=match.group(2).strip(),
        requires=tuple(r.strip() for r in fields.get("requires", "").split(",") if r.strip()),
        scope="contextual" if fields.get("scope", "").lower() == "contextual" else "core",
        context=tuple(c.strip().lower() for c in fields.get("context", "").split(",") if c.strip()),
        root=path.parent if folder else None,
        guides=_guides(path.parent) if folder else (),
    )


@lru_cache(maxsize=1)
def load_all() -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    custom = os.getenv("SKILLS_DIR", "").strip()
    disabled = {name.strip().lower() for name in os.getenv("SKILLS_DISABLED", "").split(",") if name.strip()}
    for directory in (_BUILTIN_DIR, Path(custom).expanduser() if custom else None):
        if directory is None or not directory.is_dir():
            continue
        for path in sorted([*directory.glob("*.md"), *directory.glob("*/SKILL.md")]):
            try:
                skill = _parse(path)
            except OSError as exc:
                logger.warning(f"[skills] could not read {path}: {exc}")
                continue
            if skill and skill.name.lower() not in disabled:
                skills[skill.name] = skill
    return skills


def _hits(words: tuple[str, ...], text: str) -> int:
    return sum(1 for word in words if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text))


def _score(skill: Skill, text: str) -> int:
    return _hits(skill.triggers, text)


def woken(user_input: str) -> list[str]:
    """Contextual skills this request calls for."""
    text = user_input.lower()
    return [
        s.name for s in load_all().values()
        if s.scope == "contextual" and _score(s, text) and (not s.context or _hits(s.context, text))
    ]


def select(user_input: str, limit: int = 2, active: tuple[str, ...] | list[str] = ()) -> list[Skill]:
    """Skills whose triggers appear in the request, most matches first.

    A contextual skill counts only once it is active in this session, or this
    request wakes it.
    """
    text = user_input.lower()
    awake = set(active) | set(woken(user_input))
    scored: list[tuple[int, Skill]] = []
    for skill in load_all().values():
        if skill.scope == "contextual" and skill.name not in awake:
            continue
        score = _score(skill, text)
        if score:
            scored.append((score, skill))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [skill for _, skill in scored[:limit]]


def index_text(active: tuple[str, ...] | list[str] = ()) -> str:
    """One line per skill, so the model knows what it can ask for. Dormant skills are left out."""
    skills = [s for s in load_all().values() if s.scope == "core" or s.name in active]
    if not skills:
        return ""
    lines = []
    for skill in skills:
        missing = skill.missing()
        note = f" (belum terpasang: {', '.join(missing)})" if missing else ""
        lines.append(f"- {skill.name}: {skill.description}{note}")
    return "[SKILLS: call load_skill(name) for the full guide]\n" + "\n".join(lines)


def install(skill: Skill, workspace: Path, session_id: str) -> Path:
    """Copy a folder skill into the workspace, so its code runs inside the sandbox.

    Each session gets its own copy under .skills/<session>/, so two chats working
    in the same project never share a kit, its caches, or what it renders. A copy
    that already exists is kept: the user may have changed it, say with a style
    of their own.
    """
    if skill.root is None:
        raise ValueError(f"{skill.name} has no files to install")
    session = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "").strip("._")[:64] or "default"
    target = workspace / ".skills" / session / skill.name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill.root, target, ignore=shutil.ignore_patterns("__pycache__", ".*"))
    return target


__all__ = ["Skill", "index_text", "install", "load_all", "select", "woken"]
