"""
model.registry.model_fetcher — Fetch models from the API.

Fetches the available model list from the /v1/models endpoint
and converts them to ModelInfo objects. Supports caching.
"""

from __future__ import annotations

import time
from typing import Any

from model.registry.model_info import ModelInfo
from utils.logger import get_logger

logger = get_logger(__name__)


class ModelFetcher:
    """Fetches and caches model information from the API.

    Uses the APIClient to fetch models and converts the
    raw response into ModelInfo objects.

    Attributes:
        cache_ttl: How long to cache results (seconds).
        provider_filter: Optional filter for specific provider prefix.
    """

    def __init__(
        self,
        cache_ttl: float = 300.0,  # 5 minutes
        provider_filter: str | None = None,
    ) -> None:
        self.cache_ttl = cache_ttl
        self.provider_filter = provider_filter
        self._cache: list[ModelInfo] = []
        self._cache_time: float = 0.0

    @property
    def is_cache_valid(self) -> bool:
        """Check if the cache is still valid."""
        if not self._cache:
            return False
        return (time.monotonic() - self._cache_time) < self.cache_ttl

    async def fetch(self, api_client: Any) -> list[ModelInfo]:
        """Fetch all models from the API.

        Uses caching to avoid hitting the API on every call.

        Args:
            api_client: An APIClient instance with list_models() method.

        Returns:
            list: List of ModelInfo objects.
        """
        if self.is_cache_valid:
            logger.debug(f"[fetcher] Using cached models ({len(self._cache)} models)")
            return self._cache

        logger.info("[fetcher] Fetching models from API...")
        raw_models = await api_client.list_models()

        models: list[ModelInfo] = []
        for raw in raw_models:
            model = ModelInfo.from_api_response(raw)

            # Apply provider filter if specified
            if self.provider_filter and model.provider != self.provider_filter:
                continue

            models.append(model)

        self._cache = models
        self._cache_time = time.monotonic()

        logger.info(
            f"[fetcher] Fetched {len(models)} models"
            + (f" (filtered by provider '{self.provider_filter}')" if self.provider_filter else "")
        )

        return models

    def invalidate_cache(self) -> None:
        """Force cache invalidation."""
        self._cache = []
        self._cache_time = 0.0
        logger.debug("[fetcher] Cache invalidated")

    def get_cached(self) -> list[ModelInfo]:
        """Get cached models without fetching.

        Returns:
            list: Cached ModelInfo list (may be empty if never fetched).
        """
        return list(self._cache)
