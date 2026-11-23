"""Intraday paths that are consistent with a daily candle: scenarios, never evidence.

A D1 candle fixes four prices (open, high, low, close). Many minute-by-minute days fit them. This
module draws some, so that a plan can be replayed against several possible days instead of one, and
the daily history can be stress-tested at the M5 scale the brain works in.

How a day is drawn:

* the order of the extremes follows the day: on an up day the low more likely came first (0.58), on a
  down day the high; their times are drawn inside the day, so the path is
  ``open -> first extreme -> second extreme -> close``;
* between two knots the path is a Brownian bridge (random steps from a Student-t with 4 degrees of
  freedom, so there are fat tails, minus the straight line to the next knot), with each minute's step
  scaled by the hour's typical volatility when real M5 bars are given (``hour_profile``);
* anything that would leave [low, high] is reflected back in (a triangle-wave fold), so the day's range
  is exactly the candle's and the extremes sit on the knots;
* a minute's high and low add a wick to its open-close, clipped to the range; the day's tick volume is
  shared in proportion to the hour's weight and the minute's move.

What these paths are not: data to train the brain on. A bridge is pinned to the close, so within a
day it drifts straight toward where it must end, which makes the early part of the day predict the
rest far better than a real market does. A network trained on them would learn that and look good on
more of the same. Use them for scenarios (does this stop survive the range of 2000 plausible days)
and keep the brain's memory real. ``write`` puts them under ``trading/data/synth``, where nothing
reads them back as history.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from trading.quant import ticks

MINUTES = 1440
FIRST_EXTREME_ODDS = 0.58            # the low first on an up day, the high first on a down day
FOLDER = Path(__file__).resolve().parents[1] / "data" / "synth"


def hour_profile(base_m5: list[dict[str, float]]) -> np.ndarray:
    """Typical |close - open| of an M5 bar for each UTC hour from real bars, scaled to mean 1 (flat without data)."""
    sums, counts = np.zeros(24), np.zeros(24)
    for b in base_m5:
        hour = int(b["t"] % 86400 // 3600)
        sums[hour] += abs(b["c"] - b["o"])
        counts[hour] += 1
    if counts.min() < 20 or sums.sum() <= 0:
        return np.ones(24)
    profile = sums / counts
    return np.maximum(profile / profile.mean(), 0.2)


def _fold(x: np.ndarray, low: float, high: float) -> np.ndarray:
    """Reflect values back into [low, high]."""
    span = high - low
    r = np.mod(x - low, 2 * span)
    folded: np.ndarray = low + np.where(r <= span, r, 2 * span - r)
    return folded


def _bridge(rng: np.random.Generator, start: float, end: float, weights: np.ndarray, scale: float) -> np.ndarray:
    """Values at the segment's minutes (start excluded, end included): a bridge from start to end."""
    n = len(weights)
    steps = rng.standard_t(4, n) / math.sqrt(2.0) * np.sqrt(weights) * scale
    walk = np.cumsum(steps)
    line = np.arange(1, n + 1) / n
    return start + (end - start) * line + (walk - walk[-1] * line)


def day_bars(day: dict[str, float], rng: np.random.Generator, profile: np.ndarray | None = None) -> list[dict[str, float]]:
    """One day of M1 bars whose open, high, low, close and total tick volume are the candle's."""
    o, h, lo, c = day["o"], day["h"], day["l"], day["c"]
    start = float(day["t"] // 86400 * 86400)
    weights = np.repeat(profile if profile is not None else np.ones(24), 60)
    if h - lo <= 1e-12:
        closes = np.full(MINUTES, c)
    else:
        up = c >= o
        low_first = rng.random() < (FIRST_EXTREME_ODDS if up else 1 - FIRST_EXTREME_ODDS)
        t1 = int(rng.uniform(0.05, 0.55) * MINUTES)
        t2 = int(rng.uniform(t1 / MINUTES + 0.1, 0.95) * MINUTES)
        first, second = (lo, h) if low_first else (h, lo)
        knots = [(0, o), (max(1, t1), first), (max(2, t2), second), (MINUTES, c)]
        scale = (h - lo) / math.sqrt(MINUTES) * 0.9
        closes = np.empty(MINUTES)
        for (a, va), (b, vb) in zip(knots[:-1], knots[1:], strict=True):
            closes[a:b] = _fold(_bridge(rng, va, vb, weights[a:b], scale), lo, h)
            closes[b - 1] = vb                          # the knot itself, exactly (the fold leaves it alone)
        closes[-1] = c
    opens = np.concatenate(([o], closes[:-1]))
    move = np.abs(closes - opens)
    wick = np.abs(rng.standard_t(4, MINUTES)) * 0.3 * (move + (h - lo) / MINUTES * 0.3)
    highs = np.minimum(np.maximum(opens, closes) + wick, h)
    lows = np.maximum(np.minimum(opens, closes) - rng.permutation(wick), lo)
    share = weights * (1.0 + move / max(float(move.mean()), 1e-12))
    volume = share / share.sum() * max(day.get("v", MINUTES), MINUTES)
    return [{"t": start + 60.0 * m, "o": float(opens[m]), "h": float(highs[m]), "l": float(lows[m]), "c": float(closes[m]),
             "v": float(volume[m])} for m in range(MINUTES)]


def to_m5(m1: list[dict[str, float]]) -> list[dict[str, float]]:
    """Group M1 bars into M5 bars (five at a time)."""
    out = []
    for i in range(0, len(m1) - 4, 5):
        chunk = m1[i:i + 5]
        out.append({"t": chunk[0]["t"], "o": chunk[0]["o"], "h": max(b["h"] for b in chunk), "l": min(b["l"] for b in chunk),
                    "c": chunk[-1]["c"], "v": sum(b["v"] for b in chunk)})
    return out


def series(days: list[dict[str, float]], *, seed: int = 0, profile: np.ndarray | None = None) -> list[dict[str, float]]:
    """M5 bars for every day, one random draw per day (the same seed gives the same history)."""
    rng = np.random.default_rng(seed)
    out: list[dict[str, float]] = []
    for day in days:
        out.extend(to_m5(day_bars(day, rng, profile)))
    return out


def write(symbol: str, bars: list[dict[str, float]], folder: Path | None = None) -> Path:
    """Save as ``<symbol>-synthetic-M5.csv`` under ``trading/data/synth`` (never in ``bars``, which is real history)."""
    root = folder or FOLDER
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{ticks._safe(symbol)}-synthetic-M5.csv"
    path.write_text("".join(f"{b['t']:.0f},{b['o']:.5f},{b['h']:.5f},{b['l']:.5f},{b['c']:.5f},{b['v']:.0f}\n" for b in bars),
                    encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - a convenience entry point
    import argparse

    from trading.quant import daily

    parser = argparse.ArgumentParser(description="Write scenario M5 bars from the saved D1 candles (not history).")
    parser.add_argument("symbol")
    parser.add_argument("--days", type=int, default=250, help="the newest N daily candles")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    days = daily.load(args.symbol)[-args.days:]
    if not days:
        raise SystemExit(f"no daily file for {args.symbol} in trading/data/bars")
    from trading.quant import memory

    profile = hour_profile(ticks.load_saved_series(args.symbol, "M5"))
    bars = series(days, seed=args.seed, profile=profile)
    print(f"{write(args.symbol, bars)}: {len(bars)} synthetic M5 bars from {len(days)} days ({memory.BASE_SECONDS}s each)")


__all__: list[Any] = ["day_bars", "to_m5", "series", "write", "hour_profile"]

if __name__ == "__main__":  # pragma: no cover
    main()
