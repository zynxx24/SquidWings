"""Price levels: Fibonacci, pivot points, yesterday's range, round numbers, and where they meet.

None of these predicts anything. A level is a price many people watch, so it marks a *zone*
for an entry, a stop or a target, and it matters more where levels of different kinds fall on
the same price (confluence): a 61.8% retracement sitting on S1, say.

The formulas:

* **Fibonacci** on the last finished swing leg (low L to high H for an up leg):
  retracement ``H - (H - L) x r`` for r in 0.236, 0.382, 0.5, 0.618, 0.786, and extension
  ``L + (H - L) x e`` for e in 1.272, 1.618, 2.618. A down leg mirrors both.
* **Pivot points** from the previous trading day's high, low and close:
  classic ``P = (H + L + C) / 3``, ``R1 = 2P - L``, ``S1 = 2P - H``, ``R2 = P + (H - L)``,
  ``S2 = P - (H - L)``, ``R3 = H + 2(P - L)``, ``S3 = L - 2(H - P)``; Fibonacci pivots
  ``P +- 0.382 / 0.618 / 1.0 x (H - L)``; Camarilla ``C +- (H - L) x 1.1 / 4`` (R3, S3) and
  ``x 1.1 / 2`` (R4, S4); Woodie ``P = (H + L + 2C) / 4``.

Two choices make them usable by a machine:

* **The day is the forex day**, 17:00 to 17:00 in New York (``sessions.trading_day``), not the UTC
  day: that is the high, low and close the market itself pivots on, and it moves with daylight saving.
* **Swings are confirmed, never guessed.** A swing high exists only once the price has fallen
  ``SWING_ATR`` ATR from it (a zigzag), so every level at bar t uses bars up to t only and the
  brain can learn from them without seeing the future.

A zone's strength is how many *kinds* of level meet there, out of four: pivot (any pivot
formula), fib (a swing retracement or extension), range (yesterday's or last week's high, low,
close, today's open) and round (a big figure). The four pivot formulas all come from the same
three numbers, so two of them agreeing is arithmetic, not evidence; they count once.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import numpy as np

from addons.trading.quant import sessions

RETRACEMENTS = (0.236, 0.382, 0.5, 0.618, 0.786)
EXTENSIONS = (1.272, 1.618, 2.618)
PIVOT_KINDS = ("classic", "fibonacci", "camarilla", "woodie")
GROUPS = ("pivot", "fib", "range", "round")
SWING_ATR = 3.0            # a swing is confirmed once the price has come back this many ATR from it
ZONE_TOL = 0.25            # levels within this many ATR of each other are one zone
FAR = 6.0                  # "no zone in sight", in ATR
CHUNK = 4096


# ── the formulas ────────────────────────────────────────────────────────────
def fib_retracements(high: float, low: float, *, up: bool = True) -> dict[str, float]:
    """Where a pullback of the leg may stop. Up leg (low to high): measured down from the high."""
    span = high - low
    return {f"{r * 100:g}%": (high - span * r) if up else (low + span * r) for r in RETRACEMENTS}


def fib_extensions(high: float, low: float, *, up: bool = True) -> dict[str, float]:
    """Targets beyond the leg's end. Up leg: ``low + span x ratio``."""
    span = high - low
    return {f"{e * 100:g}%": (low + span * e) if up else (high - span * e) for e in EXTENSIONS}


def pivots(high: float, low: float, close: float, kind: str = "classic") -> dict[str, float]:
    """Pivot levels for the day after the one with this high, low and close."""
    span = high - low
    if kind == "classic":
        p = (high + low + close) / 3
        return {"P": p, "R1": 2 * p - low, "S1": 2 * p - high, "R2": p + span, "S2": p - span,
                "R3": high + 2 * (p - low), "S3": low - 2 * (high - p)}
    if kind == "fibonacci":
        p = (high + low + close) / 3
        return {"P": p, "R1": p + 0.382 * span, "R2": p + 0.618 * span, "R3": p + span,
                "S1": p - 0.382 * span, "S2": p - 0.618 * span, "S3": p - span}
    if kind == "camarilla":
        return {"R3": close + span * 1.1 / 4, "R4": close + span * 1.1 / 2, "S3": close - span * 1.1 / 4,
                "S4": close - span * 1.1 / 2}
    if kind == "woodie":
        p = (high + low + 2 * close) / 4
        return {"P": p, "R1": 2 * p - low, "S1": 2 * p - high, "R2": p + span, "S2": p - span}
    raise ValueError(f"kind: {', '.join(PIVOT_KINDS)}")


