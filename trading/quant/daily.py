"""Daily candles (D1) as the brain's long memory.

The EA's M5 history is months long; an MT4 History Center export of D1 is years long. A daily bar
cannot say what happened inside the day, but it says a lot about the day the market is in: how
strong the trend is, how wide a normal day is, which regime it is in, and how much of today's range
has been used. Two uses, both strictly causal (a day counts only after it has closed):

* ``feature_columns``: the ``dl_`` group of the brain's memory. ``brain.choose_groups`` keeps it only
  when it predicts the validation rows better, so a bad group costs nothing.
* ``brief``: a short block of numbers for the analyst's prompt. Every statistic carries its sample size
  and whether it passed a test, because a daily pattern that did not is noise and the prompt says so.

The math, all on closed days:

* Wilder ATR(14) as the unit, so a column reads the same in 2019 at 1300 and now at 4000.
* returns over 1, 5, 20 and 60 days in ATR (divided by sqrt of the span, as the intraday ``ret_`` are);
  EMA 20, 50 and 200 distances and slope; the 20 and 55-day Donchian channel; RSI 14.
* volatility: the ATR against its 250-day median, and its rank among all closed days.
* a rolling 120-day variance ratio VR(5) = Var(5-day return) / (5 Var(1-day return)) and the lag-1
  autocorrelation: above 1 trends persist, below 1 they revert.
* a three-state Gaussian HMM on [5-day return, 20-day return, volatility ratio] (states ordered bear,
  sideways, bull by their mean 20-day return), fitted only on days before the brain's first row and
  filtered forward (no future day is ever used).
* in the brief: an EWMA volatility forecast (RiskMetrics, lambda 0.94), a filtered historical
  simulation of the next day (the standardized past returns rescaled to the forecast), the range
  used so far against the empirical range distribution (the share of past days that went wider,
  and the range typically left), and persistence tables (a Wilson interval and a z-test).
"""

from __future__ import annotations

import datetime as dt
import math
import re
import threading
from pathlib import Path
from typing import Any

import numpy as np

from trading.quant import cycles, ticks
from trading.quant.hmm import GaussianHMM

DAY = 86400
MIN_DAYS = 60                   # fewer closed days than this: no daily picture at all
MIN_FIT_DAYS = 250              # fewer days before the first row: the HMM stays neutral
FIT_ITERS = 20
RANK_DAYS = 10**6               # the whole history: ranks and range tables use every closed day
EWMA_LAMBDA = 0.94
SIM_DRAWS = 4000
DAILY_COLUMNS = (
    "dl_ret1", "dl_ret5", "dl_ret20", "dl_ret60", "dl_vol_ratio", "dl_vol_rank", "dl_ema20_dist", "dl_ema50_dist",
    "dl_ema200_dist", "dl_ema20_slope", "dl_hi20_dist", "dl_lo20_dist", "dl_hi55_dist", "dl_lo55_dist",
    "dl_prev_body", "dl_prev_upper", "dl_prev_lower", "dl_prev_range", "dl_rsi14", "dl_vr5", "dl_ac1",
    "dl_range_used", "dl_p_bear", "dl_p_side", "dl_p_bull", "dl_cy128_sin", "dl_cy128_cos", "dl_cy128_power",
    "dl_cy256_sin", "dl_cy256_cos", "dl_cy256_power",
)

_lock = threading.Lock()
_files: dict[tuple[str, int, int], list[dict[str, float]]] = {}
_fits: dict[tuple[Any, ...], tuple[GaussianHMM, dict[str, int]] | None] = {}
_indicators: dict[tuple[Any, ...], dict[str, np.ndarray]] = {}


# ── loading ─────────────────────────────────────────────────────────────────
def _candidates(symbol: str, folder: Path) -> list[Path]:
    safe = ticks._safe(symbol)
    base = re.match(r"[A-Za-z]{6}", symbol)
    names = [f"{safe}1440.csv", f"{safe}-D1.csv"]
    if base and base.group(0) != safe:           # a broker's suffix: XAUUSDcheck reads XAUUSD1440.csv
        names += [f"{base.group(0)}1440.csv", f"{base.group(0)}-D1.csv"]
    return [folder / name for name in names]


