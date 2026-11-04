"""The demo forward test, on the simulator with a fake clock. No sleeping and no network."""

from __future__ import annotations

import json
import random

import pytest
from fastapi.testclient import TestClient

from trading import journal
from trading.quant import forward
from trading.quant.forward import ForwardTest


class Backend:
    name = "fake"

    def __init__(self, live=False, refuse=False, equity=10_000.0):
        self.live, self.refuse, self.equity = live, refuse, equity
        self.sent, self.closed, self.open = [], [], {}
        self.next = 100

    def account(self):
        return {"equity": self.equity, "balance": self.equity, "live": self.live}

    def tick(self, symbol):
        return {"bid": 2000.0, "ask": 2000.2}

    def symbol_spec(self, symbol):
        return {"digits": 2}

    def send(self, order):
        self.sent.append(order)
        if self.refuse:
            return {"ok": False, "comment": "error:133"}
        self.next += 1
        self.open[self.next] = {"ticket": self.next, "profit": 1.5, "swap": 0.0}
        return {"ok": True, "order": self.next}

    def positions(self):
        return list(self.open.values()) + [{"ticket": 7, "profit": -9.0}]

    def close(self, ticket, volume):
        self.closed.append(ticket)
        self.open.pop(ticket, None)
        return {"ok": True}


def _bars(n=300, seed=4):
    rng = random.Random(seed)
    out, px = [], 2000.0
    for i in range(n):
        c = px + rng.gauss(0.05, 0.4)
        out.append({"t": 1_700_000_000.0 + i * 5, "o": px, "h": max(px, c) + 0.1, "l": min(px, c) - 0.1, "c": c, "v": 3.0})
        px = c
    return out


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def private_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_JOURNAL", str(tmp_path / "journal.jsonl"))


def _test(backend, tmp_path, minutes=2.0, **kwargs):
    clock = Clock()
    run = ForwardTest(backend, "XAUUSD", minutes, bars=lambda s: _bars(), sleep=clock.sleep, clock=clock, folder=tmp_path, **kwargs)
    return run, clock


def test_each_basket_is_five_positions_with_a_stop_and_a_target(tmp_path):
    backend = Backend()
    run, _ = _test(backend, tmp_path)
    run.run()
    assert run.baskets and len(backend.sent) == 5 * len(run.baskets)
    first = backend.sent[:5]
    assert all(o["volume"] == forward.LOT and o["sl"] and o["tp"] for o in first)
    assert len({o["tp"] for o in first}) == 5, "the targets are a ladder"
    assert all((o["tp"] > 2000 > o["sl"]) if o["side"] == "buy" else (o["tp"] < 2000 < o["sl"]) for o in first)


def test_positions_close_when_the_time_is_up_and_others_are_left_alone(tmp_path):
    backend = Backend()
    run, _ = _test(backend, tmp_path, minutes=0.5)
    run.run()
    assert len(backend.closed) == 5 and 7 not in backend.closed
    assert run.baskets[0]["seconds"] >= forward.HOLD_S


def test_a_live_account_is_refused_and_nothing_is_sent(tmp_path):
    backend = Backend(live=True)
    run, _ = _test(backend, tmp_path)
    run.run()
    assert backend.sent == [] and "demo" in run.ended


def test_a_loss_limit_ends_the_run(tmp_path):
    backend = Backend()
    run, _ = _test(backend, tmp_path, minutes=30)
    original = backend.account
    calls = {"n": 0}

    def falling():
        calls["n"] += 1
        return {**original(), "equity": 10_000.0 if calls["n"] < 4 else 9_700.0}

    backend.account = falling
    run.run()
    assert "rugi" in run.ended


def test_repeated_refusals_end_the_run(tmp_path):
    backend = Backend(refuse=True)
    run, _ = _test(backend, tmp_path, minutes=30)
    run.run()
    assert "gagal" in run.ended and len(run.baskets) == forward.MAX_ERRORS
    assert run.report()["refused_orders"] == 15 and backend.closed == []


def test_it_ends_at_its_time_limit(tmp_path):
    run, _ = _test(Backend(), tmp_path, minutes=2.0)
    run.run()
    assert run.ended == "batas waktu" and 3 <= len(run.baskets) <= 5


@pytest.mark.parametrize("minutes", [0, -1, forward.MAX_MINUTES + 1])
def test_the_time_limit_cannot_be_escaped(minutes, tmp_path):
    with pytest.raises(ValueError):
        ForwardTest(Backend(), "XAUUSD", minutes, folder=tmp_path)


