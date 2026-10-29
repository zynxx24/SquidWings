"""
brain.system_prompt.base_system_prompt — System prompt loader and manager.

Loads system prompts from text files based on task type.
Combines a shared base prompt with a task-specific prompt and caches both.
"""

from __future__ import annotations

from pathlib import Path

from config.constants import TaskType
from utils.logger import get_logger

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

        if filename in self._cache:
            return self._cache[filename]

        base_prompt = self._load_file("_base.txt")
        task_prompt = self._load_file(filename)
        prompt = "\n\n".join(part for part in (base_prompt, task_prompt) if part)
        self._cache[filename] = prompt
        return prompt

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
