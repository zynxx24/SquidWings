"""Async per-resource circuit breakers with bounded recovery probes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, TypeVar

from utils.logger import get_logger

logger = get_logger(__name__)
T = TypeVar("T")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised without performing I/O when a resource is cooling down."""

    def __init__(self, resource: str, retry_after: float) -> None:
        self.resource = resource
        self.retry_after = max(0.0, retry_after)
        super().__init__(
            f"Circuit for '{resource}' is open; retry in {self.retry_after:.1f}s"
        )


class AsyncCircuitBreaker:
    """Trip after consecutive failures and allow one recovery probe.

    State transitions and the half-open reservation are atomic in the event
    loop. A caller cancelled while holding a probe releases that reservation
    so later requests are not blocked indefinitely.
    """

    def __init__(
        self,
        resource: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if recovery_timeout < 0:
            raise ValueError("recovery_timeout cannot be negative")
        self.resource = resource
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_active = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def snapshot(self) -> dict[str, Any]:
        retry_after = (
            max(0.0, self.recovery_timeout - (time.monotonic() - self._opened_at))
            if self._state == CircuitState.OPEN
            else 0.0
        )
        return {
            "resource": self.resource,
            "state": self._state.value,
            "consecutive_failures": self._failures,
            "retry_after_seconds": round(retry_after, 2),
        }

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            now = time.monotonic()
            if self._state == CircuitState.OPEN:
                retry_after = self.recovery_timeout - (now - self._opened_at)
                if retry_after > 0:
                    raise CircuitOpenError(self.resource, retry_after)
                self._state = CircuitState.HALF_OPEN
                self._probe_active = False
            if self._state == CircuitState.HALF_OPEN:
                if self._probe_active:
                    raise CircuitOpenError(self.resource, self.recovery_timeout)
                self._probe_active = True

        try:
            result = await operation()
        except asyncio.CancelledError:
            async with self._lock:
                if self._state == CircuitState.HALF_OPEN:
                    self._probe_active = False
                    self._opened_at = time.monotonic()
            raise
        except Exception:
            async with self._lock:
                self._failures += 1
                if (
                    self._state == CircuitState.HALF_OPEN
                    or self._failures >= self.failure_threshold
                ):
                    self._state = CircuitState.OPEN
                    self._opened_at = time.monotonic()
                self._probe_active = False
            logger.warning("[circuit] %s failure recorded", self.resource)
            raise
        else:
            async with self._lock:
                self._state = CircuitState.CLOSED
                self._failures = 0
                self._probe_active = False
            return result


class CircuitBreakerRegistry:
    """Lazily create one breaker per model without a global asyncio loop."""

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._breakers: dict[str, AsyncCircuitBreaker] = {}

    def get(self, resource: str) -> AsyncCircuitBreaker:
        if resource not in self._breakers:
            self._breakers[resource] = AsyncCircuitBreaker(
                resource, self.failure_threshold, self.recovery_timeout
            )
        return self._breakers[resource]

    def snapshot(self) -> list[dict[str, Any]]:
        return [breaker.snapshot() for breaker in self._breakers.values()]
