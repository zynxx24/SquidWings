"""LLM Analyst Trade: a trade plan with a stop and a target, for a person to read. Nothing is sent to a broker.

The analyst is the trading LLM (``llm_trader``) given a different job. It reads:

* the brain (``brain.analyse``): every plan's chances from the neural ensemble, the exam on the
  newest unseen days, what happened after the most similar past moments, and how the
  reference plan went in this session, this hour, this weekday and near news;
* the last candles of the timeframe, the session, the news mood and the calendar;
* the trader's checklist (``playbook``): the risk arithmetic of each plan (reward to risk, the
  target rate that only breaks even, the edge over it), the price levels around the entry
  (pivots, Fibonacci, yesterday's range, round numbers and where they meet), and the market's
  clocks, the hour's seasonality and the cycle, each with the test it passed or failed;
* its own past plans and how each one ended, from the journal kept here. That journal is how it
  learns across runs and restarts: every plan is followed on the M5 bars until it touches its
  target or its stop, or the hold runs out, and the next prompt carries the results. A "wait"
  is followed too, as the brain's best plan at that moment, so it can see when waiting was
  right and when it missed a winner.

Its answer becomes a plan: entry at the current ask (buy) or bid (sell), stop and target from the
ATR, the reward to risk, how long the idea stays valid, and why. The panel shows it as the
"LLM Analyst Trade" card with lines on the chart, and **Isi tiket** copies it into the order
ticket, where the usual preview and confirmation still apply.

Every plan is also read back against the checklist (``playbook.plan_checks``) and tagged with
the conditions it was made in (``playbook.tags``). The warnings show on the card. The tags are
how the journal teaches: the next prompt carries how earlier plans made under the same
conditions ended (``my_record_like_now``), and the panel shows the whole record by condition.

The plans live in ``trading/data/brain/analyst-<symbol>.json`` (the newest ``KEEP``).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from trading.quant import brain as brain_mod
from trading.quant import daily, memory, playbook, riskmath, sessions, trail

KEEP = 500
DEFAULT_STOP_ATR, DEFAULT_TARGET_ATR = memory.REFERENCE
MAX_VALID_BARS = 6

SYSTEM = (
    "You are the trade analyst for one symbol on intraday bars. You write one trade plan for a person to read; "
    "nothing you say is sent to a broker. A plan opens at the current price (buy at the ask, sell at the bid) with a "
    "stop and a target in ATR, and is closed at whichever is touched first, or at the end of the hold. Your evidence: "
    "'brain' is a neural ensemble trained on every past bar of this symbol: for each plan the chance the target "
    "comes first (p_target), the chance the stop comes first (p_stop), and the expected value in ATR after the "
    "spread (ev_atr), plus how far its five networks disagree. Its 'exam' was taken on the newest days it never saw: "
    "when exam.passed is false its numbers are a weak hint, not proof. 'similar_past' says what the best buy and sell "
    "plans did after the most similar past moments, also in the same session. 'conditions_now' says how the reference "
    "plan went in this session, this hour, this weekday, and near or away from big news. Also: the last candles, "
    "the news and calendar, and your own past plans with how they ended. Learn from those: repeat what worked in this "
    "session and condition, stop doing what failed, and notice when your waits missed winners. 'my_calibration' grades "
    "your own confidence against what happened: when it says you are over-confident, give lower confidence and demand "
    "more agreement before taking a side; target_rate_shrunk is the honest rate on few plans. 'my_math' is your "
    "record in R with its t-test: until it says the profit passed the test, your past wins are not evidence of skill. "
    "'daily' is the last years of daily candles in numbers: trend, volatility rank, the daily regime, how much of "
    "today's range is used ('today': days_wider_pct is the share of past days that went wider than now) and "
    "'next_day' the simulated spread of tomorrow's move. They are priors, not signals: a share counts only when its "
    "edge is true. Wide room left supports a target, a day whose range is nearly used argues for a smaller one. "
    "'my_record_like_now' is how your earlier buys and sells ended under the conditions that hold now (same part of "
    "the day, same place against the zones, the pivot and the swing): do not repeat a combination that keeps losing. "
    "'candle_model' (when present) is a transformer trained on this symbol's M5 bars that draws the next 12 M5 candles "
    "(one hour): 'path' is its middle guess and 10-90% band for the close with the chance of a higher close, "
    "'band_hour' the low and high only one move in ten passes within the hour, and 'scenarios' price-level plans made "
    "from it (buy if the price falls to the band's bottom, sell at its top, a trend entry when it leans). Its 'exam' "
    "compares its error with a guess without a brain: while edge is false its direction is no better than that guess, "
    "so use its band for room (a stop beyond the band, a target inside it), not for a side; band_80_hit near 80 means "
    "the band is honest. "
    + playbook.BASICS +
    "Rules: take a side "
    "only when at least two of brain ev_atr, similar_past, conditions_now and candle_model (only when its exam edge is "
    "true) lean the same way; otherwise answer none. Never open within 30 minutes before a high-impact release for the symbol's currencies. Prefer the plan the "
    "brain ranks best for your side (give its index as plan), or set stop_atr and target_atr yourself (0.5 to 5). "
    "A target smaller than 0.7 times the stop needs p_target above 0.6. When 'exit' says trailing, the target does not "
    "close the trade: reaching it locks the stop just behind it and the stop then trails the best price by trail_atr "
    "ATR, so a move that keeps going keeps paying; set trail_atr (0.2-5) tighter in choppy markets, wider in a trend. "
    "Answer with JSON only: "
    '{"side": "buy" | "sell" | "none", "plan": index or null, "stop_atr": number, "target_atr": number, '
    '"trail_atr": number (optional), "valid_bars": 1-6, "confidence": 0.0-1.0, '
    '"why": "at most 40 words, cite the evidence", "risks": "at most 20 words"}'
)

_lock = threading.Lock()


def _path(symbol: str, folder: Path | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in symbol)[:32]
    return (folder or memory.FOLDER) / f"analyst-{safe}.json"


def load(symbol: str, folder: Path | None = None) -> list[dict[str, Any]]:
    try:
        data = json.loads(_path(symbol, folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in data if isinstance(p, dict)] if isinstance(data, list) else []


def _save(symbol: str, plans: list[dict[str, Any]], folder: Path | None = None) -> None:
    path = _path(symbol, folder)
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(plans[-KEEP:], ensure_ascii=False, default=float), encoding="utf-8")
        tmp.replace(path)


def record(plan: dict[str, Any], folder: Path | None = None) -> None:
    with _lock:
        plans = load(plan["symbol"], folder)
        plans.append(plan)
        _save(plan["symbol"], plans, folder)


# ── building a plan ─────────────────────────────────────────────────────────
def _levels(side: int, entry: float, atr: float, stop_atr: float, target_atr: float, digits: int) -> tuple[float, float]:
    return round(entry - side * stop_atr * atr, digits), round(entry + side * target_atr * atr, digits)


def make_plan(answer: dict[str, Any], view: dict[str, Any], *, symbol: str, timeframe: str, bid: float, ask: float,
              digits: int, now: float, source: str, model: str = "", cost_usd: float = 0.0,
              trail_atr: float | None = None, lock_atr: float | None = None,
              considerations: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn the model's answer (and the brain's view) into a plan with prices. A wait keeps the brain's best as a shadow.

    With ``trail_atr`` (the run's setting, or the model's own ``trail_atr``) the plan has a trailing exit: the
    target arms the trail instead of closing. With ``considerations`` (``playbook.considerations``) the plan
    also gets its conditions (``tags``) and the checklist's warnings (``checks``).
    """
    atr = float(view.get("atr") or 0.0)
    seconds = memory.TIMEFRAMES[timeframe]
    hold = int(view.get("hold_bars") or memory.HOLD_BARS[timeframe])
    side = {"buy": 1, "sell": -1}.get(answer.get("side", "none"), 0)
    ranked = {p["plan"]: p for p in view.get("plans", [])} | {
        view[k]["plan"]: view[k] for k in ("best_buy", "best_sell", "best") if view.get(k)}
    chosen = ranked.get(answer.get("plan")) if answer.get("plan") is not None else None
    if chosen is not None and side and (chosen["side"] == "buy") != (side > 0):
        chosen = None                                       # a plan index for the other side is ignored
    if chosen is None and side:
        chosen = view.get("best_buy" if side > 0 else "best_sell")
    stop_atr = float(answer.get("stop_atr") or (chosen or {}).get("stop_atr") or DEFAULT_STOP_ATR)
    target_atr = float(answer.get("target_atr") or (chosen or {}).get("target_atr") or DEFAULT_TARGET_ATR)
    valid = int(answer.get("valid_bars") or 2)
    plan: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12], "symbol": symbol, "timeframe": timeframe, "source": source,
        "created_at": now, "session": sessions.key(now), "session_label": sessions.label(now),
        "side": answer.get("side", "none") if side else "none", "confidence": answer.get("confidence"),
        "why": answer.get("why", ""), "risks": answer.get("risks", ""), "atr": atr, "bid": bid, "ask": ask,
        "hold_until": now + hold * seconds, "valid_until": now + min(MAX_VALID_BARS, valid) * seconds,
        "model": model, "cost_usd": round(cost_usd, 6), "status": "open" if side else "wait",
        "brain": ({"plan": chosen.get("name"), "p_target": chosen.get("p_target"), "p_stop": chosen.get("p_stop"),
                   "ev_atr": chosen.get("ev_atr")} if chosen else None),
        "exam_passed": bool((view.get("exam") or {}).get("passed")),
    }
    wanted_trail = answer.get("trail_atr") if answer.get("trail_atr") is not None else trail_atr
    if side and atr > 0:
        entry = ask if side > 0 else bid
        stop, target = _levels(side, entry, atr, stop_atr, target_atr, digits)
        plan.update(entry=entry, stop=stop, target=target, stop_atr=round(stop_atr, 2), target_atr=round(target_atr, 2),
                    rr=round(target_atr / stop_atr, 2) if stop_atr else None)
        if wanted_trail is not None and float(wanted_trail) > 0:
            t, lk = trail.clamp(wanted_trail, lock_atr)
            plan.update(exit_style="trail", trail_atr=t, lock_atr=lk)
        # the brain's chances belong to one stop and target: they are quoted only when the plan uses the same ones
        same = chosen is not None and abs(chosen["stop_atr"] - stop_atr) < 1e-9 and abs(chosen["target_atr"] - target_atr) < 1e-9
        plan["math"] = riskmath.plan_math(chosen.get("p_target") if same and chosen else None,
                                          chosen.get("p_stop") if same and chosen else None, stop_atr, target_atr)
        if not plan["exam_passed"]:
            plan["math"].pop("kelly_half", None)     # chances that have not passed their exam are no basis for a size
        plan["tags"] = playbook.tags(considerations, plan["side"])
        checks = playbook.plan_checks(plan, considerations)
        plan["checks"], plan["check_codes"] = [c["text"] for c in checks], [c["code"] for c in checks]
    elif side:
        plan["status"] = "wait"
        plan["why"] = (plan["why"] + " (tanpa ATR, rencana tidak bisa dibuat)").strip()
    best = view.get("best")
    if plan["status"] == "wait" and best and atr > 0:      # what the brain would have done, to learn from the wait
        s = 1 if best["side"] == "buy" else -1
        entry = ask if s > 0 else bid
        stop, target = _levels(s, entry, atr, best["stop_atr"], best["target_atr"], digits)
        plan["shadow"] = {"side": best["side"], "plan": best["name"], "entry": entry, "stop": stop, "target": target,
                          "stop_atr": best["stop_atr"], "target_atr": best["target_atr"], "ev_atr": best["ev_atr"],
                          "status": "open"}
        plan["wait_tags"] = playbook.tags(considerations, best["side"])     # the conditions the wait passed up
    return plan


