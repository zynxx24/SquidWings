"""
model.settings.rate_limiter — Per-model rate limiting.

Uses a token-bucket algorithm to limit requests per model.
Each model has its own bucket that refills over time.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TokenBucket:
    """A single token bucket for rate limiting.

    Attributes:
        capacity: Maximum tokens in the bucket.
        refill_rate: Tokens added per second.
        tokens: Current token count.
        last_refill: Timestamp of last refill.
    """
    capacity: float
    refill_rate: float
    tokens: float = 0.0
    last_refill: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.tokens == 0.0:
            self.tokens = self.capacity

    def refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def consume(self, amount: float = 1.0) -> bool:
        """Attempt to consume tokens.

        Args:
            amount: Number of tokens to consume.

        Returns:
            True if tokens were available and consumed.
        """
        self.refill()
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    @property
    def available(self) -> float:
        """Current available tokens after refill."""
        self.refill()
        return self.tokens


class RateLimiter:
    """Per-model rate limiter using token bucket algorithm.

    Each model gets its own bucket with configurable capacity
    and refill rate. Thread-safe.
    """

    # Default: 100 requests/minute = 1.667 requests/second
    DEFAULT_CAPACITY: float = 100.0
    DEFAULT_REFILL_RATE: float = 100.0 / 60.0

    # Default for free-tier models (100 capacity, 100 req/min)
    FREE_MODEL_CAPACITY: float = 100.0
    FREE_MODEL_REFILL_RATE: float = 100.0 / 60.0

    def __init__(
        self,
        default_capacity: float | None = None,
        default_refill_rate: float | None = None,
        model_limits: dict[str, dict[str, float]] | None = None,
    ) -> None:
        """Initialize the rate limiter.

        Args:
            default_capacity: Default bucket capacity for models.
            default_refill_rate: Default refill rate (tokens/sec).
            model_limits: Per-model overrides, e.g.
                ``{"model-id": {"capacity": 20, "refill_rate": 0.5}}``.
        """
        import os

        env_cap = os.getenv("RATE_LIMIT_CAPACITY")
        env_rpm = os.getenv("RATE_LIMIT_RPM")

        if default_capacity is not None:
            self._default_capacity = default_capacity
        elif env_cap:
            try:
                self._default_capacity = float(env_cap)
            except ValueError:
                self._default_capacity = self.DEFAULT_CAPACITY
        else:
            self._default_capacity = self.DEFAULT_CAPACITY

        if default_refill_rate is not None:
            self._default_refill_rate = default_refill_rate
        elif env_rpm:
            try:
                self._default_refill_rate = float(env_rpm) / 60.0
            except ValueError:
                self._default_refill_rate = self.DEFAULT_REFILL_RATE
        else:
            self._default_refill_rate = self.DEFAULT_REFILL_RATE

        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

        if model_limits:
            for model_id, limits in model_limits.items():
                self._buckets[model_id] = TokenBucket(
                    capacity=limits.get("capacity", self._default_capacity),
                    refill_rate=limits.get("refill_rate", self._default_refill_rate),
                )

    def _get_bucket(self, model_id: str) -> TokenBucket:
        """Get or create a bucket for a model.

        Args:
            model_id: The model identifier.

        Returns:
            The model's token bucket.
        """
        if model_id not in self._buckets:
            with self._lock:
                if model_id not in self._buckets:
                    is_free = "free" in model_id.lower()
                    cap = self.FREE_MODEL_CAPACITY if is_free else self._default_capacity
                    refill = self.FREE_MODEL_REFILL_RATE if is_free else self._default_refill_rate
                    self._buckets[model_id] = TokenBucket(
                        capacity=cap,
                        refill_rate=refill,
                    )
        return self._buckets[model_id]

    def acquire(self, model_id: str, tokens: float = 1.0) -> bool:
        """Attempt to acquire rate limit tokens for a model.

        Args:
            model_id: The model to rate-limit.
            tokens: Number of tokens to consume (default 1).

        Returns:
            True if the request is allowed (tokens available).
        """
        bucket = self._get_bucket(model_id)
        with self._lock:
            allowed = bucket.consume(tokens)

        if not allowed:
            logger.warning(
                f"[rate_limit] Rate limited: {model_id} "
                f"(available={bucket.available:.1f})"
            )
        else:
            logger.debug(
                f"[rate_limit] Acquired for {model_id} "
                f"(remaining={bucket.available:.1f})"
            )

        return allowed

    def is_available(self, model_id: str) -> bool:
        """Check if a model has available rate limit tokens.

        Args:
            model_id: The model to check.

        Returns:
            True if at least 1 token is available.
        """
        bucket = self._get_bucket(model_id)
        return bucket.available >= 1.0

    def get_wait_time(self, model_id: str) -> float:
        """Get estimated wait time until a token is available.

        Args:
            model_id: The model to check.

        Returns:
            Seconds to wait, or 0.0 if available now.
        """
        bucket = self._get_bucket(model_id)
        now = time.monotonic()
        penalty_wait = max(0.0, bucket.last_refill - now)
        available = bucket.available
        if available >= 1.0 and penalty_wait == 0.0:
            return 0.0
        needed = max(0.0, 1.0 - available)
        refill_time = (needed / bucket.refill_rate) if bucket.refill_rate > 0 else 5.0
        return penalty_wait + refill_time

    async def wait_and_acquire(
        self,
        model_id: str,
        tokens: float = 1.0,
        max_wait: float = 30.0,
    ) -> bool:
        """Wait until tokens are available (up to max_wait) and acquire them.

        Args:
            model_id: The model identifier.
            tokens: Number of tokens to consume.
            max_wait: Maximum seconds to wait before giving up.

        Returns:
            True if acquired, False if timed out.
        """
        import asyncio

        start = time.monotonic()
        while True:
            if self.acquire(model_id, tokens):
                return True
            wait_time = self.get_wait_time(model_id)
            elapsed = time.monotonic() - start
            if wait_time <= 0.0:
                wait_time = 0.5
            if (elapsed + wait_time) > max_wait:
                logger.warning(
                    f"[rate_limit] Max wait {max_wait}s exceeded for {model_id} (needed {wait_time:.1f}s)"
                )
                return False
            logger.info(
                f"[rate_limit] Throttling {model_id}: waiting {wait_time:.2f}s before sending..."
            )
            await asyncio.sleep(wait_time)

    def penalize(self, model_id: str, penalty_seconds: float = 5.0) -> None:
        """Penalize model bucket after HTTP 429 so subsequent/concurrent calls back off.

        Args:
            model_id: The model that was rate limited.
            penalty_seconds: Seconds to cool down before token refills resume.
        """
        bucket = self._get_bucket(model_id)
        with self._lock:
            bucket.tokens = 0.0
            bucket.last_refill = time.monotonic() + penalty_seconds
            logger.warning(
                f"[rate_limit] Model {model_id} penalized with {penalty_seconds:.1f}s cool-down"
            )

    def get_status(self, model_id: str) -> dict[str, Any]:
        """Get rate limit status for a model.

        Args:
            model_id: The model to check.

        Returns:
            dict with capacity, available, refill_rate.
        """
        bucket = self._get_bucket(model_id)
        return {
            "model_id": model_id,
            "capacity": bucket.capacity,
            "available": round(bucket.available, 2),
            "refill_rate": bucket.refill_rate,
            "wait_time": round(self.get_wait_time(model_id), 2),
        }

    def reset(self, model_id: str | None = None) -> None:
        """Reset rate limits.

        Args:
            model_id: Specific model to reset, or None for all.
        """
        with self._lock:
            if model_id:
                self._buckets.pop(model_id, None)
            else:
                self._buckets.clear()


_default_rate_limiter: RateLimiter | None = None


def get_default_rate_limiter() -> RateLimiter:
    """Get the shared singleton RateLimiter instance."""
    global _default_rate_limiter
    if _default_rate_limiter is None:
        _default_rate_limiter = RateLimiter()
    return _default_rate_limiter

