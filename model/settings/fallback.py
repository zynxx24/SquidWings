"""
model.settings.fallback — Fallback strategy for model failures.

Manages fallback chains per task type. When a primary model fails,
this module determines the next model to try.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from config.constants import TaskType
from model.registry.model_info import ModelInfo
from model.registry.model_registry import ModelRegistry
from utils.exceptions import FallbackExhaustedError
from utils.logger import get_logger

logger = get_logger(__name__)


class FallbackStrategy:
    """Manages fallback chains for model failures.

    Each task type has an ordered list of fallback models.
    When the primary fails, the next model in the chain is tried.

    Tracks which models have already been tried to avoid loops.
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        registry: ModelRegistry | None = None,
    ) -> None:
        self._registry = registry or ModelRegistry()
        self._chains: dict[str, list[str]] = {}
        self._tried: dict[str, set[str]] = {}  # session_key → tried model IDs

        if config_path:
            self.load_config(config_path)

    def load_config(self, config_path: str | Path) -> None:
        """Load fallback chains from YAML config.

        Args:
            config_path: Path to models_config.yaml.
        """
        path = Path(config_path)
        if not path.exists():
            return

        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        routing = config.get("routing", {})
        for task_type, task_config in routing.items():
            chain = task_config.get("fallback_chain", [])
            self._chains[task_type] = chain

        logger.info(f"[fallback] Loaded chains for {len(self._chains)} task types")

    def get_chain(self, task_type: TaskType) -> list[str]:
        """Get the fallback chain for a task type.

        Args:
            task_type: The task type.

        Returns:
            list: Ordered list of fallback model IDs.
        """
        return self._chains.get(task_type.value, [])

    def get_next(
        self,
        task_type: TaskType,
        failed_model: str,
        session_key: str = "default",
    ) -> ModelInfo | None:
        """Get the next fallback model after a failure.

        Args:
            task_type: The task type.
            failed_model: The model ID that just failed.
            session_key: Session identifier for tracking tried models.

        Returns:
            Next ModelInfo to try, or None if all exhausted.
        """
        # Track the failed model
        if session_key not in self._tried:
            self._tried[session_key] = set()
        self._tried[session_key].add(failed_model)

        chain = self.get_chain(task_type)
        tried = self._tried[session_key]

        for model_id in chain:
            if model_id not in tried:
                model = self._registry.get(model_id)
                if model:
                    logger.info(
                        f"[fallback] {task_type.value}: "
                        f"{failed_model} → {model.id}"
                    )
                    return model

        logger.warning(
            f"[fallback] All models exhausted for {task_type.value}. "
            f"Tried: {sorted(tried)}"
        )
        return None

    def get_next_or_raise(
        self,
        task_type: TaskType,
        failed_model: str,
        session_key: str = "default",
    ) -> ModelInfo:
        """Get next fallback or raise FallbackExhaustedError.

        Args:
            task_type: The task type.
            failed_model: The model that failed.
            session_key: Session identifier.

        Returns:
            ModelInfo of the next fallback.

        Raises:
            FallbackExhaustedError: If no more fallbacks available.
        """
        model = self.get_next(task_type, failed_model, session_key)
        if model is None:
            raise FallbackExhaustedError(
                task_type=task_type.value,
                tried_models=sorted(self._tried.get(session_key, set())),
            )
        return model

    def reset_session(self, session_key: str = "default") -> None:
        """Reset tried models for a session.

        Args:
            session_key: Session to reset.
        """
        self._tried.pop(session_key, None)

    def reset_all(self) -> None:
        """Reset all session tracking."""
        self._tried.clear()