# ── following plans to their end ────────────────────────────────────────────
def _follow(side: int, entry: float, stop: float, target: float, created: float, until: float, atr: float,
            spread: float, bars: list[dict[str, float]]) -> dict[str, Any] | None:
    """The end of a position on M5 bars after it was opened, or None while it is still running."""
    start = created // memory.BASE_SECONDS * memory.BASE_SECONDS + memory.BASE_SECONDS
    last = None
    for b in bars:
        if b["t"] < start:
            continue
        if b["t"] >= until:
            break
        last = b
        if side > 0:
            hit_stop, hit_target = b["l"] <= stop, b["h"] >= target
        else:
            hit_stop, hit_target = b["h"] + spread >= stop, b["l"] + spread <= target
        if hit_stop:                                        # both in one bar: the stop counts
            return {"status": "stop", "exit": stop, "closed_at": b["t"] + memory.BASE_SECONDS,
                    "result_atr": round((stop - entry) * side / atr, 3) if atr else None}
        if hit_target:
            return {"status": "target", "exit": target, "closed_at": b["t"] + memory.BASE_SECONDS,
                    "result_atr": round((target - entry) * side / atr, 3) if atr else None}
    if bars and bars[-1]["t"] + memory.BASE_SECONDS >= until and last is not None:
        exit_price = last["c"] if side > 0 else last["c"] + spread
        return {"status": "timeout", "exit": exit_price, "closed_at": until,
                "result_atr": round((exit_price - entry) * side / atr, 3) if atr else None}
    return None


