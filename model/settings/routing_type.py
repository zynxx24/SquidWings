"""
model.settings.routing_type — Smart router logic.

Routes tasks to the appropriate model based on task type,
difficulty level, and model availability. Uses the models_config.yaml
routing configuration with difficulty-based escalation:
  easy   → Gemini 3.8 Flash (default, fast)
  medium → Claude Sonnet 4.6 (quality balance)
  hard   → Claude Opus 4.6 (deep reasoning)
"""

from __future__ import annotations

from enum import StrEnum, unique
from pathlib import Path
from typing import Any

import yaml

from config.constants import TaskType
from model.registry.model_info import ModelInfo
from model.registry.model_registry import ModelRegistry
from utils.logger import get_logger

logger = get_logger(__name__)


@unique
class Difficulty(StrEnum):
    """Task difficulty level for model escalation."""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class RoutingType:
    """Smart router that selects the best model for a given task.

    Uses a difficulty-based escalation strategy:
    - EASY tasks → fast/cheap model (Gemini Flash)
    - MEDIUM tasks → balanced model (Claude Sonnet)
    - HARD tasks → premium reasoning model (Claude Opus)

    Falls back to the configured fallback chain if the primary
    model is unavailable.

    Attributes:
        routing_config: The routing section of models_config.yaml.
        default_model: Default model ID if routing fails.
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        registry: ModelRegistry | None = None,
    ) -> None:
        """Initialize the router.

        Args:
            config_path: Path to models_config.yaml.
            registry: ModelRegistry instance for model lookups.
        """
        self._registry = registry or ModelRegistry()
        self._routing_config: dict[str, Any] = {}
        self._default_model: str = "ag/gemini-3.8-flash-high"

        if config_path:
            self.load_config(config_path)

    def load_config(self, config_path: str | Path) -> None:
        """Load routing configuration from YAML.

        Args:
            config_path: Path to models_config.yaml.
        """
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"[router] Config not found: {path}")
            return

        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self._routing_config = config.get("routing", {})
        self._default_model = config.get("default_model", self._default_model)

        logger.info(
            f"[router] Loaded routing config: {len(self._routing_config)} task types"
        )

    def route(
        self,
        task_type: TaskType,
        difficulty: Difficulty = Difficulty.EASY,
        healthy_models: list[str] | None = None,
    ) -> ModelInfo | None:
        """Select the best model for a task.

        Args:
            task_type: The type of task to route.
            difficulty: Task difficulty level.
            healthy_models: Optional list of currently healthy model IDs.

        Returns:
            ModelInfo of the selected model, or None if no match.
        """
        task_config = self._routing_config.get(task_type.value, {})

        if not task_config:
            logger.warning(
                f"[router] No routing config for task type '{task_type.value}', "
                f"using default model"
            )
            return self._registry.get(self._default_model)

        # Get the model ID for the requested difficulty
        model_id = task_config.get(difficulty.value)

        if not model_id:
            # Fall back to default difficulty
            default_diff = task_config.get("default_difficulty", "easy")
            model_id = task_config.get(default_diff, self._default_model)

        # Check if model is healthy
        if healthy_models is not None and model_id not in healthy_models:
            logger.warning(
                f"[router] Primary model '{model_id}' not healthy, "
                f"checking fallback chain"
            )
            return self._find_fallback(task_config, healthy_models)

        model = self._registry.get(model_id)
        if model:
            logger.info(
                f"[router] Routed {task_type.value}/{difficulty.value} → {model.id}"
            )
        else:
            logger.warning(
                f"[router] Model '{model_id}' not in registry, "
                f"trying fallback"
            )
            return self._find_fallback(task_config, healthy_models)

        return model

    def _find_fallback(
        self,
        task_config: dict[str, Any],
        healthy_models: list[str] | None = None,
    ) -> ModelInfo | None:
        """Find the first available fallback model.

        Args:
            task_config: The routing config for this task type.
            healthy_models: Currently healthy model IDs.

        Returns:
            ModelInfo of the first available fallback, or None.
        """
        fallback_chain = task_config.get("fallback_chain", [])

        for fallback_id in fallback_chain:
            if healthy_models is not None and fallback_id not in healthy_models:
                continue
            model = self._registry.get(fallback_id)
            if model:
                logger.info(f"[router] Fallback selected: {model.id}")
                return model

        # Last resort: default model
        default = self._registry.get(self._default_model)
        if default:
            logger.warning(f"[router] Using default model: {default.id}")
            return default

        logger.error("[router] No available models found!")
        return None

    def get_task_config(self, task_type: TaskType) -> dict[str, Any]:
        """Get the routing config for a task type.

        Args:
            task_type: The task type.

        Returns:
            dict: Routing config with temperature, thinking, tools settings.
        """
        config = self._routing_config.get(task_type.value, {})
        return config if isinstance(config, dict) else {}

    def get_temperature(self, task_type: TaskType) -> float:
        """Get the recommended temperature for a task type.

        Args:
            task_type: The task type.

        Returns:
            float: Temperature value.
        """
        config = self.get_task_config(task_type)
        value = config.get("temperature", 0.5)
        return float(value) if isinstance(value, (int, float)) else 0.5

    def should_enable_thinking(self, task_type: TaskType) -> bool:
        """Check if thinking should be enabled for a task type.

        Args:
            task_type: The task type.

        Returns:
            bool: Whether to enable thinking.
        """
        config = self.get_task_config(task_type)
        value = config.get("enable_thinking", True)
        return value if isinstance(value, bool) else True

    def should_enable_tools(self, task_type: TaskType) -> bool:
        """Check if tools should be enabled for a task type.

        Args:
            task_type: The task type.

        Returns:
            bool: Whether to enable tools.
        """
        config = self.get_task_config(task_type)
        value = config.get("enable_tools", False)
        return value if isinstance(value, bool) else False

    def get_default_difficulty(self, task_type: TaskType) -> Difficulty:
        """Get the default difficulty for a task type.

        Args:
            task_type: The task type.

        Returns:
            Difficulty: Default difficulty level.
        """
        config = self.get_task_config(task_type)
        diff_str = config.get("default_difficulty", "easy")
        return Difficulty(diff_str)

    @property
    def default_model_id(self) -> str:
        """The default model ID."""
        return self._default_model
