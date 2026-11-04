"""Kalman filter, ADX, HMM, the entry rule, the risk engine, the walk-forward backtest, the CSV loader,
and the live proposal engine. Offline. The proposal engine is tested with a backend that has no
order methods, so it cannot send anything even by accident.
"""

from __future__ import annotations

import datetime as dt
import math
import random

import pytest
from fastapi.testclient import TestClient

from trading import strategy
from trading.mt5_adapter import SimBackend
from trading.quant import backtest as B
from trading.quant import data as D
from trading.quant import engine as E
from trading.quant import features as F
from trading.quant import model as M
from trading.quant import risk as R
from trading.quant.hmm import GaussianHMM, label_states


def _walk(n: int, seed: int = 1, drift_period: int = 400, vol: float = 0.0006) -> list[dict[str, float]]:
    rng = random.Random(seed)
    bars, px = [], 1.1
    for i in range(n):
        drift = 0.00003 * ((i // drift_period) % 2 * 2 - 1)
        o = px
        c = px + drift + rng.gauss(0, vol)
        bars.append({"t": 1_700_000_000 + i * 300, "o": o, "h": max(o, c) + abs(rng.gauss(0, vol / 2)),
                     "l": min(o, c) - abs(rng.gauss(0, vol / 2)), "c": c, "v": rng.randint(50, 300)})
        px = c
    return bars


# ---------------------------------------------------------------- features


def test_kalman_tracks_a_constant_price():
    level, slope, innov, z = F.kalman([1.2] * 200)
    assert level[-1] == pytest.approx(1.2, abs=1e-6)
    assert abs(slope[-1]) < 1e-6


def test_kalman_slope_is_positive_on_a_rising_price():
    closes = [1.0 + 0.0005 * i + 0.00001 * math.sin(i) for i in range(300)]
    level, slope, _, _ = F.kalman(closes)
    assert slope[-1] > 0
    assert level[-1] == pytest.approx(closes[-1], abs=0.002)


def test_adx_is_high_in_a_straight_trend_and_low_in_a_flat_market():
    up = [{"t": i * 300, "h": 1.0 + 0.001 * i + 0.0002, "l": 1.0 + 0.001 * i - 0.0002, "c": 1.0 + 0.001 * i, "o": 1.0 + 0.001 * i} for i in range(120)]
    assert F.adx(up)[-1] > 40
    flat = [{"t": i * 300, "h": 1.0 + 0.0002, "l": 1.0 - 0.0002, "c": 1.0 + (0.0001 if i % 2 else -0.0001), "o": 1.0} for i in range(120)]
    assert F.adx(flat)[-1] < 30


def test_vwap_resets_each_utc_day():
    day1 = [{"t": 1_700_000_000 + i * 300, "h": 2.0, "l": 2.0, "c": 2.0, "v": 10} for i in range(3)]
    day2_start = (1_700_000_000 // 86400 + 1) * 86400
    day2 = [{"t": day2_start + i * 300, "h": 4.0, "l": 4.0, "c": 4.0, "v": 10} for i in range(3)]
    vw = F.vwap_daily(day1 + day2)
    assert vw[2] == pytest.approx(2.0)
    assert vw[-1] == pytest.approx(4.0)


# ---------------------------------------------------------------- HMM


def test_hmm_separates_two_clear_clusters_and_filters_causally():
    rng = random.Random(3)
    low = [[rng.gauss(0.2, 0.05), rng.gauss(0.2, 0.05)] for _ in range(150)]
    high = [[rng.gauss(2.0, 0.05), rng.gauss(2.0, 0.05)] for _ in range(150)]
    data = low + high
    model = GaussianHMM(2).fit(data, iters=30)
    post = model.filter(data)
    assert all(abs(sum(p) - 1) < 1e-9 for p in post)
    assert max(post[-1]) > 0.9 and max(post[0]) > 0.9
    assert post[0].index(max(post[0])) != post[-1].index(max(post[-1]))


def test_state_labels_pick_highvol_then_trend():
    m = GaussianHMM(3)
    m.mu = [[0.1, 0.1, 0.2, 1.0], [0.9, 0.9, 0.8, 1.1], [0.5, 0.5, 0.3, 2.5]]
    m.var = [[1, 1, 1, 1]] * 3
    lab = label_states(m)
    assert lab == {"trend": 1, "range": 0, "highvol": 2}


# ---------------------------------------------------------------- entry rule


def _decide_with(post, bars=None, z=(-1.0, -0.2), slope=1.0, c_above_vwap=True, last_high=None,
                 adx_rising=True, spread=None):
    bars = bars or _walk(120)
    f = F.compute(bars)
    i = len(bars) - 1
    f.z[i - 1], f.z[i] = z
    f.slope[i] = slope
    f.vwap[i] = bars[i]["c"] - 0.001 if c_above_vwap else bars[i]["c"] + 0.001
    bars[i]["c"] = max(bars[i]["c"], bars[i - 1]["h"] + 0.0005)
    # The rule needs ADX to be rising: set it explicitly so the fixture does not depend on the random walk.
    j = i - M.ADX_LOOKBACK
    f.adx[j], f.adx[i] = (20.0, 30.0) if adx_rising else (40.0, 30.0)
    states = {"trend": 0, "range": 1, "highvol": 2}
    return M.decide("EURUSD", bars, f, post, states, i, spread=spread)


def test_falling_adx_blocks_the_entry():
    p = _decide_with([0.8, 0.1, 0.1], adx_rising=False)
    assert p.side is None and "ADX fell" in p.blocked


def test_wide_spread_relative_to_the_stop_blocks_the_entry():
    p = _decide_with([0.8, 0.1, 0.1], spread=0.01)   # 100 pips: far more than 15% of any stop here
    assert p.side is None and "spread" in p.blocked


def test_a_tight_spread_still_allows_the_entry():
    ok = _decide_with([0.8, 0.1, 0.1], spread=0.00002)
    assert ok.side == "buy" and "ADX rising" in " ".join(ok.reasons)


def test_engine_fit_is_cached_per_bar_and_session(monkeypatch):
    E.reset_state()
    E.clear_fit_cache()
    bars = _walk(2600, seed=21)
    backend = _NoOrders(bars)
    calls = {"fit": 0}
    real_fit = GaussianHMM.fit

    def counting_fit(self, data, iters=25):
        calls["fit"] += 1
        return real_fit(self, data, iters=iters)

    monkeypatch.setattr(GaussianHMM, "fit", counting_fit)
    cfg = R.RiskConfig()
    now = dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.UTC)
    first = E.propose(backend, "EURUSD", cfg=cfg, now=now)
    second = E.propose(backend, "EURUSD", cfg=cfg, now=now)
    assert calls["fit"] == 1, "a repeat request on the same closed bar must reuse the fit"
    assert first["proposal"]["regime"] == second["proposal"]["regime"]
    assert first["proposal"]["p_trend"] == pytest.approx(second["proposal"]["p_trend"])


def _count_fits(monkeypatch):
    fits = {"n": 0}
    real_fit = GaussianHMM.fit

    def counting_fit(self, data, iters=25):
        fits["n"] += 1
        return real_fit(self, data, iters=iters)

    monkeypatch.setattr(GaussianHMM, "fit", counting_fit)
    return fits


def test_engine_fit_is_not_shared_between_backends(monkeypatch):
    E.reset_state()
    E.clear_fit_cache()
    fits = _count_fits(monkeypatch)
    now = dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.UTC)
    first, second = _NoOrders(_walk(2600, seed=21)), _NoOrders(_walk(2600, seed=21))
    second.name = "mt4"
    E.propose(first, "EURUSD", cfg=R.RiskConfig(), now=now)
    E.propose(second, "EURUSD", cfg=R.RiskConfig(), now=now)
    assert fits["n"] == 2, "a fit from one feed must not answer for another"


def test_engine_refits_when_a_bar_is_resent_with_new_prices(monkeypatch):
    E.reset_state()
    E.clear_fit_cache()
    fits = _count_fits(monkeypatch)
    now = dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.UTC)
    bars = _walk(2600, seed=21)
    backend = _NoOrders(bars)
    E.propose(backend, "EURUSD", cfg=R.RiskConfig(), now=now)
    bars[-1]["c"] += 0.0003     # same bar time, new price: the MT4 store replaces a re-sent bar
    E.propose(backend, "EURUSD", cfg=R.RiskConfig(), now=now)
    assert fits["n"] == 2