def update(symbol: str, bars: list[dict[str, float]], folder: Path | None = None) -> list[dict[str, Any]]:
    """Close the plans (and the waits' shadows) whose target, stop or hold has come. Returns all plans."""
    with _lock:
        plans = load(symbol, folder)
        changed = False
        for p in plans:
            spread = max(0.0, float(p.get("ask", 0)) - float(p.get("bid", 0)))
            if p.get("status") == "open" and p.get("entry") is not None:
                side = 1 if p["side"] == "buy" else -1
                if p.get("exit_style") == "trail":
                    start = p["created_at"] // memory.BASE_SECONDS * memory.BASE_SECONDS + memory.BASE_SECONDS
                    end = trail.follow(side, p["entry"], p["stop"], p["target"], p["atr"], bars, start=start, until=p["hold_until"],
                                       bar_seconds=memory.BASE_SECONDS, spread=spread, trail_atr=p.get("trail_atr", trail.TRAIL_ATR),
                                       lock_atr=p.get("lock_atr", trail.LOCK_ATR))
                else:
                    end = _follow(side, p["entry"], p["stop"], p["target"], p["created_at"], p["hold_until"], p["atr"], spread, bars)
                if end:
                    p.update(end)
                    p["result_r"] = round(end["result_atr"] / p["stop_atr"], 3) if end.get("result_atr") is not None else None
                    changed = True
            shadow = p.get("shadow")
            if shadow and shadow.get("status") == "open":
                side = 1 if shadow["side"] == "buy" else -1
                end = _follow(side, shadow["entry"], shadow["stop"], shadow["target"], p["created_at"], p["hold_until"],
                              p["atr"], spread, bars)
                if end:
                    shadow.update(end)
                    changed = True
        if changed:
            _save(symbol, plans, folder)
        return plans


