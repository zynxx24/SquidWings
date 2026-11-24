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
does not wake a video skill but "video animasi" does. ``tools:`` names the tools
an awake contextual skill must be offered, even on a small model's short list.
From then on it stays active for that session only, so "lanjutkan" does not
lose it halfway, and other chats never see it.

``scope: always`` is a short rule set that is in every prompt its ``tasks`` cover,
whatever the request says, small model or frontier, tools or none. Keep such a
body under ``ALWAYS_MAX_CHARS``: every request pays for it.

A skill with ``companion: build-web, animate`` is built in to those works: it is
always added when one of them is picked, or when its own triggers match, on top
of the usual limit. ``aislop`` is one: every web page, video or image comes with
its no-AI-slop check, even on image requests that otherwise get no skills.

The most relevant skills are placed in the prompt automatically, because small
models rarely ask for help on their own. The rest stay available through the
``load_skill`` tool, so they cost one line of prompt until needed. Set SKILLS_DIR
to add a folder of your own; a skill there replaces a built-in of the same name.
SKILLS_DISABLED takes a comma-separated list of skills to leave out.

Skills from GitHub are listed in ``sources.json`` and handled by ``sources.py``:
cloned automatically, the important ones active here like any other skill, the
rest stored (out of the index, loadable by exact name), and the chosen ones also
linked into Claude Code's ``~/.claude/skills``.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

_BUILTIN_DIR = Path(__file__).resolve().parent
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
    # Set for folder skills: where the companion files live, and which guides there are.
    root: Path | None = None
    guides: tuple[str, ...] = field(default=())
    # "owner/repo" for skills cloned from GitHub (sources.py); empty for our own.
    origin: str = ""

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
    if fields.get("edition", "").lower() == "enterprise":
        from config.edition import trading_enabled

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
                if skill.scope == "always" and len(skill.body) > ALWAYS_MAX_CHARS:
                    logger.warning(f"[skills] {skill.name} is scope: always but {len(skill.body)} chars; kept as core")
                    skill = replace(skill, scope="core")
                skills[skill.name] = skill
    # Active skills cloned from GitHub. Built-ins and SKILLS_DIR win on a name clash.
    # Loading skills from any entry point (chat, web, ask, a team run) starts the downloader.
    try:
        from brain.skills import sources

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
        from brain.skills import sources

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


def _hits(words: tuple[str, ...], text: str) -> int:
    return sum(1 for word in words if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text))


def _score(skill: Skill, text: str) -> int:
    return _hits(skill.triggers, text)


def woken(user_input: str, task: str = "") -> list[str]:
    """Contextual skills this request calls for. A skill limited to other tasks stays asleep:
    "buat parser csv" in a coding request must not wake the document skill."""
    text = user_input.lower()
    return [
        s.name for s in load_all().values()
        if s.scope == "contextual" and _score(s, text) and (not s.context or _hits(s.context, text))
        and not (task and s.tasks and task not in s.tasks)
    ]


def select(user_input: str, limit: int = 2, active: tuple[str, ...] | list[str] = (), task: str = "") -> list[Skill]:
    """Skills whose triggers appear in the request, most matches first.

    A contextual skill counts only once it is active in this session, or this
    request wakes it.
    """
    text = user_input.lower()
    now = set(woken(user_input, task))
    awake = set(active) | now
    scored: list[tuple[int, Skill]] = []
    for skill in load_all().values():
        if skill.scope == "always":
            continue   # already in the prompt through always_text
        if skill.scope == "contextual" and skill.name not in awake:
            continue
        if task and skill.tasks and task not in skill.tasks:
            continue
        score = _score(skill, text)
        if score:
            # A specialised skill this very request woke ("gsap", "remotion") beats a general one
            # that merely shares a few words with it ("landing page" for build-web).
            if skill.name in now:
                score += 2
            scored.append((score, skill))
    scored.sort(key=lambda item: item[0], reverse=True)
    chosen = [skill for _, skill in scored if not skill.companion][:limit]
    return chosen + companions(text, [skill.name for skill in chosen])


def companions(text: str, picked: list[str]) -> list[Skill]:
    """Built-in skills that come with this work: a picked skill they accompany, or their own trigger."""
    lowered = text.lower()
    return [
        s for s in load_all().values()
        if s.companion and (set(s.companion) & set(picked) or _score(s, lowered))
    ]


