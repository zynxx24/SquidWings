"""
model.connection.health_check — Model endpoint health checking.

Periodically checks model availability and tracks health status.
Unhealthy models are excluded from routing decisions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum, unique

from utils.logger import get_logger

logger = get_logger(__name__)


@unique
class HealthStatus(StrEnum):
    """Health status of a model endpoint."""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    DEGRADED = "degraded"  # Working but slow


@dataclass
class ModelHealth:
    """Health state for a single model.

    Attributes:
        model_id: The model's identifier.
        status: Current health status.
        last_check: Timestamp of last health check.
        last_success: Timestamp of last successful request.
        consecutive_failures: Number of consecutive failures.
        avg_latency_ms: Rolling average latency in milliseconds.
    """
    model_id: str
    status: HealthStatus = HealthStatus.UNKNOWN
    last_check: float = 0.0
    last_success: float = 0.0
    consecutive_failures: int = 0
    avg_latency_ms: float = 0.0
    total_requests: int = 0
    total_failures: int = 0

    @property
    def is_healthy(self) -> bool:
        """Check if the model is considered healthy."""
        return self.status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)

    @property
    def failure_rate(self) -> float:
        """Calculate the failure rate (0.0 - 1.0)."""
        if self.total_requests == 0:
            return 0.0
        return self.total_failures / self.total_requests


class HealthChecker:
    """Tracks and checks health of model endpoints.

    Maintains health state per model and provides filtering
    for routing decisions.

    Attributes:
        unhealthy_threshold: Consecutive failures before marking unhealthy.
        recovery_threshold: Seconds to wait before rechecking unhealthy model.
        degraded_latency_ms: Latency threshold for degraded status.
    """

    def __init__(
        self,
        unhealthy_threshold: int = 3,
        recovery_threshold: float = 60.0,
        degraded_latency_ms: float = 10000.0,
    ) -> None:
        self.unhealthy_threshold = unhealthy_threshold
        self.recovery_threshold = recovery_threshold
        self.degraded_latency_ms = degraded_latency_ms
        self._health: dict[str, ModelHealth] = {}

    def _ensure_model(self, model_id: str) -> ModelHealth:
        """Get or create health record for a model."""
        if model_id not in self._health:
            self._health[model_id] = ModelHealth(model_id=model_id)
        return self._health[model_id]

    def record_success(self, model_id: str, latency_ms: float) -> None:
        """Record a successful request to a model.

        Args:
            model_id: The model that responded successfully.
            latency_ms: Response latency in milliseconds.
        """
        health = self._ensure_model(model_id)
        health.total_requests += 1
        health.consecutive_failures = 0
        health.last_success = time.monotonic()
        health.last_check = time.monotonic()

        # Rolling average latency
        if health.avg_latency_ms == 0:
            health.avg_latency_ms = latency_ms
        else:
            health.avg_latency_ms = (health.avg_latency_ms * 0.7) + (latency_ms * 0.3)

        # Update status
        if health.avg_latency_ms > self.degraded_latency_ms:
            health.status = HealthStatus.DEGRADED
        else:
            health.status = HealthStatus.HEALTHY

    def record_failure(self, model_id: str) -> None:
        """Record a failed request to a model.

        Args:
            model_id: The model that failed.
        """
        health = self._ensure_model(model_id)
        health.total_requests += 1
        health.total_failures += 1
        health.consecutive_failures += 1
        health.last_check = time.monotonic()

        if health.consecutive_failures >= self.unhealthy_threshold:
            health.status = HealthStatus.UNHEALTHY
            logger.warning(
                f"[health] Model '{model_id}' marked UNHEALTHY "
                f"({health.consecutive_failures} consecutive failures)"
            )

    def is_healthy(self, model_id: str) -> bool:
        """Check if a model is healthy enough to receive requests.

        Also considers recovery: if an unhealthy model hasn't been
        checked recently, it's allowed through for a retry.

        Args:
            model_id: Model to check.

        Returns:
            bool: True if the model should be used.
        """
        health = self._ensure_model(model_id)

        if health.status == HealthStatus.UNKNOWN:
            return True  # Give unknown models a chance

        if health.status == HealthStatus.UNHEALTHY:
            # Check if recovery period has elapsed
            elapsed = time.monotonic() - health.last_check
            if elapsed > self.recovery_threshold:
                logger.info(
                    f"[health] Model '{model_id}' recovery check "
                    f"(was unhealthy for {elapsed:.0f}s)"
                )
                return True  # Allow retry
            return False

        return True

    def filter_healthy(self, model_ids: list[str]) -> list[str]:
        """Filter a list of model IDs to only healthy ones.

        Args:
            model_ids: List of model IDs to filter.

        Returns:
            list: Only the healthy model IDs.
        """
        return [m for m in model_ids if self.is_healthy(m)]

    def get_health(self, model_id: str) -> ModelHealth:
        """Get the health record for a model.

        Args:
            model_id: Model to look up.

        Returns:
            ModelHealth: The health record.
        """
        return self._ensure_model(model_id)

    def get_all_health(self) -> dict[str, ModelHealth]:
        """Get health records for all tracked models.

        Returns:
            dict: Mapping of model_id → ModelHealth.
        """
        return dict(self._health)

    def reset(self, model_id: str | None = None) -> None:
        """Reset health tracking.

        Args:
            model_id: Specific model to reset, or None for all.
        """
        if model_id:
            self._health.pop(model_id, None)
        else:
            self._health.clear()