def summary(plans: list[dict[str, Any]]) -> dict[str, Any]:
    """The analyst's record: closed plans, how often the target came, the average result, by session; and its waits."""
    closed = [p for p in plans if p.get("status") in CLOSED]
    waits = [p for p in plans if p.get("status") == "wait" and (p.get("shadow") or {}).get("status") in CLOSED]
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in closed:
        by_session[p.get("session", "")].append(p)

    def stats(items: list[dict[str, Any]]) -> dict[str, Any]:
        rs = [p.get("result_r") or 0.0 for p in items]
        targets = sum(p["status"] in ("target", "trail") for p in items)
        return {"plans": len(items), "targets": targets,
                "stops": sum(p["status"] == "stop" for p in items),
                "target_rate": round(targets / len(items), 3) if items else None,
                # Beta(2,2) posterior mean: a 2-for-2 record reads 67%, not 100%, until there is more
                "target_rate_shrunk": round((targets + 2) / (len(items) + 4), 3) if items else None,
                "avg_r": round(sum(rs) / len(rs), 3) if rs else None, "sum_r": round(sum(rs), 3)}

    return {
        **stats(closed), "open": sum(p.get("status") == "open" for p in plans),
        "by_session": [{"session": k, "label": sessions.label_of(k), **stats(v)} for k, v in sorted(by_session.items())],
        "calibration": calibration(closed),
        # the record in R, read with the risk arithmetic: break-even rate, expectancy, its t-test, streaks, Kelly
        "math": riskmath.record([float(p["result_r"]) for p in closed if p.get("result_r") is not None]),
        "by_condition": playbook.by_tag(plans, closed=CLOSED),
        "waits": len([p for p in plans if p.get("status") == "wait"]),
        "waits_missed_winner": sum(p["shadow"]["status"] == "target" for p in waits),
        "waits_avoided_loser": sum(p["shadow"]["status"] == "stop" for p in waits),
    }


