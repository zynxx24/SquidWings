"""What the daily history says about the days ahead, with every claim tested against luck.

Three independent readers look at the closed D1 candles; the LLM gets what each one says and whether
it passed. A reader that did not pass is reported as such, because a pattern found by searching
2000 days is mostly noise unless it survives a test it could have failed.

1. **Cycles** (``cycle_view``): the strongest periodicity of the daily returns in the last 128, 256 and
   512 days and over the whole history, by the same FFT periodogram, Fisher's exact test and permutation
   test as the intraday cycle in ``cycles``. Several windows are looked at, so each test is held to
   0.05 divided by their number (Bonferroni).
   A cycle that passes comes with its phase: whether the price component is rising and how many
   days to its next peak and trough. Nothing is said about one that did not.
2. **Analogs** (``analogs``): the days whose daily picture (momentum over 5, 20 and 60 days, volatility,
   distance to the EMAs, RSI, variance ratio) is nearest to today's, in standardized distance,
   no two within ``h`` days of each other (overlapping outcomes are one observation, not two). What
   the next 1 and 5 days did after them, against what any day did, with a 99% Wilson interval.
3. **A small neural network** (``walk_forward``): one hidden layer, three seeds, on the same picture plus
   the cycle phase and the weekday, trained to say if the close will be higher in 1 and in 5 days. It is
   judged walk-forward: every prediction is made by a network trained only on days whose outcome
   was already known then, refit every ``STEP`` days. Its hit rate on those unseen days is compared
   with the best a constant guess would have scored (the more common direction) with a 99% interval.
   Only when it beats that is ``edge`` true; the live number is then the same network refitted
   on all closed days.

Cached per last closed day, so asking on every bar costs nothing after the first.
"""

from __future__ import annotations

import math
import threading
from typing import Any

import numpy as np

from addons.trading.quant import cycles, daily

WINDOWS = (128, 256, 512)
HORIZONS = (1, 5)
FIRST_TRAIN = 300           # rows before the first walk-forward prediction
STEP = 100                  # days between refits
SEEDS = 3
EPOCHS = 250
HIDDEN = 8
NEIGHBOURS = 40
Z99 = 2.58
_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
_lock = threading.Lock()


def _wilson(k: float, n: int, z: float = Z99) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


# ── 1. cycles ───────────────────────────────────────────────────────────────
def cycle_view(closes: np.ndarray, windows: tuple[int, ...] = WINDOWS) -> dict[str, Any]:
    """The cycles of the daily returns that passed both tests (corrected for looking at several windows)."""
    limit = cycles.P_LIMIT / max(1, len(windows))
    found: list[dict[str, Any]] = []
    tested = 0
    for window in windows:
        if len(closes) < window + 1:
            continue
        tested += 1
        view = cycles.dominant_cycle(closes, window=window)
        if not view.get("ok"):
            continue
        p = max(view["p_fisher"], view["p_permutation"])
        if p < limit:
            found.append({"window_days": window, "period_days": view["period_bars"], "p": round(p, 4),
                          "now": "rising" if view["direction"] == "naik" else "falling",
                          "days_to_peak": view["bars_to_peak"], "days_to_trough": view["bars_to_trough"]})
    out: dict[str, Any] = {"tested": tested, "passed": found}
    if not found:
        out["verdict"] = "no daily cycle passed the test: ignore cycles"
    elif len({f["now"] for f in found}) == 1 and len(found) > 1:
        out["verdict"] = f"{len(found)} windows agree the cycle is {found[0]['now']}"
    else:
        out["verdict"] = "a daily cycle passed the test; treat its phase as a weak lean"
    return out


# ── 2. analogs ──────────────────────────────────────────────────────────────
def _picture(ind: dict[str, np.ndarray]) -> np.ndarray:
    c, atr = ind["c"], ind["atr"]
    cols = [ind["ret5"], ind["ret20"], ind["ret60"], ind["vol_ratio"], ind["vol_rank"],
            (c - ind["ema20"]) / atr, (c - ind["ema50"]) / atr, (c - ind["ema200"]) / atr, ind["rsi14"], ind["vr5"]]
    return np.column_stack(cols)


def _forward(ind: dict[str, np.ndarray], h: int) -> np.ndarray:
    c, atr = ind["c"], ind["atr"]
    out = np.full(len(c), np.nan)
    out[:-h] = (c[h:] - c[:-h]) / atr[:-h]
    return out


