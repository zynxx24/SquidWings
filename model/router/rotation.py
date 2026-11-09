"""Which account serves the next request.

Three strategies, chosen per provider:

* ``round_robin``  every enabled account in turn, in proportion to its weight.
* ``fill_first``   the first healthy account in the list; the next one only when it is busy, cooling
                   down or failing. For a paid main key with free backups.
* ``smart``        (default) round robin that pays attention:
    - Accounts that answered 429, ran out of quota or failed are rested for a while (cooldown)
      instead of being hit again; a bad key (401/403) rests for 30 minutes.
    - A quota or rate limit rests only the model family it hit ("kg" in "kg/minimax-m2.7"). One
      account can be a router in front of several upstreams, and an empty kagiro budget says
      nothing about the Gemini key behind the same URL. A bad key or a failing server rests the
      whole account.
    - Each account's share follows its health: recent success rate, speed compared with the
      other accounts, and how many requests it is serving now. A slow or flaky key gets less
      traffic, not none, so it can show it has recovered.
    - Sticky sessions: one chat keeps using the same account while it stays healthy. Prompt
      caches live per account, so this is what turns repeated context into cached tokens.
      Different chats still spread over all accounts.
    - Smooth weighted round robin (as in nginx) does the spreading, so a heavier account is
      chosen more often without being chosen several times in a row.

The state lives in memory: a restart forgets cooldowns, which is harmless.
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from model.router.store import Account, ProviderConfig

STICKY_TTL_S = 15 * 60
STICKY_MAX = 5000
EWMA = 0.2
AUTH_COOLDOWN_S = 30 * 60
QUOTA_COOLDOWN_S = 60 * 60
MAX_RATE_COOLDOWN_S = 10 * 60
FAILURES_BEFORE_REST = 2


@dataclass
class Health:
    inflight: int = 0
    cooldown_until: float = 0.0
    cooldown_reason: str = ""
    failures: int = 0           # in a row
    rate_limits: int = 0        # 429s in a row
    success: float = 1.0        # EWMA of 1 = ok, 0 = failed
    latency_ms: float = 0.0     # EWMA of successful requests
    current: float = 0.0        # smooth weighted round robin counter
    requests: int = 0
    errors: int = 0
    last_used: float = 0.0
    last_error: str = ""

    def cooling(self, now: float) -> bool:
        return self.cooldown_until > now


@dataclass
class Pick:
    account: Account
    sticky: bool = False


class NoAccountError(RuntimeError):
    """Every account is disabled, busy or resting. ``retry_after`` says when the first one is back."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def family_of(model: str) -> str:
    """The upstream a model id belongs to: "kg" for "kg/minimax-m2.7", "" for a bare id."""
    return model.split("/", 1)[0].strip().lower() if "/" in (model or "") else ""


# Reasons that belong to one upstream behind the account, not to the account itself.
_FAMILY_REASONS = frozenset({"kuota habis", "rate limit"})


