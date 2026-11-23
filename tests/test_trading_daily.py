"""The daily (D1) memory: loading an MT4 export, the dl_ columns, and the forecast readers."""

from __future__ import annotations

import math

import numpy as np
import pytest

from trading.quant import analyst, daily, forecast, memory
from trading.quant.daily import DAY

D0 = 1_700_006_400 // DAY * DAY          # a UTC midnight in 2023


def walk(days: int = 900, seed: int = 1, *, ar: float = 0.0, cycle: float = 0.0, period: float = 40.0) -> list[dict[str, float]]:
    """Daily candles from a random walk: ``ar`` makes today's move follow yesterday's, ``cycle`` adds a sine."""
    rng = np.random.default_rng(seed)
    price, prev, out = 2000.0, 0.0, []
    for i in range(days):
        move = ar * prev + rng.normal(0, 0.01) + cycle * math.cos(2 * math.pi * i / period) * 0.01
        prev = move
        o, c = price, price * math.exp(move)
        out.append({"t": float(D0 + i * DAY), "o": o, "h": max(o, c) * (1 + abs(rng.normal(0, 0.004))),
                    "l": min(o, c) * (1 - abs(rng.normal(0, 0.004))), "c": c, "v": 1000.0})
        price = c
    return out


def test_an_mt4_export_is_read_and_bad_lines_are_skipped():
    text = ("2026.11.09,00:00,4064.71,4148.50,4054.30,4140.98,345\n"
            "2026.11.10,00:00,4086.43,4146.52,4013.68,4032.72,300\n"
            "2026.11.10,00:00,4086.43,4146.52,4013.68,4033.00,310\n"      # the same day again: the latest wins
            "garbage\n"
            "2026.11.11,00:00,4000,3990,4010,4005,1\n")                    # high below low: dropped
    bars = daily.parse(text)
    assert [b["c"] for b in bars] == [4140.98, 4033.0]
    assert bars[0]["t"] % DAY == 0 and bars[1]["t"] - bars[0]["t"] == DAY


def test_a_broker_suffix_still_finds_the_file(tmp_path):
    (tmp_path / "XAUUSD1440.csv").write_text("2026.11.09,00:00,1,2,0.5,1.5,10\n", encoding="utf-8")
    assert len(daily.load("XAUUSDcheck", tmp_path)) == 1
    assert daily.load("EURUSD", tmp_path) == []


def test_a_row_sees_only_the_days_closed_by_its_close():
    bars = walk(400)
    t_close = np.array([D0 + 300 * DAY + 5 * 3600.0])                       # five hours into day 300
    price, hi, lo = np.array([2000.0]), np.array([2010.0]), np.array([1990.0])
    base = daily.feature_columns(bars, t_close, price, hi, lo, key="a")
    changed = [dict(b) for b in bars]
    for b in changed[300:]:                                                  # day 300 (still open) and everything after
        b["o"], b["h"], b["l"], b["c"] = b["o"] * 2, b["h"] * 3, b["l"] * 0.5, b["c"] * 2
    again = daily.feature_columns(changed, t_close, price, hi, lo, key="b")
    assert set(base) == set(daily.DAILY_COLUMNS)
    for name in base:
        assert np.allclose(base[name], again[name], equal_nan=True), f"{name} looked at a day that had not closed"
    edge = np.array([D0 + 301 * DAY + 0.0])                                  # the very moment day 300 closes
    just_before = daily.feature_columns(bars, edge - 1, price, hi, lo, key="c")
    just_before_changed = daily.feature_columns(changed, edge - 1, price, hi, lo, key="d")
    assert np.allclose(just_before["dl_ret1"], just_before_changed["dl_ret1"])
    after, after_changed = (daily.feature_columns(b, edge, price, hi, lo, key=k) for b, k in ((bars, "e"), (changed, "f")))
    assert after["dl_ret1"][0] != after_changed["dl_ret1"][0], "once day 300 has closed it counts"


def test_too_little_history_gives_nothing():
    assert daily.feature_columns(walk(30), np.array([D0 + 40 * DAY]), np.array([1.0]), np.array([1.0]), np.array([1.0])) == {}
    assert daily.brief(walk(30), D0 + 40 * DAY, price=2000.0) == {}


def test_the_memory_gets_the_dl_group_only_with_daily_bars():
    base = [{"t": float(D0 + 800 * DAY + i * 300), "o": 2000.0 + i * 0.01, "h": 2001.0 + i * 0.01, "l": 1999.0 + i * 0.01,
             "c": 2000.5 + i * 0.01, "v": 10.0} for i in range(288 * 3)]
    bars = memory.resample(base, 900, base[-1]["t"] + 300)
    plain, names = memory.feature_matrix(bars, 900)
    with_daily, names_d = memory.feature_matrix(bars, 900, daily_bars=walk(1000))
    assert not any(n.startswith("dl_") for n in names)
    assert sorted(n for n in names_d if n.startswith("dl_")) == sorted(daily.DAILY_COLUMNS)
    assert with_daily.shape[1] == plain.shape[1] + len(daily.DAILY_COLUMNS)
    assert names_d[: len(names)] == names, "the existing columns keep their places"


