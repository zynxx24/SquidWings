"""Handing a skill to the model: the tool-map note for GitHub skills, and installing a skill's
files into the session's project folder so its scripts run inside the sandbox."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from brain.utils.logger import get_logger
from skills.loader import Skill

logger = get_logger(__name__)

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
    sandboxed commands only see the project folder, never ``skills/.sources``.
    """
    body = skill.body if text is None else text
    if not skill.origin or skill.root is None:
        return body, None
    installed_at = None
    files = f" This skill's files are in {skill.root}."
    if workspace and _has_code(skill.root):
        try:
            target = install(skill, Path(workspace), session_id)
            installed_at = target.relative_to(workspace).as_posix()
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