CONFIDENCE_BINS = ((0.0, 0.5, "<0.5"), (0.5, 0.7, "0.5-0.7"), (0.7, 1.01, ">=0.7"))
CLOSED = ("target", "stop", "timeout", "trail")      # "trail": closed by the trailing stop after the target was reached


def calibration(closed: list[dict[str, Any]]) -> dict[str, Any]:
    """How the analyst's own confidence fared: target rate per confidence band, and the Brier score of
    confidence against whether the target came. A model that is surest when it loses learns it here."""
    scored = [(float(p["confidence"]), float(p["status"] in ("target", "trail"))) for p in closed
              if isinstance(p.get("confidence"), (int, float))]
    if not scored:
        return {"plans": 0, "brier": None, "bands": [], "verdict": "belum ada rencana selesai dengan confidence"}
    brier = sum((c - hit) ** 2 for c, hit in scored) / len(scored)
    bands = []
    for lo, hi, label in CONFIDENCE_BINS:
        inside = [(c, hit) for c, hit in scored if lo <= c < hi]
        if inside:
            rate = sum(hit for _, hit in inside) / len(inside)
            bands.append({"confidence": label, "plans": len(inside), "target_rate": round(rate, 3),
                          "mean_confidence": round(sum(c for c, _ in inside) / len(inside), 3)})
    sure = next((b for b in bands if b["confidence"] == ">=0.7"), None)
    unsure = next((b for b in bands if b["confidence"] == "<0.5"), None)
    if sure and unsure and sure["plans"] >= 3 and unsure["plans"] >= 3 and sure["target_rate"] < unsure["target_rate"]:
        verdict = "terbalik: rencana paling yakin lebih sering gagal daripada yang ragu; turunkan confidence"
    elif sure and sure["plans"] >= 3 and sure["target_rate"] < sure["mean_confidence"] - 0.15:
        verdict = "terlalu percaya diri: confidence tinggi tidak sebanding dengan hasil"
    else:
        verdict = "cukup kalibrasi" if brier < 0.25 else "confidence belum informatif"
    return {"plans": len(scored), "brier": round(brier, 3), "bands": bands, "verdict": verdict}