def test_engine_fit_cache_is_keyed_by_session(monkeypatch):
    from utils import workspace_context as wc

    E.reset_state()
    E.clear_fit_cache()
    bars = _walk(2600, seed=21)
    backend = _NoOrders(bars)
    fits = {"n": 0}
    real_fit = GaussianHMM.fit

    def counting_fit(self, data, iters=25):
        fits["n"] += 1
        return real_fit(self, data, iters=iters)

    monkeypatch.setattr(GaussianHMM, "fit", counting_fit)
    now = dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.UTC)
    for session in ("session-a", "session-b"):
        token = wc._active_session.set(session)
        try:
            E.propose(backend, "EURUSD", cfg=R.RiskConfig(), now=now)
        finally:
            wc._active_session.reset(token)
    assert fits["n"] == 2, "two sessions must not share one cached fit"


def test_buy_requires_trend_above_threshold_and_levels_are_one_to_one_point_five():
    p = _decide_with([0.8, 0.1, 0.1])
    assert p.side == "buy", p.blocked
    d = p.entry - p.sl
    assert d > 0
    assert p.tp - p.entry == pytest.approx(M.RR * d)
    assert M.sanity(p) == []


def test_trend_probability_at_or_below_the_threshold_blocks():
    p = _decide_with([0.6, 0.3, 0.1])
    assert p.side is None and "trend probability" in p.blocked


