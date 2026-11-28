"""Reading skills: the Skill record, parsing a SKILL.md or *.md header, and ``load_all``.

``load_all`` merges our own skills (``builtin/``), the add-ons' skills (only when an add-on is on),
``SKILLS_DIR``, and the active GitHub skills (``sources.py``). It is cached and rebuilt by itself
when any of those files change (``FileWatch``), so an edited skill applies without a restart.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

from brain.utils.cache import FileWatch
from brain.utils.logger import get_logger

logger = get_logger(__name__)

_BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"
_ADDONS_DIR = Path(__file__).resolve().parents[1] / "addons"
ALWAYS_MAX_CHARS = 40_000
_SCOPES = ("core", "contextual", "always")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    triggers: tuple[str, ...]
    body: str
    requires: tuple[str, ...] = ()
    scope: str = "core"
    context: tuple[str, ...] = ()
    # Task types this guide is for. Empty means all. "bingung" in a coding request is not a heart-to-heart.
    tasks: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    # Skills this one always comes with (it is built in to those works); see the module notes.
    companion: tuple[str, ...] = ()
    # Rotation pool this skill belongs to ("design"), and pools whose partner this work wants.
    pool: str = ""
    wants: tuple[str, ...] = ()
    # Set for folder skills: where the companion files live, and which guides there are.
    root: Path | None = None
    guides: tuple[str, ...] = field(default=())
    # "owner/repo" for skills cloned from GitHub (sources.py); empty for our own.
    origin: str = ""
    # A GitHub skill with an adapter (skills/adapters/) keeps its upstream file here, unchanged,
    # so guide("SKILL.md") opens it even when the adapter alone is the body.
    upstream_file: Path | None = None
    adapted: bool = False

    def missing(self) -> list[str]:
        """Programs the skill needs that are not on PATH."""
        return [program for program in self.requires if shutil.which(program) is None]

    def guide(self, name: str) -> str | None:
        """A companion guide by its listed name. Only listed names, so no path escapes the folder."""
        if self.upstream_file is not None and name == "SKILL.md":
            return self.upstream_file.read_text(encoding="utf-8")
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
    if fields.get("edition", "").lower() == "enterprise":
        from brain.config.edition import trading_enabled

        if not trading_enabled():
            return None   # a trading skill whose module is not installed
    folder = path.name == "SKILL.md"
    return Skill(
        name=fields["name"],
        description=fields.get("description", ""),
        triggers=tuple(t.strip().lower() for t in fields.get("triggers", "").split(",") if t.strip()),
        body=match.group(2).strip(),
        requires=tuple(r.strip() for r in fields.get("requires", "").split(",") if r.strip()),
        scope=scope if (scope := fields.get("scope", "").lower()) in _SCOPES else "core",
        context=tuple(c.strip().lower() for c in fields.get("context", "").split(",") if c.strip()),
        tasks=tuple(t.strip().lower() for t in fields.get("tasks", "").split(",") if t.strip()),
        tools=tuple(t.strip() for t in fields.get("tools", "").split(",") if t.strip()),
        companion=tuple(c.strip() for c in fields.get("companion", "").split(",") if c.strip()),
        pool=fields.get("pool", "").strip().lower(),
        wants=tuple(w.strip().lower() for w in fields.get("wants", "").split(",") if w.strip()),
        root=path.parent if folder else None,
        guides=_guides(path.parent) if folder else (),
    )


def _user_dir() -> Path:
    """Skills the owner uploaded (skills/installer.py), in the user's data folder."""
    from brain.config.paths import user_data_root

    return user_data_root() / "skills"


def _addon_dirs() -> list[Path]:
    """Skill folders of the add-ons that are switched on (addons/<name>/skills)."""
    from brain.config.edition import addon_enabled

    return [folder for folder in sorted(_ADDONS_DIR.glob("*/skills")) if addon_enabled(folder.parent.name)]


def _watched_files() -> list[Path]:
    """What load_all is built from: edit, add or remove one and the next call rebuilds."""
    from skills import adapters

    folders = [_BUILTIN_DIR, adapters.ADAPTERS_DIR, *_addon_dirs(), _user_dir()]
    custom = os.getenv("SKILLS_DIR", "").strip()
    if custom:
        folders.append(Path(custom).expanduser())
    files: list[Path] = []
    for folder in folders:
        if folder.is_dir():
            files.append(folder)
            files.extend([*folder.glob("*.md"), *folder.glob("*/SKILL.md")])
    sources_root = Path(__file__).resolve().parent
    files += [sources_root / "sources.json", sources_root / ".sources" / ".state.json"]
    return files


_WATCH = FileWatch(_watched_files, interval=3.0)


def load_all() -> dict[str, Skill]:
    """Every usable skill by name. Rebuilt by itself when a skill file, adapter, or clone changes."""
    if _WATCH.changed():
        _clear_load_all()
    return _load_all_cached()


def _clear_load_all() -> None:
    _load_all_cached.cache_clear()
    from skills import selection as _select

    _select.clear_indexes()
    try:
        from skills import adapters

        adapters.reload()
    except Exception:  # noqa: BLE001
        pass

load_all.cache_clear = _clear_load_all  # type: ignore[attr-defined]


@lru_cache(maxsize=1)
def _load_all_cached() -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    custom = os.getenv("SKILLS_DIR", "").strip()
    disabled = {name.strip().lower() for name in os.getenv("SKILLS_DISABLED", "").split(",") if name.strip()}
    for directory in (_BUILTIN_DIR, *_addon_dirs(), _user_dir(), Path(custom).expanduser() if custom else None):
        if directory is None or not directory.is_dir():
            continue
        for path in sorted([*directory.glob("*.md"), *directory.glob("*/SKILL.md")]):
            try:
                skill = _parse(path)
            except OSError as exc:
                logger.warning(f"[skills] could not read {path}: {exc}")
                continue
            if skill and skill.name.lower() not in disabled:
                if skill.scope == "always" and len(skill.body) > ALWAYS_MAX_CHARS:
                    logger.warning(f"[skills] {skill.name} is scope: always but {len(skill.body)} chars; kept as core")
                    skill = replace(skill, scope="core")
                skills[skill.name] = skill
    # Active skills cloned from GitHub. Built-ins and SKILLS_DIR win on a name clash.
    # Loading skills from any entry point (chat, web, ask, a team run) starts the downloader.
    try:
        from skills import sources

        sources.start_background()

        for name, skill in sources.active().items():
            if name not in skills and name.lower() not in disabled:
                skills[name] = skill
    except Exception as exc:  # noqa: BLE001 - a broken clone must not take the built-ins down
        logger.warning(f"[skills] GitHub skills unavailable: {exc}")
    return skills


def stored(name: str) -> Skill | None:
    """A stored GitHub skill by exact name: kept out of the index, opened only when asked for."""
    try:
        from skills import sources

        return sources.stored().get(name)
    except Exception:  # noqa: BLE001
        return None


def always(task: str = "") -> list[Skill]:
    """Skills that are in every prompt of this kind of task."""
    return [
        s for s in load_all().values()
        if s.scope == "always" and not (task and s.tasks and task not in s.tasks)
    ]


def always_text(task: str = "") -> str:
    """The always-on rules for this task, ready for the system prompt. Empty when there are none."""
    bodies = [s.body for s in always(task)]
    return "\n\n" + "\n\n".join(bodies) if bodies else ""

