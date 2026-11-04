"""Causal features for the M5 model. Every value at bar t uses bars 0..t only.

* ATR (Wilder, 14) and its ratio to the median ATR of the last 100 bars.
* ADX (Wilder, 14): trend strength, 0..100.
* VWAP: cumulative typical price times volume, reset at each UTC day. Volume is the
  broker's tick volume (MT5 forex data), so this is a quote-activity VWAP, not a
  traded-volume VWAP.
* Kalman filter on price with a local-linear-trend state [level m, slope b] (F=[[1,1],[0,1]], H=[1,0]).
  Gives the fair-price estimate, the slope, the innovation e_t and its variance S_t,
  and the z-score Z_t = e_t / sqrt(S_t).
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass


@dataclass
class Features:
    atr: list[float]
    atr_ratio: list[float]
    adx: list[float]
    vwap: list[float]
    level: list[float]      # Kalman fair price m_t
    slope: list[float]      # Kalman slope b_t
    innov: list[float]      # e_t = P_t - H x^-
    z: list[float]          # Z_t = e_t / sqrt(S_t)


def _nan_list(n: int) -> list[float]:
    return [float("nan")] * n


def wilder_atr(bars: list[dict[str, float]], n: int = 14) -> list[float]:
    trs, prev = [], None
    for b in bars:
        tr = b["h"] - b["l"] if prev is None else max(b["h"] - b["l"], abs(b["h"] - prev), abs(b["l"] - prev))
        trs.append(tr)
        prev = b["c"]
    out = _nan_list(len(bars))
    if len(bars) < n:
        return out
    out[n - 1] = sum(trs[:n]) / n
    for i in range(n, len(bars)):
        out[i] = (out[i - 1] * (n - 1) + trs[i]) / n
    return out


def adx(bars: list[dict[str, float]], n: int = 14) -> list[float]:
    out = _nan_list(len(bars))
    if len(bars) <= 2 * n:
        return out
    tr_s = pdm_s = mdm_s = 0.0
    dxs: list[float] = []
    for i in range(1, len(bars)):
        up = bars[i]["h"] - bars[i - 1]["h"]
        dn = bars[i - 1]["l"] - bars[i]["l"]
        pdm = up if up > dn and up > 0 else 0.0
        mdm = dn if dn > up and dn > 0 else 0.0
        tr = max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - bars[i - 1]["c"]), abs(bars[i]["l"] - bars[i - 1]["c"]))
        if i <= n:
            tr_s += tr
            pdm_s += pdm
            mdm_s += mdm
            if i == n:
                dx = _dx(pdm_s, mdm_s, tr_s)
                dxs.append(dx)
            continue
        tr_s = tr_s - tr_s / n + tr
        pdm_s = pdm_s - pdm_s / n + pdm
        mdm_s = mdm_s - mdm_s / n + mdm
        dx = _dx(pdm_s, mdm_s, tr_s)
        if len(dxs) < n:
            dxs.append(dx)
            if len(dxs) == n:
                out[i] = sum(dxs) / n
        else:
            out[i] = (out[i - 1] * (n - 1) + dx) / n
    return out


def _dx(pdm: float, mdm: float, tr: float) -> float:
    if tr <= 0:
        return 0.0
    pdi, mdi = 100 * pdm / tr, 100 * mdm / tr
    s = pdi + mdi
    return 0.0 if s == 0 else 100 * abs(pdi - mdi) / s


def vwap_daily(bars: list[dict[str, float]]) -> list[float]:
    out: list[float] = []
    day, pv_sum, v_sum = None, 0.0, 0.0
    for b in bars:
        d = dt.datetime.fromtimestamp(b["t"], dt.UTC).date()
        if d != day:
            day, pv_sum, v_sum = d, 0.0, 0.0
        tp = (b["h"] + b["l"] + b["c"]) / 3
        v = float(b.get("v", 0)) or 1.0
        pv_sum += tp * v
        v_sum += v
        out.append(pv_sum / v_sum)
    return out


def kalman(closes: list[float], *, q_level: float = 1e-4, q_slope: float = 1e-6, r_scale: float = 1.0,
           warmup: int = 100) -> tuple[list[float], list[float], list[float], list[float]]:
    """Local-linear-trend Kalman filter. Noise is scaled by the return volatility of the warm-up window.

    Returns (level, slope, innovation, z). Only the past is used at each step.
    """
    n = len(closes)
    if n == 0:
        return [], [], [], []
    rets = [closes[i] - closes[i - 1] for i in range(1, min(n, warmup + 1))]
    sigma = statistics.pstdev(rets) if len(rets) > 1 else 1e-4
    sigma = sigma or 1e-6
    R = (r_scale * sigma) ** 2
    Q = [[q_level * sigma ** 2, 0.0], [0.0, q_slope * sigma ** 2]]
    m, b = closes[0], 0.0
    P = [[1.0, 0.0], [0.0, 1.0]]
    levels, slopes, innov, zs = [], [], [0.0], [0.0]
    levels.append(m)
    slopes.append(b)
    for t in range(1, n):
        # predict: x^- = F x, P^- = F P F^T + Q, with F = [[1,1],[0,1]]
        m_p, b_p = m + b, b
        P00 = P[0][0] + P[0][1] + P[1][0] + P[1][1] + Q[0][0]
        P01 = P[0][1] + P[1][1]
        P10 = P[1][0] + P[1][1]
        P11 = P[1][1] + Q[1][1]
        # innovation and its variance (H = [1, 0])
        e = closes[t] - m_p
        S = P00 + R
        K0, K1 = P00 / S, P10 / S
        # update
        m = m_p + K0 * e
        b = b_p + K1 * e
        P = [[(1 - K0) * P00, (1 - K0) * P01], [P10 - K1 * P00, P11 - K1 * P01]]
        levels.append(m)
        slopes.append(b)
        innov.append(e)
        zs.append(e / math.sqrt(S) if S > 0 else 0.0)
    return levels, slopes, innov, zs


def compute(bars: list[dict[str, float]]) -> Features:
    closes = [b["c"] for b in bars]
    a = wilder_atr(bars)
    ratio = _nan_list(len(bars))
    for i in range(len(bars)):
        window = [x for x in a[max(0, i - 99): i + 1] if x == x]
        if a[i] == a[i] and len(window) >= 20:
            med = statistics.median(window)
            ratio[i] = a[i] / med if med > 0 else float("nan")
    lvl, slp, inn, z = kalman(closes)
    return Features(atr=a, atr_ratio=ratio, adx=adx(bars), vwap=vwap_daily(bars),
                    level=lvl, slope=slp, innov=inn, z=z)
