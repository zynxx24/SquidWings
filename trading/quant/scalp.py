"""Short-horizon basket backtest: many strategies vote, one decision, five positions, 30 seconds.

* Eight strategies vote buy, sell or nothing on each bar, using that bar and older ones only.
* Each strategy's weight is its own recent hit rate above or below 50%, measured on moves that
  have already ended. A strategy near 50% loses its say, and one that keeps failing counts in reverse.
* One decision opens a basket of five positions at the next bar's open, moved by half the spread.
  They share one stop and take profit on a ladder. Whatever is left closes when the time is up.
* If a bar touches both the stop and a target, the stop counts (the pessimistic choice).
* After losses a reviewer may change the policy. It sees past baskets only, inside BOUNDS. It
  cannot touch size: volume and risk per trade stay with the risk engine, and there is no martingale.

A bar longer than the holding time cannot show what happened inside it. On one-minute bars the
basket lives for a whole bar, and the summary says so.
"""

from __future__ import annotations

import datetime as dt
import random
import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from trading.quant import features as F

LADDER = (0.6, 0.8, 1.0, 1.2, 1.4)   # each position's target, as a share of tp_atr
POSITIONS = len(LADDER)
TARGET_WIN_RATE = 0.8
RELIABILITY_DECAY = 0.97
WARMUP_BARS = 120
BOUNDS: dict[str, tuple[float, float]] = {
    "threshold": (0.15, 0.9), "tp_atr": (0.1, 2.0), "sl_atr": (0.3, 4.0), "cooldown_s": (0.0, 900.0),
}
STRATEGIES = ("ema_trend", "momentum", "breakout", "rsi_reversal", "band_reversal", "vwap_side", "kalman_slope", "candle_pressure")

Bar = dict[str, float]
Reviewer = Callable[[dict[str, Any], dict[str, float]], dict[str, Any] | None]


@dataclass
class Policy:
    # The start aims at the 8-in-10 win rate: a narrow target and a wide stop. That shape loses more
    # on a loss than it makes on a win, which is why the summary reports the break-even win rate.
    threshold: float = 0.7     # weighted vote needed to act, -1..1
    tp_atr: float = 0.3        # furthest target in ATR is 1.4 times this
    sl_atr: float = 3.0
    cooldown_s: float = 30.0
    hold_s: float = 30.0

    def apply(self, change: dict[str, Any]) -> dict[str, float]:
        """Take the allowed fields, forced inside BOUNDS. Returns what actually changed."""
        done: dict[str, float] = {}
        for name, (low, high) in BOUNDS.items():
            if name not in change:
                continue
            try:
                value = min(high, max(low, float(change[name])))
            except (TypeError, ValueError):
                continue
            if value == value and value != getattr(self, name):
                setattr(self, name, round(value, 4))
                done[name] = round(value, 4)
        return done


