"""
tool.planning.logic.priority_calculator — Calculate task priorities.

Factors: urgency, complexity, dependency count, estimated tokens.
"""

from __future__ import annotations

from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class PriorityCalculator:
    """Calculates priority scores for tasks.

    Score is based on multiple weighted factors:
    - Urgency (user-specified)
    - Complexity (estimated tokens / effort)
    - Dependency count (more dependents = higher priority)
    - Task type weight
    """

    # Weights for each factor (sum to ~1.0)
    WEIGHT_URGENCY: float = 0.3
    WEIGHT_COMPLEXITY: float = 0.2
    WEIGHT_DEPS: float = 0.3
    WEIGHT_TYPE: float = 0.2

    # Task type base scores
    TYPE_SCORES: dict[str, float] = {
        "code": 0.8,
        "command": 0.6,
        "edit": 0.7,
        "test": 0.9,
        "review": 0.5,
        "general": 0.4,
    }

    def calculate(
        self,
        task: dict[str, Any],
        all_tasks: list[dict[str, Any]] | None = None,
    ) -> float:
        """Calculate priority score for a task.

        Args:
            task: Task dict with keys like name, task_type,
                  estimated_tokens, depends_on, urgency.
            all_tasks: All tasks (for computing dependent count).

        Returns:
            float: Priority score (0.0 to 1.0, higher = more urgent).
        """
        urgency = self._urgency_score(task)
        complexity = self._complexity_score(task)
        deps = self._dependency_score(task, all_tasks)
        task_type = self._type_score(task)

        score = (
            urgency * self.WEIGHT_URGENCY
            + complexity * self.WEIGHT_COMPLEXITY
            + deps * self.WEIGHT_DEPS
            + task_type * self.WEIGHT_TYPE
        )

        score = max(0.0, min(1.0, score))

        logger.debug(
            f"[priority] {task.get('name', '?')}: "
            f"score={score:.2f} (u={urgency:.1f} c={complexity:.1f} "
            f"d={deps:.1f} t={task_type:.1f})"
        )

        return round(score, 3)

    def _urgency_score(self, task: dict[str, Any]) -> float:
        raw_urgency = task.get("urgency", 5)
        urgency = float(raw_urgency) if isinstance(raw_urgency, (int, float)) else 5.0
        return min(1.0, urgency / 10.0)

    def _complexity_score(self, task: dict[str, Any]) -> float:
        raw_tokens = task.get("estimated_tokens", 500)
        tokens = float(raw_tokens) if isinstance(raw_tokens, (int, float)) else 500.0
        # Higher complexity = potentially higher priority (needs more resources)
        if tokens > 5000:
            return 0.9
        elif tokens > 2000:
            return 0.7
        elif tokens > 500:
            return 0.5
        return 0.3

    def _dependency_score(self, task: dict[str, Any], all_tasks: list | None) -> float:
        if not all_tasks:
            return 0.5
        task_name = task.get("name", "")
        dependent_count = sum(
            1 for t in all_tasks
            if task_name in (t.get("depends_on") or [])
        )
        return min(1.0, dependent_count / 5.0 + 0.3)

    def _type_score(self, task: dict[str, Any]) -> float:
        task_type = task.get("task_type", "general")
        return self.TYPE_SCORES.get(task_type, 0.4)

    def rank_tasks(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rank tasks by priority (highest first).

        Args:
            tasks: List of task dicts.

        Returns:
            Tasks sorted by priority score descending.
        """
        scored = [
            {**t, "_priority_score": self.calculate(t, tasks)}
            for t in tasks
        ]
        scored.sort(key=lambda t: t["_priority_score"], reverse=True)
        return scored
