"""
brain.prompt_must_think.base_think_prompt — Thinking prompt loader.

Loads thinking prompts from text files based on task type.
Uses explicit task-specific prompts with caching.
"""

from __future__ import annotations

from pathlib import Path

from config.constants import TaskType
from utils.logger import get_logger

logger = get_logger(__name__)

# Map TaskType → prompt filename
_PROMPT_FILES: dict[TaskType, str] = {
    TaskType.CODE: "for_code.txt",
    TaskType.MATH: "for_math.txt",
    TaskType.ACTIVITY: "for_activity.txt",
    TaskType.REASON: "for_reason.txt",
    TaskType.SCHOOL_TASK: "for_solve_school_task.txt",
    TaskType.GENERATE_IMG: "for_generate_img.txt",
    TaskType.MULTIMODAL: "for_multimodal.txt",
}


class ThinkPromptManager:
    """Manages loading and caching of thinking prompts.

    Loads prompts from text files in the prompt_must_think directory
    and caches them for subsequent calls.
    """

    def __init__(self, prompts_dir: str | Path | None = None) -> None:
        if prompts_dir:
            self._dir = Path(prompts_dir)
        else:
            self._dir = Path(__file__).parent
        self._cache: dict[str, str] = {}

    def get_think_prompt(self, task_type: TaskType) -> str:
        """Get the thinking prompt for a task type.

        Args:
            task_type: The task type.

        Returns:
            str: The thinking prompt text.
        """
        filename = _PROMPT_FILES.get(task_type)
        if not filename:
            logger.debug(f"[think_prompt] No prompt file for {task_type.value}")
            return ""

        # Check cache
        if filename in self._cache:
            prompt = self._cache[filename]
        else:
            prompt = self._load_file(filename)
            self._cache[filename] = prompt

        return prompt

    def _load_file(self, filename: str) -> str:
        """Load a prompt file.

        Args:
            filename: Name of the file in the prompts directory.

        Returns:
            str: File contents, or empty string if not found.
        """
        filepath = self._dir / filename
        if not filepath.exists():
            logger.warning(f"[think_prompt] File not found: {filepath}")
            return ""

        content = filepath.read_text(encoding="utf-8").strip()
        logger.debug(f"[think_prompt] Loaded {filename} ({len(content)} chars)")
        return content

    def reload(self) -> None:
        """Clear the cache and force reload on next access."""
        self._cache.clear()
        logger.debug("[think_prompt] Cache cleared")