def analogs(ind: dict[str, np.ndarray], last: int, *, k: int = NEIGHBOURS) -> dict[str, Any]:
    """What followed the days that looked most like day ``last``; empty when too little history."""
    x = _picture(ind)[: last + 1]
    ok = np.isfinite(x).all(axis=1)
    if not ok[last] or ok.sum() < 200:
        return {}
    mu, sd = x[ok].mean(axis=0), x[ok].std(axis=0) + 1e-9
    z = (x - mu) / sd
    out: dict[str, Any] = {}
    for h in HORIZONS:
        fwd = _forward(ind, h)[: last + 1]
        known = ok & np.isfinite(fwd)
        known[last - h + 1:] = False                  # outcome not in yet
        rows = np.flatnonzero(known)
        if len(rows) < 150:
            continue
        dist = np.sqrt(((z[rows] - z[last]) ** 2).sum(axis=1))
        chosen: list[int] = []
        for i in np.argsort(dist):                    # nearest first, no two within h days of each other
            day = int(rows[i])
            if all(abs(day - d) >= h for d in chosen):
                chosen.append(day)
            if len(chosen) >= k:
                break
        moves = fwd[chosen]
        ups = int(np.sum(moves > 0))
        base_up = float(np.mean(fwd[rows] > 0))
        lo, hi = _wilson(ups, len(chosen))
        out[f"{h}d"] = {"n": len(chosen), "up_share": round(ups / len(chosen), 3), "base_up_share": round(base_up, 3),
                        "mean_atr": round(float(moves.mean()), 2), "base_mean_atr": round(float(fwd[rows].mean()), 2),
                        "edge": bool(lo > base_up or hi < base_up)}
    return out


