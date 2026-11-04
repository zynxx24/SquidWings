"""
model.settings.routing_type — Smart router logic.

Routes tasks to the appropriate model based on task type,
difficulty level, and model availability. Uses the models_config.yaml
routing configuration with difficulty-based escalation:
  easy   → a cheap, fast model
  medium → any model that is not top tier
  hard   → the strongest models listed

No model is named in the code. A task can pin one in models_config.yaml, and otherwise the choice
comes from what the endpoint lists.
"""

from __future__ import annotations

import os
from enum import StrEnum, unique
from pathlib import Path
from typing import Any

import yaml

from config.constants import TaskType
from model.registry.model_info import ModelInfo
from model.registry.model_registry import ModelRegistry
from model.settings.model_picker import (
    ModelPicker,
    discover,
    get_model_picker,
    is_chat_model,
    is_cheap,
)
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
    - EASY tasks → fast, cheap models
    - MEDIUM tasks → balanced models
    - HARD tasks → the strongest models

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
        picker: ModelPicker | None = None,
    ) -> None:
        """Initialize the router.

        Args:
            config_path: Path to models_config.yaml.
            registry: ModelRegistry instance for model lookups.
            picker: Learned chooser for easy and medium tasks. Defaults to the shared one.
        """
        self._registry = registry or ModelRegistry()
        self._picker = picker or get_model_picker()
        self._routing_config: dict[str, Any] = {}
        self._default_model: str = os.getenv("DEFAULT_MODEL", "").strip()

        if config_path:
            self.load_config(config_path)

    @property
    def picker(self) -> ModelPicker:
        return self._picker

    def record_outcome(self, model_id: str, ok: bool, latency_ms: float | None = None) -> None:
        """Feed a request's result back so the next choice learns from it."""
        self._picker.record(model_id, ok, latency_ms)

    def _learned_pick(
        self,
        task_config: dict[str, Any],
        primary_id: str,
        difficulty: Difficulty,
        healthy_models: list[str] | None,
    ) -> ModelInfo | None:
        """Choose among the configured candidates AND the models the registry lists now.

        Easy tasks stay on cheap models. Medium tasks skip frontier models unless they are the
        configured primary. Hard tasks never come here (see ``route``).
        """
        from agent.harness.tiers import Tier, tier_for  # local: harness imports the model layer

        cheap = difficulty == Difficulty.EASY

        def frontier(mid: str) -> bool:
            return tier_for(mid) == Tier.FRONTIER

        configured = [m for m in (primary_id, *task_config.get("fallback_chain", [])) if m]
        configured = [m for m in configured if m == primary_id or (is_cheap(m) if cheap else not frontier(m))]
        listed = discover(
            self._registry.get_all(),
            prefer_cheap=cheap,
            needs_tools=bool(task_config.get("enable_tools", False)),
            is_frontier=frontier,
        )
        # An endpoint's names may say nothing about price, so never end up with no choice at all.
        everything = [m.id for m in self._registry.get_all() if is_chat_model(m.id)]
        candidates = self._usable(configured + listed, healthy_models) or self._usable(everything, healthy_models)
        chosen = self._picker.choose(candidates, prefer_cheap=cheap)
        if chosen is None:
            return None
        logger.info(f"[router] Learned pick {difficulty.value}: {chosen} from {len(candidates)} candidate(s)")
        return self._registry.get(chosen)

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
        self._default_model = self._default_model or str(config.get("default_model") or "")

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

        # Easy and medium tasks rotate among the learned candidates, so the same model does not
        # win every time and models the API adds later get tried.
        if difficulty != Difficulty.HARD:
            learned = self._learned_pick(task_config, model_id, difficulty, healthy_models)
            if learned is not None:
                return learned
        elif not model_id or self._registry.get(model_id) is None:
            return self._strongest(healthy_models) or self._find_fallback(task_config, healthy_models)

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

    def _usable(self, ids: list[str], healthy_models: list[str] | None) -> list[str]:
        unique = dict.fromkeys(ids)
        return [m for m in unique if self._registry.get(m) and (healthy_models is None or m in healthy_models)]

    def _strongest(self, healthy_models: list[str] | None) -> ModelInfo | None:
        """A hard task with no pinned model: the top tier listed, else whatever is not lite."""
        from agent.harness.tiers import tier_for  # local: harness imports the model layer

        ids = self._usable([m.id for m in self._registry.get_all() if is_chat_model(m.id)], healthy_models)
        if not ids:
            return None
        best = max(tier_for(m) for m in ids)
        chosen = self._picker.choose([m for m in ids if tier_for(m) == best])
        return self._registry.get(chosen) if chosen else None

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

        # Dynamic fallback: any available model registered in the system
        for model in self._registry.get_all():
            if healthy_models is None or model.id in healthy_models:
                logger.warning(f"[router] Using registered model: {model.id}")
                return model

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