def lessons(plans: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """The newest finished plans and waits, short, for the next prompt."""
    out = []
    for p in reversed(plans):
        shadow = p.get("shadow") or {}
        if p.get("status") in CLOSED:
            out.append({"when": dt.datetime.fromtimestamp(p["created_at"], dt.UTC).strftime("%a %H:%M"), "session": p.get("session"),
                        "side": p["side"], "stop_atr": p.get("stop_atr"), "target_atr": p.get("target_atr"),
                        **({"trail_atr": p.get("trail_atr")} if p.get("exit_style") == "trail" else {}),
                        "ended": p["status"], "result_r": p.get("result_r"), "why": p.get("why", "")[:120],
                        **({"conditions": p["tags"]} if p.get("tags") else {}),
                        **({"warned": p["check_codes"]} if p.get("check_codes") else {})})
        elif p.get("status") == "wait" and shadow.get("status") in CLOSED:
            out.append({"when": dt.datetime.fromtimestamp(p["created_at"], dt.UTC).strftime("%a %H:%M"), "session": p.get("session"),
                        "side": "none", "brain_would_have": f"{shadow['plan']}: {shadow['status']}", "why": p.get("why", "")[:120]})
        if len(out) >= limit:
            break
    return out


# ── the question ────────────────────────────────────────────────────────────
def candles(bars: list[dict[str, float]], atr: float, count: int = 12) -> list[dict[str, Any]]:
    out = []
    for b in bars[-count:]:
        out.append({"time": dt.datetime.fromtimestamp(b["t"], dt.UTC).strftime("%a %H:%M"), "close": b["c"],
                    "body_atr": round((b["c"] - b["o"]) / atr, 2) if atr else None,
                    "upper_wick_atr": round((b["h"] - max(b["o"], b["c"])) / atr, 2) if atr else None,
                    "lower_wick_atr": round((min(b["o"], b["c"]) - b["l"]) / atr, 2) if atr else None})
    return out


def _daily_block(symbol: str, bars: list[dict[str, float]], now: float, price: float) -> dict[str, Any]:
    """The D1 brief, or nothing when the symbol has no daily file (or it cannot be read)."""
    try:
        history = daily.load(symbol)
        if not history:
            return {}
        today = [b for b in bars if int(b["t"] // 86400) == int(now // 86400)]
        brief = daily.brief(history, now, price=price, day_hi=max((b["h"] for b in today), default=None),
                            day_lo=min((b["l"] for b in today), default=None), key=("brief", symbol, len(history), history[-1]["t"]))
    except Exception:  # noqa: BLE001 - the daily picture is extra evidence; the analyst still answers without it
        return {}
    return {"daily": brief} if brief else {}


def build_context(view: dict[str, Any], *, symbol: str, timeframe: str, bid: float, ask: float, now: float,
                  bars: list[dict[str, float]], news: dict[str, Any] | None, plans: list[dict[str, Any]],
                  trail_atr: float | None = None, considerations: dict[str, Any] | None = None) -> dict[str, Any]:
    news = news or {}
    calendar = news.get("calendar") or {}
    record = summary(plans)
    like_now = {side: playbook.record_like_now(plans, playbook.tags(considerations, side), closed=CLOSED) for side in ("buy", "sell")}
    return {
        **({"exit": f"trailing: the target locks the stop {trail.LOCK_ATR} ATR behind it, then the stop trails the best price "
                    f"by trail_atr ATR (default {trail_atr})"} if trail_atr else {}),
        "symbol": symbol, "timeframe": timeframe, "time_utc": dt.datetime.fromtimestamp(now, dt.UTC).isoformat(timespec="minutes"),
        "session": sessions.label(now), "bid": bid, "ask": ask, "spread": round(ask - bid, 8),
        "brain": brain_mod.compact(view),
        # the brain's chances were learned with the spread paid, so no further cost is taken off here
        "plan_math": {side: riskmath.plan_math(best.get("p_target"), best.get("p_stop"), best["stop_atr"], best["target_atr"])
                      for side, best in (("buy", view.get("best_buy")), ("sell", view.get("best_sell"))) if best},
        **playbook.compact(considerations),
        "last_candles": candles(bars, float(view.get("atr") or 0.0)),
        "news_mood": (news.get("mood") or {}).get("label"), "high_impact_soon": calendar.get("near_high"),
        "next_high_impact": calendar.get("next_high"),
        "my_record": {k: v for k, v in record.items() if k not in ("by_session", "calibration", "math", "by_condition")},
        "my_record_this_session": next((s for s in record["by_session"] if s["session"] == sessions.key(now)), None),
        "my_calibration": record["calibration"],
        "my_math": {k: v for k, v in record["math"].items() if k != "kelly"},
        **({"my_record_like_now": {k: v for k, v in like_now.items() if v}} if any(like_now.values()) else {}),
        "my_last_plans": lessons(plans),
        "news_brief": news.get("brief", ""),
        **_daily_block(symbol, bars, now, (bid + ask) / 2),
        **_candle_block(symbol, (bid + ask) / 2),
    }


def _candle_block(symbol: str, price: float) -> dict[str, Any]:
    """The candle model's forecast, scenarios and exam (``candle_view``), when a model was trained for the symbol."""
    if os.getenv("CANDLE_LM_OFF", "").strip() == "1":
        return {}
    try:
        from trading.quant import candle_view

        block = candle_view.for_llm(symbol, price)
    except Exception:  # noqa: BLE001 - the analyst answers without the candle model
        return {}
    return {"candle_model": block} if block else {}


def consider(symbol: str, timeframe: str, bars: list[dict[str, float]], now: float, *, price: float | None = None,
             digits: int = 5) -> dict[str, Any] | None:
    """The checklist's picture for this bar (levels, clocks, seasonality, cycle), or None when it cannot be made."""
    try:
        return playbook.considerations(bars, memory.TIMEFRAMES[timeframe], now, price=price, digits=digits,
                                       key=(symbol, timeframe))
    except Exception:  # noqa: BLE001 - the checklist is extra evidence; the analyst still answers without it
        return None


def run(backend: Any, symbol: str, timeframe: str, trader: Any, *, news: dict[str, Any] | None = None,
        folder: Path | None = None, brain: Any = None, now: float | None = None) -> dict[str, Any]:
    """Ask the analyst once, now, and keep its plan. Raises ValueError with a reason the panel can show."""
    now = time.time() if now is None else now
    tick = backend.tick(symbol)
    bid, ask = float(tick["bid"]), float(tick["ask"])
    digits = 5
    with contextlib.suppress(Exception):
        digits = int(backend.symbol_spec(symbol).get("digits", 5))
    brain = brain or brain_mod.get_brain(symbol, timeframe)
    base = memory.history_m5(symbol, backend)
    view = brain.analyse(backend, now=now, base=base, spread=ask - bid)
    if not view.get("ok"):
        if brain.needs_training():
            brain.train_in_background(backend)
        raise ValueError(f"Otak {timeframe} belum siap: {view.get('reason')}. Pelatihan dimulai di latar; coba lagi sebentar lagi.")
    plans = update(symbol, base, folder)
    bars = memory.resample(base, memory.TIMEFRAMES[timeframe], now)
    picture = consider(symbol, timeframe, bars, now, price=(bid + ask) / 2, digits=digits)
    context = build_context(view, symbol=symbol, timeframe=timeframe, bid=bid, ask=ask, now=now, bars=bars, news=news,
                            plans=plans, considerations=picture)
    answer = trader.decide(context, system=SYSTEM)
    if answer is None:
        raise ValueError(f"LLM tidak menjawab: {trader.last_error or trader.why_unavailable}")
    plan = make_plan(answer, view, symbol=symbol, timeframe=timeframe, bid=bid, ask=ask, digits=digits, now=now,
                     source="analyst", model=trader.model, cost_usd=trader.cost_usd, considerations=picture)
    record(plan, folder)
    return {"plan": plan, "view": view}


__all__ = ["SYSTEM", "make_plan", "record", "load", "update", "summary", "lessons", "build_context", "run", "candles", "consider"]