def round_step(price: float) -> float:
    """The spacing of the big figures near a price: 0.01 at 1.08, 1 at 150, 50 at 2650, 500 at 95000."""
    if not price or price <= 0 or not math.isfinite(price):
        return 0.0
    power = 10.0 ** math.floor(math.log10(price))
    return power / 100 * (5 if price / power >= 2 else 1)


# ── per-bar frames: the previous day and week, and the confirmed swings ─────
def day_frames(bars: list[dict[str, float]]) -> dict[str, np.ndarray]:
    """For every bar: the previous forex day's high, low and close, this day's open, and last week's high and low."""
    n = len(bars)
    out = {k: np.full(n, np.nan) for k in ("prev_h", "prev_l", "prev_c", "day_open", "week_h", "week_l")}
    day_id = np.zeros(n, dtype=np.int64)
    day = week = None
    y_h = y_l = y_c = w_h = w_l = math.nan
    cur_o = cur_h = cur_l = wk_h = wk_l = last_c = math.nan
    for i, b in enumerate(bars):
        d = sessions.trading_day(float(b["t"]))
        if d != day:
            if day is not None:
                y_h, y_l, y_c = cur_h, cur_l, last_c
            wk = (d + 3) // 7                                  # day 4 was a Monday: weeks run Monday to Friday
            if wk != week:
                if week is not None:
                    w_h, w_l = wk_h, wk_l
                week, wk_h, wk_l = wk, b["h"], b["l"]
            day, cur_o, cur_h, cur_l = d, b["o"], b["h"], b["l"]
        cur_h, cur_l = max(cur_h, b["h"]), min(cur_l, b["l"])
        wk_h, wk_l = max(wk_h, b["h"]), min(wk_l, b["l"])
        last_c = b["c"]
        day_id[i] = d
        out["prev_h"][i], out["prev_l"][i], out["prev_c"][i] = y_h, y_l, y_c
        out["day_open"][i], out["week_h"][i], out["week_l"][i] = cur_o, w_h, w_l
    out["day_id"] = day_id
    return out


def zigzag(h: np.ndarray, lo: np.ndarray, atr: np.ndarray, k: float = SWING_ATR) -> dict[str, np.ndarray]:
    """The confirmed swing pivots as known at every bar.

    ``last_*`` is the newest confirmed pivot (``last_kind`` +1 a high, -1 a low), ``prev_*`` the one
    before it and ``prev2_p`` the one before that. A high is confirmed on the bar where the low has
    come ``k`` ATR down from it, so nothing here looks ahead.
    """
    n = len(h)
    out = {name: np.full(n, np.nan) for name in ("last_p", "last_i", "last_kind", "prev_p", "prev_i", "prev2_p")}
    if not n:
        return out
    direction = 0
    hi_p, hi_i, lo_p, lo_i = h[0], 0, lo[0], 0
    last = prev = prev2 = None                       # (price, index, kind)
    for i in range(n):
        reach = k * atr[i] if atr[i] == atr[i] else math.inf
        if direction == 0:                           # no swing yet: wait for the first move of k ATR either way
            if h[i] > hi_p:
                hi_p, hi_i = h[i], i
            if lo[i] < lo_p:
                lo_p, lo_i = lo[i], i
            if hi_p - lo[i] >= reach and hi_i < i:
                prev = (lo_p, lo_i, -1) if lo_i < hi_i else None
                last, direction, lo_p, lo_i = (hi_p, hi_i, 1), -1, lo[i], i
            elif h[i] - lo_p >= reach and lo_i < i:
                prev = (hi_p, hi_i, 1) if hi_i < lo_i else None
                last, direction, hi_p, hi_i = (lo_p, lo_i, -1), 1, h[i], i
        elif direction < 0:                          # falling from the last high
            if lo[i] < lo_p:
                lo_p, lo_i = lo[i], i
            elif h[i] - lo_p >= reach:
                prev2, prev, last = prev, last, (lo_p, lo_i, -1)
                direction, hi_p, hi_i = 1, h[i], i
        else:                                        # rising from the last low
            if h[i] > hi_p:
                hi_p, hi_i = h[i], i
            elif hi_p - lo[i] >= reach:
                prev2, prev, last = prev, last, (hi_p, hi_i, 1)
                direction, lo_p, lo_i = -1, lo[i], i
        if last is not None:
            out["last_p"][i], out["last_i"][i], out["last_kind"][i] = last
        if prev is not None:
            out["prev_p"][i], out["prev_i"][i] = prev[0], prev[1]
        if prev2 is not None:
            out["prev2_p"][i] = prev2[0]
    return out


