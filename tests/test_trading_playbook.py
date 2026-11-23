"""The trader's checklist: risk arithmetic, price levels, the market's clocks and cycles, and how plans learn from them.

Three things are checked throughout: the textbook formulas give the textbook numbers, nothing computed
per bar looks at a later bar, and a pattern counts only when it passes its test (and does when planted).
"""

from __future__ import annotations

import datetime as dt
import math
import os
from typing import Any

import numpy as np
import pytest

os.environ.setdefault("TRADING_BRAIN_SIZE", "kecil")
from trading.quant import analyst, brain, cycles, levels, memory, playbook, riskmath, sessions
from trading.quant.memory import PLANS


def ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> float:
    return dt.datetime(year, month, day, hour, minute, tzinfo=dt.UTC).timestamp()


def walk(n: int = 600, *, seconds: int = 3600, start: float | None = None, seed: int = 5, price: float = 2000.0,
         drift_hour_ny: int | None = None, drift: float = 0.0, wave: tuple[float, float] | None = None) -> list[dict[str, float]]:
    """Hourly bars of a random walk. Options plant something to find: a drift in one New York hour, or a sine wave."""
    rng = np.random.default_rng(seed)
    start = ts(2026, 6, 1) if start is None else start
    out = []
    for i in range(n):
        t = start + i * seconds
        step = rng.normal(0, 1.0)
        if drift_hour_ny is not None and int(sessions.local_seconds(t) % 86400 // 3600) == drift_hour_ny:
            step += drift
        if wave:
            period, size = wave
            step += size * (math.sin(2 * math.pi * (i + 1) / period) - math.sin(2 * math.pi * i / period))
        o, c = price, price + step
        out.append({"t": float(t), "o": o, "h": max(o, c) + 0.3, "l": min(o, c) - 0.3, "c": c, "v": 100.0})
        price = c
    return out


# ── risk arithmetic ─────────────────────────────────────────────────────────
def test_the_basics_give_the_textbook_numbers():
    size = riskmath.position_size(1000, 1.0, 50, 0.10, step=1, minimum=1)       # $1000, 1%, 50 pips, $0.10 a pip per micro lot
    assert size["lots"] == 2 and size["risk_money"] == 10 and size["tradable"]
    assert riskmath.risk_reward(100, 95, 110) == 2 and riskmath.risk_reward(100, 105, 90) == 2
    with pytest.raises(ValueError):
        riskmath.risk_reward(100, 95, 90)                                       # stop and target on the same side
    assert riskmath.breakeven_win_rate(2) == pytest.approx(1 / 3)
    assert riskmath.breakeven_win_rate(2, cost_r=0.1) > 1 / 3, "a spread raises the win rate that breaks even"
    assert riskmath.expectancy(0.4, 2, 1) == pytest.approx(0.2)
    assert riskmath.kelly(0.5, 2) == pytest.approx(0.25) and riskmath.kelly(0.3, 2) < 0
    assert riskmath.recovery(0.5) == pytest.approx(1.0), "down 50% needs up 100%"
    assert riskmath.drawdown([100, 120, 90, 130])["max_drawdown"] == pytest.approx(0.25)
    assert riskmath.pip_value(0.0001, 100_000) == pytest.approx(10)
    assert riskmath.pip_value(0.0001, 100_000, 1.25, account="base") == pytest.approx(8)
    assert riskmath.margin(100_000, 1.1, 100) == pytest.approx(1100)
    assert riskmath.compound(1000, 0.1, 2) == pytest.approx(1210)


def test_a_size_below_the_broker_minimum_is_said_not_rounded_up():
    size = riskmath.position_size(100, 0.5, 500, 1.0)
    assert size["lots"] == 0 and not size["tradable"] and "minimum" in size["note"]


def test_the_tests_agree_with_the_tables():
    assert riskmath.student_p(2.0, 10) == pytest.approx(0.0734, abs=5e-4)        # two-sided, the t table
    assert riskmath.student_p(2.228, 10) == pytest.approx(0.05, abs=5e-4)
    assert riskmath.student_p(0.0, 10) == pytest.approx(1.0)
    assert riskmath.student_p(2.0, 10, two_sided=False) == pytest.approx(0.0367, abs=5e-4)
    assert riskmath.student_p(-2.0, 10, two_sided=False) > 0.95
    assert riskmath.sidak(0.05, 12) == pytest.approx(1 - 0.95 ** 12, abs=1e-5), "the best of twelve is not one try"
    q = riskmath.benjamini_hochberg([0.001, 0.02, 0.04, 0.5])
    assert q == pytest.approx([0.004, 0.04, 0.04 * 4 / 3, 0.5])
    exact = riskmath.mean_test([1.0, 2.0, -1.0, 3.0, 0.5])
    sums = riskmath.mean_test_sums(5, 5.5, 1 + 4 + 1 + 9 + 0.25)
    assert exact["t_stat"] == sums["t_stat"] and exact["p_value"] == sums["p_value"]


def test_few_trades_are_worth_little():
    assert riskmath.beta_lower(2, 2) < 0.5, "two wins from two is not 100%"
    assert riskmath.beta_lower(60, 100) == pytest.approx(0.6, abs=0.06)
    lucky = riskmath.kelly_safe(4, 4, 2.0)
    assert lucky["full"] == 1.0 and lucky["suggested_risk"] <= 0.02, "Kelly on four trades would bet everything"
    assert riskmath.kelly_safe(30, 100, 1.0)["suggested_risk"] == 0.0, "no edge, no size"


def test_losing_streaks_and_ruin():
    assert riskmath.losing_streak_probability(0.5, 3, 2) == pytest.approx(3 / 8)   # LLW, WLL, LLL
    assert riskmath.losing_streak_probability(0.5, 1, 2) == 0
    assert riskmath.losing_streak_probability(0.45, 100, 5) > 0.9, "five losses in a row are normal in a hundred trades"
    assert riskmath.risk_of_ruin(0.4, 1.0, 0.01, 0.3) == 1.0, "without an edge it is only a matter of time"
    theta_units = math.log(0.7) / math.log(0.99)
    assert riskmath.risk_of_ruin(0.6, 1.0, 0.01, 0.3) == pytest.approx((0.4 / 0.6) ** theta_units, rel=1e-6)   # the classic q/p
    assert riskmath.risk_of_ruin(0.45, 2.0, 0.05, 0.3) > riskmath.risk_of_ruin(0.45, 2.0, 0.01, 0.3), "more risk, more ruin"


def test_a_record_and_a_plan_are_read_honestly():
    few = riskmath.record([2, 2, -1])
    assert few["verdict"].startswith("terlalu sedikit")
    good = riskmath.record([2, -1, 2, -1, 2, 2, -1, 2, -1, 2] * 4)
    assert good["expectancy_r"] == pytest.approx(0.8) and good["breakeven_win_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert good["p_value"] < 0.05 and "lolos uji" in good["verdict"] and good["longest_losing_streak"] == 1
    assert riskmath.record([1, -1] * 20)["verdict"].startswith("rugi")
    assert riskmath.record([1.2, -1] * 15, tried=12)["p_value_adjusted"] > riskmath.record([1.2, -1] * 15)["p_value"]
    plan = riskmath.plan_math(0.45, 0.45, 1.5, 3.0)
    assert plan["rr"] == 2 and plan["p_target_needed"] == pytest.approx(0.3) and plan["edge"] == pytest.approx(0.15)
    assert plan["ev_r"] == pytest.approx(0.45)
    assert riskmath.plan_math(0.3, 0.6, 2.0, 1.0)["edge"] < 0, "a small target with a low chance is under break-even"
    assert "ev_r" not in riskmath.plan_math(None, None, 1.0, 2.0), "no chances, no made-up value"


# ── levels ──────────────────────────────────────────────────────────────────
def test_fibonacci_and_pivots_follow_the_formulas():
    assert levels.fib_retracements(200, 100)["61.8%"] == pytest.approx(138.2)
    assert levels.fib_retracements(200, 100, up=False)["61.8%"] == pytest.approx(161.8)
    assert levels.fib_extensions(200, 100)["161.8%"] == pytest.approx(261.8)
    assert levels.fib_extensions(200, 100, up=False)["161.8%"] == pytest.approx(38.2)
    h, lo, c = 110.0, 100.0, 105.0
    classic = levels.pivots(h, lo, c)
    assert classic == pytest.approx({"P": 105, "R1": 110, "S1": 100, "R2": 115, "S2": 95, "R3": 120, "S3": 90})
    fib = levels.pivots(h, lo, c, "fibonacci")
    assert fib["R1"] == pytest.approx(108.82) and fib["S2"] == pytest.approx(98.82) and fib["R3"] == pytest.approx(115)
    cam = levels.pivots(h, lo, c, "camarilla")
    assert cam["R3"] == pytest.approx(107.75) and cam["S4"] == pytest.approx(99.5)
    assert levels.pivots(h, lo, 108.0, "woodie")["P"] == pytest.approx(106.5)
    with pytest.raises(ValueError):
        levels.pivots(h, lo, c, "gann")
    assert [levels.round_step(p) for p in (1.0850, 150.2, 2650.0, 95_000.0)] == pytest.approx([0.01, 1, 50, 500])


def test_the_day_is_the_forex_day_and_follows_daylight_saving():
    # 17:00 New York is 21:00 UTC in summer and 22:00 UTC in winter
    assert sessions.trading_day(ts(2026, 7, 1, 20, 59)) + 1 == sessions.trading_day(ts(2026, 7, 1, 21, 0))
    assert sessions.trading_day(ts(2026, 1, 14, 21, 30)) == sessions.trading_day(ts(2026, 1, 14, 12, 0))
    assert sessions.trading_day(ts(2026, 1, 14, 22, 0)) == sessions.trading_day(ts(2026, 1, 14, 12, 0)) + 1
    sunday_open, monday = ts(2026, 7, 5, 21, 30), ts(2026, 7, 6, 12, 0)
    assert sessions.trading_day(sunday_open) == sessions.trading_day(monday), "Sunday's open already belongs to Monday"
    bars = walk(72, start=ts(2026, 7, 1, 0))
    frames = levels.day_frames(bars)
    first_new_day = next(i for i in range(1, 72) if frames["day_id"][i] != frames["day_id"][i - 1])
    assert dt.datetime.fromtimestamp(bars[first_new_day]["t"], dt.UTC).hour == 21
    assert frames["prev_h"][first_new_day] == pytest.approx(max(b["h"] for b in bars[:first_new_day]))
    assert frames["prev_c"][first_new_day] == pytest.approx(bars[first_new_day - 1]["c"])
    assert math.isnan(frames["prev_h"][0]), "no yesterday on the first day"


def test_a_swing_exists_only_once_the_price_has_come_back_from_it():
    up = [100 + i for i in range(20)]
    down = [119 - 2 * i for i in range(1, 8)]
    closes = up + down
    h = np.array([c + 0.5 for c in closes])
    lo = np.array([c - 0.5 for c in closes])
    atr = np.full(len(closes), 1.0)
    z = levels.zigzag(h, lo, atr, k=3.0)
    assert z["last_kind"][19] == -1 and z["last_p"][19] == pytest.approx(99.5), "at the top nobody knows it is the top: only the low is confirmed"
    confirmed = next(i for i in range(len(closes)) if z["last_kind"][i] == 1)
    assert confirmed == 20 and z["last_p"][confirmed] == pytest.approx(119.5) and z["last_i"][confirmed] == 19
    assert z["prev_p"][confirmed] == pytest.approx(99.5), "the leg runs from the low before it"
    shorter = levels.zigzag(h[:23], lo[:23], atr[:23], k=3.0)
    assert np.array_equal(np.nan_to_num(shorter["last_p"]), np.nan_to_num(z["last_p"][:23])), "later bars change nothing earlier"


def test_no_level_measurement_looks_at_a_later_bar():
    bars = walk(500)
    full = levels.feature_columns(bars)
    part = levels.feature_columns(bars[:380])
    for name in levels.COLUMNS:
        assert np.allclose(np.nan_to_num(full[name][:380]), np.nan_to_num(part[name]), atol=1e-9), name
    x_full, names = memory.feature_matrix(bars, 3600)
    x_part, _ = memory.feature_matrix(bars[:380], 3600)
    fresh = [i for i, n in enumerate(names) if n.startswith(("lv_", "tm_", "cy_")) or n in ("macd_hist", "stoch_d")]
    assert len(fresh) >= 24 and not np.isnan(x_full[memory.WARMUP:, fresh]).any()
    assert np.allclose(x_full[:380, fresh], x_part[:, fresh], atol=1e-9, equal_nan=True), "the brain may only learn from what was known then"


def test_a_zone_is_strong_where_different_kinds_of_level_meet():
    found = levels.zones([(100.0, "S1", 0), (100.1, "Fibo 61.8%", 1), (100.15, "Fib-pivot S2", 0), (103.0, "R1", 0),
                          (103.1, "Camarilla R3", 0), (110.0, "Angka bulat atas", 3)], atr=1.0)
    assert [z["strength"] for z in found] == [2, 1, 1], "two pivot formulas agreeing is arithmetic, not confluence"
    assert found[0]["names"] == ["S1", "Fibo 61.8%", "Fib-pivot S2"] and found[0]["groups"] == ["fib", "pivot"]
    matrix = np.array([[100.0, 100.1, 103.0, 103.1, np.nan]])
    strength = levels.strengths(matrix, np.array([0, 1, 0, 0, 2]), np.array([1.0]))
    assert strength.tolist() == [[2, 2, 1, 1, 0]]


def test_the_snapshot_says_where_the_price_stands():
    bars = walk(400, seed=9)
    view = levels.snapshot(bars, digits=2)
    assert view["ok"] and view["pivot"]["day_boundary"] == "17:00 New York"
    assert view["pivot"]["classic"]["P"] == pytest.approx(sum(view["pivot"]["previous"].values()) / 3, abs=0.01)
    assert all(z["dist_atr"] > 0 for z in view["zones_above"]) and all(z["dist_atr"] < 0 for z in view["zones_below"])
    assert view["zones_above"] == sorted(view["zones_above"], key=lambda z: z["dist_atr"])
    fib = view["fib"]
    assert fib["leg"] in ("naik", "turun") and set(fib["retracements"]) == {"23.6%", "38.2%", "50%", "61.8%", "78.6%"}
    small = levels.compact(view)
    assert small["ok"] and len(small["above"]) <= 3 and "targets" in small["fib"]
    assert levels.snapshot(bars[:10])["ok"] is False
    between = levels.between(view, view["price"] - 50, view["price"] + 50, min_strength=1)
    assert between and all(view["price"] - 50 < z["price"] < view["price"] + 50 for z in between)


# ── the clocks ──────────────────────────────────────────────────────────────
def test_the_market_hour_is_read_from_each_citys_own_clock():
    assert "us_data" in cycles.windows(ts(2026, 7, 1, 12, 30)), "08:30 New York is 12:30 UTC in summer"
    assert "us_data" in cycles.windows(ts(2026, 1, 14, 13, 30)), "and 13:30 UTC in winter"
    assert "us_data" not in cycles.windows(ts(2026, 1, 14, 12, 30))
    assert cycles.phase(ts(2026, 7, 1, 7, 30)) == "london_open" and cycles.phase(ts(2026, 1, 14, 8, 30)) == "london_open"
    assert cycles.phase(ts(2026, 7, 1, 21, 10)) == "rollover" and cycles.phase(ts(2026, 7, 1, 15, 0)) == "overlap"
    assert cycles.phase(ts(2026, 7, 1, 3, 0)) == "asia" and cycles.phase(ts(2026, 7, 4, 12, 0)) == "weekend"
    assert cycles.windows(ts(2026, 7, 4, 12, 30)) == [], "nothing opens on a Saturday"
    normal, mismatch = cycles.clock(ts(2026, 7, 1, 12, 0)), cycles.clock(ts(2026, 3, 16, 12, 0))
    assert normal["london_ny_gap_hours"] == 5 and normal["dst_note"] == ""
    assert mismatch["london_ny_gap_hours"] == 4 and "4 jam" in mismatch["dst_note"], "US clocks change three weeks before UK ones"
    nxt = cycles.next_events(ts(2026, 7, 3, 21, 30))                       # Friday after the close
    assert all(e["in_minutes"] > 24 * 60 for e in nxt), "the next events are after the weekend"
    assert cycles.clock(ts(2026, 7, 1, 20, 0))["minutes_to_rollover"] == 60


def test_the_clock_measurements_and_the_session_strips():
    times = np.array([ts(2026, 7, 1, 21, 0), ts(2026, 7, 1, 15, 0), ts(2026, 7, 1, 9, 0), ts(2026, 7, 30, 12, 0), ts(2026, 7, 31, 12, 0)])
    cols = cycles.clock_columns(times)
    assert set(cols) == set(cycles.CLOCK_COLUMNS)
    assert cols["tm_rollover"][0] == pytest.approx(1.0) and cols["tm_rollover"][1] == pytest.approx(0.0)
    assert cols["tm_london_open"][2] == pytest.approx(2 / 9), "09:00 UTC in summer is two hours into London"
    assert cols["tm_ny_open"][2] == 0 and cols["tm_ny_open"][1] == pytest.approx(3 / 9)
    assert cols["tm_month_end"].tolist() == [0, 0, 0, 1, 1], "the last two weekdays of July 2026"
    assert cycles.moon_phase(cycles.NEW_MOON_TS) == 0 and cycles.moon_phase(cycles.NEW_MOON_TS + 14.765 * 86400) == pytest.approx(0.5, abs=0.01)
    bands = cycles.session_bands(ts(2026, 7, 1, 0), ts(2026, 7, 2, 0))
    london = next(b for b in bands if b["key"] == "london")
    assert london["start"] == ts(2026, 7, 1, 7) and london["end"] == ts(2026, 7, 1, 16)
    winter = next(b for b in cycles.session_bands(ts(2026, 1, 14, 0), ts(2026, 1, 15, 0)) if b["key"] == "london")
    assert winter["start"] == ts(2026, 1, 14, 8)
    assert not cycles.session_bands(ts(2026, 7, 4, 2), ts(2026, 7, 5, 2)), "no session on a Saturday"
    assert cycles.fib_count(21)["on_count"] and not cycles.fib_count(27)["on_count"] and cycles.fib_count(27)["next_count"] == 34


# ── seasonality and cycles: found when planted, not found in noise ──────────
def test_an_hour_that_really_drifts_passes_the_test_and_noise_does_not():
    planted = cycles.seasonality(walk(24 * 60, drift_hour_ny=9, drift=2.0), 3600)
    hits = [r for r in planted["significant"] if "NY" in r["label"]]
    assert [r["key"] for r in hits] == [9] and hits[0]["avg_move_atr"] > 0 and hits[0]["q_value"] < cycles.Q_LIMIT
    noise = cycles.seasonality(walk(24 * 60, seed=11), 3600)
    assert noise["ok"] and not noise["significant"] and "tidak ada" in noise["verdict"]
    assert noise["tested"] >= 24, "every hour was a try, and the verdict counts them all"
    assert cycles.seasonality(walk(20), 3600)["ok"] is False


def test_the_seasonal_measurement_only_uses_earlier_days():
    bars = walk(24 * 30, drift_hour_ny=9, drift=2.0)
    full = cycles.seasonal_columns(bars, 3600)
    part = cycles.seasonal_columns(bars[:400], 3600)
    for name in cycles.SEASONAL_COLUMNS:
        assert np.allclose(full[name][:400], part[name]), name
    before_nine = [i for i, b in enumerate(bars) if int(sessions.local_seconds(b["t"] + 3600) % 86400 // 3600) == 9]
    assert full["cy_seas_drift"][before_nine[-1]] > 1.5, "by the end it knows the nine o'clock bar climbs"
    assert full["cy_seas_drift"][before_nine[2]] == 0, "after three days it does not claim to know yet"


def test_a_planted_cycle_is_found_with_its_direction_and_noise_is_not_called_a_cycle():
    n, period = 400, 16
    closes = [2000 + 5 * math.sin(2 * math.pi * i / period) + 0.01 * i for i in range(n)]
    found = cycles.dominant_cycle(closes)
    assert found["significant"] and found["period_bars"] == pytest.approx(period, abs=0.5)
    rising = closes[-1] > closes[-2]
    assert found["direction"] == ("naik" if rising else "turun")
    last_peak = max(range(n - period, n), key=lambda i: closes[i] - 0.01 * i)
    assert found["bars_to_peak"] == pytest.approx((last_peak + period - (n - 1)) % period, abs=1.5)
    false_alarms = sum(cycles.dominant_cycle([b["c"] for b in walk(200, seed=s)])["significant"] for s in range(30))
    assert false_alarms <= 4, "a random walk must rarely pass (the limit is 5% twice over)"
    assert cycles.fisher_g_p(0.9, 16) < 1e-6 and cycles.fisher_g_p(1 / 16, 16) == 1.0
    assert cycles.dominant_cycle(closes[:50])["ok"] is False
    cols = cycles.cycle_columns(np.array(closes))
    again = cycles.cycle_columns(np.array(closes[:300]))
    for name in cycles.CYCLE_COLUMNS:
        assert np.allclose(np.nan_to_num(cols[name][:300]), np.nan_to_num(again[name])), name
    assert math.isnan(cols["cy_sin"][cycles.WINDOW - 1]) and cols["cy_power"][-1] > 0.8


# ── the playbook: conditions, warnings, and learning from them ──────────────
def picture(**levels_over: Any) -> dict[str, Any]:
    zone = {"price": 104.0, "low": 103.9, "high": 104.1, "strength": 3, "names": ["R1", "Fibo 61.8%", "High kemarin"],
            "groups": ["fib", "pivot", "range"], "dist_atr": 2.0}
    under = {"price": 98.6, "low": 98.5, "high": 98.7, "strength": 2, "names": ["S1", "Angka bulat bawah"],
             "groups": ["pivot", "round"], "dist_atr": -0.7}
    lv = {"ok": True, "price": 100.0, "atr": 2.0, "zones": [zone, under], "zones_above": [zone], "zones_below": [under],
          "at_zone": None, "room_up_atr": 2.0, "room_down_atr": 0.7,
          "pivot": {"side_of_pivot": "di atas P", "classic": {"P": 99.0}, "camarilla_place": 0.4, "day_boundary": "17:00 New York"},
          "fib": {"leg": "naik", "from": 90.0, "to": 106.0, "leg_atr": 8.0, "retraced": 0.55, "read": "zona emas 50-61,8%",
                  "bars_since_swing": 13, "extensions": {"161.8%": 115.9}}} | levels_over
    return {"ok": True, "levels": lv, "clock": cycles.clock(ts(2026, 7, 1, 7, 30)),
            "cycle": {"ok": True, "significant": True, "direction": "naik", "verdict": "x", "period_bars": 16,
                      "bars_to_peak": 3, "bars_to_trough": 11, "p_permutation": 0.01},
            "seasonality": {"ok": False}}


def test_a_plan_is_tagged_with_the_conditions_it_was_made_in():
    buy = playbook.tags(picture(), "buy")
    assert buy == {"waktu": "london_open", "zona": "dari zona", "pivot": "searah pivot", "fibo": "searah leg, koreksi zona emas",
                   "siklus": "searah siklus"}
    sell = playbook.tags(picture(), "sell")
    assert sell["zona"] == "menuju zona dekat" and sell["pivot"] == "melawan pivot" and sell["siklus"] == "melawan siklus"
    assert playbook.tags(picture(), "none") == {} and playbook.tags(None, "buy") == {}
    quiet = picture()
    quiet["cycle"]["significant"] = False
    assert "siklus" not in playbook.tags(quiet, "buy"), "a cycle that failed its test is not a condition"
    small = playbook.compact(picture())
    assert set(small) == {"levels", "time"} and small["time"]["clock"]["phase"] == "london_open"
    assert playbook.compact(None) == {}


def view_for() -> dict[str, Any]:
    best = {"plan": PLANS.index((1, 1.5, 3.0)), "name": "buy SL1.5/TP3", "side": "buy", "stop_atr": 1.5, "target_atr": 3.0,
            "p_target": 0.5, "p_stop": 0.4, "ev_atr": 0.4, "edge_atr": 0.3}
    other = {**best, "side": "sell", "plan": PLANS.index((-1, 1.0, 1.0)), "name": "sell SL1/TP1", "stop_atr": 1.0, "target_atr": 1.0,
             "p_target": 0.3, "p_stop": 0.6}
    return {"ok": True, "atr": 2.0, "hold_bars": 4, "best": best, "plans": [best, other], "best_buy": best, "best_sell": other,
            "exam": {"passed": True}}


def make(answer: dict[str, Any], now: float = ts(2026, 7, 1, 7, 30), **kw: Any) -> dict[str, Any]:
    return analyst.make_plan(answer, view_for(), symbol="XAU", timeframe="H1", bid=99.9, ask=100.0, digits=2, now=now,
                             source="analyst", considerations=picture(), **kw)


def test_a_plan_is_read_back_like_a_careful_trader_would():
    buy = make({"side": "buy", "why": "trend"})
    assert buy["target"] == pytest.approx(106.0) and buy["math"]["edge"] == pytest.approx(0.2)
    assert buy["tags"]["zona"] == "dari zona"
    assert buy["check_codes"] == ["zona_menghalang"], "a strength-3 zone at 104 stands before the 106 target"
    assert "R1 + Fibo 61.8%" in buy["checks"][0]
    near = make({"side": "buy", "stop_atr": 1.0, "target_atr": 1.5})
    assert near["target"] == pytest.approx(103.0) and "zona_menghalang" not in near["check_codes"]
    assert "p_target" not in near["math"], "the brain's chances belong to its own stop and target"
    sell = make({"side": "sell", "plan": PLANS.index((-1, 1.0, 1.0))})
    assert "impas" in sell["check_codes"], "P(TP) 30% is under the 50% a 1:1 plan needs"
    tight = make({"side": "buy", "stop_atr": 0.6, "target_atr": 1.0})
    assert "stop_di_depan_zona" in tight["check_codes"], "the stop at 98.8 sits just in front of the zone at 98.6"
    wide = analyst.make_plan({"side": "buy", "stop_atr": 0.5, "target_atr": 1.0}, view_for(), symbol="XAU", timeframe="H1",
                             bid=99.0, ask=100.0, digits=2, now=ts(2026, 7, 1, 7, 30), source="analyst", considerations=picture())
    assert "spread" in wide["check_codes"]
    late = make({"side": "buy"}, now=ts(2026, 7, 3, 20, 50))
    assert {"rollover", "akhir_pekan"} <= set(late["check_codes"])
    bare = analyst.make_plan({"side": "buy"}, view_for(), symbol="XAU", timeframe="H1", bid=99.9, ask=100.0, digits=2,
                             now=ts(2026, 7, 1, 7, 30), source="analyst")
    assert bare["tags"] == {} and bare["checks"] == [] and bare["math"]["rr"] == 2, "without a picture a plan is still a plan"
    wait = make({"side": "none"})
    assert wait["wait_tags"]["zona"] == "dari zona" and "checks" not in wait


def closed(side: str, status: str, r: float, **tags: str) -> dict[str, Any]:
    return {"status": status, "side": side, "result_r": r, "tags": tags, "check_codes": [], "confidence": 0.6, "session": "london",
            "created_at": ts(2026, 7, 1, 8), "stop_atr": 1.5, "target_atr": 3.0, "why": ""}


def test_the_journal_teaches_by_condition():
    plans = ([closed("sell", "stop", -1.0, zona="menuju zona dekat", waktu="london_open")] * 4
             + [closed("buy", "target", 2.0, zona="dari zona", waktu="london_open")] * 3
             + [closed("buy", "stop", -1.0, zona="dari zona", waktu="asia")] * 2)
    now_sell = playbook.record_like_now(plans, playbook.tags(picture(), "sell"), closed=analyst.CLOSED)
    into_zone = next(r for r in now_sell if r["condition"] == "zona: menuju zona dekat")
    assert into_zone["plans"] == 4 and into_zone["targets"] == 0 and into_zone["avg_r"] == -1.0
    assert into_zone["target_rate_cautious"] < 0.3
    assert not any("asia" in r["condition"] for r in now_sell), "only today's conditions, and only with enough plans"
    summary = analyst.summary(plans)
    table = {(r["name"], r["value"]): r for r in summary["by_condition"]}
    assert table[("zona", "dari zona")]["plans"] == 5 and table[("zona", "dari zona")]["sum_r"] == 4.0
    assert table[("peringatan", "tidak ada")]["plans"] == 9
    assert summary["math"]["trades"] == 9 and summary["math"]["verdict"].startswith("terlalu sedikit")
    bars = walk(200)
    context = analyst.build_context(view_for(), symbol="XAU", timeframe="H1", bid=99.9, ask=100.0, now=ts(2026, 7, 1, 7, 30),
                                    bars=bars, news=None, plans=plans, considerations=picture())
    assert context["plan_math"]["buy"]["edge"] == pytest.approx(0.2) and context["plan_math"]["sell"]["edge"] < 0
    assert context["levels"]["room_down_atr"] == 0.7 and context["time"]["clock"]["phase"] == "london_open"
    assert context["my_record_like_now"]["sell"][0]["plans"] >= 3 and context["my_math"]["trades"] == 9
    assert "kelly" not in context["my_math"], "sizing is the risk engine's job, not the model's"
    lesson = analyst.lessons(plans, limit=1)[0]
    assert lesson["conditions"]["waktu"] == "asia"
    plain = analyst.build_context(view_for(), symbol="XAU", timeframe="H1", bid=99.9, ask=100.0, now=ts(2026, 7, 1, 7, 30),
                                  bars=bars, news=None, plans=[])
    assert "levels" not in plain and "my_record_like_now" not in plain
    for word in ("plan_math", "levels", "p_target_needed", "rollover", "significant"):
        assert word in analyst.SYSTEM, word


def test_the_whole_picture_is_built_from_bars_and_kept_per_bar():
    bars = walk(500, seed=2)
    now = bars[-1]["t"] + 3600
    view = playbook.considerations(bars, 3600, now, digits=2, key=("XAU", "H1"))
    assert view["ok"] and view["levels"]["ok"] and view["seasonality"]["ok"] and view["cycle"]["ok"]
    assert view["fib_time"]["bars_since_swing"] == view["levels"]["fib"]["bars_since_swing"]
    again = playbook.considerations(bars, 3600, now + 30, price=bars[-1]["c"] + 5, digits=2, key=("XAU", "H1"))
    assert again["seasonality"] is view["seasonality"], "the tables are worked out once per closed bar"
    assert again["levels"]["price"] == pytest.approx(bars[-1]["c"] + 5), "but the price they are read against is live"
    assert playbook.considerations(bars[:5], 3600, now)["levels"]["ok"] is False


# ── the brain keeps new measurements only when they help ────────────────────
def test_slow_calendars_wait_for_enough_history_and_groups_can_be_left_out():
    names = ["news_known", "live_context", *brain.NEWS_COLUMNS[1:], *brain.LIVE_COLUMNS[1:], "tm_month_pos", "tm_moon_sin",
             "tm_ny_sin", "lv_pp", "cy_sin"]
    x = np.ones((10, len(names)))
    short, share = brain.coverage_mask(names, x, np.linspace(0, 20 * 86400, 10))
    assert short[names.index("tm_month_pos")] and short[names.index("tm_moon_sin")] and not short[names.index("tm_ny_sin")]
    assert share["span_days"] == 20
    long_, _ = brain.coverage_mask(names, x, np.linspace(0, 120 * 86400, 10))
    assert not long_[names.index("tm_month_pos")]
    assert not brain.coverage_mask(names, x)[0][names.index("tm_month_pos")], "without times nothing is said about the span"
    assert brain.group_mask(names, ("level", "siklus")).tolist() == [n.startswith(("lv_", "cy_")) for n in names]
    assert not brain.group_mask(names, ()).any()


def test_training_tries_the_groups_and_says_which_it_kept(tmp_path):
    rng = np.random.default_rng(3)
    price, base = 2000.0, []
    for i in range(30 * 288):
        t = ts(2026, 6, 1) + i * 300
        step = rng.normal(0, 1.0)
        base.append({"t": float(t), "o": price, "h": max(price, price + step) + 0.3, "l": min(price, price + step) - 0.3,
                     "c": price + step, "v": 100.0})
        price += step
    b = brain.Brain("SYNTH", "M15", folder=tmp_path)
    status = b.train(base=base, now=base[-1]["t"] + 300, spread=0.2, members=2)
    assert status["ready"], status["error"]
    assert [g["name"] for g in status["groups"]] == ["level", "waktu", "siklus", "rezim"]
    tries = status["group_tries"]
    assert len(tries) == 7 and sum(t["chosen"] for t in tries) == 1, "the ten variants fold to seven without a daily file"
    assert tries[0]["used"] == [] and tries[0]["gain"] == 0, "the plain brain is the yardstick"
    chosen = next(t for t in tries if t["chosen"])
    assert chosen["used"] == [g["name"] for g in status["groups"] if g["used"]]
    assert chosen["used"] == [] or chosen["gain"] > brain.GROUP_MARGIN, "a richer set must earn its place"
    assert "tm_moon_sin" in status["masked"], "thirty days is not three moons"
    for g in status["groups"]:
        prefix = brain.GROUPS[g["name"]]
        assert g["used"] == any(n.startswith(prefix) and n not in status["masked"] for n in b._wave.names)
    view = b.analyse(base=base, now=base[-1]["t"] + 300, spread=0.2)
    assert view["ok"]
    regime_now = view["regime_now"]
    assert regime_now["state"] in {"trend", "range", "highvol"} and abs(sum(regime_now["p"].values()) - 1) < 0.01
    assert "regime_now" in brain.compact(view), "the LLM analyst reads the regime too"
    b._wave.names = b._wave.names[:-1]                                    # a brain trained before a measurement was added
    assert b.needs_training(), "it cannot answer for the new memory, so it trains again"


# ── the live simulation reads the same picture ──────────────────────────────
class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class Feed:
    def __init__(self, clock: Clock, bars: list[dict[str, float]]) -> None:
        self.clock, self.bars = clock, bars

    def tick(self, symbol: str) -> dict[str, Any]:
        mid = self.bars[-1]["c"] + (self.clock.t - self.bars[-1]["t"] - 3600) / 3600.0
        return {"bid": mid - 0.1, "ask": mid + 0.1, "time": self.clock.t}

    def symbol_spec(self, symbol: str) -> dict[str, Any]:
        return {"digits": 2}


class Model:
    """A trading LLM that always buys, and keeps what it was shown."""

    model, cost_usd, available, last_error, why_unavailable = "fake", 0.0, True, "", ""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def decide(self, context: dict[str, Any], system: str | None = None) -> dict[str, Any]:
        self.seen.append(context)
        return {"side": "buy", "confidence": 0.6, "why": "test", "stop_atr": 1.0, "target_atr": 2.0}

    def report(self) -> dict[str, Any]:
        return {"model": self.model, "calls": len(self.seen)}

    def close(self) -> None:
        pass


def test_the_simulation_tags_every_trade_and_judges_the_best_rule_as_one_of_many():
    from trading.quant import llm_trader, papersim

    bars = walk(400, seed=4)
    clock = Clock(bars[-1]["t"] + 3600 + 20)
    model = Model()
    sim = papersim.PaperSim(Feed(clock, bars), "XAUUSD", bars=lambda symbol: bars, clock=clock, sleep=clock.sleep, seed=1,
                            timeframe="H1", hold_bars=1, max_minutes=600, llm=model)
    sim.state = "running"                             # as inside run(): an answer is only kept while the run is going
    sim.step()
    assert sim.picture and sim.picture["ok"] and sim.picture["levels"]["ok"]
    assert sim.open and all(p.tags.get("waktu") for p in sim.open), "every position knows the conditions it was opened in"
    import time as real_time
    for _ in range(100):                              # let the model's answer arrive
        if sim._llm_answer is not None:
            break
        real_time.sleep(0.02)
    shown = model.seen[0]
    assert shown["levels"]["ok"] and shown["time"]["clock"]["phase"] and "hour_ahead" in shown["time"]
    clock.t += 5
    sim.step()
    mine = next(p for p in sim.open if p.rule == papersim.LLM)
    assert mine.risk == pytest.approx(sim.atr, rel=0.02) and mine.tags
    clock.t += 3600
    sim.step()
    report = sim.report()
    trade = next(t for t in report["trades"] if t["rule"] == papersim.LLM)
    assert trade["tags"] == mine.tags and "r" in trade, "an intraday trade has a stop, so its result is also in R"
    assert report["by_condition"] and {"rule", "name", "value", "trades", "win_rate"} <= set(report["by_condition"][0])
    assert report["rules_tried"] >= 2 and report["picture"]["phase"]
    row = next(r for r in report["ranking"] if r["name"] == papersim.LLM)
    assert {"expectancy", "t_stat", "p_value", "p_adjusted", "payoff"} <= set(row)
    assert report["cycle_log"][-1]["picture"]["phase"]
    for word in ("levels", "my_math", "my_record_like_now", "significant"):
        assert word in llm_trader.SYSTEM, word


def test_significance_counts_every_rule_that_was_tried():
    from trading.quant import papersim

    lucky, flat = papersim.Stats(), papersim.Stats()
    for points in (1.0, 1.2, 0.8, 1.1, -0.5, 0.9, 1.0, -0.4, 1.3, 0.7):
        lucky.add(points)
    for points in (1.0, -1.0) * 5:
        flat.add(points)
    row = lucky.report("ensemble")
    assert row["t_stat"] > 2 and row["p_value"] < 0.05 and row["payoff"] == pytest.approx(1.0 / 0.45, abs=0.01)
    assert riskmath.sidak(row["p_value"], 12) > row["p_value"] * 5, "one good rule out of twelve is much weaker evidence"
    assert flat.report("x")["t_stat"] == 0 and flat.report("x")["p_value"] == pytest.approx(0.5)
    assert papersim.Stats().report("x")["t_stat"] is None
