"""
utils.timer — Performance timing utilities.

Provides a context manager and decorator for measuring execution time
of functions, API calls, and other operations.

Usage:
    # As context manager
    with Timer("api_call") as t:
        result = await client.send(...)
    print(f"Took {t.elapsed_ms:.1f}ms")

    # As decorator
    @timed
    async def process_request():
        ...
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Callable
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class Timer:
    """Context manager for measuring execution time.

    Attributes:
        name: Label for this timer (used in logs).
        start_time: When the timer started (monotonic).
        end_time: When the timer stopped (monotonic).
        elapsed: Elapsed time in seconds.
        elapsed_ms: Elapsed time in milliseconds.
    """

    def __init__(self, name: str = "operation", auto_log: bool = True) -> None:
        """Initialize timer.

        Args:
            name: Label for this timer.
            auto_log: If True, automatically logs elapsed time on exit.
        """
        self.name = name
        self.auto_log = auto_log
        self.start_time: float = 0.0
        self.end_time: float = 0.0

    @property
    def elapsed(self) -> float:
        """Elapsed time in seconds."""
        if self.end_time:
            return self.end_time - self.start_time
        return time.monotonic() - self.start_time

    @property
    def elapsed_ms(self) -> float:
        """Elapsed time in milliseconds."""
        return self.elapsed * 1000

    def __enter__(self) -> Timer:
        self.start_time = time.monotonic()
        return self

    def __exit__(self, *args: Any) -> None:
        self.end_time = time.monotonic()
        if self.auto_log:
            logger.debug(f"[timer] {self.name}: {self.elapsed_ms:.1f}ms")

    async def __aenter__(self) -> Timer:
        self.start_time = time.monotonic()
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.end_time = time.monotonic()
        if self.auto_log:
            logger.debug(f"[timer] {self.name}: {self.elapsed_ms:.1f}ms")

    def __repr__(self) -> str:
        return f"Timer(name='{self.name}', elapsed_ms={self.elapsed_ms:.1f})"


def timed[F: Callable[..., Any]](func: F) -> F:
    """Decorator that logs execution time of a function.

    Works with both sync and async functions.

    Args:
        func: Function to time.

    Returns:
        Wrapped function that logs its execution time.
    """

    @functools.wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        async with Timer(func.__qualname__):
            return await func(*args, **kwargs)

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        with Timer(func.__qualname__):
            return func(*args, **kwargs)

    if asyncio.iscoroutinefunction(func):
        return async_wrapper  # type: ignore[return-value]
    return sync_wrapper  # type: ignore[return-value]


class StopWatch:
    """Multi-lap stopwatch for tracking multiple phases of an operation.

    Usage:
        sw = StopWatch()
        sw.start()
        # ... phase 1 ...
        sw.lap("classify")
        # ... phase 2 ...
        sw.lap("route")
        # ... phase 3 ...
        sw.lap("execute")
        sw.stop()
        print(sw.summary())
    """

    def __init__(self) -> None:
        self._start_time: float = 0.0
        self._laps: list[tuple[str, float]] = []
        self._stop_time: float = 0.0

    def start(self) -> None:
        """Start the stopwatch."""
        self._start_time = time.monotonic()
        self._laps = []

    def lap(self, name: str) -> float:
        """Record a lap.

        Args:
            name: Label for this lap.

        Returns:
            float: Elapsed milliseconds since last lap (or start).
        """
        now = time.monotonic()
        self._laps.append((name, now))
        return (now - (self._laps[-2][1] if len(self._laps) > 1 else self._start_time)) * 1000

    def stop(self) -> float:
        """Stop the stopwatch.

        Returns:
            float: Total elapsed milliseconds.
        """
        self._stop_time = time.monotonic()
        return (self._stop_time - self._start_time) * 1000

    @property
    def total_ms(self) -> float:
        """Total elapsed time in milliseconds."""
        end = self._stop_time or time.monotonic()
        return (end - self._start_time) * 1000

    def summary(self) -> dict[str, float]:
        """Get a summary of all laps.

        Returns:
            Dict mapping lap names to their duration in milliseconds.
        """
        result: dict[str, float] = {}
        prev_time = self._start_time

        for name, lap_time in self._laps:
            result[name] = (lap_time - prev_time) * 1000
            prev_time = lap_time

        result["_total"] = self.total_ms
        return result