def test_every_basket_reaches_the_file_and_the_journal(tmp_path):
    run, _ = _test(Backend(), tmp_path, minutes=0.5)
    run.run()
    rows = [json.loads(line) for line in run.path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["opened"] == 5 and rows[0]["profit"] == pytest.approx(7.5)
    events = journal.read()
    assert sum(r["event"] == "order" for r in events) == 5 and sum(r["event"] == "close" for r in events) == 5


def test_the_report_counts_wins_and_money(tmp_path):
    run, _ = _test(Backend(), tmp_path, minutes=1.0)
    run.run()
    report = run.report()
    assert report["win_rate"] == 1.0 and report["profit"] == pytest.approx(7.5 * report["baskets"]) and report["positions"] == 5 * report["baskets"]


def test_a_thin_tick_history_waits_instead_of_trading(tmp_path):
    backend = Backend()
    clock = Clock()
    run = ForwardTest(backend, "XAUUSD", 1.0, bars=lambda s: _bars(20), sleep=clock.sleep, clock=clock, folder=tmp_path)
    run.run()
    assert backend.sent == [] and run.skipped > 0


def test_closes_made_in_the_terminal_reach_the_journal_once(monkeypatch):
    from web import mt4_bridge

    monkeypatch.setenv("MT4_RECORD_TICKS", "1")
    monkeypatch.setattr(mt4_bridge.ticks, "append_bars", lambda *a: None)
    store = mt4_bridge.Store()
    deal = {"ticket": 55, "symbol": "XAUUSD", "side": "buy", "volume": "0.05", "price": "2000", "profit": "-12.5", "time": 9, "comment": "[sl]"}
    store.ingest([{"type": "history", "deals": [deal]}])
    store.ingest([{"type": "history", "deals": [deal]}])
    closes = [r for r in journal.read() if r["event"] == "close"]
    assert len(closes) == 1 and closes[0]["profit"] == -12.5 and closes[0]["source"] == "terminal"


def test_trade_history_survives_a_restart(monkeypatch):
    from web import mt4_bridge

    monkeypatch.setenv("MT4_RECORD_TICKS", "1")
    monkeypatch.setattr(mt4_bridge.ticks, "load_saved_bars", dict)
    journal.record_closes([{"ticket": 56, "symbol": "XAUUSD", "side": "sell", "volume": 0.01, "price": 2, "profit": 3, "time": 8}])
    store = mt4_bridge.Store()
    store.restore()
    assert [d["ticket"] for d in store.deals] == [56]


def test_a_close_counts_once_with_the_terminals_figure():
    journal.record({"event": "order", "ticket": 60, "symbol": "XAUUSD", "side": "buy"})
    journal.record({"event": "close", "ticket": 60, "symbol": "XAUUSD", "profit": 1.0, "source": "forward"})
    journal.record_closes([{"ticket": 60, "symbol": "XAUUSD", "side": "buy", "volume": 0.01, "price": 2, "profit": 1.4, "time": 8}])
    assert [t["profit"] for t in journal.trades(journal.read())] == [1.4]


def test_the_report_shows_what_the_balance_did(tmp_path):
    backend = Backend()
    run, _ = _test(backend, tmp_path, minutes=0.5)
    original = backend.account
    backend.account = lambda: {**original(), "balance": 10_006.0} if run.state == "running" and run.baskets else original()
    run.run()
    assert run.report()["balance_change"] == 6.0


def test_a_stop_too_far_to_protect_anything_is_refused():
    from trading import mt5_rules

    assert mt5_rules.check_risk({"pct_of_equity": 22.0}) and "MT5_MAX_RISK_PCT" in mt5_rules.check_risk({"pct_of_equity": 22.0})[0]
    assert mt5_rules.check_risk({"pct_of_equity": 1.0}) == [] and mt5_rules.check_risk(None) == []


def test_the_api_refuses_a_live_account(monkeypatch):
    from web import quant_api, trading_server

    monkeypatch.setattr(quant_api, "_backend", lambda: Backend(live=True))
    monkeypatch.setattr(quant_api, "_forward", None)
    client = TestClient(trading_server.app, client=("127.0.0.1", 5000))
    assert client.post("/api/quant/forward/start", json={"symbol": "XAUUSD", "minutes": 5}).status_code == 403


def test_the_report_names_why_every_order_was_refused(tmp_path):
    run, _ = _test(Backend(refuse=True), tmp_path, minutes=30)
    run.run()
    assert run.report()["refusal_examples"] == ["error:133"]