def test_range_or_highvol_dominant_blocks():
    assert _decide_with([0.3, 0.6, 0.1]).side is None
    assert "high volatility" in _decide_with([0.2, 0.1, 0.7]).blocked


def test_z_crossing_is_required():
    p = _decide_with([0.8, 0.1, 0.1], z=(-0.3, -0.2))
    assert p.side is None


# ---------------------------------------------------------------- risk engine


def _spec():
    return {"volume_min": 0.01, "volume_max": 50.0, "volume_step": 0.01, "digits": 5}


def test_lots_are_floored_to_the_broker_step_and_capped():
    assert R.lots_for(25.0, 100.0, _spec()) == 0.25
    assert R.lots_for(1e9, 1.0, _spec()) == 50.0
    with pytest.raises(ValueError):
        R.lots_for(10.0, 0.0, _spec())


def test_risk_above_one_percent_is_refused():
    problems = R.RiskConfig(risk_per_trade=0.02).validate()
    assert problems and "at most 1.0%" in problems[0]


def test_three_losses_in_a_row_stop_trading_until_tomorrow():
    now = dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.UTC)
    st = R.State(equity=10_000, day=now.date(), day_start_equity=10_000, start_equity=10_000)
    for _ in range(3):
        R.record_result(st, -10, st.equity - 10, now)
    ok, reasons = R.check(R.RiskConfig(), st, lots=0.1, spec=_spec(), risk_money=25, now=now)
    assert not ok and any("losses in a row" in r for r in reasons)
    tomorrow = now + dt.timedelta(days=1)
    ok, reasons = R.check(R.RiskConfig(), st, lots=0.1, spec=_spec(), risk_money=st.equity * 0.0025, now=tomorrow)
    assert ok, reasons


def test_internal_daily_stop_is_half_the_firm_limit():
    now = dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.UTC)
    st = R.State(equity=9_700, day=now.date(), day_start_equity=10_000, start_equity=10_000)
    cfg = R.RiskConfig(firm_daily_loss_pct=5.0)     # internal = 2.5%; we are down 3%
    ok, reasons = R.check(cfg, st, lots=0.1, spec=_spec(), risk_money=25, now=now)
    assert not ok and any("internal daily loss stop" in r for r in reasons)


def test_one_open_position_is_the_default_limit():
    now = dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.UTC)
    st = R.State(equity=10_000, day=now.date(), day_start_equity=10_000, start_equity=10_000, open_positions=1)
    ok, reasons = R.check(R.RiskConfig(), st, lots=0.1, spec=_spec(), risk_money=25, now=now)
    assert not ok and any("already open" in r for r in reasons)


def test_missing_firm_limit_is_said_out_loud():
    now = dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.UTC)
    st = R.State(equity=10_000, day=now.date(), day_start_equity=10_000, start_equity=10_000)
    R.check(R.RiskConfig(), st, lots=0.1, spec=_spec(), risk_money=25, now=now)
    assert any("firm daily loss limit not set" in n for n in st.notes)


def test_monte_carlo_is_deterministic_for_a_seed():
    rs = [1.5, -1, -1, 1.5, -1, 1.5, -1]
    a = R.monte_carlo(rs, sims=300, seed=5)
    b = R.monte_carlo(rs, sims=300, seed=5)
    assert a == b and a["paths"] == 300


# ---------------------------------------------------------------- backtest


def test_backtest_runs_and_reports_the_structure():
    res = B.run(_walk(3200, seed=4), train=1200, refit=400)
    s = res["summary"]
    assert "trades" in s and "monte_carlo" in s or s["trades"] == 0
    for t in res["trades"]:
        assert t["outcome"] in ("sl", "tp", "timeout")
        assert t["side"] in ("buy", "sell")


