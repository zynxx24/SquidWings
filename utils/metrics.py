"""Bounded, process-local request metrics; prompts and responses are never stored."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _Series:
    requests: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    fallbacks: int = 0


class MetricsCollector:
    """Thread-safe aggregate counters suitable for concurrent web requests.

    Metrics reset on process restart. They are intentionally not durable
    billing records and do not retain user content.
    """

    def __init__(self) -> None:
        self._started_at = time.time()
        self._lock = threading.Lock()
        self._total = _Series()
        self._models: dict[str, _Series] = {}
        self._tools: dict[str, _Series] = {}

    @staticmethod
    def _add(series: _Series, success: bool, latency_ms: float, tokens: int = 0) -> None:
        series.requests += 1
        series.failures += int(not success)
        series.prompt_tokens += tokens
        series.latency_ms += max(0.0, latency_ms)

    def record_request(
        self,
        model_id: str,
        *,
        success: bool,
        latency_ms: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        task_type: str = "unknown",
        fallback_used: bool = False,
    ) -> None:
        """Record one completed orchestration request, including failed ones."""
        if model_id:
            with self._lock:
                series = self._models.setdefault(model_id, _Series())
                self._add(series, success, latency_ms)
                series.prompt_tokens += max(0, prompt_tokens)
                series.completion_tokens += max(0, completion_tokens)
                series.fallbacks += int(fallback_used)
                self._add(self._total, success, latency_ms)
                self._total.prompt_tokens += max(0, prompt_tokens)
                self._total.completion_tokens += max(0, completion_tokens)
                self._total.fallbacks += int(fallback_used)

    def record_tool(self, tool_name: str, *, success: bool, latency_ms: float) -> None:
        with self._lock:
            self._add(
                self._tools.setdefault(tool_name, _Series()), success, latency_ms
            )

    @staticmethod
    def _snapshot(series: _Series) -> dict[str, Any]:
        return {
            "requests": series.requests,
            "failures": series.failures,
            "success_rate": round(
                (series.requests - series.failures) / series.requests, 4
            ) if series.requests else 0.0,
            "average_latency_ms": round(series.latency_ms / series.requests, 2)
            if series.requests else 0.0,
            "prompt_tokens": series.prompt_tokens,
            "completion_tokens": series.completion_tokens,
            "total_tokens": series.prompt_tokens + series.completion_tokens,
            "fallbacks": series.fallbacks,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "started_at": self._started_at,
                "uptime_seconds": round(time.time() - self._started_at, 1),
                "total": self._snapshot(self._total),
                "models": {name: self._snapshot(value) for name, value in self._models.items()},
                "tools": {name: self._snapshot(value) for name, value in self._tools.items()},
            }


class TokenTracker:
    """Compatibility facade for the token aggregation part of MetricsCollector."""

    def __init__(self, metrics: MetricsCollector | None = None) -> None:
        self.metrics = metrics or MetricsCollector()

    def record(self, model_id: str, prompt_tokens: int, completion_tokens: int, **kwargs: Any) -> None:
        self.metrics.record_request(
            model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            **kwargs,
        )
