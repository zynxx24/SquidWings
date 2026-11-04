"""Walk-forward backtest of the Kalman-HMM model on M5 bars.

* Features are causal (features.py), so each bar sees only the past.
* The HMM is trained on the `train` bars before each refit point and used only for the next
  `refit` bars. Nothing after a bar is ever used to trade that bar.
* A signal on bar t fills at bar t+1's open, moved by half the spread against you.
* Stop and target are the levels computed at t, shifted to the fill price.
* If a bar touches both stop and target, the stop counts (the pessimistic choice).
* Commission is a fixed cost in R per trade.
* One position at a time. Timeout exit after `max_hold` bars.

The result says how many trades there were and what they did. A short sample says almost
nothing; the summary carries a warning below 100 trades.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from trading.quant import features as F
from trading.quant import model as M
from trading.quant.hmm import GaussianHMM, label_states
from trading.quant.risk import monte_carlo


def _valid_rows(f: F.Features, bars: list[dict[str, float]], lo: int, hi: int) -> list[tuple[int, list[float]]]:
    out = []
    for t in range(lo, hi):
        u = M.feature_vector(f, bars, t)
        if u is not None:
            out.append((t, u))
    return out


def run(bars: list[dict[str, float]], *, symbol: str = "XXXYYY", train: int = 2000, refit: int = 500,
        max_hold: int = 48, spread: float = 0.0, commission_r: float = 0.05, n_states: int = 3,
        hmm_iters: int = 25) -> dict[str, Any]:
    if len(bars) < train + refit + 10:
        return {"summary": {"trades": 0, "note": f"need at least {train + refit + 10} bars, got {len(bars)}"}, "trades": []}
    bars = sorted(bars, key=lambda b: b["t"])
    f = F.compute(bars)
    trades: list[dict[str, Any]] = []
    regime_counts = {"trend": 0, "range": 0, "highvol": 0}
    s = train
    next_free = 0
    while s < len(bars) - 1:
        end = min(s + refit, len(bars) - 1)
        rows = _valid_rows(f, bars, s - train, s)
        if len(rows) < n_states * 20:
            s += refit
            continue
        hmm = GaussianHMM(n_states).fit([u for _, u in rows], iters=hmm_iters)
        states = label_states(hmm)
        lo = s - train
        window_rows = [(t, M.feature_vector(f, bars, t)) for t in range(lo, end)]
        valid_idx = [k for k, (_, u) in enumerate(window_rows) if u is not None]
        post_all: dict[int, list[float]] = {}
        if valid_idx:
            seq = [u for _, u in window_rows if u is not None]
            post = hmm.filter(seq)
            for pos, k in enumerate(valid_idx):
                post_all[window_rows[k][0]] = post[pos]
        for t in range(s, end):
            if t < next_free or t + 1 >= len(bars):
                continue
            prop = M.decide(symbol, bars, f, post_all.get(t), states, t, spread=spread)
            if prop.regime in regime_counts:
                regime_counts[prop.regime] += 1
            if prop.side is None or prop.sl is None or M.sanity(prop):
                continue
            fill = bars[t + 1]["o"] + (spread / 2 if prop.side == "buy" else -spread / 2)
            risk = abs(prop.entry - prop.sl)
            if risk <= 0:
                continue
            sign = 1 if prop.side == "buy" else -1
            sl = fill - sign * risk
            tp = fill + sign * risk * M.RR
            outcome, exit_idx, exit_px = "timeout", min(t + 1 + max_hold, len(bars) - 1), None
            for j in range(t + 1, min(t + 1 + max_hold, len(bars))):
                b = bars[j]
                if (prop.side == "buy" and b["l"] <= sl) or (prop.side == "sell" and b["h"] >= sl):
                    outcome, exit_idx, exit_px = "sl", j, sl
                    break
                if (prop.side == "buy" and b["h"] >= tp) or (prop.side == "sell" and b["l"] <= tp):
                    outcome, exit_idx, exit_px = "tp", j, tp
                    break
            if exit_px is None:
                exit_px = bars[exit_idx]["c"]
            r = sign * (exit_px - fill) / risk - commission_r
            trades.append({
                "time": dt.datetime.fromtimestamp(bars[t]["t"], dt.UTC).isoformat(timespec="minutes"),
                "side": prop.side, "regime": prop.regime, "p_trend": round(prop.p_trend, 3),
                "entry": round(fill, 6), "sl": round(sl, 6), "tp": round(tp, 6), "exit": round(exit_px, 6),
                "outcome": outcome, "r": round(r, 3), "bars_held": exit_idx - t - 1, "reasons": prop.reasons,
            })
            next_free = exit_idx + 1
        s += refit
    return {"summary": summarize(trades, regime_counts), "trades": trades}


def summarize(trades: list[dict[str, Any]], regime_counts: dict[str, int] | None = None) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "regime_bars": regime_counts or {}, "note": "no trades: the rules never lined up on this data"}
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    streak = best = 0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        best = max(best, streak)
    expectancy = sum(rs) / n
    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 3),
        "expectancy_r": round(expectancy, 3),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "max_losing_streak": best,
        "regime_bars": regime_counts or {},
        "monte_carlo": monte_carlo(rs, sims=1000),
        "note": "small sample: treat as noise under ~100 trades" if n < 100 else "",
    }


__all__ = ["run", "summarize"]