def test_a_cycle_that_is_really_there_passes_and_noise_does_not():
    planted = np.array([b["c"] for b in walk(700, cycle=1.5, period=40.0)])
    found = forecast.cycle_view(planted)
    assert found["passed"] and any(abs(f["period_days"] - 40) <= 6 for f in found["passed"])
    noise = np.array([b["c"] for b in walk(700, seed=5)])
    assert forecast.cycle_view(noise)["passed"] == []


def test_the_network_claims_an_edge_only_when_it_beats_a_constant_guess_on_unseen_days():
    noise = walk(800, seed=7)
    ind = daily.indicators(noise)
    result = forecast.walk_forward(ind, len(noise) - 1)
    assert result["1d"]["edge"] is False and "p_up" not in result["1d"], "a number that did not earn its place is withheld"
    momentum = walk(800, seed=7, ar=0.7)
    ind = daily.indicators(momentum)
    result = forecast.walk_forward(ind, len(momentum) - 1)
    assert result["1d"]["edge"] is True and result["1d"]["oos_hit"] > result["1d"]["constant_guess_hit"] + 0.05
    assert 0.0 <= result["1d"]["p_up"] <= 1.0


def test_the_brief_says_what_it_rests_on_and_what_failed(tmp_path):
    bars = walk(900, seed=2)
    now = bars[-1]["t"] + DAY + 10 * 3600
    brief = daily.brief(bars, now, price=bars[-1]["c"], day_hi=bars[-1]["c"] * 1.01, day_lo=bars[-1]["c"] * 0.99, key="t")
    for key in ("days", "atr_d", "trend", "volatility", "regime", "today", "next_day", "after_a_day", "forecast", "note"):
        assert key in brief
    assert brief["forecast"]["verdict"].startswith("none of the three passed")
    assert 0 <= brief["today"]["days_wider_pct"] <= 100
    low, high = brief["next_day"]["move_90pct_atr"]
    assert low < 0 < high


def test_the_analyst_prompt_carries_the_daily_block_only_when_there_is_a_file(tmp_path, monkeypatch):
    bars = walk(300)
    monkeypatch.setattr(daily, "load", lambda symbol, folder=None: bars if symbol == "SYN" else [])
    now = bars[-1]["t"] + DAY + 3600
    assert "daily" in analyst._daily_block("SYN", [], now, bars[-1]["c"])
    assert analyst._daily_block("OTHER", [], now, 1.0) == {}


def test_a_synthetic_day_is_the_candle_it_was_drawn_from(tmp_path):
    from trading.quant import synth

    days = walk(40, seed=9)
    profile = np.linspace(0.5, 1.5, 24)
    for k, day in enumerate(days):
        day = {**day, "v": 5000.0}                 # at least one tick a minute, so a smaller day is raised to 1440
        m1 = synth.day_bars(day, np.random.default_rng(k), profile)
        m5 = synth.to_m5(m1)
        assert len(m1) == 1440 and len(m5) == 288
        assert m1[0]["o"] == day["o"] and m1[-1]["c"] == day["c"]
        assert max(b["h"] for b in m1) == pytest.approx(day["h"]) and min(b["l"] for b in m1) == pytest.approx(day["l"])
        assert all(b["l"] <= min(b["o"], b["c"]) and b["h"] >= max(b["o"], b["c"]) for b in m1 + m5)
        assert sum(b["v"] for b in m1) == pytest.approx(day["v"])
        assert m5[0]["o"] == day["o"] and m5[-1]["c"] == day["c"]
    assert synth.series(days[:3], seed=4) == synth.series(days[:3], seed=4), "the same seed draws the same history"
    assert synth.series(days[:3], seed=4) != synth.series(days[:3], seed=5)
    path = synth.write("XAUUSDcheck", synth.series(days[:2]), tmp_path)
    assert path.name == "XAUUSDcheck-synthetic-M5.csv" and len(path.read_text().splitlines()) == 576


def test_the_hour_profile_follows_where_the_moves_are():
    from trading.quant import synth

    bars = [{"t": float(D0 + i * 300), "o": 100.0, "h": 102.0, "l": 99.0, "c": 100.0 + (2.0 if (i * 300 % 86400) // 3600 == 8 else 0.1),
             "v": 1.0} for i in range(288 * 5)]
    profile = synth.hour_profile(bars)
    assert profile.argmax() == 8 and profile[8] > 5 * profile[3]
    assert np.array_equal(synth.hour_profile(bars[:50]), np.ones(24)), "too little data: flat"


def test_every_row_counts_equally_unless_recency_is_switched_on(monkeypatch):
    from trading.quant import neural

    calls: list[int] = []
    real = neural.recency_weights
    monkeypatch.setattr(neural, "recency_weights", lambda n, *a, **k: calls.append(n) or real(n, *a, **k))
    rng = np.random.default_rng(0)
    x = rng.normal(size=(60, 4))
    y = rng.integers(0, 3, size=(60, len(neural.PLANS)))
    res = rng.normal(size=(60, len(neural.PLANS)))
    names = [f"f{i}" for i in range(4)]
    monkeypatch.delenv("TRADING_BRAIN_RECENCY", raising=False)
    neural.fit(x, y, res, names=names, members=1, epochs=1)
    assert calls == [], "by default no row is down-weighted"
    monkeypatch.setenv("TRADING_BRAIN_RECENCY", "on")
    neural.fit(x, y, res, names=names, members=1, epochs=1)
    assert calls == [60]
