"""What a trader checks before a trade, gathered once per bar: levels, time, cycles, and the risk arithmetic.

``considerations`` is the full picture (``levels.snapshot`` + ``cycles.snapshot``), which the panel
draws on the chart. ``compact`` is the same in a prompt's worth of tokens. The rest is how the
LLM learns from it:

* ``tags`` names the conditions a plan was made in (which part of the day, buying into a
  resistance zone or off support, with or against the pivot and the cycle, how deep the pullback
  was). The analyst's journal stores them with each plan, and ``record_like_now`` reads back how
  plans with today's tags ended, so "my buys into resistance: 1 of 7 reached the target" is a
  number in the next prompt and not something the model has to notice by itself.
* ``plan_checks`` reads a finished plan against the same picture and says what a careful trader
  would: the spread is a large part of the stop, the brain's chance is under break-even for this
  reward to risk, a confluence zone stands between the entry and the target, the stop sits just in
  front of a zone instead of behind it, the plan runs into the rollover or the weekend. They are
  warnings on the card, and their codes are tags too, so the journal shows what ignoring them cost.

Nothing here opens, sizes or blocks a trade.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

from trading.quant import cycles, levels, riskmath, sessions

RISK_FIRST = (
    "Before a side, in this order: (1) risk: 'plan_math' gives each plan's reward to risk, the target rate that only "
    "breaks even (p_target_needed), and the brain's edge over it; a plan whose p_target is not above "
    "p_target_needed is a losing plan however good the chart looks. "
)
LEVELS_AND_TIME = (
    "(2) 'levels': zones where pivot points, Fibonacci "
    "levels of the last swing, yesterday's and last week's range and round numbers meet, with strength 1-4 (2+ is "
    "confluence) and distance in ATR. Levels are zones to act at, not predictions: put the stop behind the nearest "
    "zone, not in front of it; do not set a target beyond a strength 2+ zone unless you say why it will break; buying "
    "just under such a zone or selling just above one needs room_up_atr / room_down_atr larger than your target. "
    "(3) 'time': the market's own clocks. Spreads widen at the rollover (17:00 New York) and around the US data "
    "minute; the opens of London and New York move most. 'hour_ahead' is what this hour did on past days: trust "
    "width_ratio (how wide its bars run), and trust its direction only when significant is true. 'cycle' appears with "
    "numbers only when it passed its test; otherwise ignore it. "
)
BASICS = RISK_FIRST + LEVELS_AND_TIME

LEVEL_BARS = 1500         # the swings and yesterday's range need this much history at most
_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
_cache_lock = threading.Lock()


def considerations(bars: list[dict[str, float]], seconds: int, now: float, *, price: float | None = None,
                   digits: int = 5, key: tuple[Any, ...] | None = None) -> dict[str, Any]:
    """Levels, clocks, seasonality and the cycle as of ``now``, from the closed bars of one timeframe.

    With ``key`` (say the symbol and timeframe) the tables are worked out once per closed bar and only
    the clock and the price-relative parts are refreshed in between.
    """
    stamp = (key, len(bars), bars[-1]["t"] if bars else 0) if key is not None else None
    with _cache_lock:
        cached = _cache.get(stamp) if stamp is not None else None
    if cached is not None:
        slow = cached
    else:
        slow = cycles.snapshot(bars, seconds, now)
        if stamp is not None:
            with _cache_lock:
                if len(_cache) > 64:
                    _cache.clear()
                _cache[stamp] = slow
    try:
        lv = levels.snapshot(bars[-LEVEL_BARS:], price=price, digits=digits)
    except Exception as exc:  # noqa: BLE001 - odd bars cost the levels, not the caller
        lv = {"ok": False, "reason": f"error: {exc}"[:160]}
    out: dict[str, Any] = {"ok": True, "as_of": now, "seconds": seconds, "levels": lv, "clock": cycles.clock(now),
                           "seasonality": slow.get("seasonality"), "cycle": slow.get("cycle")}
    if lv.get("fib"):
        out["fib_time"] = cycles.fib_count(int(lv["fib"]["bars_since_swing"]))
    return out


def compact(view: dict[str, Any] | None) -> dict[str, Any]:
    """Two prompt blocks: ``levels`` and ``time``."""
    if not view or not view.get("ok"):
        return {}
    time_block = cycles.compact(view)
    if view.get("fib_time"):
        time_block["fib_time"] = view["fib_time"]
    return {"levels": levels.compact(view.get("levels") or {}), "time": time_block}


# ── the conditions a plan was made in ───────────────────────────────────────
NEAR_ATR = 1.0


def _fib_word(retraced: float | None) -> str | None:
    if retraced is None:
        return None
    return ("leg ditembus" if retraced >= 1 else "lanjutan" if retraced < 0 else "dangkal" if retraced < 0.382
            else "zona emas" if 0.5 <= retraced <= 0.618 else "dalam" if retraced > 0.618 else "sedang")


def tags(view: dict[str, Any] | None, side: str) -> dict[str, str]:
    """Names for the conditions a ``side`` plan is made in now. Empty when there is no picture."""
    if not view or not view.get("ok") or side not in ("buy", "sell"):
        return {}
    out: dict[str, str] = {"waktu": (view.get("clock") or {}).get("phase", "")}
    lv = view.get("levels") or {}
    if lv.get("ok"):
        ahead, behind = (lv.get("room_up_atr"), lv.get("room_down_atr")) if side == "buy" else (lv.get("room_down_atr"), lv.get("room_up_atr"))
        at_zone = (lv.get("at_zone") or {}).get("strength", 0) >= 2
        if ahead is not None and ahead < NEAR_ATR:
            out["zona"] = "menuju zona dekat"            # buying just under resistance, or selling just above support
        elif at_zone or (behind is not None and behind < NEAR_ATR):
            out["zona"] = "dari zona"                    # buying off support, or selling off resistance
        else:
            out["zona"] = "ruang terbuka"
        pivot = lv.get("pivot") or {}
        if pivot:
            above = pivot.get("side_of_pivot") == "di atas P"
            out["pivot"] = "searah pivot" if above == (side == "buy") else "melawan pivot"
        fib = lv.get("fib") or {}
        word = _fib_word(fib.get("retraced"))
        if word:
            with_leg = (fib.get("leg") == "naik") == (side == "buy")
            out["fibo"] = f"{'searah' if with_leg else 'melawan'} leg, koreksi {word}"
    cycle = view.get("cycle") or {}
    if cycle.get("ok") and cycle.get("significant"):
        out["siklus"] = "searah siklus" if (cycle["direction"] == "naik") == (side == "buy") else "melawan siklus"
    return {k: v for k, v in out.items() if v}


def record_like_now(plans: list[dict[str, Any]], now_tags: dict[str, str], *, closed: tuple[str, ...], min_plans: int = 3
                    ) -> list[dict[str, Any]]:
    """How earlier plans made under each of today's conditions ended. Only conditions seen ``min_plans`` times."""
    out = []
    for name, value in now_tags.items():
        same = [p for p in plans if p.get("status") in closed and (p.get("tags") or {}).get(name) == value]
        if len(same) < min_plans:
            continue
        hits = sum(p["status"] in ("target", "trail") for p in same)
        results = [p.get("result_r") or 0.0 for p in same]
        out.append({"condition": f"{name}: {value}", "plans": len(same), "targets": hits,
                    "target_rate_cautious": round(riskmath.beta_lower(hits, len(same)), 3),
                    "avg_r": round(sum(results) / len(results), 3)})
    return out


