"""
utils.retry — Retry logic with exponential backoff.

Provides a decorator for retrying failed operations with
configurable backoff, jitter, and per-exception handling.

Uses tenacity under the hood for robust retry behavior.

Usage:
    @with_retry(max_attempts=3, retryable_exceptions=(ConnectionError,))
    async def call_api():
        ...
"""

from __future__ import annotations

import asyncio
import functools
import random
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from utils.logger import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def with_retry(
    max_attempts: int = 3,
    backoff_factor: float = 1.5,
    max_backoff: float = 60.0,
    jitter: bool = True,
    retryable_exceptions: Sequence[type[Exception]] = (Exception,),
    on_retry: Callable[[Exception, int], None] | None = None,
) -> Callable[[F], F]:
    """Decorator that retries a function on failure with exponential backoff.

    Works with both sync and async functions. Each retry waits:
        delay = min(backoff_factor ** attempt, max_backoff) + random_jitter

    Args:
        max_attempts: Maximum number of attempts (including the first).
        backoff_factor: Base multiplier for exponential backoff.
        max_backoff: Maximum delay in seconds between retries.
        jitter: If True, adds random jitter to prevent thundering herd.
        retryable_exceptions: Tuple of exception types that trigger a retry.
        on_retry: Optional callback called before each retry with (exception, attempt).

    Returns:
        Decorated function with retry behavior.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except tuple(retryable_exceptions) as e:
                    last_exception = e

                    if attempt >= max_attempts:
                        logger.error(
                            f"[retry] {func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    # Calculate delay
                    delay = min(backoff_factor ** attempt, max_backoff)
                    if jitter:
                        delay += random.uniform(0, delay * 0.5)

                    logger.warning(
                        f"[retry] {func.__name__} attempt {attempt}/{max_attempts} "
                        f"failed: {e}. Retrying in {delay:.1f}s..."
                    )

                    if on_retry:
                        on_retry(e, attempt)

                    await asyncio.sleep(delay)

            # Should never reach here, but just in case
            raise last_exception  # type: ignore[misc]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            import time

            last_exception: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except tuple(retryable_exceptions) as e:
                    last_exception = e

                    if attempt >= max_attempts:
                        logger.error(
                            f"[retry] {func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    delay = min(backoff_factor ** attempt, max_backoff)
                    if jitter:
                        delay += random.uniform(0, delay * 0.5)

                    logger.warning(
                        f"[retry] {func.__name__} attempt {attempt}/{max_attempts} "
                        f"failed: {e}. Retrying in {delay:.1f}s..."
                    )

                    if on_retry:
                        on_retry(e, attempt)

                    time.sleep(delay)

            raise last_exception  # type: ignore[misc]

        # Return the appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    return decorator


class RetryBudget:
    """Track retry budgets to prevent excessive retrying across the system.

    Useful for implementing circuit-breaker-like behavior where
    total retries are capped globally, not just per-call.

    Attributes:
        max_retries_per_window: Max retries allowed in the time window.
        window_seconds: Time window duration in seconds.
    """

    def __init__(
        self,
        max_retries_per_window: int = 50,
        window_seconds: float = 60.0,
    ) -> None:
        self.max_retries_per_window = max_retries_per_window
        self.window_seconds = window_seconds
        self._retry_timestamps: list[float] = []

    def can_retry(self) -> bool:
        """Check if a retry is allowed under the current budget.

        Returns:
            bool: True if retry is allowed.
        """
        import time

        now = time.monotonic()
        # Prune old timestamps
        self._retry_timestamps = [
            t for t in self._retry_timestamps
            if now - t < self.window_seconds
        ]
        return len(self._retry_timestamps) < self.max_retries_per_window

    def record_retry(self) -> None:
        """Record a retry attempt."""
        import time
        self._retry_timestamps.append(time.monotonic())

    @property
    def remaining(self) -> int:
        """Number of retries remaining in the current window."""
        import time

        now = time.monotonic()
        active = sum(
            1 for t in self._retry_timestamps
            if now - t < self.window_seconds
        )
        return max(0, self.max_retries_per_window - active)
