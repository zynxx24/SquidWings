"""The regime group (``rg_``) of the brain's memory: the M5 quant model's math, measured on every bar.

Before this group the neural brain never saw what ``model`` (the Kalman-HMM trend-pullback rule)
and ``features`` compute: trend strength, the hidden market state, the Kalman surprise and the slow
trend. Each column is known at the bar's close and uses nothing after it.

* ``rg_adx``, ``rg_adx_slope``: Wilder's ADX (trend strength, centred on 25) and its change over three
  bars, so a fading trend reads differently from a growing one.
* ``rg_di``: +DI minus -DI, the direction of that strength.
* ``rg_kalman_z``, ``rg_kalman_zmin``, ``rg_kalman_zmax``: the Kalman filter's surprise on this bar
  (innovation over its standard deviation) and its extremes over the three bars before: the
  pullback depth ``model.decide`` checks.
* ``rg_vwap_gap``: distance to the day's VWAP in ATR (signed; confluence's ``vwap_gap`` is the same idea
  on its own scaling and stays there too).
* ``rg_htf``: the slow trend (``features.htf_bias``) with its EMA scaled to about 20 hours at any timeframe.
* ``rg_p_trend``, ``rg_p_range``, ``rg_p_highvol``, ``rg_trend_dir``: the three-state Gaussian HMM's
  causal posterior (forward filter) on ``model``'s regime features [|slope|/ATR, ADX/100,
  |C - VWAP|/ATR, ATR ratio], and P(trend) signed by the Kalman slope.
* ``rg_rule_buy``, ``rg_rule_sell``, ``rg_setup``: the quant model's 0..100 score for each side (over 100),
  and +1/-1 when its full entry rule fires (P(trend) > 0.65, slope, VWAP side, pullback, breakout).

The HMM is fitted only on the oldest ``FIT_SHARE`` of the bars (at most ``FIT_ROWS``), which lie
inside the training rows of ``brain.train``: the validation and exam rows never shape it. The fit
is cached on its window, so reading the newest bar does not refit it.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import numpy as np

from addons.trading.quant import features as F
from addons.trading.quant import model as quant_model
from addons.trading.quant.hmm import GaussianHMM, label_states

FIT_SHARE = 0.5          # the HMM learns from the oldest half of the bars, inside the brain's training rows
FIT_ROWS = 2_500         # at most this many bars (the newest of that half), so a fit stays a second or two
FIT_ITERS = 20
MIN_FIT_ROWS = 150        # fewer known bars in the oldest half: no HMM yet, its columns stay neutral (0)
HTF_HOURS = 20.0         # the slow trend's EMA spans about 20 hours, the H1 EMA 20 of ``features``
REGIME_COLUMNS = ("rg_adx", "rg_adx_slope", "rg_di", "rg_kalman_z", "rg_kalman_zmin", "rg_kalman_zmax", "rg_vwap_gap",
                  "rg_htf", "rg_p_trend", "rg_p_range", "rg_p_highvol", "rg_trend_dir", "rg_rule_buy", "rg_rule_sell",
                  "rg_setup")


def _directional(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int = 14) -> tuple[np.ndarray, np.ndarray]:
    """Wilder's +DI and -DI (0..100). NaN until ``n`` bars are known."""
    size = len(c)
    pdi, mdi = np.full(size, np.nan), np.full(size, np.nan)
    if size <= n + 1:
        return pdi, mdi
    up = np.diff(h, prepend=h[0])
    dn = -np.diff(lo, prepend=lo[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    prev_c = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum.reduce([h - lo, np.abs(h - prev_c), np.abs(lo - prev_c)])
    tr_s, p_s, m_s = tr[1:n + 1].sum(), pdm[1:n + 1].sum(), mdm[1:n + 1].sum()
    for i in range(n, size):
        if i > n:
            tr_s += tr[i] - tr_s / n
            p_s += pdm[i] - p_s / n
            m_s += mdm[i] - m_s / n
        if tr_s > 0:
            pdi[i], mdi[i] = 100 * p_s / tr_s, 100 * m_s / tr_s
    return pdi, mdi


def _atr_ratio(atr: np.ndarray, window: int = 100) -> np.ndarray:
    """ATR over its median of the last ``window`` bars (``features.compute``'s ratio), 1 until known."""
    out = np.ones(len(atr))
    clean = np.where(np.isfinite(atr), atr, np.nan)
    if len(atr) < 20:
        return out
    pad = np.concatenate((np.full(window - 1, np.nan), clean))
    views = np.lib.stride_tricks.sliding_window_view(pad, window)
    with np.errstate(all="ignore"):
        med = np.nanmedian(views, axis=1) if len(views) else np.full(len(atr), np.nan)
    counts = np.isfinite(views).sum(axis=1)
    ok = (counts >= 20) & np.isfinite(clean) & (med > 0)
    out[ok] = clean[ok] / med[ok]
    return out


@lru_cache(maxsize=16)
def _fit_cached(key: tuple[Any, ...], data: bytes, shape: tuple[int, int]) -> tuple[Any, ...]:
    """Fit the HMM once per window. ``key`` names the window; ``data`` is its rows."""
    rows = np.frombuffer(data, dtype=float).reshape(shape).tolist()
    hmm = GaussianHMM(3).fit(rows, iters=FIT_ITERS)
    states = label_states(hmm, trend_idx=0, adx_idx=1, atr_idx=3)
    return (np.array(hmm.pi), np.array(hmm.A), np.array(hmm.mu), np.array(hmm.var), states["trend"], states["range"],
            states["highvol"])


def _forward(u: np.ndarray, pi: np.ndarray, a: np.ndarray, mu: np.ndarray, var: np.ndarray) -> np.ndarray:
    """P(state_t | u_0..t) for every row, the causal filter of ``hmm.GaussianHMM.filter`` in numpy."""
    log_b = -0.5 * (np.log(2 * math.pi * var)[None, :, :] + (u[:, None, :] - mu[None, :, :]) ** 2 / var[None, :, :]).sum(axis=2)
    log_b -= log_b.max(axis=1, keepdims=True)
    b = np.exp(log_b)
    out: np.ndarray = np.empty_like(b)
    alpha = pi * b[0]
    alpha /= alpha.sum() or 1.0
    out[0] = alpha
    for t in range(1, len(b)):
        alpha = (alpha @ a) * b[t]
        total = alpha.sum()
        alpha = alpha / total if total > 0 else np.full(len(pi), 1.0 / len(pi))
        out[t] = alpha
    return out


def regime_columns(bars: list[dict[str, float]], atr: np.ndarray, seconds: int) -> dict[str, np.ndarray]:
    """The ``rg_`` columns for ``bars`` (one timeframe, oldest first). NaN where not known yet."""
    n = len(bars)
    out = {name: np.full(n, np.nan) for name in REGIME_COLUMNS}
    if n < 40:
        return out
    h = np.array([b["h"] for b in bars], dtype=float)
    lo = np.array([b["l"] for b in bars], dtype=float)
    c = np.array([b["c"] for b in bars], dtype=float)
    a = np.where(np.isfinite(atr) & (atr > 0), atr, np.nan)

    adx = np.array(F.adx(bars), dtype=float)
    pdi, mdi = _directional(h, lo, c)
    _, slope_l, _, z_l = F.kalman(c.tolist())
    slope, z = np.array(slope_l, dtype=float), np.array(z_l, dtype=float)
    vwap = np.array(F.vwap_daily(bars), dtype=float)
    period = max(8, int(round(HTF_HOURS * 3600 / seconds)))
    htf = np.array(F.htf_bias(c.tolist(), period=period, slope_bars=max(1, int(round(3600 / seconds)))), dtype=float)
    ratio = _atr_ratio(a)

    out["rg_adx"] = (adx - 25) / 25
    adx_prev = np.concatenate((np.full(3, np.nan), adx[:-3]))
    out["rg_adx_slope"] = (adx - adx_prev) / 10
    out["rg_di"] = (pdi - mdi) / 50
    out["rg_kalman_z"] = z
    prev = np.lib.stride_tricks.sliding_window_view(np.concatenate((np.full(3, np.nan), z[:-1])), 3)
    with np.errstate(all="ignore"):
        out["rg_kalman_zmin"] = np.nanmin(np.where(np.isfinite(prev), prev, np.inf), axis=1)
        out["rg_kalman_zmax"] = np.nanmax(np.where(np.isfinite(prev), prev, -np.inf), axis=1)
        out["rg_vwap_gap"] = (c - vwap) / a
    out["rg_htf"] = htf

    # the hidden state: fitted on the oldest bars only, read causally on every bar
    with np.errstate(all="ignore"):
        u = np.column_stack([np.abs(slope) / a, adx / 100, np.abs(c - vwap) / a, ratio])
    known = np.all(np.isfinite(u), axis=1)
    p_trend = np.full(n, np.nan)
    first = int(np.argmax(known)) if known.any() else n
    fit_end = int(n * FIT_SHARE)
    window = np.flatnonzero(known[:fit_end])[-FIT_ROWS:]
    if len(window) >= MIN_FIT_ROWS and first < n:
        rows = np.ascontiguousarray(u[window])
        key = (bars[int(window[0])]["t"], bars[int(window[-1])]["t"], len(window), seconds)
        pi, trans, mu, var, i_trend, i_range, i_high = _fit_cached(key, rows.tobytes(), rows.shape)
        stream = np.where(known[:, None], u, mu.mean(axis=0)[None, :])[first:]
        post = _forward(np.clip(stream, -50, 50), pi, trans, mu, var)
        p_trend[first:] = post[:, i_trend]
        out["rg_p_trend"][first:] = post[:, i_trend]
        out["rg_p_range"][first:] = post[:, i_range]
        out["rg_p_highvol"][first:] = post[:, i_high]
        out["rg_trend_dir"] = p_trend * np.sign(slope)

    # the quant model's own verdict: its score per side and its full entry rule
    pt = np.nan_to_num(p_trend, nan=0.0)
    depth_buy = -np.nan_to_num(out["rg_kalman_zmin"], nan=0.0, posinf=0.0)
    depth_sell = np.nan_to_num(out["rg_kalman_zmax"], nan=0.0, neginf=0.0)
    for i in range(n):
        if not np.isfinite(a[i]):
            continue
        out["rg_rule_buy"][i] = quant_model._score(pt[i], adx[i], int(htf[i]), 1, None, depth_buy[i]) / 100
        out["rg_rule_sell"][i] = quant_model._score(pt[i], adx[i], int(htf[i]), -1, None, depth_sell[i]) / 100
    prev_h = np.concatenate(([np.nan], h[:-1]))
    prev_l = np.concatenate(([np.nan], lo[:-1]))
    with np.errstate(invalid="ignore"):
        buy = ((pt > quant_model.TREND_MIN) & (slope > 0) & (c > vwap) & (out["rg_kalman_zmin"] <= quant_model.Z_LOW)
               & (z >= quant_model.Z_BACK) & (c > prev_h))
        sell = ((pt > quant_model.TREND_MIN) & (slope < 0) & (c < vwap) & (out["rg_kalman_zmax"] >= quant_model.Z_HIGH)
                & (z <= quant_model.Z_BACK_SELL) & (c < prev_l))
    out["rg_setup"] = np.where(np.isfinite(a), buy.astype(float) - sell.astype(float), np.nan)
    for name, col in out.items():
        col[~np.isfinite(col)] = np.nan
        out[name] = col
    return out


def snapshot(names: list[str], row: np.ndarray) -> dict[str, Any]:
    """The regime on one memory row, in words and numbers, for the panel and the LLM."""
    get = {name: float(row[i]) for i, name in enumerate(names) if name.startswith("rg_") and math.isfinite(float(row[i]))}
    if "rg_adx" not in get:
        return {}
    states = {"trend": get.get("rg_p_trend", 0.0), "range": get.get("rg_p_range", 0.0), "highvol": get.get("rg_p_highvol", 0.0)}
    if sum(states.values()) < 0.5:         # the memory read "not known" as 0: the HMM had too few bars to learn
        state, label = "unknown", "belum dikenali (HMM butuh lebih banyak bar)"
    else:
        state = max(states, key=lambda k: states[k])
        label = {"trend": "tren", "range": "sideways", "highvol": "volatil tinggi"}[state]
    direction = get.get("rg_trend_dir", 0.0)
    setup = get.get("rg_setup", 0.0)
    return {
        "state": state, "label": label, "p": {k: round(v, 3) for k, v in states.items()},
        "trend_direction": "naik" if direction > 0.2 else "turun" if direction < -0.2 else "datar",
        "adx": round(get.get("rg_adx", 0.0) * 25 + 25, 1), "di_gap": round(get.get("rg_di", 0.0) * 50, 1),
        "kalman_z": round(get.get("rg_kalman_z", 0.0), 2), "htf_bias": int(get.get("rg_htf", 0.0)),
        "quant_rule": {"buy_score": round(get.get("rg_rule_buy", 0.0) * 100, 1),
                       "sell_score": round(get.get("rg_rule_sell", 0.0) * 100, 1),
                       "setup": "buy" if setup > 0 else "sell" if setup < 0 else "none"},
    }


__all__ = ["regime_columns", "snapshot", "REGIME_COLUMNS"]
