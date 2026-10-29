"""
brain.min_token.min_token_config — Minimum token thresholds per task type.

Defines minimum acceptable output token counts for each task type.
If a response falls below the threshold, it may be flagged as
potentially incomplete.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from config.constants import TaskType
from utils.logger import get_logger

logger = get_logger(__name__)

# Default minimum output token thresholds
# Responses shorter than this are flagged as potentially incomplete
DEFAULT_MIN_THRESHOLDS: dict[str, int] = {
    TaskType.CODE.value: 100,
    TaskType.MATH.value: 50,
    TaskType.ACTIVITY.value: 20,
    TaskType.SCHOOL_TASK.value: 100,
    TaskType.GENERATE_IMG.value: 10,
    TaskType.REASON.value: 200,
    TaskType.MULTIMODAL.value: 20,
}


class MinTokenConfig:
    """Configuration for minimum output token thresholds.

    Provides per-task-type minimum token thresholds.
    Loaded from models_config.yaml or uses defaults.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Initialize the config.

        Args:
            config_path: Path to models_config.yaml. If None, uses defaults.
        """
        self._thresholds: dict[str, int] = dict(DEFAULT_MIN_THRESHOLDS)

        if config_path:
            self.load_config(config_path)

    def load_config(self, config_path: str | Path) -> None:
        """Load thresholds from YAML config.

        Args:
            config_path: Path to models_config.yaml.
        """
        path = Path(config_path)
        if not path.exists():
            return

        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        token_limits = config.get("token_limits", {})
        for task, limits in token_limits.items():
            min_out = limits.get("min_output")
            if min_out is not None:
                self._thresholds[task] = min_out

        logger.info(
            f"[min_token] Loaded thresholds for "
            f"{len(self._thresholds)} task types"
        )

    def get_min_threshold(self, task_type: TaskType) -> int:
        """Get the minimum token threshold for a task type.

        Args:
            task_type: The task type.

        Returns:
            int: Minimum acceptable output tokens.
        """
        return self._thresholds.get(task_type.value, 20)

    def get_all_thresholds(self) -> dict[str, int]:
        """Get all configured thresholds.

        Returns:
            dict: TaskType value → minimum tokens.
        """
        return dict(self._thresholds)