# ── 3. the walk-forward network ─────────────────────────────────────────────
def _features(ind: dict[str, np.ndarray], closes: np.ndarray) -> np.ndarray:
    c, atr = ind["c"], ind["atr"]
    weekday = np.array([(int(t // daily.DAY) + 3) % 7 for t in ind["t"]], dtype=float)       # 1970-01-01 was a Thursday
    cols = [ind["ret1"], ind["ret5"], ind["ret20"], ind["ret60"], ind["vol_ratio"], ind["vol_rank"],
            (c - ind["ema20"]) / atr, (c - ind["ema50"]) / atr, (c - ind["ema200"]) / atr, (c - ind["hi20"]) / atr,
            (c - ind["lo20"]) / atr, ind["prev_body"], ind["prev_upper"], ind["prev_lower"], ind["prev_range"],
            ind["rsi14"], ind["vr5"], ind["ac1"], np.sin(weekday / 7 * 2 * math.pi), np.cos(weekday / 7 * 2 * math.pi)]
    for w in (128, 256):
        cc = cycles.cycle_columns(closes, w)
        cols += [cc["cy_sin"], cc["cy_cos"], cc["cy_power"]]
    return np.column_stack(cols)


def _fit_mlp(x: np.ndarray, y: np.ndarray, seed: int) -> tuple[np.ndarray, ...]:
    """One tanh layer and a sigmoid output, Adam on the log loss with a small L2."""
    rng = np.random.default_rng(seed)
    n, d = x.shape
    w1 = rng.normal(0, 1 / math.sqrt(d), (d, HIDDEN))
    b1 = np.zeros(HIDDEN)
    w2 = rng.normal(0, 1 / math.sqrt(HIDDEN), HIDDEN)
    b2 = np.zeros(1)
    params = [w1, b1, w2, b2]
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    for step in range(1, EPOCHS + 1):
        h = np.tanh(x @ w1 + b1)
        p = 1.0 / (1.0 + np.exp(-(h @ w2 + b2[0])))
        g = (p - y) / n
        gh = np.outer(g, w2) * (1 - h * h)
        grads = [x.T @ gh + 1e-2 * w1, gh.sum(axis=0), h.T @ g + 1e-2 * w2, np.array([g.sum()])]
        for i, grad in enumerate(grads):
            m[i] = 0.9 * m[i] + 0.1 * grad
            v[i] = 0.999 * v[i] + 0.001 * grad * grad
            params[i] -= 0.02 * (m[i] / (1 - 0.9 ** step)) / (np.sqrt(v[i] / (1 - 0.999 ** step)) + 1e-8)
    return w1, b1, w2, b2


def _predict(net: tuple[np.ndarray, ...], x: np.ndarray) -> np.ndarray:
    w1, b1, w2, b2 = net
    out: np.ndarray = 1.0 / (1.0 + np.exp(-(np.tanh(x @ w1 + b1) @ w2 + b2[0])))
    return out


def _ensemble(x: np.ndarray, y: np.ndarray, at: np.ndarray) -> np.ndarray:
    mu, sd = x.mean(axis=0), x.std(axis=0) + 1e-9
    xs, ats = np.clip((x - mu) / sd, -5, 5), np.clip((at - mu) / sd, -5, 5)
    return np.mean([_predict(_fit_mlp(xs, y, seed), ats) for seed in range(SEEDS)], axis=0)


def oos_predictions(ind: dict[str, np.ndarray], last: int, h: int) -> dict[str, np.ndarray] | None:
    """Every walk-forward prediction for horizon ``h``: day index, P(up), whether it went up, and the refit it came from.

    Each prediction is made by a network trained only on days whose outcome was already known then. None when
    there is too little history for even one refit or fewer than 100 predictions.
    """
    x = _features(ind, ind["c"])[: last + 1]          # every column at day i uses days up to i only
    ok = np.isfinite(x).all(axis=1)
    if ok.sum() < FIRST_TRAIN + STEP:
        return None
    first = int(np.argmax(ok))
    fwd = _forward(ind, h)[: last + 1]
    y = (fwd > 0).astype(float)
    labelled = ok & np.isfinite(fwd)
    days: list[np.ndarray] = []
    probs: list[np.ndarray] = []
    folds: list[np.ndarray] = []
    for fold, start in enumerate(range(first + FIRST_TRAIN, last + 1 - h, STEP)):
        train = np.flatnonzero(labelled[: start - h + 1])           # an outcome is known only h days later
        stop = min(start + STEP, last + 1 - h)
        test = np.arange(start, stop)
        test = test[ok[test] & labelled[test]]
        if len(train) < FIRST_TRAIN or not len(test):
            continue
        days.append(test)
        probs.append(_ensemble(x[train], y[train], x[test]))
        folds.append(np.full(len(test), fold))
    if not days:
        return None
    idx = np.concatenate(days)
    if len(idx) < 100:
        return None
    return {"idx": idx, "p": np.concatenate(probs), "y": y[idx], "fold": np.concatenate(folds)}


def walk_forward(ind: dict[str, np.ndarray], last: int) -> dict[str, Any]:
    """Out-of-sample hit rate for each horizon, and the live P(up) from a network trained on every closed day."""
    x = _features(ind, ind["c"])[: last + 1]
    ok = np.isfinite(x).all(axis=1)
    if ok.sum() < FIRST_TRAIN + STEP:
        return {}
    out: dict[str, Any] = {}
    for h in HORIZONS:
        pred = oos_predictions(ind, last, h)
        if pred is None:
            continue
        p, actual = pred["p"], pred["y"]
        n, wins = len(p), float(np.sum((p > 0.5) == (actual > 0.5)))
        up_rate = float(np.mean(actual))
        naive = max(up_rate, 1 - up_rate)
        lo, _ = _wilson(wins, n)
        fwd = _forward(ind, h)[: last + 1]
        labelled = ok & np.isfinite(fwd)
        train = np.flatnonzero(labelled)
        y = (fwd > 0).astype(float)
        p_now = float(_ensemble(x[train], y[train], x[last: last + 1])[0]) if ok[last] and len(train) >= FIRST_TRAIN else None
        edge = bool(lo > naive)
        out[f"{h}d"] = {"oos_days": n, "oos_hit": round(wins / n, 3), "constant_guess_hit": round(naive, 3),
                        "oos_brier": round(float(np.mean((p - actual) ** 2)), 4), "brier_if_always_base": round(up_rate * (1 - up_rate), 4),
                        "edge": edge,
                        # a number that did not beat a constant guess is withheld: nobody should anchor on it
                        **({"p_up": round(p_now, 3)} if edge and p_now is not None else {})}
    return out


# ── the block for the prompt ────────────────────────────────────────────────
def forecast(daily_bars: list[dict[str, float]], last: int, ind: dict[str, np.ndarray], key: Any = None) -> dict[str, Any]:
    """Cycles, analogs and the network for the day after day ``last``; cached per last closed day."""
    stamp = (key, last, float(ind["t"][last]))
    with _lock:
        cached = _cache.get(stamp)
    if cached is not None:
        return cached
    closes = ind["c"][: last + 1]
    # the three fixed windows and one over the whole history (every closed day), each held to the corrected limit
    windows = WINDOWS + ((len(closes) - 1,) if len(closes) - 1 > WINDOWS[-1] else ())
    out: dict[str, Any] = {"cycle": cycle_view(closes, windows)}
    for name, build in (("analogs", lambda: analogs(ind, last)), ("network", lambda: walk_forward(ind, last))):
        try:
            out[name] = build()
        except Exception as exc:  # noqa: BLE001 - one reader failing leaves the others
            out[name] = {"error": f"{type(exc).__name__}"}
    passed = []
    if out["cycle"]["passed"]:
        passed.append("cycle")
    if any(v.get("edge") for v in (out.get("analogs") or {}).values() if isinstance(v, dict)):
        passed.append("analogs")
    if any(v.get("edge") for v in (out.get("network") or {}).values() if isinstance(v, dict)):
        passed.append("network")
    out["verdict"] = (f"passed their tests: {', '.join(passed)}; the others are noise" if passed
                      else "none of the three passed its test: the daily history gives context, not a direction")
    with _lock:
        _cache[stamp] = out
        if len(_cache) > 8:
            _cache.pop(next(iter(_cache)))
    return out


__all__ = ["cycle_view", "analogs", "oos_predictions", "walk_forward", "forecast", "WINDOWS", "HORIZONS"]