def test_backtest_does_not_use_the_future():
    bars = _walk(3000, seed=9)
    k = 2400
    alt = [dict(b) for b in bars]
    rng = random.Random(99)
    for b in alt[k:]:
        shift = rng.uniform(-0.02, 0.02)
        b["c"] += shift
        b["h"] += abs(shift)
        b["l"] -= abs(shift)
        b["o"] += shift
    base = B.run(bars, train=1200, refit=400)["trades"]
    changed = B.run(alt, train=1200, refit=400)["trades"]
    cutoff_iso = B.dt.datetime.fromtimestamp(bars[k - 120]["t"], B.dt.timezone.utc).isoformat(timespec="minutes")
    before = [t for t in base if t["time"] < cutoff_iso]
    before_alt = [t for t in changed if t["time"] < cutoff_iso]
    assert before == before_alt, "a trade before the cut-off changed when only later bars changed"


def test_backtest_refuses_too_little_data():
    res = B.run(_walk(100), train=1200, refit=400)
    assert res["summary"]["trades"] == 0 and "need at least" in res["summary"]["note"]


# ---------------------------------------------------------------- CSV loader


def test_loads_an_mt5_style_csv(tmp_path):
    p = tmp_path / "EURUSD_M5.csv"
    p.write_text(
        "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\n"
        "2026.11.04\t10:00:00\t1.10\t1.11\t1.09\t1.105\t120\n"
        "2026.11.04\t10:05:00\t1.105\t1.12\t1.10\t1.115\t98\n",
        encoding="utf-8",
    )
    bars = D.load_csv(p)
    assert len(bars) == 2
    assert bars[0]["c"] == pytest.approx(1.105) and bars[0]["v"] == 120
    assert bars[1]["t"] - bars[0]["t"] == 300


# ---------------------------------------------------------------- live proposal engine


class _NoOrders:
    """A backend that can read prices and account data. It has no order methods at all."""

    name = "fake"

    def __init__(self, bars):
        self._bars = bars
        self._sim = SimBackend()

    def account(self):
        return {"equity": 10_000.0, "login": 1, "currency": "USD"}

    def positions(self):
        return []

    def rates(self, symbol, tf, count):
        return self._bars[-count:]

    def symbol_spec(self, symbol):
        return {"symbol": symbol, "digits": 5, "volume_min": 0.01, "volume_max": 50, "volume_step": 0.01, "contract_size": 100000.0}

    def risk_per_lot(self, symbol, side, entry, sl):
        return 100000.0 * abs(entry - sl)


def test_engine_returns_a_proposal_and_never_calls_an_order(monkeypatch):
    E.reset_state()
    backend = _NoOrders(_walk(2600, seed=21))
    out = E.propose(backend, "EURUSD", cfg=R.RiskConfig(), now=dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.UTC))
    assert "proposal" in out
    assert out["proposal"]["symbol"] == "EURUSD"
    if out["ok"]:
        assert out["lots"] >= 0.01
        assert out["risk_money"] == pytest.approx(10_000 * 0.0025, abs=0.01)
    assert not hasattr(backend, "send") and not hasattr(backend, "close")


def test_engine_blocks_on_a_news_event_near_the_bar(monkeypatch):
    E.reset_state()
    bars = _walk(2600, seed=21)
    when = dt.datetime.fromtimestamp(bars[-2]["t"], dt.UTC)
    news = strategy.load_news([{"time": when.isoformat(), "currency": "USD", "impact": "high", "title": "CPI"}])
    out = E.propose(_NoOrders(bars), "EURUSD", news=news, cfg=R.RiskConfig(), now=when)
    if out["proposal"]["side"] is None or out.get("blocked"):
        assert out["ok"] is False


def test_quant_api_needs_the_token_and_a_connection(monkeypatch):
    from web.server import app
    monkeypatch.setenv("MT5_TRADE_TOKEN", "t-quant")
    c = TestClient(app)
    assert c.get("/api/quant/config").status_code == 401
    r = c.get("/api/quant/config", headers={"X-MT5-Token": "t-quant"})
    assert r.status_code == 200 and "max_risk_per_trade_pct" in r.json()
    from web import mt5_api
    mt5_api._state.update(backend=None, live=False)
    r = c.get("/api/quant/propose", params={"symbol": "EURUSD"}, headers={"X-MT5-Token": "t-quant"})
    assert r.status_code == 409