def parse(text: str) -> list[dict[str, float]]:
    """Daily bars from an MT4 History Center export (``2026.11.10,00:00,o,h,l,c,v``, no header) or from
    ``unix,o,h,l,c,v`` lines. A bad line is skipped; a day written twice keeps its latest values."""
    rows: dict[int, dict[str, float]] = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        try:
            if len(parts) >= 7 and ":" in parts[1]:
                t = dt.datetime.strptime(parts[0], "%Y.%m.%d").replace(tzinfo=dt.UTC).timestamp()
                o, h, lo, c = (float(p) for p in parts[2:6])
                v = float(parts[6])
            elif len(parts) == 6:
                t = float(parts[0])
                o, h, lo, c, v = (float(p) for p in parts[1:6])
            else:
                continue
        except ValueError:
            continue
        if not (h >= lo > 0 and h >= max(o, c) - 1e-9 and lo <= min(o, c) + 1e-9):
            continue
        day = int(t // DAY) * DAY
        rows[day] = {"t": float(day), "o": o, "h": h, "l": lo, "c": c, "v": v}
    return [rows[t] for t in sorted(rows)]


def load(symbol: str, folder: Path | None = None) -> list[dict[str, float]]:
    """Every saved daily bar for the symbol, oldest first; empty when there is no file."""
    root = folder or ticks.TICK_DIR.parent / "bars"
    for path in _candidates(symbol, root):
        try:
            stat = path.stat()
        except OSError:
            continue
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with _lock:
            if key in _files:
                return _files[key]
        try:
            bars = parse(path.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        with _lock:
            _files[key] = bars
            if len(_files) > 16:
                _files.pop(next(iter(_files)))
        return bars
    return []


# ── per-day indicators (value at index i uses days 0..i only) ────────────────
def _arrays(bars: list[dict[str, float]]) -> dict[str, np.ndarray]:
    return {k: np.array([b[k] for b in bars], dtype=float) for k in ("t", "o", "h", "l", "c")}


def _atr(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    prev = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev), np.abs(lo - prev)))
    out = np.empty_like(tr)
    acc = tr[0]
    for i, value in enumerate(tr):
        acc = value if i == 0 else acc + (value - acc) / n
        out[i] = acc
    return np.maximum(out, 1e-12)


def _ema(x: np.ndarray, period: int) -> np.ndarray:
    alpha, out = 2.0 / (period + 1), np.empty_like(x)
    acc = x[0]
    for i, value in enumerate(x):
        acc = value if i == 0 else acc + alpha * (value - acc)
        out[i] = acc
    return out