def tools_for(active: tuple[str, ...] | list[str]) -> list[str]:
    """Tools the awake contextual skills need."""
    found = load_all()
    return [tool for name in active if name in found for tool in found[name].tools]


def visible(active: tuple[str, ...] | list[str] = (), task: str = "") -> list[Skill]:
    """Skills the model is told about: core ones meant for this task, plus contextual ones awake in the session."""
    out = []
    for s in load_all().values():
        if s.scope == "always":
            continue   # its rules are already in the prompt
        if s.scope == "contextual" and s.name not in active:
            continue
        if task and s.tasks and task not in s.tasks and s.name not in active:
            continue
        out.append(s)
    return out


def index_text(active: tuple[str, ...] | list[str] = (), task: str = "") -> str:
    """One line per skill, so the model knows what it can ask for.

    Dormant skills are left out, and so are core skills meant for other kinds of task:
    a heart-to-heart does not need the backend checklist in its prompt.
    """
    skills = visible(active, task)
    if not skills:
        return ""
    lines = []
    for skill in skills:
        missing = skill.missing()
        note = f" (belum terpasang: {', '.join(missing)})" if missing else ""
        lines.append(f"- {skill.name}: {skill.description}{note}")
    header = "[SKILLS: call load_skill(name) for the full guide]"
    if _stored_count():
        header += f"\n(Plus {_stored_count()} stored skills not listed here: load_skill(search=\"what you need\") finds them.)"
    return header + "\n" + "\n".join(lines)


def _stored_count() -> int:
    try:
        from brain.skills import sources

        return len(sources.stored())
    except Exception:  # noqa: BLE001
        return 0


# Skills from GitHub were written for Claude Code. Their text stays as the authors wrote it;
# this note, put in front when the harness hands one out, maps their words to our tools.
GITHUB_SKILL_NOTE = (
    "[This guide comes from {origin} and was written for Claude Code. In this harness: "
    "Read = read_file, Write = file_write, Edit = file_patch, Bash = execute_command, Grep = file_search, "
    "Glob = file_find, WebFetch = web_read, WebSearch = web_research. The Skill tool and names like "
    "`superpowers:writing-plans` mean load_skill(name=\"writing-plans\"). AskUserQuestion: ask the user in "
    "your reply and wait. TodoWrite: keep a short numbered checklist in your reply. Task tool or subagents: "
    "not available here, do the steps yourself in order.{files}]\n\n"
)


def _has_code(root: Path) -> bool:
    return any(p.suffix in {".py", ".js", ".mjs", ".cjs", ".ts", ".sh"} and "node_modules" not in p.parts
               for p in root.rglob("*") if p.is_file())


def runnable(skill: Skill, text: str | None = None, workspace: str | Path | None = None,
             session_id: str = "") -> tuple[str, str | None]:
    """A guide ready for this harness: ``(text, installed_at)``.

    Our own skills come back unchanged. A GitHub skill gets the tool-mapping note in front,
    and when it ships code and a project folder is open, its files are installed into this
    session's own copy (``install``) and every path into the clone is rewritten to that copy:
    sandboxed commands only see the project folder, never ``brain/skills/.sources``.
    """
    body = skill.body if text is None else text
    if not skill.origin or skill.root is None:
        return body, None
    installed_at = None
    files = f" This skill's files are in {skill.root}."
    if workspace and _has_code(skill.root):
        try:
            target = install(skill, Path(workspace), session_id)
            installed_at = str(target.relative_to(workspace))
            body = body.replace(str(skill.root), installed_at)
            files = (f" Its files are installed in {installed_at} (inside the project), and the paths below "
                     "already point there: run its scripts from that folder.")
        except (OSError, ValueError) as exc:
            logger.debug(f"[skills] could not install {skill.name}: {exc}")
    note = GITHUB_SKILL_NOTE.format(origin=skill.origin, files=files) if text is None else ""
    return note + body, installed_at


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


__all__ = ["Skill", "always", "always_text", "index_text", "install", "load_all", "runnable", "select", "stored", "tools_for", "visible", "woken"]
