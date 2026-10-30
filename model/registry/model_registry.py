"""
model.registry.model_registry — Central model registry.

Singleton registry that holds all known models. Models can be
registered from API fetch, YAML config, or manually.
Provides lookup by ID, capability, and task type.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from model.registry.model_info import ModelInfo
from utils.logger import get_logger

logger = get_logger(__name__)


class ModelRegistry:
    """Central registry for all known models.

    Provides model lookup by ID, capability matching, and
    task-type-based model discovery.

    Usage:
        registry = ModelRegistry()
        registry.load_from_config("config/models_config.yaml")
        model = registry.get("ag/gemini-3.8-flash-high")
    """

    _instance: ModelRegistry | None = None
    _models: dict[str, ModelInfo]
    _initialized: bool

    def __new__(cls) -> ModelRegistry:
        """Singleton pattern — only one registry exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._models = {}
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if not self._initialized:
            self._models: dict[str, ModelInfo] = {}
            self._initialized = True

    # --- Registration ---

    def register(self, model: ModelInfo) -> None:
        """Register a model in the registry.

        Args:
            model: ModelInfo to register.
        """
        self._models[model.id] = model
        logger.debug(f"[registry] Registered model: {model.id}")

    def register_many(self, models: list[ModelInfo]) -> None:
        """Register multiple models at once.

        Args:
            models: List of ModelInfo objects.
        """
        for model in models:
            self.register(model)
        logger.info(f"[registry] Registered {len(models)} models")

    # --- Loading ---

    def load_from_config(self, config_path: str | Path) -> None:
        """Load models from a YAML config file.

        Args:
            config_path: Path to models_config.yaml.
        """
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"[registry] Config file not found: {path}")
            return

        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        models_config = config.get("models", {})
        for model_id, model_data in models_config.items():
            model = ModelInfo.from_config(model_id, model_data)
            self.register(model)

        logger.info(f"[registry] Loaded {len(models_config)} models from {path.name}")

    def register_from_api_models(
        self,
        models_data: list[dict[str, Any]],
        filter_gemini_only: bool = False,
    ) -> list[ModelInfo]:
        """Register models directly from 9Router API /v1/models response.

        Args:
            models_data: List of model dicts from APIClient.list_models().
            filter_gemini_only: If True, only register models containing 'gemini' or 'gemma'.

        Returns:
            List of registered ModelInfo objects.
        """
        registered: list[ModelInfo] = []
        for item in models_data:
            m_id = item.get("id") or ""
            if filter_gemini_only and not ("gemini" in m_id.lower() or "gemma" in m_id.lower()):
                continue
            model = ModelInfo.from_api_response(item)
            # Retain custom display_name and tags if already defined in config
            existing = self._models.get(model.id)
            if existing:
                if existing.display_name and existing.display_name != existing.id:
                    model.display_name = existing.display_name
                if existing.tags:
                    model.tags = existing.tags
            self.register(model)
            registered.append(model)

        logger.info(
            f"[registry] Registered {len(registered)} models from API "
            f"(filter_gemini={filter_gemini_only})"
        )
        return registered

    # --- Lookup ---

    def get(self, model_id: str) -> ModelInfo | None:
        """Get a model by its ID.

        Args:
            model_id: The model ID to look up.

        Returns:
            ModelInfo if found, None otherwise.
        """
        return self._models.get(model_id)

    def get_or_raise(self, model_id: str) -> ModelInfo:
        """Get a model by ID, raising if not found.

        Args:
            model_id: The model ID to look up.

        Returns:
            ModelInfo for the model.

        Raises:
            KeyError: If model is not registered.
        """
        model = self._models.get(model_id)
        if model is None:
            raise KeyError(
                f"Model '{model_id}' not found in registry. "
                f"Registered models: {list(self._models.keys())}"
            )
        return model

    def get_all(self) -> list[ModelInfo]:
        """Get all registered models.

        Returns:
            list: All ModelInfo objects.
        """
        return list(self._models.values())

    # --- Filtering ---

    def get_by_provider(self, provider: str) -> list[ModelInfo]:
        """Get models by provider prefix.

        Args:
            provider: Provider string (e.g., "ag", "TH").

        Returns:
            list: Models matching the provider.
        """
        return [m for m in self._models.values() if m.provider == provider]

    def get_by_tag(self, tag: str) -> list[ModelInfo]:
        """Get models that have a specific tag.

        Args:
            tag: Tag to filter by.

        Returns:
            list: Models with the matching tag.
        """
        return [m for m in self._models.values() if tag in m.tags]

    def get_with_capability(self, capability: str) -> list[ModelInfo]:
        """Get models that support a specific capability.

        Args:
            capability: Capability name (vision, audio_input, tools, etc.).

        Returns:
            list: Models supporting the capability.
        """
        return [
            m for m in self._models.values()
            if getattr(m.capabilities, capability, False)
        ]

    def get_by_thinking_format(self, thinking_format: str) -> list[ModelInfo]:
        """Get models with a specific thinking format.

        Args:
            thinking_format: Thinking format string.

        Returns:
            list: Models with matching thinking format.
        """
        return [
            m for m in self._models.values()
            if m.thinking_format == thinking_format
        ]

    # --- Utilities ---

    @property
    def count(self) -> int:
        """Number of registered models."""
        return len(self._models)

    @property
    def model_ids(self) -> list[str]:
        """List of all registered model IDs."""
        return list(self._models.keys())

    def clear(self) -> None:
        """Remove all registered models."""
        self._models.clear()
        logger.debug("[registry] Registry cleared")

    def unregister(self, model_id: str) -> bool:
        """Remove a single model from the registry.

        Args:
            model_id: The ID of the model to remove.

        Returns:
            True if removed, False if not found.
        """
        if model_id in self._models:
            del self._models[model_id]
            logger.info(f"[registry] Unregistered model: {model_id}")
            return True
        return False

    @classmethod
    def reset_singleton(cls) -> None:
        """Reset the singleton (for testing)."""
        cls._instance = None

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._models

    def __len__(self) -> int:
        return len(self._models)

    def __repr__(self) -> str:
        return f"ModelRegistry(count={self.count}, models={self.model_ids})"
