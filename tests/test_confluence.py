"""The combined math behind the "matematika" rule, and the trading sessions. Offline and seeded."""

from __future__ import annotations

import datetime as dt
import random

import numpy as np
import pytest

from addons.trading.quant import confluence, sessions


def trending_bars(n: int = 700, seed: int = 2) -> list[dict[str, float]]:
    """Moves that persist: each bar's step leans on the last one."""
    rng = random.Random(seed)
    out, price, drift = [], 100.0, 0.0
    for i in range(n):
        drift = 0.95 * drift + rng.gauss(0, 0.05)
        close = price + drift
        out.append({"t": 1_800_000_000.0 + i * 5, "o": price, "h": max(price, close) + 0.02, "l": min(price, close) - 0.02, "c": close, "v": 1.0})
        price = close
    return out


def random_bars(n: int = 700, seed: int = 5) -> list[dict[str, float]]:
    rng = random.Random(seed)
    out, price = [], 100.0
    for i in range(n):
        close = price + rng.gauss(0, 0.1)
        out.append({"t": 1_800_000_000.0 + i * 5, "o": price, "h": max(price, close) + 0.03, "l": min(price, close) - 0.03, "c": close, "v": 1.0})
        price = close
    return out


def test_a_persistent_market_is_read_and_passes_its_test():
    bars = trending_bars()
    r = confluence.analyse(bars, horizon=3)
    assert r["ok"] and r["graded"] and r["test_accuracy"] > r["base_rate"]
    recent = bars[-1]["c"] - bars[-4]["c"]
    assert r["side"] == (1 if recent > 0 else -1), "the side follows the move that keeps going"
    assert r["regime"]["hurst"] > 0.55 and r["regime"]["variance_ratio"] > 0
    assert len(r["contributions"]) == len(confluence.NAMES)
    pushes = [abs(c["push"]) for c in r["contributions"]]
    assert pushes == sorted(pushes, reverse=True), "the strongest reasons first"


def test_a_random_walk_mostly_gets_no_side():
    sides = [confluence.analyse(random_bars(seed=s), horizon=6)["side"] for s in range(6)]
    assert sides.count(0) >= 4, "measurements that do not beat a guess on unseen bars give no direction"


def test_no_measurement_looks_ahead():
    bars = trending_bars(400)
    early = confluence.matrix(bars[:300])
    later = confluence.matrix(bars)
    np.testing.assert_allclose(early[-1], later[299], rtol=1e-9, equal_nan=True)


def test_too_little_data_says_so():
    r = confluence.analyse(trending_bars(100), horizon=3)
    assert not r["ok"] and r["side"] == 0 and "butuh" in r["reason"]


def test_regime_measures():
    rng = np.random.default_rng(1)
    noise = rng.normal(size=256)
    assert 0.35 < confluence.hurst(noise) < 0.65
    persistent = np.cumsum(rng.normal(size=256)) * 0.1 + 1     # a strongly autocorrelated series
    assert confluence.hurst(np.diff(persistent).cumsum() + persistent[1:]) > 0.6
    assert confluence.sign_entropy(np.array([1.0] * 20)) == 0
    assert confluence.sign_entropy(np.array([1.0, -1.0] * 10)) == pytest.approx(1.0)


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return dt.datetime(year, month, day, hour, minute, tzinfo=dt.UTC).timestamp()


@pytest.mark.parametrize(("when", "expected"), [
    (_ts(2026, 11, 10, 3), "sydney+tokyo"),          # Tuesday: Sydney afternoon, Tokyo noon
    (_ts(2026, 11, 10, 6), "tokyo"),                 # Sydney has closed (16:00 there)
    (_ts(2026, 11, 10, 14), "london+newyork"),       # winter: London 14:00, New York 09:00
    (_ts(2026, 7, 14, 12, 30), "london+newyork"),    # summer: New York opens at 12:00 UTC
    (_ts(2026, 11, 10, 12, 30), "london"),           # winter: New York not open yet
    (_ts(2026, 11, 10, 23), "sydney"),               # Wednesday morning in Sydney
    (_ts(2026, 11, 14, 12), "weekend"),              # Saturday
])
def test_sessions_follow_each_citys_clock(when, expected):
    assert sessions.key(when) == expected


def test_session_labels():
    assert sessions.label(_ts(2026, 11, 10, 14)) == "London + New York"
    assert sessions.label_of("weekend") == "akhir pekan" and sessions.label_of("off") == "di luar sesi utama"
    info = sessions.describe(_ts(2026, 11, 10, 14))
    assert info["overlap"] and info["new_york"] == "Tue 09:00"
