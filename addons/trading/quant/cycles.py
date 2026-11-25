"""Time: the market's clocks, what each hour has done before, and whether the price is cycling.

Three questions, each answered with its own honesty check.

**What time is it for the market?** (``clock``, ``windows``, ``phase``, ``clock_columns``)
A UTC hour is not a market hour: London opens at 07:00 UTC in summer and 08:00 in winter, and
for a few weeks each March and autumn London and New York are four hours apart instead of five,
which moves their overlap. Everything here is read from each city's own clock: the opens, the
US data minute (08:30 New York), the stock open (09:30), the gold fixes (10:30 and 15:00
London), the 16:00 London fix, and the 17:00 New York rollover where the forex day ends and
spreads widen. Slow calendars ride along (place in the month, month end, the moon's phase), and
the brain only reads those once its memory spans enough of them (see ``brain.SLOW_SPAN_DAYS``):
over three weeks of history "day of the month" is just a clock that always goes up.

**Does this hour usually go somewhere?** (``seasonality``, ``seasonal_columns``)
Bars are grouped by the New York hour they open in. For each hour: the average move in ATR, its
t-statistic and p-value (Student), and how wide its bars are against the average bar. Twenty-four
hours are twenty-four tries, so the p-values are turned into q-values (Benjamini-Hochberg) and an
hour counts as a finding only below ``Q_LIMIT``. How *wide* an hour's bars are is real and stable
(the opens are busy, the Asian afternoon is not). Which *way* it goes rarely survives the test,
and the report says so instead of showing the raw averages as if they meant something.

**Is there a cycle?** (``dominant_cycle``, ``cycle_columns``)
The periodogram of the returns of the last ``WINDOW`` bars. Returns, not prices: a random walk's
price spectrum always peaks at the longest period, which looks like a cycle and is not. Under no
cycle the return spectrum is flat, so the test is Fisher's g (the largest ordinate's share of the
total, with its exact p-value) plus a permutation check that shuffles the returns, which keeps
their fat tails. A cycle is reported as usable only when both agree. Its phase says whether the
cycle is pushing up or down now and how many bars to its next peak and trough.

Everything computed per bar uses that bar and the ones before it only.
"""

from __future__ import annotations

import calendar
import datetime as dt
import math
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from addons.trading.quant import riskmath, sessions

SYNODIC_DAYS = 29.530588853
NEW_MOON_TS = 947182440.0          # 6 January 2000, 18:14 UTC
WIB = ZoneInfo("Asia/Jakarta")
WINDOW = 128                       # bars the cycle is read from
MIN_PERIOD = 8                     # shorter "cycles" are bar noise
PERMUTATIONS = 199
P_LIMIT = 0.05
Q_LIMIT = 0.10
MIN_SLOT_ROWS = 8
SHRINK = 20.0                      # a slot's drift is believed in proportion n / (n + SHRINK)
CHUNK = 4096

# key, label, city, hour, minute, minutes before and after that count as "in the window"
EVENTS: tuple[tuple[str, str, str, int, int, int, int], ...] = (
    ("sydney_open", "Sydney buka", "sydney", 7, 0, 0, 60),
    ("tokyo_open", "Tokyo buka", "tokyo", 9, 0, 0, 60),
    ("london_open", "London buka", "london", 8, 0, 0, 120),
    ("gold_fix_am", "Fix emas pagi (LBMA)", "london", 10, 30, 10, 10),
    ("ny_open", "New York buka", "newyork", 8, 0, 0, 120),
    ("us_data", "Jam rilis data AS", "newyork", 8, 30, 5, 15),
    ("nyse_open", "Bursa saham AS buka", "newyork", 9, 30, 0, 30),
    ("gold_fix_pm", "Fix emas sore (LBMA)", "london", 15, 0, 10, 10),
    ("london_fix", "Fix London 16:00", "london", 16, 0, 15, 15),
    ("london_close", "London tutup", "london", 17, 0, 0, 30),
    ("rollover", "Rollover (17:00 New York)", "newyork", 17, 0, 15, 65),
)
EVENT_LABELS = {key: label for key, label, *_ in EVENTS}
PHASE_LABELS = {"weekend": "akhir pekan", "rollover": "rollover", "london_open": "pembukaan London",
                "ny_open": "pembukaan New York", "overlap": "London + New York", "london": "London",
                "newyork": "New York", "asia": "Asia", "off": "di luar sesi utama"}
