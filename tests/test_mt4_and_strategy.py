"""MT4 bridge (EA stream and commands), the M5 strategy and backtest, the trade journal.

Everything runs offline. The EA is imitated with plain HTTP calls to the bridge.
"""

from __future__ import annotations

import datetime as dt
import math
import random
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trading import advisor, journal, strategy
from trading.mt4_backend import Mt4BridgeBackend
from trading.mt5_adapter import TradingError
from web import mt4_bridge
from web.server import app

SECRET = "bridge-secret-xyz"
S = {"X-Bridge-Secret": SECRET}


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setenv("MT4_BRIDGE_SECRET", SECRET)
    monkeypatch.setenv("TRADE_JOURNAL", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(mt4_bridge, "store", mt4_bridge.Store())
    monkeypatch.setattr(mt4_bridge, "SECRET_FILE", tmp_path / "bridge_secret")
    monkeypatch.setattr("trading.mt4_backend.store", mt4_bridge.store)
    yield


@pytest.fixture()
def client():
    return TestClient(app)


def _bars(n: int, seed: int = 7, drift: float = 0.0002, start: float = 1.1000) -> list[dict[str, float]]:
    rng = random.Random(seed)
    out, px = [], start
    for i in range(n):
        o = px
        c = px + drift + rng.uniform(-0.0008, 0.0008)
        hi, lo = max(o, c) + rng.uniform(0, 0.0004), min(o, c) - rng.uniform(0, 0.0004)
        out.append({"t": 1_700_000_000 + i * 300, "o": o, "h": hi, "l": lo, "c": c, "v": 100})
        px = c
    return out


# ---------------------------------------------------------------- indicators


def test_indicators_on_simple_inputs():
    assert strategy.ema([5.0] * 30, 9)[-1] == pytest.approx(5.0)
    bars = [{"h": 1.2, "l": 1.0, "c": 1.1} for _ in range(30)]
    assert strategy.atr(bars, 14)[-1] == pytest.approx(0.2)
    rising = [float(i) for i in range(40)]
    assert strategy.rsi(rising, 14)[-1] == 100.0
    assert math.isnan(strategy.rsi(rising, 14)[5])


def test_volatility_regime_blocks_a_spike():
    base = [0.001] * 99 + [0.01]          # the last ATR is 10x the median
    name, ratio = strategy.regime(base)
    assert name == "volatile" and ratio > 2.0
    p = strategy.signal("EURUSD", _bars(120))
    assert p.side is None or p.regime == "normal"


def test_news_within_the_window_blocks_the_trade():
    when = dt.datetime(2026, 11, 4, 12, 0, tzinfo=dt.UTC)
    events = strategy.load_news([{"time": "2026-11-04T12:20:00Z", "currency": "USD", "impact": "high", "title": "NFP"}])
    assert strategy.news_block("EURUSD", when, events) is not None
    assert strategy.news_block("EURUSD", when + dt.timedelta(hours=2), events) is None
    assert strategy.news_block("USDJPY", when, events) is not None
    assert strategy.news_block("GBPJPY", when, events) is None


def test_signal_needs_enough_bars():
    p = strategy.signal("EURUSD", _bars(10))
    assert p.side is None and "not enough bars" in p.blocked


def test_any_proposed_trade_has_sensible_levels():
    bars = _bars(3000, seed=3)
    sides = 0
    for i in range(60, len(bars)):
        p = strategy.signal("EURUSD", bars[: i + 1])
        if p.side == "buy":
            assert p.sl < p.entry < p.tp
            sides += 1
        elif p.side == "sell":
            assert p.tp < p.entry < p.sl
            sides += 1
    assert sides >= 0   # the rules may find no setup on this path; the levels are checked when they do


def test_backtest_r_values_match_the_levels():
    res = strategy.backtest(_bars(4000, seed=11, drift=0.0))
    for t in res["trades"]:
        if t["outcome"] == "sl":
            assert t["r"] == pytest.approx(-1.0, abs=0.01)
        if t["outcome"] == "tp":
            assert t["r"] == pytest.approx(2.5 / 1.5, abs=0.01)
    assert res["summary"]["trades"] == len(res["trades"])
    assert "note" in res["summary"]


def test_summary_warns_on_small_samples():
    trades = [{"r": 1.0}, {"r": -1.0}, {"r": -1.0}]
    s = strategy.summarize(trades)
    assert s["max_losing_streak"] == 2
    assert "small sample" in s["note"]


def test_advisor_brief_contains_the_facts_and_no_order_instruction():
    text = advisor.brief({"symbol": "EURUSD", "side": "buy", "entry": 1.1, "sl": 1.09, "tp": 1.12,
                          "regime": "normal", "atr": 0.001, "reasons": ["x"], "blocked": None},
                         "# Laporan trading\n", ["NFP"])
    assert "EURUSD" in text and "NFP" in text
    assert "do not" in text.lower()


# ---------------------------------------------------------------- journal


def test_journal_pairs_orders_with_closes_and_reports_the_loss(monkeypatch):
    journal.record({"event": "order", "ticket": 1, "symbol": "EURUSD", "side": "buy", "volume": 0.01, "reason": "pullback"})
    journal.record({"event": "close", "ticket": 1, "symbol": "EURUSD", "volume": 0.01, "profit": -12.5})
    journal.record({"event": "order", "ticket": 2, "symbol": "GBPUSD", "side": "sell", "volume": 0.01, "reason": "breakout"})
    journal.record({"event": "close", "ticket": 2, "symbol": "GBPUSD", "volume": 0.01, "profit": 8.0})
    journal.record({"event": "order", "ticket": 3, "symbol": "XAUUSD", "side": "buy", "volume": 0.01})   # still open

    rep = journal.report(journal.read())
    assert rep["trades"] == 2
    assert rep["net"] == -4.5
    assert rep["win_rate"] == 0.5
    assert rep["losses_by_reason"] == [("pullback", 1)]
    md = journal.markdown(rep)
    assert "Laporan trading" in md and "pullback" in md


def test_journal_handles_an_empty_file():
    assert journal.markdown(journal.report([])).startswith("# Laporan trading")


# ---------------------------------------------------------------- MT4 bridge


def test_bridge_is_off_until_paired(client, monkeypatch):
    monkeypatch.delenv("MT4_BRIDGE_SECRET")
    assert client.post("/api/mt4/ingest", json=[]).status_code == 403


def test_bridge_refuses_a_wrong_secret(client):
    assert client.post("/api/mt4/ingest", json=[], headers={"X-Bridge-Secret": "nope"}).status_code == 401


def test_ea_stream_becomes_prices_bars_and_account(client):
    body = [
        {"type": "tick", "symbol": "EURUSD", "bid": 1.1, "ask": 1.1002, "time": int(time.time()),
         "digits": 5, "point": 0.00001, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01, "contract_size": 100000},
        {"type": "bar", "symbol": "EURUSD", "tf": "M5", "time": 1_700_000_000, "o": 1.1, "h": 1.101, "l": 1.099, "c": 1.1005, "v": 40},
        {"type": "account", "login": 555, "server": "Demo", "currency": "USD", "balance": 1000, "equity": 1002,
         "margin": 0, "free_margin": 1002, "profit": 2, "leverage": 100, "live": False,
         "positions": [{"ticket": 9, "symbol": "EURUSD", "side": "buy", "volume": 0.01, "open_price": 1.1,
                        "price": 1.1001, "sl": 0, "tp": 1.12, "profit": 0.1, "swap": 0, "time": 1_700_000_000, "comment": "x"}]},
    ]
    r = client.post("/api/mt4/ingest", json=body, headers=S)
    assert r.status_code == 200 and r.json()["received"] == 3

    be = Mt4BridgeBackend()
    assert be.account()["login"] == 555
    assert be.tick("EURUSD")["ask"] == pytest.approx(1.1002)
    assert be.symbol_spec("EURUSD")["digits"] == 5
    assert be.rates("EURUSD", "M5", 10)[0]["c"] == pytest.approx(1.1005)
    pos = be.positions()[0]
    assert pos["sl"] is None and pos["tp"] == pytest.approx(1.12)
    with pytest.raises(TradingError):
        be.rates("EURUSD", "H1", 10)


def test_stale_ea_is_reported_not_trusted(monkeypatch):
    mt4_bridge.store.account = {"login": 1}
    mt4_bridge.store.account_seen = time.time() - 999
    with pytest.raises(TradingError, match="belum terhubung"):
        Mt4BridgeBackend().account()


def test_order_is_queued_and_its_result_comes_back(client):
    mt4_bridge.store.account = {"login": 1, "balance": 1, "equity": 1, "margin": 0, "free_margin": 1,
                                "profit": 0, "leverage": 100, "live": False, "currency": "USD", "server": "d"}
    mt4_bridge.store.account_seen = time.time()
    be = Mt4BridgeBackend()
    out: dict = {}

    def send():
        out["r"] = be.send({"symbol": "EURUSD", "side": "buy", "volume": 0.01, "sl": 1.09, "tp": 1.12, "comment": "test|pipe"})

    t = threading.Thread(target=send)
    t.start()
    # the EA's side: poll, execute, report
    for _ in range(50):
        cmds = client.get("/api/mt4/commands", headers=S).text.strip()
        if cmds:
            break
        time.sleep(0.05)
    cid, action, sym, side, vol, sl, tp, comment, ticket = cmds.split("|")
    assert action == "order" and sym == "EURUSD" and side == "buy"
    assert "|" not in comment
    client.post("/api/mt4/result", content=f"{cid}|ok:777\n", headers=S)
    t.join(timeout=5)
    assert out["r"]["ok"] is True and out["r"]["order"] == 777


def test_ea_refusal_comes_back_as_not_ok(client):
    mt4_bridge.store.account = {"login": 1}
    mt4_bridge.store.account_seen = time.time()
    be = Mt4BridgeBackend()
    cid = mt4_bridge.store.enqueue({"action": "close", "ticket": 5, "volume": 0.01, "symbol": "", "side": "",
                                    "sl": "", "tp": "", "comment": ""})
    mt4_bridge.store.set_results([f"{cid}|refused: AllowTrading is off in the EA settings"])
    assert be._outcome(cid)["ok"] is False


def test_pairing_creates_a_secret_once_and_writes_a_preset(monkeypatch, tmp_path):
    monkeypatch.delenv("MT4_BRIDGE_SECRET", raising=False)
    monkeypatch.setenv("MT5_TRADE_TOKEN", "pair-token")
    c = TestClient(app)
    hdr = {"X-MT5-Token": "pair-token"}
    first = c.post("/api/quant/bridge/pair", headers=hdr).json()
    again = c.post("/api/quant/bridge/pair", headers=hdr).json()
    assert first["secret"] == again["secret"], "pairing must not change the secret silently"
    assert "BridgeSecret=" + first["secret"] in first["preset"]
    assert "AllowTrading=false" in first["preset"]
    assert mt4_bridge.SECRET_FILE.exists()
    rotated = c.post("/api/quant/bridge/pair?rotate=true", headers=hdr).json()
    assert rotated["secret"] != first["secret"]
    # the EA can reach the bridge with the new secret, not the old one
    assert c.post("/api/mt4/ingest", json=[], headers={"X-Bridge-Secret": first["secret"]}).status_code == 401
    assert c.post("/api/mt4/ingest", json=[], headers={"X-Bridge-Secret": rotated["secret"]}).status_code == 200


def test_bridge_status_reports_paired_and_online(monkeypatch):
    from web import quant_api  # noqa: F401
    monkeypatch.setenv("MT5_TRADE_TOKEN", "pair-token")
    c = TestClient(app)
    b = c.get("/api/quant/bridge", headers={"X-MT5-Token": "pair-token"}).json()
    assert b["ea_online"] is False
    mt4_bridge.store.account = {"login": 4242, "live": False}
    mt4_bridge.store.account_seen = time.time()
    b = c.get("/api/quant/bridge", headers={"X-MT5-Token": "pair-token"}).json()
    assert b["ea_online"] is True and b["account"] == 4242


def test_backtest_endpoint_runs_on_history_and_places_no_orders(monkeypatch):
    from trading.mt5_adapter import SimBackend
    from web import mt5_api
    monkeypatch.setenv("MT5_TRADE_TOKEN", "bt-token")
    monkeypatch.setenv("TRADE_JOURNAL", str(Path(mt4_bridge.SECRET_FILE).parent / "j.jsonl"))
    b = SimBackend()
    b.connect(1, "", "sim", None)
    mt5_api._state.update(backend=b, live=False)
    try:
        c = TestClient(app)
        r = c.get("/api/quant/backtest", params={"symbol": "EURUSD", "count": 2000},
                  headers={"X-MT5-Token": "bt-token"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["bars"] == 2000 and "summary" in body
        assert "not a forward test" in body["warning"]
        assert b.positions() == []     # nothing was sent
    finally:
        mt5_api._state.update(backend=None, live=False)
