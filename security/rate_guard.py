"""
security.rate_guard - Token-bucket rate limiting per principal.

A single TokenBucket is cheap; a RateGuard holds a bounded LRU map of them
keyed by principal (user id, session id, IP, tool name). Buckets refill
lazily on access, so there is no background thread and no timer cost.

Two limits can be enforced simultaneously:
    * requests per window  (burst protection)
    * cost per window      (token / byte budget)

Both are expressed as token buckets: a request consumes 1 token from the
request bucket and `cost` tokens from the cost bucket.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field


class RateLimitExceededError(RuntimeError):
    """Raised when a principal has exhausted its budget."""

    def __init__(self, principal: str, retry_after: float, scope: str = "requests") -> None:
        self.principal = principal
        self.retry_after = max(0.0, retry_after)
        self.scope = scope
        super().__init__(
            f"Rate limit exceeded for {principal!r} ({scope}); "
            f"retry in {self.retry_after:.2f}s"
        )


# N818 wants an ``Error`` suffix; the short public name is kept as an alias so
# existing imports (tests, security/__init__.py) keep working unchanged.
RateLimitExceeded = RateLimitExceededError


@dataclass
class TokenBucket:
    """Classic token bucket with lazy refill.

    Args:
        capacity: Maximum tokens held.
        refill_rate: Tokens added per second.
        tokens: Initial tokens (defaults to full).
    """

    capacity: float
    refill_rate: float
    tokens: float = -1.0
    updated_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity must be > 0")
        if self.refill_rate < 0:
            raise ValueError("refill_rate must be >= 0")
        if self.tokens < 0:
            self.tokens = self.capacity

    def _refill(self, now: float) -> None:
        elapsed = now - self.updated_at
        if elapsed > 0 and self.refill_rate > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.updated_at = now

    def available(self, now: float | None = None) -> float:
        self._refill(now if now is not None else time.monotonic())
        return self.tokens

    def try_consume(self, amount: float = 1.0, now: float | None = None) -> tuple[bool, float]:
        """Attempt to consume `amount`.

        Returns:
            (allowed, retry_after_seconds)
        """
        if amount <= 0:
            return True, 0.0
        now = now if now is not None else time.monotonic()
        self._refill(now)
        if self.tokens >= amount:
            self.tokens -= amount
            return True, 0.0
        deficit = amount - self.tokens
        retry = deficit / self.refill_rate if self.refill_rate > 0 else float("inf")
        return False, retry

    def reset(self) -> None:
        self.tokens = self.capacity
        self.updated_at = time.monotonic()


@dataclass(frozen=True)
class Limit:
    """Declarative bucket configuration.

    Args:
        capacity: Burst size (max tokens held).
        window: Seconds for the bucket to refill from empty to full.
    """

    capacity: float
    window: float

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity must be > 0")
        if self.window <= 0:
            raise ValueError("window must be > 0")

    @property
    def refill_rate(self) -> float:
        return self.capacity / self.window


class RateGuard:
    """Per-principal token-bucket limiter with bounded memory.

    Args:
        requests: Request-rate limit (None disables request limiting).
        cost: Cost budget limit (None disables cost limiting).
        max_principals: LRU cap; least-recently-used principals are evicted.
    """

    def __init__(
        self,
        requests: Limit | None = None,
        cost: Limit | None = None,
        *,
        max_principals: int = 4096,
    ) -> None:
        # Default is materialised here rather than in the signature so the
        # mutable Limit instance is never shared between RateGuard objects.
        if requests is None:
            requests = Limit(capacity=60, window=60.0)
        if requests is None and cost is None:
            raise ValueError("at least one of requests/cost must be set")
        self._req = requests
        self._cost = cost
        self._max = max(16, max_principals)
        self._buckets: OrderedDict[str, tuple[TokenBucket, TokenBucket]] = OrderedDict()
        self._lock = threading.Lock()

    # -- public ---------------------------------------------------------
    def check(self, principal: str, *, cost: float = 1.0) -> None:
        """Consume budget or raise RateLimitExceededError.

        Args:
            principal: Identity to bill (user, session, IP, tool).
            cost: Units to charge against the cost bucket.
        """
        allowed, retry, scope = self._consume(principal, cost)
        if not allowed:
            raise RateLimitExceededError(principal, retry, scope)

    def try_acquire(self, principal: str, *, cost: float = 1.0) -> tuple[bool, float]:
        """Non-raising variant. Returns (allowed, retry_after)."""
        allowed, retry, _ = self._consume(principal, cost)
        return allowed, retry

    def remaining(self, principal: str) -> dict[str, float]:
        """Snapshot of available budget for a principal."""
        now = time.monotonic()
        with self._lock:
            entry = self._buckets.get(principal)
            if entry is None:
                return {
                    "requests": self._req.capacity if self._req else float("inf"),
                    "cost": self._cost.capacity if self._cost else float("inf"),
                }
            req_bucket, cost_bucket = entry
            return {
                "requests": req_bucket.available(now),
                "cost": cost_bucket.available(now),
            }

    def reset(self, principal: str | None = None) -> None:
        """Reset one principal's budget, or all budgets when None."""
        with self._lock:
            if principal is None:
                self._buckets.clear()
                return
            entry = self._buckets.pop(principal, None)
            if entry is not None:
                for bucket in entry:
                    bucket.reset()

    def tracked(self) -> int:
        with self._lock:
            return len(self._buckets)

    def stats(self) -> dict[str, object]:
        """Serialisable snapshot for health/status endpoints."""
        with self._lock:
            tracked = len(self._buckets)
            buckets = list(self._buckets.items())
        now = time.monotonic()
        saturated = 0
        for _principal, (req_bucket, cost_bucket) in buckets:
            if self._req is not None and req_bucket.available(now) < 1.0 or self._cost is not None and cost_bucket.available(now) <= 0.0:
                saturated += 1
        return {
            "tracked_principals": tracked,
            "max_principals": self._max,
            "throttled_principals": saturated,
            "requests": (
                {"capacity": self._req.capacity, "window": self._req.window}
                if self._req
                else None
            ),
            "cost": (
                {"capacity": self._cost.capacity, "window": self._cost.window}
                if self._cost
                else None
            ),
        }

    # -- internals ------------------------------------------------------
    def _consume(self, principal: str, cost: float) -> tuple[bool, float, str]:
        now = time.monotonic()
        with self._lock:
            entry = self._buckets.get(principal)
            if entry is None:
                entry = (
                    TokenBucket(self._req.capacity, self._req.refill_rate)
                    if self._req
                    else TokenBucket(1, 0),
                    TokenBucket(self._cost.capacity, self._cost.refill_rate)
                    if self._cost
                    else TokenBucket(1, 0),
                )
                self._buckets[principal] = entry
                self._evict()
            else:
                self._buckets.move_to_end(principal)

            req_bucket, cost_bucket = entry

            if self._req is not None:
                ok, retry = req_bucket.try_consume(1.0, now)
                if not ok:
                    return False, retry, "requests"

            if self._cost is not None and cost > 0:
                ok, retry = cost_bucket.try_consume(cost, now)
                if not ok:
                    # Refund the request token: the call never happened.
                    if self._req is not None:
                        req_bucket.tokens = min(req_bucket.capacity, req_bucket.tokens + 1.0)
                    return False, retry, "cost"

            return True, 0.0, ""

    def _evict(self) -> None:
        while len(self._buckets) > self._max:
            self._buckets.popitem(last=False)


_guard: RateGuard | None = None
_guard_lock = threading.Lock()


def get_guard() -> RateGuard:
    """Return the process-wide default guard (60 req/min, no cost limit)."""
    global _guard
    if _guard is None:
        with _guard_lock:
            if _guard is None:
                _guard = RateGuard()
    return _guard


def set_guard(guard: RateGuard) -> None:
    """Replace the process-wide default guard."""
    global _guard
    _guard = guard