def _ema(values: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    out, current = [], values[0]
    for value in values:
        current = value * k + current * (1 - k)
        out.append(current)
    return out


def _rsi(closes: list[float], period: int = 14) -> list[float]:
    gain = loss = 0.0
    out = [50.0]
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = (gain * (period - 1) + max(change, 0)) / period
        loss = (loss * (period - 1) + max(-change, 0)) / period
        out.append(100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    return out


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def votes(bars: list[Bar]) -> list[list[int]]:
    """One row per strategy, one vote per bar. Every vote at bar i uses bars up to i only."""
    closes = [b["c"] for b in bars]
    n = len(bars)
    fast, slow = _ema(closes, 9), _ema(closes, 21)
    rsi = _rsi(closes)
    vwap = F.vwap_daily(bars)
    _, slope, _, _ = F.kalman(closes)
    rows = [[0] * n for _ in STRATEGIES]
    for i in range(30, n):
        c, window = closes[i], closes[i - 20:i]
        mean, spread = statistics.fmean(window), statistics.pstdev(window)
        high, low = max(b["h"] for b in bars[i - 20:i]), min(b["l"] for b in bars[i - 20:i])
        width = bars[i]["h"] - bars[i]["l"]
        place = (c - bars[i]["l"]) / width if width > 0 else 0.5
        rows[0][i] = _sign(fast[i] - slow[i])
        rows[1][i] = _sign(c - closes[i - 5])
        rows[2][i] = 1 if c > high else -1 if c < low else 0
        rows[3][i] = 1 if rsi[i] < 30 else -1 if rsi[i] > 70 else 0
        rows[4][i] = (1 if c < mean - 2 * spread else -1 if c > mean + 2 * spread else 0) if spread > 0 else 0
        rows[5][i] = _sign(c - vwap[i])
        rows[6][i] = _sign(slope[i])
        rows[7][i] = 1 if place > 0.8 else -1 if place < 0.2 else 0
    return rows


def weighted_scores(bars: list[Bar], rows: list[list[int]], horizon: int) -> list[float]:
    """The vote at each bar, -1..1, weighted by each strategy's hit rate on moves already finished."""
    closes = [b["c"] for b in bars]
    hit = [0.5] * len(rows)
    out = [0.0] * len(bars)
    for i in range(horizon, len(bars)):
        moved = _sign(closes[i] - closes[i - horizon])
        for j, row in enumerate(rows):
            past = row[i - horizon]
            if past and moved:      # the vote at i - horizon has now played out
                hit[j] = hit[j] * RELIABILITY_DECAY + (1 - RELIABILITY_DECAY) * (past == moved)
        # Dividing by every strategy's weight means silence counts against a trade: one voter out
        # of eight cannot reach a high score alone, so the threshold really does measure agreement.
        # A negative weight reads a strategy backwards: one that keeps being wrong is a signal too.
        # On real one-minute gold, breakouts failed more than they held.
        weights = [value - 0.5 for value in hit]
        weight_sum = sum(abs(w) for w in weights)
        out[i] = sum(w * row[i] for w, row in zip(weights, rows, strict=True)) / weight_sum if weight_sum else 0.0
    return out


def _bar_seconds(bars: list[Bar]) -> float:
    gaps = [bars[i + 1]["t"] - bars[i]["t"] for i in range(min(len(bars) - 1, 500))]
    return float(statistics.median(gaps)) if gaps else 60.0


def _walk_basket(bars: list[Bar], start: int, end: int, side: int, fill: float, stop: float, targets: list[float]) -> tuple[list[float], int]:
    """Each position's exit price, and the index of the bar where the last one closed."""
    exits: list[float | None] = [None] * len(targets)
    for j in range(start, end + 1):
        b = bars[j]
        if (side > 0 and b["l"] <= stop) or (side < 0 and b["h"] >= stop):
            return [stop if x is None else x for x in exits], j
        for k, target in enumerate(targets):
            if exits[k] is None and ((side > 0 and b["h"] >= target) or (side < 0 and b["l"] <= target)):
                exits[k] = target
        if all(x is not None for x in exits):
            return [float(x) for x in exits], j      # type: ignore[arg-type]
    return [bars[end]["c"] if x is None else x for x in exits], end


def _open_basket(bars: list[Bar], i: int, side: int, atr: float, policy: Policy, spread: float, commission_r: float, step: float) -> dict[str, Any]:
    fill = bars[i + 1]["o"] + side * spread / 2
    stop_distance = policy.sl_atr * atr
    stop = fill - side * stop_distance
    targets = [fill + side * share * policy.tp_atr * atr for share in LADDER]
    end = min(len(bars) - 1, i + 1 + max(0, int(policy.hold_s / step + 0.999) - 1))
    exits, last = _walk_basket(bars, i + 1, end, side, fill, stop, targets)
    r = sum(side * (price - fill) / stop_distance / POSITIONS for price in exits) - commission_r
    return {"i": i, "exit_bar": last, "side": "buy" if side > 0 else "sell", "entry": fill, "r": r,
            "stopped": any(price == stop for price in exits), "time": bars[i]["t"]}


def run(bars: list[Bar], *, policy: Policy | None = None, spread: float = 0.0, commission_r: float = 0.01,
        reviewer: Reviewer | None = None, random_entries: bool = False, seed: int = 7) -> dict[str, Any]:
    """Backtest the ensemble. `random_entries` replaces the decision with a coin, as a yardstick."""
    policy = policy or Policy()
    if len(bars) < WARMUP_BARS * 2:
        return {"summary": {"baskets": 0, "note": f"need at least {WARMUP_BARS * 2} bars, got {len(bars)}"}, "baskets": [], "reviews": []}
    bars = sorted(bars, key=lambda b: b["t"])
    step = _bar_seconds(bars)
    horizon = max(1, round(policy.hold_s / step))
    rows = votes(bars)
    scores = weighted_scores(bars, rows, horizon)
    atr = F.wilder_atr(bars)
    rng = random.Random(seed)
    baskets: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    free_at = WARMUP_BARS
    for i in range(WARMUP_BARS, len(bars) - 1):
        if i < free_at or atr[i] != atr[i] or atr[i] <= 0:
            continue
        side = _sign(scores[i]) if abs(scores[i]) >= policy.threshold else 0
        if random_entries and side:
            side = rng.choice((1, -1))
        if not side:
            continue
        basket = _open_basket(bars, i, side, atr[i], policy, spread, commission_r, step)
        baskets.append(basket)
        free_at = basket["exit_bar"] + 1 + int(policy.cooldown_s / step)
        if reviewer and (basket["r"] <= 0 or len(baskets) % 10 == 0):
            _review(reviewer, baskets, policy, reviews)
    return {"summary": summarize(baskets, step, policy, spread), "baskets": baskets[-50:], "reviews": reviews}


def recent_stats(baskets: list[dict[str, Any]], window: int = 10) -> dict[str, Any]:
    last = baskets[-window:]
    wins = [b["r"] for b in last if b["r"] > 0]
    losses = [b["r"] for b in last if b["r"] <= 0]
    streak = 0
    for b in reversed(baskets):
        if b["r"] > 0:
            break
        streak += 1
    return {"baskets": len(baskets), "window": len(last), "win_rate": round(len(wins) / len(last), 3),
            "expectancy_r": round(sum(b["r"] for b in last) / len(last), 4),
            "avg_win_r": round(statistics.fmean(wins), 4) if wins else 0.0,
            "avg_loss_r": round(statistics.fmean(losses), 4) if losses else 0.0,
            "losing_streak": streak, "stopped_out": sum(1 for b in last if b["stopped"])}


def _review(reviewer: Reviewer, baskets: list[dict[str, Any]], policy: Policy, reviews: list[dict[str, Any]]) -> None:
    stats = recent_stats(baskets)
    answer = reviewer(stats, asdict(policy)) or {}
    changed = policy.apply(answer)
    if changed:
        reviews.append({"after": len(baskets), "stats": stats, "changed": changed, "why": str(answer.get("why", ""))[:200]})


def summarize(baskets: list[dict[str, Any]], step: float, policy: Policy, spread: float) -> dict[str, Any]:
    n = len(baskets)
    out: dict[str, Any] = {"baskets": n, "positions_each": POSITIONS, "bar_seconds": step, "hold_seconds": policy.hold_s,
                           "spread": spread, "final_policy": asdict(policy), "target_win_rate": TARGET_WIN_RATE}
    if step > policy.hold_s:
        out["resolution_warning"] = (f"Bars are {step:.0f}s long, so a {policy.hold_s:.0f}s hold cannot be seen. "
                                     f"Each basket here lived for a whole {step:.0f}s bar.")
    if not n:
        out["note"] = "no baskets: the votes never reached the threshold"
        return out
    rs = [b["r"] for b in baskets]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_loss = -sum(losses)
    avg_win = statistics.fmean(wins) if wins else 0.0
    avg_loss = -statistics.fmean(losses) if losses else 0.0
    out.update(win_rate=round(len(wins) / n, 3), expectancy_r=round(sum(rs) / n, 4), total_r=round(sum(rs), 2),
               profit_factor=round(sum(wins) / gross_loss, 3) if gross_loss else None,
               avg_win_r=round(avg_win, 4), avg_loss_r=round(avg_loss, 4),
               breakeven_win_rate=round(avg_loss / (avg_win + avg_loss), 3) if avg_win + avg_loss else None,
               first=dt.datetime.fromtimestamp(baskets[0]["time"], dt.UTC).isoformat(timespec="minutes"),
               note="small sample: treat as noise under ~100 baskets" if n < 100 else "")
    return out


__all__ = ["Policy", "BOUNDS", "POSITIONS", "STRATEGIES", "TARGET_WIN_RATE", "run", "votes", "weighted_scores", "recent_stats", "summarize"]
