"""
model.settings.load_balancer — Load balancing across models.

Distributes requests across candidate models using configurable
strategies: round-robin, least-connections, or weighted random.
"""

from __future__ import annotations

import random
import threading
from enum import StrEnum, unique

from model.registry.model_info import ModelInfo
from utils.logger import get_logger

logger = get_logger(__name__)


@unique
class BalancingStrategy(StrEnum):
    """Load balancing strategy options."""
    ROUND_ROBIN = "round_robin"
    LEAST_CONNECTIONS = "least_connections"
    WEIGHTED = "weighted"


class LoadBalancer:
    """Distributes requests across candidate models.

    Supports multiple strategies and tracks active request counts
    per model to enable least-connections routing.

    Thread-safe: uses a lock for counter mutations.
    """

    def __init__(
        self,
        strategy: BalancingStrategy = BalancingStrategy.ROUND_ROBIN,
        weights: dict[str, float] | None = None,
    ) -> None:
        """Initialize the load balancer.

        Args:
            strategy: The balancing strategy to use.
            weights: Optional weight map (model_id → weight) for
                     weighted strategy. Higher weight = more traffic.
        """
        self._strategy = strategy
        self._weights: dict[str, float] = weights or {}
        self._active_counts: dict[str, int] = {}
        self._rr_index: int = 0
        self._lock = threading.Lock()

    @property
    def strategy(self) -> BalancingStrategy:
        """Current balancing strategy."""
        return self._strategy

    def select(self, candidates: list[ModelInfo]) -> ModelInfo:
        """Select the next model from candidates.

        Args:
            candidates: List of available models to choose from.

        Returns:
            The selected ModelInfo.

        Raises:
            ValueError: If candidates list is empty.
        """
        if not candidates:
            raise ValueError("No candidate models provided to load balancer")

        if len(candidates) == 1:
            return candidates[0]

        if self._strategy == BalancingStrategy.ROUND_ROBIN:
            return self._round_robin(candidates)
        elif self._strategy == BalancingStrategy.LEAST_CONNECTIONS:
            return self._least_connections(candidates)
        elif self._strategy == BalancingStrategy.WEIGHTED:
            return self._weighted(candidates)
        else:
            return candidates[0]

    def _round_robin(self, candidates: list[ModelInfo]) -> ModelInfo:
        """Select using round-robin rotation."""
        with self._lock:
            idx = self._rr_index % len(candidates)
            self._rr_index += 1
        selected = candidates[idx]
        logger.debug(f"[lb] Round-robin selected: {selected.id} (idx={idx})")
        return selected

    def _least_connections(self, candidates: list[ModelInfo]) -> ModelInfo:
        """Select the model with fewest active connections."""
        with self._lock:
            counts = {
                c.id: self._active_counts.get(c.id, 0)
                for c in candidates
            }

        min_count = min(counts.values())
        least_loaded = [
            c for c in candidates
            if counts.get(c.id, 0) == min_count
        ]

        selected = least_loaded[0]
        logger.debug(
            f"[lb] Least-connections selected: {selected.id} "
            f"(active={min_count})"
        )
        return selected

    def _weighted(self, candidates: list[ModelInfo]) -> ModelInfo:
        """Select using weighted random choice."""
        weights = [
            self._weights.get(c.id, 1.0) for c in candidates
        ]
        total = sum(weights)
        if total == 0:
            return candidates[0]

        selected = random.choices(candidates, weights=weights, k=1)[0]
        logger.debug(f"[lb] Weighted selected: {selected.id}")
        return selected

    def acquire(self, model_id: str) -> None:
        """Record that a request is being sent to a model.

        Args:
            model_id: The model receiving a request.
        """
        with self._lock:
            self._active_counts[model_id] = (
                self._active_counts.get(model_id, 0) + 1
            )

    def release(self, model_id: str) -> None:
        """Record that a request to a model has completed.

        Args:
            model_id: The model that finished a request.
        """
        with self._lock:
            count = self._active_counts.get(model_id, 0)
            self._active_counts[model_id] = max(0, count - 1)

    def get_active_count(self, model_id: str) -> int:
        """Get active request count for a model.

        Args:
            model_id: The model to check.

        Returns:
            int: Number of active requests.
        """
        return self._active_counts.get(model_id, 0)

    def get_all_active_counts(self) -> dict[str, int]:
        """Get active counts for all tracked models."""
        with self._lock:
            return dict(self._active_counts)

    def reset(self) -> None:
        """Reset all counters and state."""
        with self._lock:
            self._active_counts.clear()
            self._rr_index = 0