class Rotator:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._health: dict[tuple[str, str], Health] = {}
        # (provider, account, family) -> (rest until, reason)
        self._family_rest: dict[tuple[str, str, str], tuple[float, str]] = {}
        self._sticky: OrderedDict[tuple[str, str], tuple[str, float]] = OrderedDict()
        self._turn: dict[str, int] = {}

    def health(self, provider_id: str, account_id: str) -> Health:
        key = (provider_id, account_id)
        found = self._health.get(key)
        if found is None:
            found = self._health[key] = Health()
        return found

    # ------------------------------------------------------------ choosing

    def _rest_until(self, provider_id: str, account_id: str, family: str) -> float:
        until = self.health(provider_id, account_id).cooldown_until
        if family:
            until = max(until, self._family_rest.get((provider_id, account_id, family), (0.0, ""))[0])
        return until

    def pick(self, provider: ProviderConfig, session: str = "", exclude: set[str] | frozenset[str] = frozenset(),
             model: str = "") -> Pick:
        family = family_of(model)
        with self._lock:
            now = self._clock()
            enabled = [a for a in provider.accounts if a.enabled and a.id not in exclude]
            if not enabled:
                tried = " lain" if exclude else ""
                raise NoAccountError(f"Provider {provider.name} tidak punya akun{tried} yang aktif.")
            ready = [a for a in enabled if self._rest_until(provider.id, a.id, family) <= now
                     and (a.max_concurrency <= 0 or self.health(provider.id, a.id).inflight < a.max_concurrency)]
            if not ready:
                waits = [self._rest_until(provider.id, a.id, family) - now for a in enabled
                         if self._rest_until(provider.id, a.id, family) > now]
                wait = max(1.0, min(waits)) if waits else 1.0
                scope = f" untuk model {family}/*" if family and any(
                    self._family_rest.get((provider.id, a.id, family), (0.0, ""))[0] > now for a in enabled) else ""
                raise NoAccountError(f"Semua akun {provider.name} sedang istirahat atau penuh{scope}.", retry_after=wait)

            if provider.strategy == "fill_first":
                return self._take(provider, ready[0], now)
            if provider.strategy == "smart" and session:
                sticky = self._sticky.get((provider.id, session))
                if sticky and now - sticky[1] < STICKY_TTL_S:
                    account = next((a for a in ready if a.id == sticky[0]), None)
                    if account is not None and self._good(provider, account):
                        self._sticky.move_to_end((provider.id, session))
                        self._sticky[(provider.id, session)] = (account.id, now)
                        return Pick(self._take(provider, account, now).account, sticky=True)
            weights = self._weights(provider, ready) if provider.strategy == "smart" else {a.id: a.weight for a in ready}
            account = self._smooth(provider, ready, weights)
            if provider.strategy == "smart" and session:
                self._sticky[(provider.id, session)] = (account.id, now)
                self._sticky.move_to_end((provider.id, session))
                while len(self._sticky) > STICKY_MAX:
                    self._sticky.popitem(last=False)
            return self._take(provider, account, now)

    def _good(self, provider: ProviderConfig, account: Account) -> bool:
        h = self.health(provider.id, account.id)
        return h.failures == 0 and h.success >= 0.6

    def _weights(self, provider: ProviderConfig, ready: list[Account]) -> dict[str, float]:
        latencies = [self.health(provider.id, a.id).latency_ms for a in ready if self.health(provider.id, a.id).latency_ms > 0]
        median = statistics.median(latencies) if latencies else 0.0
        out: dict[str, float] = {}
        for a in ready:
            h = self.health(provider.id, a.id)
            speed = 1.0
            if median > 0 and h.latency_ms > 0:
                speed = min(1.5, max(0.5, median / h.latency_ms))
            out[a.id] = max(0.01, a.weight * (0.1 + 0.9 * h.success ** 2) * speed / (1 + h.inflight))
        return out

    def _smooth(self, provider: ProviderConfig, ready: list[Account], weights: dict[str, float]) -> Account:
        total = sum(weights.values())
        best: Account | None = None
        for a in ready:
            h = self.health(provider.id, a.id)
            h.current += weights[a.id]
            if best is None or h.current > self.health(provider.id, best.id).current:
                best = a
        assert best is not None
        self.health(provider.id, best.id).current -= total
        return best

    def _take(self, provider: ProviderConfig, account: Account, now: float) -> Pick:
        h = self.health(provider.id, account.id)
        h.inflight += 1
        h.requests += 1
        h.last_used = now
        return Pick(account)

    # ------------------------------------------------------------ outcomes

    def release(self, provider_id: str, account_id: str) -> None:
        with self._lock:
            h = self.health(provider_id, account_id)
            h.inflight = max(0, h.inflight - 1)

    def success(self, provider_id: str, account_id: str, latency_ms: float) -> None:
        with self._lock:
            h = self.health(provider_id, account_id)
            h.failures = h.rate_limits = 0
            h.success = h.success * (1 - EWMA) + EWMA
            h.latency_ms = latency_ms if h.latency_ms <= 0 else h.latency_ms * (1 - EWMA) + latency_ms * EWMA
            if h.cooldown_reason and not h.cooling(self._clock()):
                h.cooldown_reason = ""

    def failure(self, provider_id: str, account_id: str, *, status: int = 0, retry_after: float | None = None,
                message: str = "", model: str = "") -> float:
        """Record a failed request. Returns the cooldown it caused, in seconds (0 = none).

        With ``model``, a quota or rate limit rests only that model's family on this account.
        """
        family = family_of(model)
        with self._lock:
            now = self._clock()
            h = self.health(provider_id, account_id)
            h.errors += 1
            h.last_error = message[:200]
            h.success = h.success * (1 - EWMA)
            lowered = message.lower()
            rest = 0.0
            if status in (401, 403):
                rest, h.cooldown_reason = AUTH_COOLDOWN_S, "key ditolak"
            elif status == 402 or "quota" in lowered or "insufficient" in lowered or "billing" in lowered or "credit" in lowered:
                rest, h.cooldown_reason = QUOTA_COOLDOWN_S, "kuota habis"
            elif status == 429:
                h.rate_limits += 1
                rest = retry_after if retry_after and retry_after > 0 else min(MAX_RATE_COOLDOWN_S, 5.0 * 2 ** (h.rate_limits - 1))
                h.cooldown_reason = "rate limit"
            elif status == 0 or status >= 500 or status in (408, 409, 425):
                h.failures += 1
                if h.failures >= FAILURES_BEFORE_REST:
                    rest, h.cooldown_reason = min(120.0, 10.0 * h.failures), "sering gagal"
            if rest and family and h.cooldown_reason in _FAMILY_REASONS:
                family_key = (provider_id, account_id, family)
                until = max(self._family_rest.get(family_key, (0.0, ""))[0], now + rest)
                self._family_rest[family_key] = (until, h.cooldown_reason)
                # The account itself is fine for other upstreams; its record shows why one family rests.
                h.cooldown_reason = ""
                return rest
            if rest:
                h.cooldown_until = max(h.cooldown_until, now + rest)
                # A resting account must not hold sessions; they move to a healthy one.
                for key in [k for k, v in self._sticky.items() if k[0] == provider_id and v[0] == account_id]:
                    self._sticky.pop(key, None)
            return rest

    def reset(self, provider_id: str, account_id: str | None = None) -> None:
        with self._lock:
            for key in [k for k in self._health if k[0] == provider_id and (account_id is None or k[1] == account_id)]:
                self._health[key] = Health()
            for rest_key in [k for k in self._family_rest if k[0] == provider_id and (account_id is None or k[1] == account_id)]:
                self._family_rest.pop(rest_key, None)

    # ------------------------------------------------------------ for the page

    def snapshot(self, provider: ProviderConfig) -> dict[str, dict[str, object]]:
        with self._lock:
            now = self._clock()
            out: dict[str, dict[str, object]] = {}
            for a in provider.accounts:
                h = self.health(provider.id, a.id)
                cooling = h.cooling(now)
                out[a.id] = {
                    "inflight": h.inflight, "requests": h.requests, "errors": h.errors,
                    "success_pct": round(h.success * 100), "latency_ms": round(h.latency_ms),
                    "cooling_s": round(h.cooldown_until - now) if cooling else 0,
                    "cooldown_reason": h.cooldown_reason if cooling else "",
                    "last_error": h.last_error,
                    "sessions": sum(1 for k, v in self._sticky.items() if k[0] == provider.id and v[0] == a.id),
                    "resting_families": {k[2]: {"seconds": round(v[0] - now), "reason": v[1]}
                                         for k, v in self._family_rest.items()
                                         if k[0] == provider.id and k[1] == a.id and v[0] > now},
                    "status": ("mati" if not a.enabled else "istirahat" if cooling else
                               "penuh" if a.max_concurrency and h.inflight >= a.max_concurrency else "siap"),
                }
            return out


_rotator = Rotator()


def get_rotator() -> Rotator:
    return _rotator


__all__ = ["Rotator", "Health", "Pick", "NoAccountError", "family_of", "get_rotator"]
