"""
brain.system_prompt.base_system_prompt — System prompt loader and manager.

Loads system prompts from text files based on task type.
Combines a shared base prompt with a task-specific prompt and caches both.
"""

from __future__ import annotations

import os
from pathlib import Path

from brain.config.constants import TaskType
from brain.utils.cache import FileWatch
from brain.utils.logger import get_logger
from brain.utils.workspace_context import get_active_ponytail

logger = get_logger(__name__)

# Map TaskType → system prompt filename
_PROMPT_FILES: dict[TaskType, str] = {
    TaskType.CODE: "code_system_prompt.txt",
    TaskType.MATH: "math_system_prompt.txt",
    TaskType.ACTIVITY: "activity_system_prompt.txt",
    TaskType.SCHOOL_TASK: "school_system_prompt.txt",
    TaskType.REASON: "reason_system_prompt.txt",
    TaskType.MULTIMODAL: "multimodal_system_prompt.txt",
    TaskType.GENERATE_IMG: "generate_img_system_prompt.txt",
}

# Ponytail minimal-code ruleset. Customize without editing code:
#   PONYTAIL_MODE  = off | lite | full (default) | ultra
#   PONYTAIL_TASKS = comma-separated task types (default "code")
# The wording is the original Ponytail skill (DietrichGebert/ponytail, cloned through
# skills/sources.json) with our adapter in skills/adapters/ponytail.md in front. The
# ponytail*.txt files here are the offline fallback until that clone exists.
_PONYTAIL_FILES = {
    "lite": "ponytail_lite.txt",
    "full": "ponytail.txt",
    "ultra": "ponytail_ultra.txt",
}


def _ponytail_tasks() -> set[TaskType]:
    names = {n.strip().lower() for n in os.environ.get("PONYTAIL_TASKS", "code").split(",")}
    return {t for t in TaskType if t.value.lower() in names}


def _ponytail_file() -> str:
    mode = get_active_ponytail() or os.environ.get("PONYTAIL_MODE", "full").strip().lower()
    return _PONYTAIL_FILES.get(mode, "")


class SystemPromptManager:
    """Manages loading and caching of system prompts.

    Loads a shared base prompt plus a task-specific prompt from the
    system_prompt directory and caches the combined result.
    """

    def __init__(self, prompts_dir: str | Path | None = None) -> None:
        """Initialize the manager.

        Args:
            prompts_dir: Directory containing prompt .txt files.
                         Defaults to this file's parent directory.
        """
        if prompts_dir:
            self._dir = Path(prompts_dir)
        else:
            self._dir = Path(__file__).parent
        self._cache: dict[str, str] = {}
        # A prompt .txt edited, or the Ponytail skill pulled from GitHub: rebuilt on the next request.
        state = Path(__file__).resolve().parents[2] / "skills" / ".sources" / ".state.json"
        self._watch = FileWatch(lambda: [*sorted(self._dir.glob("*.txt")), state], interval=3.0)

    def get_system_prompt(self, task_type: TaskType) -> str:
        """Get the system prompt for a task type.

        Args:
            task_type: The task type to get the prompt for.

        Returns:
            str: The system prompt text. Empty string if not found.
        """
        filename = _PROMPT_FILES.get(task_type)
        if not filename:
            logger.debug(f"[system_prompt] No prompt file mapped for {task_type.value}")
            return ""

        pony = _ponytail_file()
        # Sessions choose their own mode, so the mode is part of the cache key.
        key = f"{filename}:{pony}"
        if self._watch.changed():
            self._cache.clear()
        if key in self._cache:
            return self._cache[key]

        base_prompt = self._load_file("_base.txt")
        task_prompt = self._load_file(filename)
        extra = self._ponytail(pony) if pony and task_type in _ponytail_tasks() else ""
        prompt = "\n\n".join(part for part in (base_prompt, task_prompt, extra) if part)
        self._cache[key] = prompt
        return prompt

    def _ponytail(self, filename: str) -> str:
        """The Ponytail rules for this chat's level: the upstream skill plus our adapter, else the local copy."""
        level = next((mode for mode, name in _PONYTAIL_FILES.items() if name == filename), "full")
        try:
            from skills import load_all

            skill = load_all().get("ponytail")
        except Exception:  # noqa: BLE001 - the skills package must never take the prompt down
            skill = None
        if skill is not None and skill.upstream_file is not None:
            return f"{skill.body}\n\nPonytail level for this chat: {level}."
        return self._load_file(filename)

    def _load_file(self, filename: str) -> str:
        """Load a prompt file from disk.

        Args:
            filename: Name of the .txt file in the prompts directory.

        Returns:
            str: File contents, or empty string if not found.
        """
        filepath = self._dir / filename
        if not filepath.exists():
            logger.warning(f"[system_prompt] File not found: {filepath}")
            return ""

        content = filepath.read_text(encoding="utf-8").strip()
        logger.debug(f"[system_prompt] Loaded {filename} ({len(content)} chars)")
        return content

    def reload(self) -> None:
        """Clear the cache and force reload on next access."""
        self._cache.clear()
        logger.debug("[system_prompt] Cache cleared")

    def get_all_prompts(self) -> dict[str, str]:
        """Load and return all system prompts.

        Returns:
            dict: TaskType.value → prompt text.
        """
        result: dict[str, str] = {}
        for task_type in TaskType:
            prompt = self.get_system_prompt(task_type)
            if prompt:
                result[task_type.value] = prompt
        return result
