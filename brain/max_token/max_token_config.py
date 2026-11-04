"""
brain.max_token.max_token_config — Max token limits per model.

Loads max output token configurations from models_config.yaml
and provides per-model and per-task-type lookup.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from config.constants import TaskType
from utils.logger import get_logger

logger = get_logger(__name__)

# Default max output tokens per task type (used when config is missing)
DEFAULT_MAX_OUTPUT: dict[str, int] = {
    TaskType.CODE.value: 16384,
    TaskType.MATH.value: 4096,
    TaskType.ACTIVITY.value: 8192,
    TaskType.SCHOOL_TASK.value: 16384,
    TaskType.GENERATE_IMG.value: 4096,
    TaskType.REASON.value: 32768,
    TaskType.MULTIMODAL.value: 8192,
}

# Limits for models the endpoint does not report. Filled from models_config.yaml.
MODEL_MAX_OUTPUT: dict[str, int] = {}


class MaxTokenConfig:
    """Configuration for max output tokens.

    Provides per-model and per-task-type max output token limits.
    Loaded from models_config.yaml or uses defaults.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Initialize the config.

        Args:
            config_path: Path to models_config.yaml. If None, uses defaults.
        """
        self._model_limits: dict[str, int] = dict(MODEL_MAX_OUTPUT)
        self._task_limits: dict[str, int] = dict(DEFAULT_MAX_OUTPUT)

        if config_path:
            self.load_config(config_path)

    def load_config(self, config_path: str | Path) -> None:
        """Load limits from YAML config.

        Args:
            config_path: Path to models_config.yaml.
        """
        path = Path(config_path)
        if not path.exists():
            return

        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # Load model limits
        models = config.get("models", {})
        for model_id, model_config in models.items():
            max_out = model_config.get("max_output")
            if max_out:
                self._model_limits[model_id] = max_out

        # Load task-type limits
        token_limits = config.get("token_limits", {})
        for task, limits in token_limits.items():
            default_max = limits.get("default_max_output")
            if default_max:
                self._task_limits[task] = default_max

        logger.info(
            f"[max_token] Loaded limits: {len(self._model_limits)} models, "
            f"{len(self._task_limits)} task types"
        )

    def get_model_max(self, model_id: str) -> int:
        """Get the max output tokens for a specific model.

        Args:
            model_id: The model identifier.

        Returns:
            int: Max output tokens.
        """
        return self._model_limits.get(model_id, 8192)

    def get_task_default_max(self, task_type: TaskType) -> int:
        """Get the default max output for a task type.

        Args:
            task_type: The task type.

        Returns:
            int: Default max output tokens for this task type.
        """
        return self._task_limits.get(task_type.value, 8192)

    def get_effective_max(
        self,
        model_id: str,
        task_type: TaskType,
        override: int | None = None,
    ) -> int:
        """Get the effective max tokens (min of model limit and task default).

        Args:
            model_id: The model to use.
            task_type: The task type.
            override: Optional explicit override.

        Returns:
            int: Effective max output tokens.
        """
        requested = (
            override
            if override is not None and override > 0
            else self.get_task_default_max(task_type)
        )
        return max(1, min(self.get_model_max(model_id), requested))
