"""Push relevant memory to the model instead of hoping it asks for it.

Small models rarely call memory tools on their own, so the harness looks up
playbooks, vault notes, and likely files before the first request.
"""

from __future__ import annotations

import asyncio
from typing import Any

from brain import skills
from config.constants import TaskType
from tool.execute.execute_code_intel import quality_tools
from tool.execute.execute_local_memory import project_memory_tool, vault_tool
from tool.execute.execute_local_memory.vault import constants as C
from utils import session_settings
from utils.logger import get_logger
from utils.workspace_context import get_active_workspace

from . import playbooks

logger = get_logger(__name__)

_MIN_QUERY_CHARS = 12
_NO_SKILLS = frozenset({TaskType.MATH, TaskType.GENERATE_IMG, TaskType.MULTIMODAL})


async def _vault_notes(query: str) -> str:
    lines: list[str] = []
    # This chat's notes first, then the vault shared by all chats.
    for scope, label in (("session", ""), ("global", " (shared vault)")):
        result: dict[str, Any] = await vault_tool.vault_recall(
            query=query, depth=0, limit=2, token_budget=120, scope=scope
        )
        lines += [
            f"- {note.get('path')}{label}: {str(note.get('summary') or note.get('title') or '')[:140]}"
            for note in (result.get("notes") or [])
            if C.FOLDER_PLAYBOOKS not in str(note.get("path"))
        ]
    return "[RELATED VAULT NOTES: use vault_read for details]\n" + "\n".join(lines) if lines else ""



async def _ranked_files(query: str, workspace: str) -> str:
    result = await project_memory_tool.memory_rank_files(query=query, limit=6, workspace_path=workspace)
    paths = [str(f.get("path")) for f in (result.get("files") or []) if float(f.get("score") or 0) > 0]
    return "[LIKELY RELEVANT FILES]\n" + "\n".join(f"- {p}" for p in paths) if paths else ""


def _active_skills(user_input: str) -> list[str]:
    """This session's awake contextual skills, including any this request wakes."""
    woken = skills.woken(user_input)
    workspace = get_active_workspace()
    if not workspace:
        return woken
    return session_settings.activate_skills(workspace, woken)


def skill_guidance(user_input: str, task_type: TaskType, max_chars: int) -> tuple[str, list[str]]:
    """The skill index, plus the full guides this request calls for.

    Contextual skills join the index only once woken in this session.
    """
    try:
        if task_type in _NO_SKILLS:
            return "", []
        active = _active_skills(user_input)
        index = skills.index_text(active)
        if not index:
            return "", []
        selected = skills.select(user_input, active=active) if max_chars > 0 else []
        chosen: list[str] = []
        parts = [index]
        room = max_chars
        for skill in selected:
            if len(skill.body) > room:
                continue
            parts.append(skill.body)
            chosen.append(skill.name)
            room -= len(skill.body)
        return "\n\n" + "\n\n".join(parts), chosen
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[briefing] skills unavailable: {exc}")
        return "", []


async def build_briefing(
    user_input: str, task_type: TaskType, workspace: str, max_chars: int
) -> tuple[str, int]:
    """Returns ``(text, playbooks_used)``. Never raises."""
    if len(user_input.strip()) < _MIN_QUERY_CHARS or max_chars <= 0:
        return "", 0

    parts: list[str] = []
    notes = playbooks.find_relevant(user_input, task_type.value)
    if notes:
        parts.append(playbooks.render_for_prompt(notes, max_chars))

    try:
        vault_text = await _vault_notes(user_input)
        if vault_text:
            parts.append(vault_text)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[briefing] vault lookup failed: {exc}")

    if task_type == TaskType.CODE and workspace:
        try:
            files_text = await _ranked_files(user_input, workspace)
            if files_text:
                parts.append(files_text)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[briefing] file ranking failed: {exc}")
        graph_text = await asyncio.to_thread(quality_tools.graph_brief, user_input, workspace)
        if graph_text:
            parts.append(f"[CODE GRAPH for this request: use code_graph for more]\n{graph_text}")

    text = "\n\n".join(parts)[:max_chars]
    return (f"\n\n{text}" if text else ""), len(notes)