CLOCK_COLUMNS = ("tm_ny_sin", "tm_ny_cos", "tm_london_open", "tm_ny_open", "tm_rollover", "tm_month_pos", "tm_month_end",
                 "tm_moon_sin", "tm_moon_cos")
SLOW_COLUMNS = ("tm_month_pos", "tm_month_end", "tm_moon_sin", "tm_moon_cos")
SEASONAL_COLUMNS = ("cy_seas_drift", "cy_seas_vol")
CYCLE_COLUMNS = ("cy_sin", "cy_cos", "cy_power")
FIBONACCI = (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233)


# ── the clocks ──────────────────────────────────────────────────────────────
def _second_of_day(ts: float, city: str) -> float:
    return sessions.local_seconds(ts, city) % 86400


def _weekday(ts: float, city: str) -> int:
    """0 Monday to 6 Sunday on the city's own calendar."""
    return (int(sessions.local_seconds(ts, city) // 86400) + 3) % 7


def minutes_since(ts: float, key: str) -> float:
    """Minutes since the event's time today on its own clock, from -720 (still to come) to 720."""
    _, _, city, hour, minute, _, _ = next(e for e in EVENTS if e[0] == key)
    delta = (_second_of_day(ts, city) - hour * 3600 - minute * 60) / 60.0
    return (delta + 720) % 1440 - 720


def windows(ts: float) -> list[str]:
    """The named moments ``ts`` falls in: an open's first hours, a fix, the data minute, the rollover."""
    if sessions.weekend(ts):
        return []
    out = []
    for key, _, city, _, _, before, after in EVENTS:
        if _weekday(ts, city) < 5 and -before <= minutes_since(ts, key) < after:
            out.append(key)
    return out


def phase(ts: float) -> str:
    """One word for the part of the day, for counting results by it."""
    if sessions.weekend(ts):
        return "weekend"
    now = windows(ts)
    for key in ("rollover", "london_open", "ny_open"):
        if key in now:
            return key
    live = sessions.active(ts)
    if "london" in live and "newyork" in live:
        return "overlap"
    if "london" in live:
        return "london"
    if "newyork" in live:
        return "newyork"
    return "asia" if live else "off"


def next_events(ts: float, limit: int = 4) -> list[dict[str, Any]]:
    """The next named moments, soonest first, skipping the weekend."""
    out: list[dict[str, Any]] = []
    for key, label, city, hour, minute, _, _ in EVENTS:
        at = ts + (hour * 3600 + minute * 60 - _second_of_day(ts, city)) % 86400
        for _ in range(4):
            if _weekday(at, city) < 5 and not sessions.weekend(at):
                break
            at += 86400
        out.append({"event": key, "label": label, "in_minutes": round((at - ts) / 60), "at": at})
    return sorted(out, key=lambda e: float(e["at"]))[:limit]


def moon_phase(ts: float) -> float:
    """0 new moon, 0.5 full, back to 1. Good to a few hours, which is all a daily effect could need."""
    return ((ts - NEW_MOON_TS) / 86400.0 / SYNODIC_DAYS) % 1.0


def moon_name(ts: float) -> str:
    p = moon_phase(ts)
    return ("bulan baru", "sabit awal", "paruh awal", "cembung awal", "purnama", "cembung akhir", "paruh akhir",
            "sabit akhir")[int((p + 1 / 16) * 8) % 8]


_month_cache: dict[int, tuple[float, float]] = {}


def _month_place(day_number: int) -> tuple[float, float]:
    """For a calendar day: how far through its month it is (0-1), and whether it is one of the last two weekdays."""
    if day_number not in _month_cache:
        if len(_month_cache) > 50_000:
            _month_cache.clear()
        day = dt.date(1970, 1, 1) + dt.timedelta(days=day_number)
        length = calendar.monthrange(day.year, day.month)[1]
        left = sum(1 for d in range(day.day + 1, length + 1) if dt.date(day.year, day.month, d).weekday() < 5)
        _month_cache[day_number] = ((day.day - 1) / max(1, length - 1), float(left <= 1 and day.weekday() < 5))
    return _month_cache[day_number]


def clock(ts: float) -> dict[str, Any]:
    """Every clock that matters at ``ts``, and what is about to happen."""
    utc = dt.datetime.fromtimestamp(ts, dt.UTC)
    gap = (sessions.utc_offset(ts, "london") - sessions.utc_offset(ts, "newyork")) / 3600.0
    place, month_end = _month_place(int(sessions.local_seconds(ts) // 86400))
    live = windows(ts)
    to_rollover = -minutes_since(ts, "rollover") % 1440
    return {
        "utc": utc.strftime("%a %H:%M"), "new_york": sessions._local(ts, "newyork").strftime("%a %H:%M"),
        "london": sessions._local(ts, "london").strftime("%a %H:%M"), "tokyo": sessions._local(ts, "tokyo").strftime("%a %H:%M"),
        "sydney": sessions._local(ts, "sydney").strftime("%a %H:%M"), "wib": utc.astimezone(WIB).strftime("%a %H:%M"),
        "session": sessions.label(ts), "phase": phase(ts), "phase_label": PHASE_LABELS[phase(ts)],
        "windows": live, "window_labels": [EVENT_LABELS[k] for k in live],
        "next": [{k: e[k] for k in ("event", "label", "in_minutes")} for e in next_events(ts)],
        "minutes_to_rollover": round(to_rollover),
        "london_ny_gap_hours": gap,
        "dst_note": ("" if gap == 5 else f"London dan New York sedang terpaut {gap:g} jam, bukan 5: jam musim panasnya belum "
                                         "sejajar, jadi tumpang tindih kedua sesi bergeser satu jam terhadap London"),
        "weekday": ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")[(sessions.trading_day(ts) + 3) % 7],
        "month_place": round(place, 2), "month_end": bool(month_end),
        "moon": {"phase": round(moon_phase(ts), 3), "name": moon_name(ts)},
    }


def clock_columns(times: np.ndarray) -> dict[str, np.ndarray]:
    """What the brain is told about the time at each bar's close.

    The New York clock as a point on a circle; hours into the London and New York opens (0 outside
    their first nine hours); closeness to the rollover (1 at 17:00 New York, 0 six hours away or
    more); and the slow calendars: place in the month, month end, and the moon's phase on a circle.
    """
    n = len(times)
    out = {name: np.zeros(n) for name in CLOCK_COLUMNS}
    for i, t in enumerate(times):
        t = float(t)
        ny = _second_of_day(t, "newyork") / 86400.0 * 2 * math.pi
        out["tm_ny_sin"][i], out["tm_ny_cos"][i] = math.sin(ny), math.cos(ny)
        for name, key in (("tm_london_open", "london_open"), ("tm_ny_open", "ny_open")):
            since = minutes_since(t, key) / 60.0
            out[name][i] = since / 9.0 if 0 <= since < 9 else 0.0
        out["tm_rollover"][i] = max(0.0, 1.0 - (-minutes_since(t, "rollover") % 1440) / 360.0)
        out["tm_month_pos"][i], out["tm_month_end"][i] = _month_place(int(sessions.local_seconds(t) // 86400))
        moon = moon_phase(t) * 2 * math.pi
        out["tm_moon_sin"][i], out["tm_moon_cos"][i] = math.sin(moon), math.cos(moon)
    return out


def session_bands(start: float, end: float) -> list[dict[str, Any]]:
    """When each session was open between two moments, for shading a chart. Weekends are left out."""
    out = []
    for key, _, _, open_hour, close_hour in sessions.SESSIONS:
        day = int(sessions.local_seconds(start, key) // 86400) - 1
        while True:
            opens = float(day * 86400 + open_hour * 3600)
            opens -= sessions.utc_offset(opens - sessions.utc_offset(opens, key), key)     # local wall clock back to UTC
            closes = opens + (close_hour - open_hour) * 3600
            if opens > end:
                break
            if closes > start and (day + 3) % 7 < 5 and not sessions.weekend(opens + 60):
                out.append({"key": key, "start": max(opens, start), "end": min(closes, end)})
            day += 1
    return sorted(out, key=lambda b: (b["start"], b["key"]))


def fib_count(bars_since: int) -> dict[str, Any]:
    """Fibonacci time: is the count of bars since the last swing on a Fibonacci number (8, 13, 21, 34...)?"""
    nearest = min(FIBONACCI, key=lambda f: abs(f - bars_since))
    return {"bars_since_swing": bars_since, "nearest_fibonacci": nearest,
            "on_count": bars_since >= 5 and abs(nearest - bars_since) <= (1 if nearest >= 13 else 0),
            "next_count": next((f for f in FIBONACCI if f > bars_since), None)}


# ── what each hour has done before ──────────────────────────────────────────
def _bar_stats(bars: list[dict[str, float]], atr: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each bar's move and range in the ATR known before it, and the New York hour it opened in."""
    c = np.array([b["c"] for b in bars], dtype=float)
    h = np.array([b["h"] for b in bars], dtype=float)
    lo = np.array([b["l"] for b in bars], dtype=float)
    if atr is None:
        prev = np.concatenate([[c[0]], c[:-1]])
        tr = np.maximum(h - lo, np.maximum(np.abs(h - prev), np.abs(lo - prev)))
        atr = np.empty_like(tr)
        acc = 0.0
        for i, value in enumerate(tr):
            acc = value if i == 0 else acc + (value - acc) / 14
            atr[i] = acc
    before = np.concatenate([[np.nan], np.maximum(atr[:-1], 1e-12)])
    move = np.concatenate([[np.nan], np.diff(c)]) / before
    width = (h - lo) / before
    slot = np.array([int(_second_of_day(float(b["t"]), "newyork") // 3600) for b in bars])
    return move, width, slot


def seasonal_columns(bars: list[dict[str, float]], seconds: int, atr: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """For each bar: what the hour the *next* bar opens in has done on earlier days.

    ``cy_seas_drift`` is that hour's t-statistic so far, shrunk towards zero while there are few
    days, and ``cy_seas_vol`` the log of how wide its bars run against the average bar.
    """
    n = len(bars)
    out = {name: np.zeros(n) for name in SEASONAL_COLUMNS}
    if n < 2:
        return out
    move, width, slot = _bar_stats(bars, atr)
    count, total, total_sq, wide = np.zeros(24), np.zeros(24), np.zeros(24), np.zeros(24)
    all_wide = all_n = 0.0
    for i in range(n):
        if move[i] == move[i]:
            s = slot[i]
            count[s] += 1
            total[s] += move[i]
            total_sq[s] += move[i] ** 2
            wide[s] += width[i]
            all_wide += width[i]
            all_n += 1
        nxt = int(_second_of_day(float(bars[i]["t"]) + seconds, "newyork") // 3600)
        k = count[nxt]
        if k >= MIN_SLOT_ROWS:
            mean = total[nxt] / k
            var = max(0.0, (total_sq[nxt] - k * mean * mean) / (k - 1))
            if var > 0:
                out["cy_seas_drift"][i] = max(-4.0, min(4.0, mean / math.sqrt(var / k) * k / (k + SHRINK)))
            if all_wide > 0 and wide[nxt] > 0:
                out["cy_seas_vol"][i] = math.log((wide[nxt] / k) / (all_wide / all_n))
    return out


def _group_table(move: np.ndarray, width: np.ndarray, keys: np.ndarray, labels: dict[int, str]) -> list[dict[str, Any]]:
    ok = np.isfinite(move)
    mean_width = float(width[ok].mean()) if ok.any() else 0.0
    rows = []
    for key in sorted(set(keys[ok].tolist())):
        pick = ok & (keys == key)
        k = int(pick.sum())
        values = move[pick]
        row: dict[str, Any] = {"key": int(key), "label": labels.get(int(key), str(key)), "bars": k,
                               "avg_move_atr": round(float(values.mean()), 4),
                               "width_ratio": round(float(width[pick].mean()) / mean_width, 3) if mean_width else None,
                               "up_share": round(float((values > 0).mean()), 3), "t_stat": None, "p_value": None}
        if k >= MIN_SLOT_ROWS and values.std(ddof=1) > 0:
            t = float(values.mean() / (values.std(ddof=1) / math.sqrt(k)))
            row["t_stat"], row["p_value"] = round(t, 2), round(riskmath.student_p(t, k - 1), 4)
        rows.append(row)
    tested = [r for r in rows if r["p_value"] is not None]
    for row, q in zip(tested, riskmath.benjamini_hochberg([r["p_value"] for r in tested]), strict=True):
        row["q_value"] = round(q, 4)
        row["significant"] = q < Q_LIMIT
    return rows


def seasonality(bars: list[dict[str, float]], seconds: int, now: float | None = None) -> dict[str, Any]:
    """Every New York hour and every weekday: the average move, its test, and how wide the bars run."""
    if len(bars) < 5 * MIN_SLOT_ROWS:
        return {"ok": False, "reason": f"butuh {5 * MIN_SLOT_ROWS} bar, ada {len(bars)}"}
    move, width, slot = _bar_stats(bars)
    hours = _group_table(move, width, slot, {h: f"{h:02d}:00 NY" for h in range(24)})
    weekday = np.array([(sessions.trading_day(float(b["t"])) + 3) % 7 for b in bars])
    days = _group_table(move, width, weekday, dict(enumerate(("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"))))
    now = float(bars[-1]["t"]) + seconds if now is None else now
    next_slot = int(_second_of_day(now, "newyork") // 3600)
    today = (sessions.trading_day(now) + 3) % 7
    found = [r for r in hours + days if r.get("significant")]
    span = (float(bars[-1]["t"]) - float(bars[0]["t"])) / 86400.0
    return {
        "ok": True, "bars": len(bars), "days": round(span, 1), "hours": hours, "weekdays": days,
        "next_hour": next((r for r in hours if r["key"] == next_slot), {"key": next_slot, "label": f"{next_slot:02d}:00 NY", "bars": 0}),
        "today": next((r for r in days if r["key"] == today), None),
        "significant": found, "tested": sum(1 for r in hours + days if r["p_value"] is not None),
        "verdict": (f"{len(found)} jam/hari lolos uji (q < {Q_LIMIT:g}) dari {sum(1 for r in hours + days if r['p_value'] is not None)} yang diuji"
                    if found else "tidak ada jam atau hari yang arahnya lolos uji: pakai lebarnya (volatilitas), bukan arahnya"),
    }


# ── is there a cycle ────────────────────────────────────────────────────────
def fisher_g_p(g: float, m: int) -> float:
    """Fisher's exact test: the chance the largest of ``m`` periodogram ordinates takes a share ``g`` or more by luck."""
    if m < 2 or g <= 1.0 / m:
        return 1.0
    total = 0.0
    for j in range(1, int(1.0 / g) + 1):
        total += (-1) ** (j - 1) * math.comb(m, j) * (1.0 - j * g) ** (m - 1)
    return max(0.0, min(1.0, total))


def _band(n: int) -> np.ndarray:
    """The frequency bins whose period is between MIN_PERIOD bars and half the window."""
    return np.arange(2, n // MIN_PERIOD + 1)


def _peak(returns: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each row of returns: the strongest bin in the band, its share of the band's power, and its coefficient."""
    centred = returns - returns.mean(axis=1, keepdims=True)
    spectrum = np.fft.rfft(centred, axis=1)
    band = _band(returns.shape[1])
    power = np.abs(spectrum[:, band]) ** 2
    total = power.sum(axis=1)
    best = power.argmax(axis=1)
    rows = np.arange(len(returns))
    share = np.where(total > 0, power[rows, best] / np.where(total > 0, total, 1.0), 0.0)
    return band[best], share, spectrum[rows, band[best]]


def _price_phase(coefficient: np.ndarray, k: np.ndarray, n: int) -> np.ndarray:
    """The price cycle's phase at the window's last bar: 0 at its peak, pi at its trough.

    A return component cos(w t + phi) sums to a price component sin(w t + phi + w / 2), a quarter turn behind.
    """
    w = 2 * math.pi * k / n
    out: np.ndarray = np.angle(coefficient) + w * (n - 1) + w / 2 - math.pi / 2
    return out


def dominant_cycle(closes: list[float] | np.ndarray, *, window: int = WINDOW, permutations: int = PERMUTATIONS,
                   seed: int = 0) -> dict[str, Any]:
    """The strongest cycle in the last ``window`` bars, and whether it is more than luck."""
    c = np.asarray(closes, dtype=float)
    if len(c) < window + 1:
        return {"ok": False, "reason": f"butuh {window + 1} bar, ada {len(c)}"}
    series = np.log(c[-window - 1:]) if np.all(c[-window - 1:] > 0) else c[-window - 1:]
    returns = np.diff(series)[None, :]
    if not np.any(returns):
        return {"ok": False, "reason": "harga tidak bergerak"}
    k, share, coefficient = _peak(returns)
    bins = len(_band(window))
    g, period = float(share[0]), window / float(k[0])
    p_fisher = fisher_g_p(g, bins)
    rng = np.random.default_rng(seed)
    shuffled = np.stack([rng.permutation(returns[0]) for _ in range(permutations)])
    _, null_share, _ = _peak(shuffled)
    p_perm = (1 + int((null_share >= g).sum())) / (permutations + 1)
    theta = float(_price_phase(coefficient, k, window)[0]) % (2 * math.pi)
    rising = math.sin(theta) < 0                      # the price component cos(theta) is going up
    usable = p_fisher < P_LIMIT and p_perm < P_LIMIT
    return {
        "ok": True, "window_bars": window, "period_bars": round(period, 1), "power_share": round(g, 4),
        "expected_share_if_none": round(float(null_share.mean()), 4),
        "p_fisher": round(p_fisher, 4), "p_permutation": round(p_perm, 4), "significant": usable,
        "phase": round(theta / (2 * math.pi), 3), "direction": "naik" if rising else "turun",
        "bars_to_peak": round((-theta) % (2 * math.pi) / (2 * math.pi) * period, 1),
        "bars_to_trough": round((math.pi - theta) % (2 * math.pi) / (2 * math.pi) * period, 1),
        "verdict": (f"siklus {period:.0f} bar lolos uji (p {max(p_fisher, p_perm):.3f}), sedang {'naik' if rising else 'turun'}"
                    if usable else f"tidak ada siklus yang lolos uji (p {max(p_fisher, p_perm):.2f}): abaikan fasenya"),
    }


def cycle_columns(closes: np.ndarray, window: int = WINDOW) -> dict[str, np.ndarray]:
    """For each bar: the phase of the strongest cycle in the ``window`` bars ending there, on a circle,
    and that cycle's share of the power (high means one period stands out)."""
    n = len(closes)
    out = {name: np.full(n, np.nan) for name in CYCLE_COLUMNS}
    if n < window + 1:
        return out
    c = np.asarray(closes, dtype=float)
    series = np.log(c) if np.all(c > 0) else c
    views = np.lib.stride_tricks.sliding_window_view(np.diff(series), window)       # row j ends at bar j + window
    for start in range(0, len(views), CHUNK):
        chunk = views[start:start + CHUNK]
        k, share, coefficient = _peak(chunk)
        theta = _price_phase(coefficient, k, window)
        at = slice(start + window, start + window + len(chunk))
        out["cy_sin"][at], out["cy_cos"][at], out["cy_power"][at] = np.sin(theta), np.cos(theta), share
    return out


# ── the picture now ─────────────────────────────────────────────────────────
def snapshot(bars: list[dict[str, float]], seconds: int, now: float) -> dict[str, Any]:
    """The clocks, the seasonal tables and the cycle, as of ``now``."""
    out: dict[str, Any] = {"clock": clock(now)}
    try:
        out["seasonality"] = seasonality(bars, seconds, now)
    except Exception as exc:  # noqa: BLE001 - odd data costs this block, not the caller
        out["seasonality"] = {"ok": False, "reason": f"error: {exc}"[:160]}
    try:
        out["cycle"] = dominant_cycle([b["c"] for b in bars])
    except Exception as exc:  # noqa: BLE001
        out["cycle"] = {"ok": False, "reason": f"error: {exc}"[:160]}
    return out


def compact(view: dict[str, Any]) -> dict[str, Any]:
    """The same in fewer tokens, for a prompt. What did not pass its test is said in one line, not listed."""
    c = view.get("clock", {})
    out: dict[str, Any] = {
        "clock": {k: c.get(k) for k in ("utc", "new_york", "london", "tokyo", "phase", "windows", "minutes_to_rollover", "weekday")},
        "next_events": c.get("next", [])[:3],
    }
    if c.get("dst_note"):
        out["clock"]["dst_note"] = c["dst_note"]
    if c.get("month_end"):
        out["clock"]["month_end"] = True
    season = view.get("seasonality") or {}
    if season.get("ok"):
        nxt = season.get("next_hour") or {}
        keys = ("label", "bars", "avg_move_atr", "width_ratio", "t_stat", "q_value", "significant")
        out["hour_ahead"] = {k: nxt.get(k) for k in keys if k in nxt}
        out["seasonality_verdict"] = season["verdict"]
        if season.get("significant"):
            out["seasonal_findings"] = [{k: r.get(k) for k in keys} for r in season["significant"][:3]]
    cycle = view.get("cycle") or {}
    if cycle.get("ok"):
        out["cycle"] = ({k: cycle[k] for k in ("period_bars", "direction", "bars_to_peak", "bars_to_trough", "p_permutation")}
                        if cycle["significant"] else {}) | {"significant": cycle["significant"], "verdict": cycle["verdict"]}
    return out


__all__ = ["EVENTS", "CLOCK_COLUMNS", "SLOW_COLUMNS", "SEASONAL_COLUMNS", "CYCLE_COLUMNS", "clock", "windows", "phase",
           "next_events", "minutes_since", "moon_phase", "clock_columns", "session_bands", "fib_count", "seasonal_columns",
           "seasonality", "fisher_g_p", "dominant_cycle", "cycle_columns", "snapshot", "compact", "PHASE_LABELS"]