def _lag(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(x, np.nan)
    if k < len(x):
        out[k:] = x[:-k]
    return out


def _roll(x: np.ndarray, window: int, fn: Any) -> np.ndarray:
    out = np.full_like(x, np.nan)
    for i in range(window - 1, len(x)):
        out[i] = fn(x[i - window + 1:i + 1])
    return out


def _rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    delta = np.diff(c, prepend=c[0])
    up, dn = np.maximum(delta, 0.0), np.maximum(-delta, 0.0)
    au, ad = _ema(up, 2 * n - 1), _ema(dn, 2 * n - 1)       # Wilder's smoothing is an EMA of span 2n-1
    return 100.0 - 100.0 / (1.0 + au / np.maximum(ad, 1e-12))


def _rank(x: np.ndarray, window: int) -> np.ndarray:
    """Share of the last ``window`` values (itself included) at or below each value, centred on 0."""
    out = np.full_like(x, np.nan)
    for i in range(len(x)):
        seg = x[max(0, i - window + 1):i + 1]
        if len(seg) >= 30:
            out[i] = (float(np.mean(seg <= x[i])) - 0.5) * 2
    return out


def _variance_ratio(ret1: np.ndarray, window: int = 120, q: int = 5) -> np.ndarray:
    out = np.full_like(ret1, np.nan)
    cum = np.nancumsum(np.nan_to_num(ret1))
    ret_q = cum - np.concatenate((np.zeros(q), cum[:-q]))
    for i in range(window + q, len(ret1)):
        v1 = np.var(ret1[i - window + 1:i + 1])
        vq = np.var(ret_q[i - window + 1:i + 1])
        out[i] = vq / (q * max(v1, 1e-18)) - 1.0
    return out


def _autocorr(ret1: np.ndarray, window: int = 120) -> np.ndarray:
    out = np.full_like(ret1, np.nan)
    for i in range(window, len(ret1)):
        a, b = ret1[i - window + 1:i], ret1[i - window + 2:i + 1]
        sa, sb = np.std(a), np.std(b)
        out[i] = float(np.mean((a - a.mean()) * (b - b.mean())) / (sa * sb)) if sa > 0 and sb > 0 else 0.0
    return out


def indicators(bars: list[dict[str, float]]) -> dict[str, np.ndarray]:
    """Everything per day, aligned to ``bars``; NaN where the history is still too short. Kept for the last few series."""
    stamp = (len(bars), bars[0]["t"], bars[-1]["t"], bars[-1]["c"], bars[-1]["h"], bars[-1]["l"]) if bars else None
    with _lock:
        if stamp in _indicators:
            return _indicators[stamp]
    out = _indicators_of(bars)
    if stamp is not None:
        with _lock:
            _indicators[stamp] = out
            if len(_indicators) > 6:
                _indicators.pop(next(iter(_indicators)))
    return out


def _indicators_of(bars: list[dict[str, float]]) -> dict[str, np.ndarray]:
    a = _arrays(bars)
    o, h, lo, c = a["o"], a["h"], a["l"], a["c"]
    atr = _atr(h, lo, c)
    out: dict[str, np.ndarray] = {"t": a["t"], "c": c, "atr": atr}
    for k in (1, 5, 20, 60):
        out[f"ret{k}"] = (c - _lag(c, k)) / atr / math.sqrt(k)
    median250 = _roll(atr, 250, np.median)
    out["vol_ratio"] = np.log(atr / median250)
    out["vol_rank"] = _rank(atr, RANK_DAYS)
    for p in (20, 50, 200):
        out[f"ema{p}"] = _ema(c, p)
    out["ema20_slope"] = (out["ema20"] - _lag(out["ema20"], 5)) / atr
    for w in (20, 55):
        out[f"hi{w}"] = _roll(h, w, np.max)
        out[f"lo{w}"] = _roll(lo, w, np.min)
    out["prev_body"] = (c - o) / atr
    out["prev_upper"] = (h - np.maximum(o, c)) / atr
    out["prev_lower"] = (np.minimum(o, c) - lo) / atr
    out["prev_range"] = (h - lo) / atr - 1.0
    out["rsi14"] = (_rsi(c) - 50.0) / 50.0
    ret1 = np.log(c / _lag(c, 1))
    out["vr5"] = np.clip(_variance_ratio(np.nan_to_num(ret1)), -1.0, 2.0)
    out["ac1"] = _autocorr(np.nan_to_num(ret1))
    return out


# ── the hidden regime ───────────────────────────────────────────────────────
def _hmm_inputs(ind: dict[str, np.ndarray]) -> np.ndarray:
    return np.column_stack([ind["ret5"], ind["ret20"], np.nan_to_num(ind["vol_ratio"])])


def _fit_regime(ind: dict[str, np.ndarray], fit_until: float, key: Any) -> tuple[GaussianHMM, dict[str, int]] | None:
    cache_key = (key, int(fit_until // DAY))
    with _lock:
        if cache_key in _fits:
            return _fits[cache_key]
    x = _hmm_inputs(ind)
    closed = ind["t"] + DAY <= fit_until
    rows = x[closed & np.isfinite(x).all(axis=1)]
    fitted = None
    if len(rows) >= MIN_FIT_DAYS:
        model = GaussianHMM(3).fit(rows.tolist(), iters=FIT_ITERS)
        order = sorted(range(3), key=lambda j: model.mu[j][1])           # by the mean 20-day return
        fitted = (model, {"bear": order[0], "side": order[1], "bull": order[2]})
    with _lock:
        _fits[cache_key] = fitted
        if len(_fits) > 16:
            _fits.pop(next(iter(_fits)))
    return fitted


def regime_posterior(ind: dict[str, np.ndarray], fit_until: float, key: Any = None) -> np.ndarray | None:
    """(days, 3) causal P(bear, sideways, bull | days up to each one), or None when too little was known to fit."""
    fitted = _fit_regime(ind, fit_until, key)
    if fitted is None:
        return None
    model, names = fitted
    x = _hmm_inputs(ind)
    post = np.full((len(x), 3), np.nan)
    ok = np.isfinite(x).all(axis=1)
    if ok.any():
        first = int(np.argmax(ok))
        filtered = np.array(model.filter(x[first:].tolist()))
        post[first:] = filtered[:, [names["bear"], names["side"], names["bull"]]]
    return post


# ── the brain's columns ─────────────────────────────────────────────────────
def feature_columns(daily: list[dict[str, float]], t_close: np.ndarray, price: np.ndarray, day_hi: np.ndarray,
                    day_lo: np.ndarray, *, key: Any = None) -> dict[str, np.ndarray]:
    """The ``dl_`` columns for rows decided at ``t_close`` at ``price``; a row sees only days closed by then.

    ``day_hi`` and ``day_lo`` are the running high and low of the row's own day (``range_used``).
    The HMM is fitted on the days closed before the first row, so no row's own period shaped it.
    """
    n = len(t_close)
    nan = np.full(n, np.nan)
    if len(daily) < MIN_DAYS or n == 0:
        return {}
    ind = indicators(daily)
    closed = np.searchsorted(ind["t"] + DAY, np.asarray(t_close, dtype=float), side="right") - 1    # last closed day
    ok = closed >= 0
    idx = np.where(ok, closed, 0)

    def day(name: str) -> np.ndarray:
        return np.where(ok, ind[name][idx], np.nan)

    atr = day("atr")
    cols: dict[str, np.ndarray] = {f"dl_{k}": day(k) for k in ("ret1", "ret5", "ret20", "ret60", "vol_ratio", "vol_rank",
                                                              "ema20_slope", "prev_body", "prev_upper", "prev_lower",
                                                              "prev_range", "rsi14", "vr5", "ac1")}
    for p in (20, 50, 200):
        cols[f"dl_ema{p}_dist"] = (price - day(f"ema{p}")) / atr
    for w in (20, 55):
        cols[f"dl_hi{w}_dist"] = (price - day(f"hi{w}")) / atr
        cols[f"dl_lo{w}_dist"] = (price - day(f"lo{w}")) / atr
    cols["dl_range_used"] = (day_hi - day_lo) / atr
    post = regime_posterior(ind, float(np.min(t_close)), key)
    for j, name in enumerate(("dl_p_bear", "dl_p_side", "dl_p_bull")):
        cols[name] = np.where(ok, post[idx, j], np.nan) if post is not None else nan
    for window in (128, 256):             # the daily cycle's phase and how much one period stands out, as of each closed day
        cyc = cycles.cycle_columns(ind["c"], window)
        for src, dst in (("cy_sin", "sin"), ("cy_cos", "cos"), ("cy_power", "power")):
            cols[f"dl_cy{window}_{dst}"] = np.where(ok, cyc[src][idx], np.nan)
    return cols


# ── the brief for the analyst ───────────────────────────────────────────────
def _wilson(k: int, n: int, z: float = 2.58) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def _share(k: int, n: int) -> dict[str, Any]:
    """An up-share with its 99% interval; ``edge`` only when the interval excludes 50%."""
    lo, hi = _wilson(k, n)
    return {"up": round(k / n, 3) if n else None, "n": n, "edge": bool(n and (lo > 0.5 or hi < 0.5))}


def ewma_sigma(r: np.ndarray, lam: float = EWMA_LAMBDA) -> np.ndarray:
    """RiskMetrics volatility: sigma_t uses returns up to t-1 only (so a return is divided by what was expected)."""
    sigma = np.empty_like(r)
    var = float(np.var(r[:30])) if len(r) >= 30 else float(np.var(r)) or 1e-8
    for i in range(len(r)):
        sigma[i] = math.sqrt(var)
        var = lam * var + (1 - lam) * r[i] ** 2
    return sigma


def simulate_next_day(ret: np.ndarray, *, draws: int = SIM_DRAWS, seed: int = 0) -> dict[str, float]:
    """Filtered historical simulation: past returns divided by the volatility expected then, redrawn and
    rescaled to tomorrow's forecast. Quantiles of the one-day log return."""
    sigma = ewma_sigma(ret)
    z = (ret / sigma)[30:]
    z = z[np.isfinite(z)]
    var_next = EWMA_LAMBDA * sigma[-1] ** 2 + (1 - EWMA_LAMBDA) * ret[-1] ** 2
    forecast = math.sqrt(var_next)
    rng = np.random.default_rng(seed)
    sample = rng.choice(z, size=draws, replace=True) * forecast
    q = np.quantile(sample, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {"sigma": forecast, "q05": q[0], "q25": q[1], "q50": q[2], "q75": q[3], "q95": q[4]}


def brief(daily: list[dict[str, float]], now: float, *, price: float, day_hi: float | None = None,
          day_lo: float | None = None, key: Any = None) -> dict[str, Any]:
    """A compact description of the daily picture as of ``now``, or {} with too little history.

    Statistics are priors from past days, not signals: each says how many days it rests on, and the
    persistence ones whether they beat chance (``edge``).
    """
    if len(daily) < MIN_DAYS:
        return {}
    ind = indicators(daily)
    last = int(np.searchsorted(ind["t"] + DAY, now, side="right")) - 1       # the last closed day
    if last < MIN_DAYS - 1:
        return {}
    atr = float(ind["atr"][last])
    c = ind["c"][: last + 1]
    ret = np.log(c[1:] / c[:-1])
    out: dict[str, Any] = {"days": last + 1, "since": dt.datetime.fromtimestamp(ind["t"][0], dt.UTC).strftime("%Y-%m"),
                           "atr_d": round(atr, 2), "atr_pct": round(atr / float(c[-1]) * 100, 2)}

    def at(name: str) -> float:
        return float(ind[name][last])

    trend = {"ret5_atr": round(at("ret5") * math.sqrt(5), 2), "ret20_atr": round(at("ret20") * math.sqrt(20), 2),
             "ret60_atr": round(at("ret60") * math.sqrt(60), 2)}
    for p in (20, 50, 200):
        trend[f"vs_ema{p}_atr"] = round((price - at(f"ema{p}")) / atr, 2)
    trend["rsi14"] = round(at("rsi14") * 50 + 50, 1)
    out["trend"] = trend
    out["recent_days"] = [{"date": dt.datetime.fromtimestamp(ind["t"][i], dt.UTC).strftime("%m-%d"),
                           "body_atr": round(float(daily[i]["c"] - daily[i]["o"]) / float(ind["atr"][i]), 2),
                           "range_atr": round(float(daily[i]["h"] - daily[i]["l"]) / float(ind["atr"][i]), 2),
                           "close": round(float(daily[i]["c"]), 2)} for i in range(max(0, last - 9), last + 1)]
    out["channel"] = {f"{w}d": {"high_atr": round((float(ind[f'hi{w}'][last]) - price) / atr, 2),
                                "low_atr": round((price - float(ind[f'lo{w}'][last])) / atr, 2)} for w in (20, 55)}
    vr, ac = at("vr5"), at("ac1")
    out["behaviour"] = {"variance_ratio5": round(vr + 1, 2), "autocorr1": round(ac, 2),
                        "reads": "trends tend to continue" if vr > 0.15 else "moves tend to revert" if vr < -0.15 else "no clear tendency"}
    rank = at("vol_rank")
    out["volatility"] = {"rank_pct": round((rank / 2 + 0.5) * 100) if math.isfinite(rank) else None,
                         "vs_median_250d": round(math.exp(at("vol_ratio")), 2) if math.isfinite(at("vol_ratio")) else None}
    post = regime_posterior(ind, now, key)
    if post is not None and np.isfinite(post[last]).all():
        names = ("bear", "sideways", "bull")
        fitted = _fit_regime(ind, now, key)
        stay = [fitted[0].A[fitted[1][k]][fitted[1][k]] for k in ("bear", "side", "bull")] if fitted else [0.0] * 3
        j = int(np.argmax(post[last]))
        out["regime"] = {"state": names[j], "p": {n: round(float(post[last][i]), 2) for i, n in enumerate(names)},
                         "days_expected": round(1.0 / max(1.0 - stay[j], 1e-3), 1)}
    window = slice(max(1, last - RANK_DAYS + 1), last + 1)
    width = np.array([b["h"] - b["l"] for b in daily[: last + 1]])
    rng_atr = (width / np.maximum(_lag(ind["atr"], 1)[: last + 1], 1e-12))[window]
    rng_atr = rng_atr[np.isfinite(rng_atr)]
    if len(rng_atr):
        out["day_range_atr"] = {"median": round(float(np.median(rng_atr)), 2), "p80": round(float(np.quantile(rng_atr, 0.8)), 2),
                                "n": int(len(rng_atr))}
        if day_hi is not None and day_lo is not None and day_hi >= day_lo:
            used = (day_hi - day_lo) / atr
            wider = rng_atr[rng_atr > used]
            out["today"] = {"range_used_atr": round(used, 2), "days_wider_pct": round(len(wider) / len(rng_atr) * 100),
                            "typical_left_atr": round(float(np.median(wider)) - used, 2) if len(wider) >= 5 else 0.0}
    sim = simulate_next_day(ret, seed=int(ind["t"][last] // DAY))
    out["next_day"] = {"sigma_pct": round(sim["sigma"] * 100, 2),
                       "move_90pct_atr": [round(float(c[-1]) * (math.exp(sim["q05"]) - 1) / atr, 2),
                                          round(float(c[-1]) * (math.exp(sim["q95"]) - 1) / atr, 2)],
                       "method": "filtered historical simulation, EWMA 0.94"}
    up = ret > 0
    prior = up[:-1]
    out["after_a_day"] = {"up": _share(int(np.sum(up[1:][prior])), int(prior.sum())),
                          "down": _share(int(np.sum(up[1:][~prior])), int((~prior).sum()))}
    weekday = dt.datetime.fromtimestamp(now, dt.UTC).weekday()
    days = np.array([dt.datetime.fromtimestamp(t, dt.UTC).weekday() for t in ind["t"][1: last + 1]])
    same = days == weekday
    if same.sum() >= 30:
        out["this_weekday"] = {**_share(int(np.sum(up[same])), int(same.sum())),
                               "mean_ret_atr": round(float(np.mean(ret[same] * c[:-1][same] / ind["atr"][: last][same])), 3)}
    from trading.quant import forecast       # imported here: forecast reads this module's indicators

    out["forecast"] = forecast.forecast(daily, last, ind, key=key)
    out["note"] = "priors from past days, not signals; use a share only when edge is true"
    return out


__all__ = ["load", "parse", "indicators", "feature_columns", "brief", "regime_posterior", "simulate_next_day", "ewma_sigma",
           "DAILY_COLUMNS", "MIN_DAYS", "DAY"]
