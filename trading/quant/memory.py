"""Trading memory: every closed bar, what was known when it closed, and what every possible trade did next.

This is what the trading brain (``neural``, ``brain``) learns from. One row per closed bar of the
chosen timeframe (M15, M30, H1 or H4, built from the EA's M5 bars):

* **What was known** (``feature_matrix``), about 110 numbers:
  - the last three candles' body, wicks and range, in ATR;
  - moves over 1 to 55 bars, distances to EMAs 9 to 200 and their slopes;
  - RSI, stochastic %K and %D, the MACD histogram, Bollinger, and volatility now against before;
  - the 20, 50 and 100-bar highs and lows, the day's open, high and low, and yesterday's;
  - the fourteen measurements of ``confluence`` (momentum, Kalman slope, VWAP, Hurst, variance
    ratio, autocorrelation, entropy...);
  - tick volume, the hour and the weekday, and which sessions are open;
  - high-impact news for the symbol's currencies: hours to the next one, hours since the last,
    how many are near (from the calendar archive, flagged when the archive does not reach back
    that far);
  - the spread and the news mood when a live run recorded them.
  - price levels (``levels``): the place against the daily pivot and the Camarilla band, how much
    of the last swing leg has been retraced and the distance to the nearest Fibonacci level, and
    the room to the nearest confluence zone above and below;
  - the market's clocks (``cycles``): the New York clock, hours into the London and New York
    opens, closeness to the rollover, place in the month, the moon;
  - what the coming hour did on earlier days (drift and width), and the phase and strength of the
    strongest cycle in the last 128 bars.
  - the daily memory (``daily``, ``dl_``, only when a D1 export is in ``data/bars``): the trend, volatility rank,
    channel, variance ratio and the daily HMM regime of the closed days, and how much of the day's range is used.
  - the regime (``regime``, ``rg_``): ADX and its slope, +DI against -DI, the Kalman surprise and the
    pullback before it, the day's VWAP, the slow trend, the hidden state of the three-state HMM
    (trend, sideways, high volatility), and the M5 quant model's own score and entry rule.
* **What happened** (``outcomes``): for each of 24 plans (buy or sell, stop 1, 1.5 or 2 ATR,
  target 1, 1.5, 2 or 3 ATR), entered at the bar's close at the ask (buy) or bid (sell),
  whether the target or the stop was touched first within the hold. The order is read from the
  M5 bars inside, and when both fall in one M5 bar the stop counts, so the memory never
  flatters a plan. A plan that touches neither closes at the end of the hold, and its result
  is kept in ATR.

Rows whose future is not complete yet are kept without an outcome. They are the "now" the brain
is asked about, and they get their outcome on a later build.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from trading.quant import confluence, cycles, daily, levels, regime, sessions, ticks

BASE_SECONDS = 300
TIMEFRAMES = {"M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}
HOLD_BARS = {"M15": 16, "M30": 12, "H1": 8, "H4": 6}      # how long a plan may run before it is closed
SL_GRID = (1.0, 1.5, 2.0)
TP_GRID = (1.0, 1.5, 2.0, 3.0)
PLANS: tuple[tuple[int, float, float], ...] = tuple((side, sl, tp) for side in (1, -1) for sl in SL_GRID for tp in TP_GRID)
REFERENCE = (1.5, 2.0)          # the plan the condition tables describe
TP, SL, TIMEOUT = 0, 1, 2
WARMUP = 120                    # bars before the first row: EMA 100, 100-bar range, regime windows
BASE_KEEP = 200_000             # M5 bars read at most (about two years)
# TRADING_BRAIN_DIR moves the brain, its live context and the analyst journal
FOLDER = Path(os.getenv("TRADING_BRAIN_DIR", "").strip() or Path(__file__).resolve().parents[1] / "data" / "brain")
SESSION_KEYS = ("sydney", "tokyo", "london", "newyork")


def resample(bars: list[dict[str, float]], seconds: int, now: float) -> list[dict[str, float]]:
    """Group shorter bars into ``seconds`` bars, keeping only the ones that have closed by ``now``."""
    out: list[dict[str, float]] = []
    for b in bars:
        t = float(b["t"]) // seconds * seconds
        if out and out[-1]["t"] == t:
            last = out[-1]
            last.update(h=max(last["h"], b["h"]), l=min(last["l"], b["l"]), c=b["c"], v=last["v"] + b.get("v", 0))
        else:
            out.append({"t": t, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": float(b.get("v", 0))})
    while out and out[-1]["t"] + seconds > now:
        out.pop()
    return out


HISTORY_TTL_S = 20.0
_history_cache: dict[tuple[str, int, str], tuple[float, list[dict[str, float]]]] = {}
_history_lock = threading.Lock()
_history_reading: dict[tuple[str, int, str], threading.Lock] = {}


def history_m5(symbol: str, backend: Any = None, folder: Path | None = None) -> list[dict[str, float]]:
    """Every M5 bar known for the symbol: the ones saved on disk since recording began, plus the backend's.

    Kept for ``HISTORY_TTL_S``: the panel, the analyst and the simulation all ask, and a new M5 bar
    only comes every five minutes.
    """
    key = (symbol, id(backend), str(folder))
    with _history_lock:
        reading = _history_reading.setdefault(key, threading.Lock())
    with reading:                      # a second caller waits for the first read instead of reading again
        with _history_lock:
            cached = _history_cache.get(key)
            if cached and _clock() - cached[0] < HISTORY_TTL_S:
                return cached[1]
        bars = _read_history(symbol, backend, folder)
        with _history_lock:
            _history_cache[key] = (_clock(), bars)
            if len(_history_cache) > 32:
                old = next(iter(_history_cache))
                _history_cache.pop(old)
                _history_reading.pop(old, None)
        return bars


def _clock() -> float:
    import time

    return time.monotonic()


def _read_history(symbol: str, backend: Any, folder: Path | None) -> list[dict[str, float]]:
    merged = {b["t"]: b for b in ticks.load_saved_series(symbol, "M5", folder)}
    if backend is not None:
        try:
            for b in backend.rates(symbol, "M5", 6000):
                merged[float(b["t"])] = {"t": float(b["t"]), "o": float(b["o"]), "h": float(b["h"]), "l": float(b["l"]),
                                         "c": float(b["c"]), "v": float(b.get("v", 0))}
        except Exception:  # noqa: BLE001 - the saved history alone is still a memory
            pass
    return [merged[t] for t in sorted(merged)][-BASE_KEEP:]


# ── small numeric helpers ────────────────────────────────────────────────────
def _ema(x: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    out = np.empty_like(x)
    acc = x[0]
    for i, value in enumerate(x):
        acc = alpha * value + (1 - alpha) * acc
        out[i] = acc
    return out


def _roll(x: np.ndarray, window: int, fn: Any) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= window:
        out[window - 1:] = fn(np.lib.stride_tricks.sliding_window_view(x, window), axis=1)
    return out


def _lag(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:-k]
    return out


def wilder_atr(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev), np.abs(lo - prev)))
    out = np.empty_like(tr)
    acc = tr[0]
    for i, value in enumerate(tr):
        acc = value if i == 0 else acc + (value - acc) / n
        out[i] = acc
    result: np.ndarray = np.maximum(out, 1e-12)
    return result


# ── what was known ───────────────────────────────────────────────────────────
@dataclass
class NewsIndex:
    """High and medium-impact events for some currencies, and the spans the archive covers."""

    high: np.ndarray
    medium: np.ndarray
    coverage: list[tuple[float, float]]

    @classmethod
    def of(cls, archive: dict[str, Any] | None, currencies: list[str]) -> NewsIndex:
        events = (archive or {}).get("events", [])
        wanted = set(currencies)
        pick = [e for e in events if not wanted or str(e.get("currency", "")).upper() in wanted]
        high = np.array(sorted(float(e["time"]) for e in pick if e.get("impact") == "high"))
        medium = np.array(sorted(float(e["time"]) for e in pick if e.get("impact") == "medium"))
        return cls(high, medium, [(float(a), float(b)) for a, b in (archive or {}).get("coverage", [])])

    def known(self, t: np.ndarray) -> np.ndarray:
        out = np.zeros(len(t), dtype=bool)
        for start, end in self.coverage:
            out |= (t >= start) & (t <= end)
        return out


def _hours_to(times: np.ndarray, t: np.ndarray, *, after: bool, cap: float = 24.0) -> np.ndarray:
    if not len(times):
        return np.full(len(t), cap)
    if after:
        k = np.searchsorted(times, t, side="left")
        nxt = np.where(k < len(times), times[np.minimum(k, len(times) - 1)], np.inf)
        ahead: np.ndarray = np.minimum((nxt - t) / 3600.0, cap)
        return ahead
    k = np.searchsorted(times, t, side="right") - 1
    prv = np.where(k >= 0, times[np.maximum(k, 0)], -np.inf)
    since: np.ndarray = np.minimum((t - prv) / 3600.0, cap)
    return since


def _count_between(times: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    if not len(times):
        return np.zeros(len(start))
    return (np.searchsorted(times, end, side="right") - np.searchsorted(times, start, side="left")).astype(float)


_session_cache: dict[int, tuple[float, ...]] = {}


def _session_flags(t: float) -> tuple[float, ...]:
    hour = int(t // 3600)
    if hour not in _session_cache:
        open_now = set(sessions.active(hour * 3600.0 + 1800.0))
        _session_cache[hour] = tuple(float(k in open_now) for k in SESSION_KEYS)
        if len(_session_cache) > 200_000:
            _session_cache.clear()
    return _session_cache[hour]


def feature_matrix(bars: list[dict[str, float]], seconds: int, *, news: NewsIndex | None = None,
                   context: dict[int, dict[str, float]] | None = None, spread: float = 0.0,
                   daily_bars: list[dict[str, float]] | None = None) -> tuple[np.ndarray, list[str]]:
    """One row per bar of what was known at its close; columns named by the second value."""
    n = len(bars)
    o = np.array([b["o"] for b in bars], dtype=float)
    h = np.array([b["h"] for b in bars], dtype=float)
    lo = np.array([b["l"] for b in bars], dtype=float)
    c = np.array([b["c"] for b in bars], dtype=float)
    v = np.array([b.get("v", 0.0) for b in bars], dtype=float)
    t_close = np.array([b["t"] for b in bars], dtype=float) + seconds
    atr = wilder_atr(h, lo, c)
    atr100 = _roll(atr, 100, np.mean)
    cols: dict[str, np.ndarray] = {}

    def candle(k: int) -> None:
        oo, hh, ll, cc = (_lag(x, k) if k else x for x in (o, h, lo, c))
        a = _lag(atr, k) if k else atr
        cols[f"c{k}_body"] = (cc - oo) / a
        cols[f"c{k}_upper"] = (hh - np.maximum(oo, cc)) / a
        cols[f"c{k}_lower"] = (np.minimum(oo, cc) - ll) / a
        cols[f"c{k}_range"] = (hh - ll) / a

    for k in (0, 1, 2):
        candle(k)
    for k in (1, 2, 3, 5, 8, 13, 21, 34, 55):
        cols[f"ret_{k}"] = (c - _lag(c, k)) / atr / math.sqrt(k)
    r = np.diff(c, prepend=c[0])
    cols["vol_atr_ratio"] = atr / atr100 - 1
    cols["vol_std_ratio"] = _roll(r, 10, np.std) / np.maximum(_roll(r, 50, np.std), 1e-12) - 1
    cols["vol_range_ratio"] = (h - lo) / np.maximum(_roll(h - lo, 20, np.mean), 1e-12) - 1
    emas = {p: _ema(c, p) for p in (9, 21, 50, 100, 200)}
    for p, e in emas.items():
        cols[f"ema{p}_dist"] = (c - e) / atr
    cols["ema21_slope"] = (emas[21] - _lag(emas[21], 5)) / atr
    cols["ema50_slope"] = (emas[50] - _lag(emas[50], 10)) / atr
    cols["ema9_21"] = (emas[9] - emas[21]) / atr
    cols["ema50_200"] = (emas[50] - emas[200]) / atr
    rsi7, rsi14 = confluence._rsi(c, 7), confluence._rsi(c, 14)
    cols["rsi7"] = (rsi7 - 50) / 50
    cols["rsi14"] = (rsi14 - 50) / 50
    hi14, lo14 = _roll(h, 14, np.max), _roll(lo, 14, np.min)
    cols["stoch14"] = (c - lo14) / np.maximum(hi14 - lo14, 1e-12) * 2 - 1
    cols["stoch_d"] = _roll(cols["stoch14"], 3, np.mean)                  # %D: the three-bar average of %K
    macd = _ema(c, 12) - _ema(c, 26)
    cols["macd_hist"] = (macd - _ema(macd, 9)) / atr
    for w in (20, 50, 100):
        top, bottom = _roll(h, w, np.max), _roll(lo, w, np.min)
        cols[f"hi{w}_dist"] = (c - top) / atr
        cols[f"lo{w}_dist"] = (c - bottom) / atr
    cols["place20"] = (c - _roll(lo, 20, np.min)) / np.maximum(_roll(h, 20, np.max) - _roll(lo, 20, np.min), 1e-12) * 2 - 1
    cols["vol_z"] = np.log1p(v) - _roll(np.log1p(v), 50, np.mean)

    # the day: its open, high and low so far, and yesterday's high, low and close (UTC days)
    day_open, day_hi, day_lo = np.empty(n), np.empty(n), np.empty(n)
    prev_hi, prev_lo, prev_c = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    day = -1
    y_hi = y_lo = y_c = math.nan
    cur_open = cur_hi = cur_lo = 0.0
    for i in range(n):
        d = int(bars[i]["t"] // 86400)
        if d != day:
            if day >= 0:
                y_hi, y_lo, y_c = cur_hi, cur_lo, c[i - 1]
            day, cur_open, cur_hi, cur_lo = d, o[i], h[i], lo[i]
        cur_hi, cur_lo = max(cur_hi, h[i]), min(cur_lo, lo[i])
        day_open[i], day_hi[i], day_lo[i] = cur_open, cur_hi, cur_lo
        prev_hi[i], prev_lo[i], prev_c[i] = y_hi, y_lo, y_c
    cols["day_open_dist"] = (c - day_open) / atr
    cols["day_hi_dist"] = (c - day_hi) / atr
    cols["day_lo_dist"] = (c - day_lo) / atr
    cols["prev_hi_dist"] = (c - prev_hi) / atr
    cols["prev_lo_dist"] = (c - prev_lo) / atr
    cols["prev_close_dist"] = (c - prev_c) / atr

    conf = confluence.matrix(bars)
    for j, name in enumerate(confluence.NAMES):
        cols[f"m_{name}"] = conf[:, j]

    utc = [dt.datetime.fromtimestamp(t, dt.UTC) for t in t_close]
    hour = np.array([u.hour + u.minute / 60 for u in utc])
    dow = np.array([u.weekday() for u in utc], dtype=float)
    cols["hour_sin"], cols["hour_cos"] = np.sin(hour / 24 * 2 * math.pi), np.cos(hour / 24 * 2 * math.pi)
    cols["dow_sin"], cols["dow_cos"] = np.sin(dow / 7 * 2 * math.pi), np.cos(dow / 7 * 2 * math.pi)
    flags = np.array([_session_flags(t) for t in t_close])
    for j, key in enumerate(SESSION_KEYS):
        cols[f"s_{key}"] = flags[:, j]
    cols["s_overlap"] = (flags.sum(axis=1) >= 2).astype(float)

    idx = news or NewsIndex(np.array([]), np.array([]), [])
    known = idx.known(t_close)
    hours_next = _hours_to(idx.high, t_close, after=True)
    hours_prev = _hours_to(idx.high, t_close, after=False)
    cols["news_known"] = known.astype(float)
    cols["news_next_h"] = np.where(known, hours_next / 24, 1.0)
    cols["news_prev_h"] = np.where(known, hours_prev / 24, 1.0)
    cols["news_high_4h"] = np.where(known, _count_between(idx.high, t_close, t_close + 4 * 3600), 0.0)
    cols["news_high_past2h"] = np.where(known, _count_between(idx.high, t_close - 2 * 3600, t_close), 0.0)
    cols["news_medium_4h"] = np.where(known, _count_between(idx.medium, t_close, t_close + 4 * 3600), 0.0)

    ctx = context or {}
    live = np.array([float(int(t) in ctx) for t in t_close])
    cols["live_context"] = live
    cols["spread_atr"] = np.array([ctx.get(int(t), {}).get("spread", spread) for t in t_close]) / atr
    cols["mood"] = np.array([ctx.get(int(t), {}).get("mood", 0.0) for t in t_close])
    cols["fear_greed"] = np.array([ctx.get(int(t), {}).get("fear_greed", 0.0) for t in t_close])

    # levels (lv_), the market's clocks (tm_), seasonality and the cycle (cy_), the regime (rg_): the brain keeps each
    # group only when it predicts the validation rows better with it (``brain.choose_groups``)
    extra: dict[str, np.ndarray] = {}
    extra.update(levels.feature_columns(bars, atr))
    extra.update(cycles.clock_columns(t_close))
    extra.update(cycles.seasonal_columns(bars, seconds, atr))
    extra.update(cycles.cycle_columns(c))
    extra.update(regime.regime_columns(bars, atr, seconds))     # rg_: ADX, DI, Kalman, HMM state, the quant rule
    if daily_bars:                  # dl_: the D1 history, only the days closed by each bar's close
        extra.update(daily.feature_columns(daily_bars, t_close, c, day_hi, day_lo, key=("fm", len(daily_bars), daily_bars[-1]["t"])))
    for name, col in extra.items():        # not known yet (no swing confirmed, no full cycle window) reads as neutral
        cols[name] = np.where(np.isfinite(col), col, 0.0)

    names = list(cols)
    with np.errstate(invalid="ignore", divide="ignore"):
        x = np.column_stack([cols[k] for k in names]).astype(float)
    x[~np.isfinite(x)] = np.nan
    return np.clip(x, -8, 8), names


# ── what happened ───────────────────────────────────────────────────────────
def outcomes(bars: list[dict[str, float]], base: list[dict[str, float]], seconds: int, hold: int, spread: float
             ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each bar and plan: TP, SL or TIMEOUT first, the result in ATR, and whether the future is complete."""
    n, plans = len(bars), len(PLANS)
    cls = np.full((n, plans), -1, dtype=np.int8)
    res = np.zeros((n, plans), dtype=np.float32)
    done = np.zeros(n, dtype=bool)
    if not n or not base:
        return cls, res, done
    bt = np.array([b["t"] for b in base])
    bh = np.array([b["h"] for b in base])
    bl = np.array([b["l"] for b in base])
    bc = np.array([b["c"] for b in base])
    h = np.array([b["h"] for b in bars])
    lo = np.array([b["l"] for b in bars])
    c = np.array([b["c"] for b in bars])
    atr = wilder_atr(h, lo, c)
    width = hold * max(1, seconds // BASE_SECONDS)
    starts = np.searchsorted(bt, np.array([b["t"] for b in bars]) + seconds, side="left")
    for i in range(n):
        j0 = int(starts[i])
        j1 = j0 + width
        if j1 > len(bt):
            continue
        done[i] = True
        low_path = np.minimum.accumulate(bl[j0:j1])
        high_path = np.maximum.accumulate(bh[j0:j1])
        neg_low = -low_path
        a = atr[i]
        for p, (side, sl, tp) in enumerate(PLANS):
            if side > 0:
                entry = c[i] + spread                                   # bought at the ask, sold at the bid
                hit_sl = int(np.searchsorted(neg_low, -(entry - sl * a), side="left"))
                hit_tp = int(np.searchsorted(high_path, entry + tp * a, side="left"))
                exit_r = (bc[j1 - 1] - entry) / a
            else:
                entry = c[i]                                            # sold at the bid, bought back at the ask
                hit_sl = int(np.searchsorted(high_path, entry + sl * a - spread, side="left"))
                hit_tp = int(np.searchsorted(neg_low, -(entry - tp * a - spread), side="left"))
                exit_r = (entry - bc[j1 - 1] - spread) / a
            if hit_sl < width and hit_sl <= hit_tp:                    # both in one M5 bar: the stop counts
                cls[i, p], res[i, p] = SL, -sl
            elif hit_tp < width:
                cls[i, p], res[i, p] = TP, tp
            else:
                cls[i, p], res[i, p] = TIMEOUT, exit_r
    return cls, res, done


# ── live context recorded by the simulation ─────────────────────────────────
_context_lock = threading.Lock()


def _context_path(symbol: str, timeframe: str, folder: Path | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in symbol)[:32]
    return (folder or FOLDER) / f"{safe}-{timeframe}-context.jsonl"


def record_context(symbol: str, timeframe: str, decided_at: float, *, spread: float, mood: float = 0.0,
                   fear_greed: float | None = None, folder: Path | None = None) -> None:
    """Keep what a live run saw at a bar's close: the spread and the news mood. Training joins it later."""
    seconds = TIMEFRAMES[timeframe]
    row = {"t": int(decided_at // seconds * seconds), "spread": round(float(spread), 8), "mood": round(float(mood), 4),
           "fear_greed": round((float(fear_greed) - 50) / 50, 4) if fear_greed is not None else 0.0}
    path = _context_path(symbol, timeframe, folder)
    with _context_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(row) + "\n")
        except OSError:
            pass


def load_context(symbol: str, timeframe: str, folder: Path | None = None) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    try:
        text = _context_path(symbol, timeframe, folder).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            row = json.loads(line)
            out[int(row["t"])] = {k: float(row.get(k, 0.0)) for k in ("spread", "mood", "fear_greed")}
        except (ValueError, KeyError, TypeError):
            continue
    return out


# ── the dataset ─────────────────────────────────────────────────────────────
@dataclass
class Dataset:
    symbol: str
    timeframe: str
    x: np.ndarray                     # (rows, features), NaN where a measurement had no history yet
    names: list[str]
    times: np.ndarray                 # the moment each row was decided: its bar's close
    cls: np.ndarray                   # (rows, plans): TP, SL, TIMEOUT, or -1 with no outcome yet
    result: np.ndarray                # (rows, plans): the plan's result in ATR
    labelled: np.ndarray              # (rows,) bool
    atr: np.ndarray
    close: np.ndarray
    spread: float
    hold: int
    base_bars: int
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def rows(self) -> int:
        return len(self.times)


def build(symbol: str, timeframe: str, base: list[dict[str, float]], *, now: float, spread: float,
          archive: dict[str, Any] | None = None, currencies: list[str] | None = None,
          context: dict[int, dict[str, float]] | None = None) -> Dataset:
    """The memory for one symbol and timeframe from its M5 history."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"timeframe: {', '.join(TIMEFRAMES)}")
    seconds, hold = TIMEFRAMES[timeframe], HOLD_BARS[timeframe]
    bars = resample(base, seconds, now)
    news = NewsIndex.of(archive, currencies or [])
    x, names = feature_matrix(bars, seconds, news=news, context=context, spread=spread, daily_bars=daily.load(symbol))
    cls, res, done = outcomes(bars, base, seconds, hold, spread)
    keep = slice(WARMUP, None)
    h = np.array([b["h"] for b in bars])
    lo = np.array([b["l"] for b in bars])
    c = np.array([b["c"] for b in bars])
    atr = wilder_atr(h, lo, c) if bars else np.array([])
    times = np.array([b["t"] for b in bars], dtype=float) + seconds
    known = news.known(times[keep]) if len(times) else np.array([], dtype=bool)
    return Dataset(symbol, timeframe, x[keep], names, times[keep], cls[keep], res[keep], done[keep], atr[keep], c[keep],
                   spread, hold, len(base),
                   info={"bars": len(bars), "news_events": int(len(news.high) + len(news.medium)),
                         "news_known_rows": int(known.sum()),
                         "live_rows": int(sum(1 for t in times[keep] if int(t) in (context or {})))})


__all__ = ["PLANS", "TIMEFRAMES", "HOLD_BARS", "SL_GRID", "TP_GRID", "TP", "SL", "TIMEOUT", "Dataset", "build", "resample",
           "history_m5", "feature_matrix", "outcomes", "record_context", "load_context", "NewsIndex", "FOLDER", "REFERENCE"]
