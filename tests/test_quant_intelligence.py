"""The smarter entry rule: pullback window, higher timeframe, room to the target, rollover hours,
adaptive target and score; the stop-management suggestion; the backtest and API that use them.

Offline. Bars are synthetic, and the fixture sets each feature explicitly so a test does not
depend on what a random walk happened to do.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest
from fastapi.testclient import TestClient

from trading.quant import backtest as B
from trading.quant import features as F
from trading.quant import model as M

STATES = {"trend": 0, "range": 1, "highvol": 2}
TREND = [0.85, 0.1, 0.05]


def _bars(n: int = 120, seed: int = 3, hour: int = 9) -> list[dict[str, float]]:
    rng = random.Random(seed)
    start = int(dt.datetime(2026, 11, 4, hour, 0, tzinfo=dt.UTC).timestamp()) - (n - 1) * 300
    out, px = [], 1.1
    for i in range(n):
        o = px
        c = px + rng.gauss(0, 0.0005)
        out.append({"t": start + i * 300, "o": o, "h": max(o, c) + 0.0002, "l": min(o, c) - 0.0002, "c": c, "v": 100})
        px = c
    return out


def _setup(bars=None, *, z=(-1.0, -0.3, -0.2), htf=0, adx=(20.0, 30.0), clear_highs=True):
    """A valid BUY on the last bar unless a test changes one thing. z = (Z_{t-2}, Z_{t-1}, Z_t)."""
    bars = bars or _bars()
    f = F.compute(bars)
    i = len(bars) - 1
    f.z[i - 2], f.z[i - 1], f.z[i] = z
    f.slope[i] = 1.0
    if clear_highs:   # no swing high above the entry inside the lookback
        top = bars[i - 1]["h"]
        for b in bars[i - M.ROOM_LOOKBACK - 2: i - 1]:
            b["h"] = min(b["h"], top - 0.0001)
            b["l"] = min(b["l"], b["h"] - 0.0001)
    bars[i]["c"] = bars[i - 1]["h"] + 0.0005
    bars[i]["h"] = max(bars[i]["h"], bars[i]["c"])
    f.vwap[i] = bars[i]["c"] - 0.001
    f.adx[i - M.ADX_LOOKBACK], f.adx[i] = adx
    f.htf_bias = [0] * len(bars)
    f.htf_bias[i] = htf
    return bars, f, i


def _decide(bars, f, i, post=TREND, spread=None):
    return M.decide("EURUSD", bars, f, post, STATES, i, spread=spread)


@pytest.fixture(autouse=True)
def _default_hours(monkeypatch):
    monkeypatch.delenv("QUANT_BLOCK_HOURS_UTC", raising=False)
    monkeypatch.delenv("QUANT_MIN_SCORE", raising=False)


def test_a_pullback_two_bars_back_still_counts():
    bars, f, i = _setup(z=(-1.0, -0.3, -0.2))
    p = _decide(bars, f, i)
    assert p.side == "buy", p.blocked
    assert any("pullback to -1.00" in r for r in p.reasons)


def test_no_pullback_in_the_window_means_no_entry():
    bars, f, i = _setup(z=(-0.3, -0.3, -0.2))
    f.z[i - 3] = -0.2
    assert _decide(bars, f, i).side is None


def test_higher_timeframe_against_the_trade_blocks_it():
    bars, f, i = _setup(htf=-1)
    p = _decide(bars, f, i)
    assert p.side is None and "higher timeframe" in p.blocked


def test_rollover_hour_blocks_new_entries_and_can_be_turned_off(monkeypatch):
    bars, f, i = _setup(_bars(hour=21))
    assert "rollover" in (_decide(bars, f, i).blocked or "")
    monkeypatch.setenv("QUANT_BLOCK_HOURS_UTC", "")
    assert _decide(bars, f, i).side == "buy"


def test_a_swing_high_just_above_the_entry_blocks_it():
    bars, f, i = _setup()
    d = M.stop_distance(bars, i, f.atr[i], "buy")
    k = i - 10   # a clear swing high half a stop above the entry
    bars[k]["h"] = bars[i]["c"] + 0.5 * d
    p = _decide(bars, f, i)
    assert p.side is None and "no room" in p.blocked


def test_a_strong_setup_aims_for_two_r_and_a_standard_one_for_one_and_a_half():
    bars, f, i = _setup(htf=1, adx=(20.0, 32.0))
    strong = _decide(bars, f, i)
    assert strong.side == "buy" and strong.rr == M.RR_STRONG
    d = strong.entry - strong.sl
    assert strong.tp - strong.entry == pytest.approx(M.RR_STRONG * d)
    bars, f, i = _setup(htf=0, adx=(20.0, 32.0))
    standard = _decide(bars, f, i)
    assert standard.rr == M.RR and standard.score < strong.score
    assert 0 <= standard.score <= 100 and M.sanity(strong) == [] and M.sanity(standard) == []


def test_min_score_can_gate_entries(monkeypatch):
    bars, f, i = _setup()
    monkeypatch.setenv("QUANT_MIN_SCORE", "99")
    p = _decide(bars, f, i)
    assert p.side is None and "QUANT_MIN_SCORE" in p.blocked


def test_htf_bias_is_causal_and_unknown_at_first():
    closes = [1.0 + 0.001 * k for k in range(400)]
    bias = F.htf_bias(closes)
    assert bias[: F.HTF_PERIOD] == [0] * F.HTF_PERIOD and bias[-1] == 1
    changed = closes[:300] + [0.5] * 100
    assert F.htf_bias(changed)[:300] == bias[:300]


# ---------------------------------------------------------------- managing an open trade


def _pos(**extra):
    return {"ticket": 7, "side": "buy", "open_price": 1.1000, "sl": 1.0990, **extra}


def test_nothing_changes_before_the_trigger():
    bars = [{"t": 0, "o": 1.1, "h": 1.1010, "l": 1.0995, "c": 1.1005}]   # 1 R in favour
    assert M.manage(_pos(), bars, atr=0.001) is None


def test_break_even_then_trail_and_never_wider():
    be = M.manage(_pos(), [{"t": 0, "o": 1.1, "h": 1.1016, "l": 1.1, "c": 1.1012}], atr=0.001)
    assert be is not None and be["sl"] == pytest.approx(1.1001) and "break-even" in be["why"]
    far = M.manage(_pos(), [{"t": 0, "o": 1.1, "h": 1.1100, "l": 1.1, "c": 1.1090}], atr=0.001)
    assert far is not None and far["sl"] == pytest.approx(1.1100 - M.TRAIL_ATR * 0.001) and "trail" in far["why"]
    # A stop already above the suggestion is left alone.
    assert M.manage(_pos(sl=1.1080, initial_risk=0.001), [{"t": 0, "o": 1.1, "h": 1.1100, "l": 1.1, "c": 1.109}], atr=0.001) is None


def test_sell_management_mirrors_buy():
    pos = {"ticket": 8, "side": "sell", "open_price": 1.1000, "sl": 1.1010}
    be = M.manage(pos, [{"t": 0, "o": 1.1, "h": 1.1, "l": 1.0984, "c": 1.099}], atr=0.001)
    assert be is not None and be["sl"] == pytest.approx(1.0999)


# ---------------------------------------------------------------- backtest and API


def _walk(n: int, seed: int) -> list[dict[str, float]]:
    rng = random.Random(seed)
    bars, px = [], 1.1
    for i in range(n):
        drift = 0.00004 * ((i // 300) % 2 * 2 - 1)
        o = px
        c = px + drift + rng.gauss(0, 0.0006)
        bars.append({"t": 1_700_000_000 + i * 300, "o": o, "h": max(o, c) + abs(rng.gauss(0, 0.0003)),
                     "l": min(o, c) - abs(rng.gauss(0, 0.0003)), "c": c, "v": 100})
        px = c
    return bars


def test_backtest_uses_each_trades_own_target_and_can_manage():
    bars = _walk(3000, seed=11)
    plain = B.run(bars, train=1200, refit=400)
    managed = B.run(bars, train=1200, refit=400, manage=True)
    for t in plain["trades"]:
        risk = abs(t["entry"] - t["sl"])
        assert abs(t["tp"] - t["entry"]) == pytest.approx(t["rr"] * risk, rel=1e-3)
        assert t["outcome"] != "trail"
    assert all(t["outcome"] in ("sl", "tp", "trail", "timeout") for t in managed["trades"])


def test_manage_endpoint_suggests_and_sends_nothing(monkeypatch):
    from trading.mt5_adapter import SimBackend
    from web import mt5_api
    from web.server import app

    monkeypatch.delenv("MT5_TRADE_TOKEN", raising=False)
    backend = SimBackend()
    backend.connect(1, "", "", None)
    mt5_api._state.update(backend=backend, live=False, view_only="")
    try:
        r = TestClient(app, client=("203.0.113.9", 50000)).get("/api/quant/manage")
        assert r.status_code == 200
        body = r.json()
        assert "positions" in body and "Suggestions only" in body["note"]
    finally:
        mt5_api._state.update(backend=None, live=False, view_only="")
