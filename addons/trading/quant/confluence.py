"""Many measurements, one probability: how likely the price is higher after the hold than now.

Fourteen measurements are taken on every bar, each from that bar and the ones before it only:

* momentum over 3, 10 and 30 bars, divided by the volatility of that many bars (a z-score),
* EMA 9/21 gap and Kalman slope, both in ATR,
* RSI, Bollinger z, distance to VWAP, and where the last closes sat inside their bars,
* the regime: Hurst exponent (above 0.5 trends, below reverts), variance ratio, lag-1
  autocorrelation and sign entropy, each multiplied by the recent move so it says which way,
* the short/long ATR ratio times the short move: a breakout in fresh volatility.

A ridge logistic regression learns, on the bars already finished, how much each measurement
was worth for the move ``horizon`` bars later, and turns today's measurements into P(up).
It is graded walk-forward: the labelled bars are cut into ``FOLDS`` consecutive blocks, and each
block after the first is predicted by weights fitted on the blocks before it, with a gap of
``horizon`` bars between them so a label that reaches into the test block never teaches the
fit (purging). Every answer carries that out-of-sample record. Rows closer to now weigh more
in the fit (an exponential half-life), and the ridge strength is the one of ``RIDGES`` that
predicted the held-out blocks best. When the out-of-sample hit rate does not beat the share of
the most common outcome (always answering "up" in a rising market), or the log loss does not
beat the base rate, the side is 0: the measurements did not say more than a guess. More
measurements do not make a direction clearer by themselves; the grade on unseen bars is what
says whether they did.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from addons.trading.quant import features as F

NAMES = (
    "mom_3", "mom_10", "mom_30", "ema_gap", "kalman_slope", "rsi", "bollinger_z", "vwap_gap", "close_place",
    "hurst_x_move", "variance_ratio_x_move", "autocorr_x_last", "order_x_move", "vol_burst",
)
LABELS = {
    "mom_3": "Momentum 3 bar", "mom_10": "Momentum 10 bar", "mom_30": "Momentum 30 bar", "ema_gap": "Jarak EMA 9/21",
    "kalman_slope": "Kemiringan Kalman", "rsi": "RSI", "bollinger_z": "Z Bollinger", "vwap_gap": "Jarak VWAP",
    "close_place": "Posisi close di bar", "hurst_x_move": "Hurst × arah", "variance_ratio_x_move": "Variance ratio × arah",
    "autocorr_x_last": "Autokorelasi × bar terakhir", "order_x_move": "Keteraturan (1−entropi) × arah",
    "vol_burst": "Lonjakan volatilitas × arah",
}
WARMUP = 60            # bars before the first usable row
MIN_SAMPLES = 120      # labelled rows needed to fit and grade
MAX_SAMPLES = 1500     # most recent rows used, so the weights follow the current market
RIDGE = 1.0
RIDGES = (0.3, 1.0, 3.0, 10.0)   # ridge strengths tried; the one that predicted held-out blocks best is kept
FOLDS = 4              # walk-forward blocks: each is predicted from the blocks before it
HALF_LIFE_SHARE = 0.5  # the row this share of the sample back weighs half as much in the fit
MIN_EDGE = 0.05        # |P(up) - 0.5| below this is no side
REGIME_WINDOW = 64


def _ema(x: np.ndarray, period: int) -> np.ndarray:
    out = np.empty_like(x)
    k = 2 / (period + 1)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = out[i - 1] + k * (x[i] - out[i - 1])
    return out


def _rsi(c: np.ndarray, period: int = 14) -> np.ndarray:
    diff = np.diff(c, prepend=c[0])
    gain, loss = np.clip(diff, 0, None), np.clip(-diff, 0, None)
    avg_gain, avg_loss = _ema(gain, 2 * period - 1), _ema(loss, 2 * period - 1)   # Wilder smoothing
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss > 0, avg_gain / avg_loss, np.inf)
    return 100 - 100 / (1 + rs)


def _rolling(x: np.ndarray, window: int, fn: Any) -> np.ndarray:
    out = np.full(len(x), np.nan)
    for i in range(window, len(x) + 1):
        out[i - 1] = fn(x[i - window:i])
    return out


def hurst(returns: np.ndarray) -> float:
    """Rescaled-range Hurst exponent over a window of returns: 0.5 random, above trending, below reverting."""
    n = len(returns)
    if n < 16 or not np.any(returns):
        return 0.5
    sizes, rs = [], []
    for size in (8, 16, 32, 64):
        if size > n:
            break
        chunks = returns[n - (n // size) * size:].reshape(-1, size)
        dev = np.cumsum(chunks - chunks.mean(axis=1, keepdims=True), axis=1)
        spread = dev.max(axis=1) - dev.min(axis=1)
        std = chunks.std(axis=1)
        ok = std > 0
        if ok.any():
            sizes.append(math.log(size))
            rs.append(math.log(float(np.mean(spread[ok] / std[ok]))))
    if len(sizes) < 2:
        return 0.5
    return float(np.clip(np.polyfit(sizes, rs, 1)[0], 0.0, 1.0))


def variance_ratio(returns: np.ndarray, q: int = 4) -> float:
    """Lo-MacKinlay VR(q) - 1: above 0 moves keep going, below 0 they reverse."""
    if len(returns) < q * 4:
        return 0.0
    var1 = returns.var()
    if var1 <= 0:
        return 0.0
    summed = np.convolve(returns, np.ones(q), mode="valid")
    return float(summed.var() / (q * var1) - 1)


def autocorr(returns: np.ndarray) -> float:
    if len(returns) < 8 or returns[:-1].std() == 0 or returns[1:].std() == 0:
        return 0.0
    return float(np.corrcoef(returns[:-1], returns[1:])[0, 1])


def sign_entropy(returns: np.ndarray) -> float:
    """Shannon entropy of up/down bars, 0 (all one way) to 1 (a fair coin)."""
    moves = returns[returns != 0]
    if len(moves) == 0:
        return 1.0
    p = float(np.mean(moves > 0))
    if p in (0.0, 1.0):
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def matrix(bars: list[dict[str, float]]) -> np.ndarray:
    """One row of measurements per bar (NaN until there is enough history), columns as NAMES."""
    n = len(bars)
    c = np.array([b["c"] for b in bars], dtype=float)
    h = np.array([b["h"] for b in bars], dtype=float)
    lo = np.array([b["l"] for b in bars], dtype=float)
    r = np.diff(c, prepend=c[0])
    sigma = np.maximum(_rolling(r, 30, np.std), 1e-12)
    atr = np.array(F.wilder_atr(bars), dtype=float)
    atr = np.where(np.isfinite(atr) & (atr > 0), atr, np.nan)
    atr_long = _rolling(np.nan_to_num(atr), 60, np.mean)
    ema_fast, ema_slow = _ema(c, 9), _ema(c, 21)
    _, slope, _, _ = F.kalman(c.tolist())
    mean20, std20 = _rolling(c, 20, np.mean), _rolling(c, 20, np.std)
    vwap = np.array(F.vwap_daily(bars), dtype=float)
    width = h - lo
    place = np.where(width > 0, ((c - lo) - (h - c)) / np.where(width > 0, width, 1), 0.0)
    place3 = np.convolve(place, np.ones(3) / 3, mode="full")[:n]

    def move(k: int) -> np.ndarray:
        out = np.full(n, np.nan)
        out[k:] = (c[k:] - c[:-k]) / (sigma[k:] * math.sqrt(k))
        return out

    m3, m10, m30 = move(3), move(10), move(30)
    direction = np.sign(np.nan_to_num(m10))
    hu, vr, ac, ent = (np.full(n, np.nan) for _ in range(4))
    for i in range(REGIME_WINDOW, n):
        window = r[i - REGIME_WINDOW + 1:i + 1]
        hu[i], vr[i], ac[i], ent[i] = hurst(window), variance_ratio(window), autocorr(window[-32:]), sign_entropy(window[-20:])
    with np.errstate(divide="ignore", invalid="ignore"):
        cols = [
            m3, m10, m30,
            (ema_fast - ema_slow) / atr,
            np.array(slope) / atr,
            (_rsi(c) - 50) / 50,
            np.where(std20 > 0, (c - mean20) / std20, 0.0),
            (c - vwap) / atr,
            place3,
            (hu - 0.5) * 2 * direction,
            vr * direction,
            ac * r / sigma,
            (1 - ent) * direction,
            (atr / atr_long - 1) * np.nan_to_num(m3),
        ]
    out = np.column_stack(cols)
    out[:WARMUP] = np.nan
    out[~np.isfinite(out)] = np.nan
    clipped: np.ndarray = np.clip(out, -6, 6)
    return clipped


def _fit(x: np.ndarray, y: np.ndarray, ridge: float = RIDGE, steps: int = 25, weights: np.ndarray | None = None) -> np.ndarray:
    """Weighted ridge logistic regression by Newton steps. x already has its bias column first."""
    w = np.zeros(x.shape[1])
    sw = np.ones(len(x)) if weights is None else weights
    penalty = np.eye(x.shape[1]) * ridge
    penalty[0, 0] = 0.0                         # the bias is not shrunk
    for _ in range(steps):
        p = 1 / (1 + np.exp(-np.clip(x @ w, -30, 30)))
        grad = x.T @ (sw * (p - y)) + penalty @ w
        hess = (x * (sw * p * (1 - p))[:, None]).T @ x + penalty
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            break
        w -= step
        if np.max(np.abs(step)) < 1e-6:
            break
    return w


def _sigmoid(z: np.ndarray) -> np.ndarray:
    out: np.ndarray = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
    return out


def recency(n: int, half_life_share: float = HALF_LIFE_SHARE) -> np.ndarray:
    """Row weights with mean 1 that halve every ``half_life_share`` of the sample going back in time."""
    if n <= 0:
        return np.ones(0)
    age = np.arange(n, dtype=float)[::-1]
    w = 0.5 ** (age / max(1.0, half_life_share * n))
    out: np.ndarray = w / w.mean()
    return out


def walk_forward(x: np.ndarray, y: np.ndarray, *, gap: int, folds: int = FOLDS, ridges: tuple[float, ...] = RIDGES
                 ) -> tuple[float, np.ndarray, np.ndarray]:
    """Out-of-sample predictions for every block after the first, from weights fitted on the blocks before it.

    Returns the ridge that predicted best (lowest log loss), the predictions, and the rows they belong to.
    ``gap`` rows before each test block are left out of its fit, because their labels reach into it.
    """
    n = len(y)
    cuts = [int(n * k / folds) for k in range(folds + 1)]
    best: tuple[float, float, np.ndarray, np.ndarray] | None = None
    for ridge in ridges:
        preds, rows = [], []
        for k in range(1, folds):
            lo, hi = cuts[k], cuts[k + 1]
            fit_end = max(1, lo - gap)
            w = _fit(x[:fit_end], y[:fit_end], ridge, weights=recency(fit_end))
            preds.append(_sigmoid(x[lo:hi] @ w))
            rows.append(np.arange(lo, hi))
        p = np.concatenate(preds)
        idx = np.concatenate(rows)
        eps = 1e-9
        loss = float(-np.mean(y[idx] * np.log(p + eps) + (1 - y[idx]) * np.log(1 - p + eps)))
        if best is None or loss < best[0]:
            best = (loss, ridge, p, idx)
    assert best is not None
    return best[1], best[2], best[3]


def analyse(bars: list[dict[str, float]], horizon: int = 1, *, min_edge: float = MIN_EDGE) -> dict[str, Any]:
    """P(up) for the move ``horizon`` bars after the last bar, its grade on unseen bars, and why."""
    horizon = max(1, int(horizon))
    if len(bars) < WARMUP + MIN_SAMPLES + horizon:
        return {"ok": False, "side": 0, "reason": f"butuh {WARMUP + MIN_SAMPLES + horizon} bar, ada {len(bars)}"}
    feats = matrix(bars)
    closes = np.array([b["c"] for b in bars], dtype=float)
    future = np.full(len(bars), np.nan)
    future[:-horizon] = closes[horizon:] - closes[:-horizon]
    usable = np.all(np.isfinite(feats), axis=1) & np.isfinite(future) & (future != 0)
    idx = np.flatnonzero(usable)[-MAX_SAMPLES:]
    if len(idx) < MIN_SAMPLES or not np.all(np.isfinite(feats[-1])):
        return {"ok": False, "side": 0, "reason": f"baru {len(idx)} bar berlabel, butuh {MIN_SAMPLES}"}
    x_all, y_all = feats[idx], (future[idx] > 0).astype(float)
    mu, sd = x_all.mean(axis=0), x_all.std(axis=0)
    sd[sd == 0] = 1.0

    def design(x: np.ndarray) -> np.ndarray:
        return np.column_stack([np.ones(len(x)), (x - mu) / sd])

    x_design = design(x_all)
    ridge, p_test, test_rows = walk_forward(x_design, y_all, gap=horizon)
    y_test = y_all[test_rows]
    split = int(test_rows[0])
    calls = np.abs(p_test - 0.5) >= min_edge
    hits = ((p_test > 0.5) == (y_test > 0.5))
    accuracy = float(hits.mean())
    called_accuracy = float(hits[calls].mean()) if calls.any() else None
    base = float(max(y_test.mean(), 1 - y_test.mean()))
    eps = 1e-9
    log_loss = float(-np.mean(y_test * np.log(p_test + eps) + (1 - y_test) * np.log(1 - p_test + eps)))
    # the base rate each block would have known: the share of ups in the rows before it
    prior = np.array([np.clip(y_all[:max(1, r - horizon)].mean(), eps, 1 - eps) for r in test_rows])
    base_loss = float(-np.mean(y_test * np.log(prior) + (1 - y_test) * np.log(1 - prior)))
    brier = float(np.mean((p_test - y_test) ** 2))

    weights = _fit(x_design, y_all, ridge, weights=recency(len(y_all)))   # refit on every finished bar for today's answer
    z_now = (feats[-1] - mu) / sd
    logit = float(weights[0] + z_now @ weights[1:])
    p_up = 1 / (1 + math.exp(-max(-30.0, min(30.0, logit))))
    graded = accuracy > base and log_loss < base_loss
    side = 0
    if graded and abs(p_up - 0.5) >= min_edge:
        side = 1 if p_up > 0.5 else -1
    pushes = [float(weights[k + 1] * z_now[k]) for k in range(len(NAMES))]
    contributions = [{"name": n, "label": LABELS[n], "value": round(float(feats[-1][k]), 4),
                      "weight": round(float(weights[k + 1]), 4), "push": round(pushes[k], 4)}
                     for k, n in sorted(enumerate(NAMES), key=lambda kn: -abs(pushes[kn[0]]))]
    last = np.diff(closes[-REGIME_WINDOW - 1:])
    return {
        "ok": True, "side": side, "p_up": round(p_up, 4), "clarity": round(abs(2 * p_up - 1), 4),
        "horizon_bars": horizon, "train_samples": int(split), "test_samples": int(len(idx) - split),
        "test_accuracy": round(accuracy, 4), "test_accuracy_when_called": round(called_accuracy, 4) if called_accuracy is not None else None,
        "test_calls": int(calls.sum()), "base_rate": round(base, 4),
        "test_log_loss": round(log_loss, 4), "base_log_loss": round(base_loss, 4), "graded": graded,
        "test_brier": round(brier, 4), "ridge": ridge, "folds": FOLDS, "purge_bars": horizon,
        "regime": {"hurst": round(hurst(last), 3), "variance_ratio": round(variance_ratio(last), 3),
                   "autocorr": round(autocorr(last[-32:]), 3), "entropy": round(sign_entropy(last[-20:]), 3)},
        "contributions": contributions,
        "reason": ("lulus uji: lebih baik dari tebakan mayoritas di bar yang belum dilihat" if graded
                   else f"tidak lulus uji: akurasi {accuracy:.0%} vs tebakan mayoritas {base:.0%}, jadi tidak ambil arah"),
    }


__all__ = ["analyse", "matrix", "hurst", "variance_ratio", "autocorr", "sign_entropy", "walk_forward", "recency", "NAMES", "LABELS"]