def _atr(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    prev = np.concatenate([[c[0]], c[:-1]]) if len(c) else c
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev), np.abs(lo - prev)))
    out = np.empty_like(tr)
    acc = 0.0
    for i, value in enumerate(tr):
        acc = value if i == 0 else acc + (value - acc) / n
        out[i] = acc
    result: np.ndarray = np.maximum(out, 1e-12)
    return result


def _arrays(bars: list[dict[str, float]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (np.array([b["h"] for b in bars], dtype=float), np.array([b["l"] for b in bars], dtype=float),
            np.array([b["c"] for b in bars], dtype=float))


# ── every level at every bar ────────────────────────────────────────────────
def level_matrix(bars: list[dict[str, float]], atr: np.ndarray | None = None
                 ) -> tuple[np.ndarray, list[str], np.ndarray, dict[str, np.ndarray]]:
    """All levels known at each bar: (rows, levels) prices (NaN where not known yet), their names,
    each one's group as an index into ``GROUPS``, and the frames they were built from."""
    h, lo, c = _arrays(bars)
    atr = _atr(h, lo, c) if atr is None else atr
    frames = day_frames(bars) | zigzag(h, lo, atr)
    ph, pl, pc = frames["prev_h"], frames["prev_l"], frames["prev_c"]
    span = ph - pl
    cols: list[tuple[str, int, np.ndarray]] = []
    p = (ph + pl + pc) / 3
    for name, values in (("P", p), ("R1", 2 * p - pl), ("S1", 2 * p - ph), ("R2", p + span), ("S2", p - span),
                         ("R3", ph + 2 * (p - pl)), ("S3", pl - 2 * (ph - p))):
        cols.append((name, 0, values))
    for name, mult in (("R1", 0.382), ("R2", 0.618), ("R3", 1.0), ("S1", -0.382), ("S2", -0.618), ("S3", -1.0)):
        cols.append((f"Fib-pivot {name}", 0, p + mult * span))
    for name, mult in (("R3", 1.1 / 4), ("R4", 1.1 / 2), ("S3", -1.1 / 4), ("S4", -1.1 / 2)):
        cols.append((f"Camarilla {name}", 0, pc + mult * span))
    cols.append(("Woodie P", 0, (ph + pl + 2 * pc) / 4))
    # the last finished leg runs prev -> last; a retracement is measured back from its end, an extension from its start
    last, prev, prev2 = frames["last_p"], frames["prev_p"], frames["prev2_p"]
    leg = last - prev
    for r in RETRACEMENTS:
        cols.append((f"Fibo {r * 100:g}%", 1, last - leg * r))
    for e in EXTENSIONS:
        cols.append((f"Fibo ext {e * 100:g}%", 1, prev + leg * e))
    older = prev - prev2
    for r in RETRACEMENTS:
        cols.append((f"Fibo lama {r * 100:g}%", 1, prev - older * r))
    for name, values in (("High kemarin", ph), ("Low kemarin", pl), ("Close kemarin", pc), ("Open hari ini", frames["day_open"]),
                         ("High minggu lalu", frames["week_h"]), ("Low minggu lalu", frames["week_l"])):
        cols.append((name, 2, values))
    step = np.array([round_step(float(x)) for x in c])
    with np.errstate(invalid="ignore", divide="ignore"):
        below = np.where(step > 0, np.floor(c / np.where(step > 0, step, 1)) * step, np.nan)
    cols.append(("Angka bulat bawah", 3, below))
    cols.append(("Angka bulat atas", 3, below + step))
    matrix = np.column_stack([v for _, _, v in cols]) if len(c) else np.zeros((0, len(cols)))
    frames["atr"], frames["close"] = atr, c
    return matrix, [name for name, _, _ in cols], np.array([g for _, g, _ in cols]), frames


def strengths(matrix: np.ndarray, groups: np.ndarray, atr: np.ndarray, tol: float = ZONE_TOL) -> np.ndarray:
    """For each level at each bar: how many kinds of level (1-4) lie within ``tol`` ATR of it, itself included."""
    out = np.zeros(matrix.shape, dtype=np.int8)
    for start in range(0, len(matrix), CHUNK):
        m = matrix[start:start + CHUNK]
        with np.errstate(invalid="ignore"):
            near = np.abs(m[:, :, None] - m[:, None, :]) <= (tol * atr[start:start + CHUNK])[:, None, None]
        total = np.zeros(m.shape, dtype=np.int8)
        for g in range(len(GROUPS)):
            total += near[:, :, groups == g].any(axis=2)
        out[start:start + CHUNK] = np.where(np.isfinite(m), total, 0)
    return out


COLUMNS = ("lv_pp", "lv_cam", "lv_fib_frac", "lv_fib_dir", "lv_fib_leg", "lv_fib_near", "lv_swing_age", "lv_zone_up",
           "lv_zone_down", "lv_zone_here")


def feature_columns(bars: list[dict[str, float]], atr: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """What the brain is told about levels at each bar's close, every distance in ATR.

    ``lv_pp`` distance to the daily pivot, ``lv_cam`` place in the Camarilla band (+-1 at R3/S3, +-2 at
    R4/S4), ``lv_fib_frac`` how much of the last leg has been retraced (1 = all of it),
    ``lv_fib_dir`` which way that leg went, ``lv_fib_leg`` its size, ``lv_fib_near`` the distance to the
    nearest Fibonacci level, ``lv_swing_age`` how long ago the last swing was confirmed,
    ``lv_zone_up`` / ``lv_zone_down`` the room to the nearest confluence zone above and below, and
    ``lv_zone_here`` the strength of the zone the price is standing in.
    """
    n = len(bars)
    if not n:
        return {name: np.zeros(0) for name in COLUMNS}
    matrix, names, groups, f = level_matrix(bars, atr)
    a, c = f["atr"], f["close"]
    strength = strengths(matrix, groups, a)
    out: dict[str, np.ndarray] = {}
    with np.errstate(invalid="ignore", divide="ignore"):
        out["lv_pp"] = (c - matrix[:, 0]) / a
        band = (f["prev_h"] - f["prev_l"]) * 1.1 / 4
        out["lv_cam"] = np.clip((c - f["prev_c"]) / np.where(band > 0, band, np.nan), -4, 4)
        leg = f["last_p"] - f["prev_p"]
        out["lv_fib_frac"] = np.clip((f["last_p"] - c) / np.where(leg != 0, leg, np.nan), -0.5, 2.0)
        out["lv_fib_dir"] = np.sign(leg)
        out["lv_fib_leg"] = np.abs(leg) / a
        fib = matrix[:, [i for i, name in enumerate(names) if name.startswith("Fibo ")]]      # both legs, and the extensions
        gap = c[:, None] - fib
        gap_abs = np.where(np.isfinite(gap), np.abs(gap), np.inf)
        nearest = np.argmin(gap_abs, axis=1)
        picked = gap[np.arange(n), nearest]
        out["lv_fib_near"] = np.where(np.isfinite(gap_abs.min(axis=1)), picked, np.nan) / a
        out["lv_swing_age"] = np.log1p(np.arange(n) - f["last_i"]) / math.log(101)
        strong = (strength >= 2) & np.isfinite(matrix)
        dist = (matrix - c[:, None]) / a[:, None]
        edge = ZONE_TOL
        out["lv_zone_up"] = np.minimum(np.where(strong & (dist > edge), dist, FAR).min(axis=1), FAR)
        out["lv_zone_down"] = np.minimum(np.where(strong & (dist < -edge), -dist, FAR).min(axis=1), FAR)
        here = np.isfinite(dist) & (np.abs(dist) <= edge)
        out["lv_zone_here"] = np.where(here, strength, 0).max(axis=1) / len(GROUPS)
    return out


# ── the picture now, for the LLM and the panel ──────────────────────────────
def zones(prices: list[tuple[float, str, int]], atr: float, tol: float = ZONE_TOL) -> list[dict[str, Any]]:
    """Group levels that sit within ``tol`` ATR of each other. Each zone: its span, its names, its strength."""
    out: list[dict[str, Any]] = []
    width = tol * atr
    for price, name, group in sorted(p for p in prices if math.isfinite(p[0])):
        zone = out[-1] if out else None
        if zone is not None and price - zone["high"] <= width and price - zone["low"] <= 2 * width:
            zone["high"] = price
            zone["names"].append(name)
            zone["groups"].add(GROUPS[group])
        else:
            out.append({"low": price, "high": price, "names": [name], "groups": {GROUPS[group]}})
    for zone in out:
        zone["price"] = (zone["low"] + zone["high"]) / 2
        zone["strength"] = len(zone["groups"])
        zone["groups"] = sorted(zone["groups"])
    return out


def snapshot(bars: list[dict[str, float]], *, price: float | None = None, keep: int = 4, digits: int = 5) -> dict[str, Any]:
    """Every level as of the last bar, and the zones around ``price`` (the last close when not given)."""
    if len(bars) < 30:
        return {"ok": False, "reason": f"butuh 30 bar, ada {len(bars)}"}
    matrix, names, groups, f = level_matrix(bars)
    row, atr = matrix[-1], float(f["atr"][-1])
    px = float(f["close"][-1]) if price is None else float(price)
    rnd = lambda v: round(float(v), digits)  # noqa: E731
    out: dict[str, Any] = {"ok": True, "price": rnd(px), "atr": round(atr, digits + 1)}
    ph, pl, pc = float(f["prev_h"][-1]), float(f["prev_l"][-1]), float(f["prev_c"][-1])
    if math.isfinite(ph):
        day = dt.date(1970, 1, 1) + dt.timedelta(days=int(f["day_id"][-1]))
        out["pivot"] = {"trading_day": day.isoformat(), "day_boundary": "17:00 New York",
                        "previous": {"high": rnd(ph), "low": rnd(pl), "close": rnd(pc)},
                        **{kind: {k: rnd(v) for k, v in pivots(ph, pl, pc, kind).items()} for kind in PIVOT_KINDS}}
        band = (ph - pl) * 1.1 / 4
        place = (px - pc) / band if band > 0 else 0.0
        out["pivot"]["camarilla_place"] = round(place, 2)
        out["pivot"]["side_of_pivot"] = "di atas P" if px > out["pivot"]["classic"]["P"] else "di bawah P"
    last, prev = float(f["last_p"][-1]), float(f["prev_p"][-1])
    if math.isfinite(last) and math.isfinite(prev) and last != prev:
        up = last > prev
        high, low = max(last, prev), min(last, prev)
        retraced = (last - px) / (last - prev)
        out["fib"] = {"leg": "naik" if up else "turun", "from": rnd(prev), "to": rnd(last),
                      "leg_atr": round(abs(last - prev) / atr, 2), "retraced": round(retraced, 3),
                      "bars_since_swing": int(len(bars) - 1 - f["last_i"][-1]),
                      "retracements": {k: rnd(v) for k, v in fib_retracements(high, low, up=up).items()},
                      "extensions": {k: rnd(v) for k, v in fib_extensions(high, low, up=up).items()},
                      "read": ("leg sudah ditembus balik seluruhnya" if retraced >= 1 else
                               "harga melampaui ujung leg (lanjutan)" if retraced < 0 else
                               "koreksi dangkal (< 38,2%)" if retraced < 0.382 else
                               "zona emas 50-61,8%" if 0.5 <= retraced <= 0.618 else
                               "koreksi dalam (> 61,8%)" if retraced > 0.618 else "koreksi sedang (38,2-50%)")}
    found = zones([(float(row[i]), names[i], int(groups[i])) for i in range(len(names))], atr)
    for zone in found:
        zone["dist_atr"] = round((zone["price"] - px) / atr, 2)
        for key in ("low", "high", "price"):
            zone[key] = rnd(zone[key])
    edge = ZONE_TOL * atr
    above = [z for z in found if z["low"] - px > edge]
    below = [z for z in found if px - z["high"] > edge][::-1]
    away = {id(z) for z in above} | {id(z) for z in below}
    here = [z for z in found if id(z) not in away]
    strong_up = next((z for z in above if z["strength"] >= 2), None)
    strong_down = next((z for z in below if z["strength"] >= 2), None)
    out.update(
        zones_above=above[:keep], zones_below=below[:keep],
        at_zone=max(here, key=lambda z: z["strength"]) if here else None,
        room_up_atr=round(strong_up["dist_atr"], 2) if strong_up else None,
        room_down_atr=round(-strong_down["dist_atr"], 2) if strong_down else None,
        zones=[z for z in found if abs(z["dist_atr"]) <= 8],
    )
    return out


def compact(view: dict[str, Any], keep: int = 3) -> dict[str, Any]:
    """The same picture in fewer tokens, for a prompt."""
    if not view.get("ok"):
        return {"ok": False, "reason": view.get("reason", "")}

    def short(zone: dict[str, Any]) -> dict[str, Any]:
        return {"price": zone["price"], "dist_atr": zone["dist_atr"], "strength": zone["strength"], "what": zone["names"][:3]}

    out: dict[str, Any] = {"ok": True, "zone_strength_scale": "1-4 kinds of level meeting; 2+ is confluence",
                           "above": [short(z) for z in view["zones_above"][:keep]],
                           "below": [short(z) for z in view["zones_below"][:keep]],
                           "at_zone": short(view["at_zone"]) if view.get("at_zone") else None,
                           "room_up_atr": view.get("room_up_atr"), "room_down_atr": view.get("room_down_atr")}
    if view.get("pivot"):
        piv = view["pivot"]
        out["pivot"] = {"P": piv["classic"]["P"], "side": piv["side_of_pivot"], "camarilla_place": piv["camarilla_place"],
                        "day_boundary": piv["day_boundary"]}
    if view.get("fib"):
        fib = view["fib"]
        out["fib"] = {k: fib[k] for k in ("leg", "from", "to", "leg_atr", "retraced", "read", "bars_since_swing")}
        out["fib"]["targets"] = fib["extensions"]
    return out


def between(view: dict[str, Any], start: float, end: float, *, min_strength: int = 2) -> list[dict[str, Any]]:
    """The confluence zones a move from ``start`` to ``end`` has to pass through, nearest first."""
    lo, hi = min(start, end), max(start, end)
    hit = [z for z in view.get("zones", []) if z["strength"] >= min_strength and lo < z["price"] < hi]
    return sorted(hit, key=lambda z: abs(z["price"] - start))


__all__ = ["RETRACEMENTS", "EXTENSIONS", "PIVOT_KINDS", "GROUPS", "COLUMNS", "fib_retracements", "fib_extensions", "pivots",
           "round_step", "day_frames", "zigzag", "level_matrix", "strengths", "feature_columns", "zones", "snapshot",
           "compact", "between"]
