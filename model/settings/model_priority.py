"""
model.settings.model_priority — Priority ordering per task type.

Defines which models are preferred for each task type and difficulty.
Used by the router to make primary model selection.
"""

from __future__ import annotations

from config.constants import TaskType
from utils.logger import get_logger

logger = get_logger(__name__)


# Static priority map: TaskType → difficulty → ordered model IDs
# First model = highest priority
MODEL_PRIORITY: dict[str, dict[str, list[str]]] = {
    TaskType.CODE.value: {
        "easy": ["gemini/gemini-3.8-flash", "ag/gemini-3.8-flash-high", "kg/gemini-3-8-flash", "kg/kimi-k2-7-code"],
        "medium": ["gemini/gemini-4-argon", "gemini/gemini-3.8-flash", "ag/claude-sonnet-4-6", "kg/mimo-v2.6-flash", "kg/deepseek-4-1-flash", "kg/deepseek-v4-pro"],
        "hard": ["gemini/gemini-4-argon", "ag/claude-opus-4-6-thinking", "kg/claude-sonnet-4-6", "kg/deepseek-v4-pro", "kg/qwen3.8-max", "kg/claude-haiku-5-5"],
    },
    TaskType.MATH.value: {
        "easy": ["gemini/gemini-3.8-flash", "ag/gemini-3.8-flash-high", "kg/gemini-3-8-flash"],
        "medium": ["gemini/gemini-4-argon", "gemini/gemini-3.8-flash", "ag/claude-sonnet-4-6", "kg/deepseek-v4-pro"],
        "hard": ["gemini/gemini-4-argon", "ag/claude-opus-4-6-thinking", "kg/claude-sonnet-4-6", "kg/deepseek-v4-pro", "kg/qwen3.8-max", "ag/claude-sonnet-4-6"],
    },
    TaskType.ACTIVITY.value: {
        "easy": ["gemini/gemini-3.8-flash", "ag/gemini-3.8-flash-high", "kg/gemini-3-8-flash", "kg/gpt-5-6-luna"],
        "medium": ["gemini/gemini-4-argon", "gemini/gemini-3.8-flash", "ag/claude-sonnet-4-6", "kg/gpt-5-6-luna"],
        "hard": ["gemini/gemini-4-argon", "ag/claude-opus-4-6-thinking", "kg/claude-sonnet-4-6", "kg/qwen3.8-max", "ag/claude-sonnet-4-6"],
    },
    TaskType.SCHOOL_TASK.value: {
        "easy": ["gemini/gemini-3.8-flash", "ag/gemini-3.8-flash-high", "kg/gemini-3-8-flash"],
        "medium": ["gemini/gemini-4-argon", "gemini/gemini-3.8-flash", "ag/claude-sonnet-4-6", "kg/deepseek-v4-pro"],
        "hard": ["gemini/gemini-4-argon", "ag/claude-opus-4-6-thinking", "kg/claude-sonnet-4-6", "kg/qwen3.8-max", "ag/claude-sonnet-4-6"],
    },
    TaskType.GENERATE_IMG.value: {
        "easy": ["gemini/gemini-3.8-flash", "ag/gemini-3.8-flash-high", "kg/gpt-5-6-luna"],
        "medium": ["ag/claude-sonnet-4-6", "gemini/gemini-4-argon"],
        "hard": ["gemini/gemini-4-argon", "ag/claude-opus-4-6-thinking", "kg/qwen3.8-max"],
    },
    TaskType.REASON.value: {
        "easy": ["gemini/gemini-3.8-flash", "ag/gemini-3.8-flash-high", "kg/gemini-3-8-flash"],
        "medium": ["gemini/gemini-4-argon", "gemini/gemini-3.8-flash", "ag/claude-sonnet-4-6", "kg/deepseek-v4-pro", "kg/glm5-3"],
        "hard": ["gemini/gemini-4-argon", "ag/claude-opus-4-6-thinking", "kg/claude-sonnet-4-6", "kg/deepseek-v4-pro", "kg/qwen3.8-max", "ag/claude-sonnet-4-6"],
    },
    TaskType.MULTIMODAL.value: {
        "easy": ["gemini/gemini-3.8-flash", "ag/gemini-3.8-flash-high", "kg/gemini-3-8-flash", "kg/mimo-v2.6-flash"],  # Best multimodal
        "medium": ["gemini/gemini-4-argon", "gemini/gemini-3.8-flash", "ag/claude-sonnet-4-6", "kg/gpt-5-6-luna", "kg/mimo-v2.6-flash"],  # Vision + tools
        "hard": ["gemini/gemini-4-argon", "ag/claude-opus-4-6-thinking", "kg/claude-sonnet-4-6", "kg/qwen3.8-max"],
    },
}


def get_priority_models(task_type: TaskType, difficulty: str = "easy") -> list[str]:
    """Get the priority-ordered model list for a task type + difficulty.

    Args:
        task_type: The task type.
        difficulty: Difficulty level (easy, medium, hard).

    Returns:
        list: Ordered model IDs (first = highest priority).
    """
    task_priorities = MODEL_PRIORITY.get(task_type.value, {})
    return task_priorities.get(difficulty, ["ag/gemini-3.8-flash-high"])