def by_tag(plans: list[dict[str, Any]], *, closed: tuple[str, ...], min_plans: int = 3) -> list[dict[str, Any]]:
    """The whole journal split by condition, for the panel: where the analyst makes and loses."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for p in plans:
        if p.get("status") in closed:
            for name, value in (p.get("tags") or {}).items():
                groups[(name, value)].append(p)
            if p.get("check_codes") is not None:
                groups[("peringatan", "ada" if p["check_codes"] else "tidak ada")].append(p)
    out = []
    for (name, value), items in sorted(groups.items()):
        if len(items) < min_plans:
            continue
        hits = sum(p["status"] in ("target", "trail") for p in items)
        results = [p.get("result_r") or 0.0 for p in items]
        out.append({"name": name, "value": value, "plans": len(items), "target_rate": round(hits / len(items), 3),
                    "target_rate_cautious": round(riskmath.beta_lower(hits, len(items)), 3),
                    "avg_r": round(sum(results) / len(results), 3), "sum_r": round(sum(results), 3)})
    return out


# ── reading a finished plan the way a careful trader would ──────────────────
SPREAD_SHARE = 0.15       # of the stop distance
BLOCK_SHARE = 0.8         # a zone this far along the way to the target or nearer is "in the way"
STOP_FRONT_ATR = 0.5


def plan_checks(plan: dict[str, Any], view: dict[str, Any] | None) -> list[dict[str, str]]:
    """Warnings for a plan with prices. Each is ``{"code", "text"}``; the text is for the card."""
    out: list[dict[str, str]] = []
    if plan.get("entry") is None or plan.get("stop") is None:
        return out
    entry, stop, target = float(plan["entry"]), float(plan["stop"]), plan.get("target")
    atr = float(plan.get("atr") or 0.0)
    risk = abs(entry - stop)
    spread = max(0.0, float(plan.get("ask") or 0.0) - float(plan.get("bid") or 0.0))
    if risk > 0 and spread > SPREAD_SHARE * risk:
        out.append({"code": "spread", "text": f"Spread {spread / risk:.0%} dari jarak stop: biaya ini dibayar di setiap trade."})
    math_ = plan.get("math") or {}
    if math_.get("edge") is not None and math_["edge"] <= 0:
        out.append({"code": "impas", "text": f"P(TP) otak {math_['p_target']:.0%} tidak di atas titik impas "
                                             f"{math_['p_target_needed']:.0%} untuk R:R 1:{math_['rr']:g}."})
    lv = (view or {}).get("levels") or {}
    if lv.get("ok") and atr > 0:
        if target is not None:
            for zone in levels.between(lv, entry, float(target)):
                share = abs(zone["price"] - entry) / abs(float(target) - entry)
                if share <= BLOCK_SHARE:
                    out.append({"code": "zona_menghalang",
                                "text": f"Zona {' + '.join(zone['names'][:2])} (kekuatan {zone['strength']}) di {zone['price']} "
                                        f"menghalang, {abs(zone['price'] - entry) / atr:.1f} ATR dari entry, sebelum target."})
                    break
        if not levels.between(lv, entry, stop):
            side = 1 if stop < entry else -1
            beyond = [z for z in lv.get("zones", []) if z["strength"] >= 2 and 0 < (stop - z["price"]) * side <= STOP_FRONT_ATR * atr]
            if beyond:
                zone = min(beyond, key=lambda z: abs(z["price"] - stop))
                out.append({"code": "stop_di_depan_zona",
                            "text": f"Stop tepat di depan zona {' + '.join(zone['names'][:2])} ({zone['price']}): "
                                    "level itu sering disentuh dulu. Taruh stop di belakangnya."})
    created = float(plan.get("created_at") or 0.0)
    if created:
        now_windows = cycles.windows(created)
        if "rollover" in now_windows:
            out.append({"code": "rollover", "text": "Jam rollover (17:00 New York): spread melebar dan likuiditas tipis."})
        if "us_data" in now_windows:
            out.append({"code": "data_as", "text": "Menit rilis data AS (08:30 New York): harga bisa melompat lewat stop."})
        until = float(plan.get("hold_until") or 0.0)
        if until and not sessions.weekend(created) and sessions.weekend(until):
            out.append({"code": "akhir_pekan", "text": "Posisi bisa menginap akhir pekan: harga Senin bisa dibuka melompati stop."})
    return out


__all__ = ["BASICS", "RISK_FIRST", "LEVELS_AND_TIME", "considerations", "compact", "tags", "record_like_now", "by_tag", "plan_checks"]
