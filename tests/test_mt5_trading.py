"""MT5 trading panel: the safety gates and the order flow, on the offline simulator.

No test here reaches a broker. The simulator never sends anything anywhere.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from trading import mt5_rules
from trading.mt5_adapter import SimBackend, TradingError, fingerprint, make_backend
from web import mt5_api
from web.server import app

TOKEN = "test-token-123"
H = {"X-MT5-Token": TOKEN}


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setenv("MT5_TRADE_TOKEN", TOKEN)
    monkeypatch.setenv("TRADE_JOURNAL", str(tmp_path / "journal.jsonl"))
    monkeypatch.delenv("MT5_ALLOW_LIVE", raising=False)
    monkeypatch.delenv("MT5_MAX_VOLUME", raising=False)
    monkeypatch.delenv("MT5_REQUIRE_SL", raising=False)
    mt5_api._state.update(backend=None, live=False)
    mt5_api._previews.clear()
    yield
    mt5_api._state.update(backend=None, live=False)
    mt5_api._previews.clear()


@pytest.fixture()
def client():
    # Simulate this computer: the request comes from loopback, and the server is not in public mode.
    return TestClient(app, client=("127.0.0.1", 50000))


def _login(client, **extra):
    body = {"backend": "sim", "login": 12345, "password": "x", "server": "sim"}
    body.update(extra)
    return client.post("/api/mt5/connect", json=body, headers=H)


def _ask(client):
    return client.get("/api/mt5/symbol", params={"symbol": "EURUSD"}, headers=H).json()["tick"]["ask"]


# ---------------------------------------------------------------- gates


def test_status_is_public_and_says_trading_is_on(client):
    r = client.get("/api/mt5/status")
    assert r.status_code == 200
    assert r.json()["trading_enabled"] is True
    assert r.json()["connected"] is False


def test_local_use_needs_no_token(client, monkeypatch):
    monkeypatch.delenv("MT5_TRADE_TOKEN")
    assert client.get("/api/mt5/status").json()["trading_enabled"] is True
    assert client.post("/api/mt5/connect", json={"backend": "sim"}).status_code == 200


def test_public_access_needs_a_token(monkeypatch):
    from security import web_access
    monkeypatch.delenv("MT5_TRADE_TOKEN")
    monkeypatch.setattr(web_access, "_public_mode", True)
    c = TestClient(app, client=("127.0.0.1", 50000))   # a tunnel arrives from loopback too
    assert c.get("/api/mt5/status").json()["trading_enabled"] is False
    assert c.post("/api/mt5/connect", json={"backend": "sim"}).status_code == 403
    monkeypatch.setenv("MT5_TRADE_TOKEN", TOKEN)
    assert c.post("/api/mt5/connect", json={"backend": "sim"}, headers=H).status_code == 200


def test_remote_client_is_refused_without_a_token(monkeypatch):
    monkeypatch.delenv("MT5_TRADE_TOKEN")
    c = TestClient(app, client=("203.0.113.9", 50000))
    assert c.post("/api/mt5/connect", json={"backend": "sim"}).status_code == 403


def test_wrong_or_missing_token_is_refused(client):
    assert client.post("/api/mt5/connect", json={"backend": "sim"}).status_code == 401
    assert client.post("/api/mt5/connect", json={"backend": "sim"}, headers={"X-MT5-Token": "nope"}).status_code == 401


def test_demo_login_works(client):
    r = _login(client)
    assert r.status_code == 200
    assert r.json()["live"] is False
    assert client.get("/api/mt5/status").json()["connected"] is True


def test_live_account_is_refused_unless_allowed(client, monkeypatch):
    real = dict(SimBackend.account(SimBackend()), live=True)
    monkeypatch.setattr(SimBackend, "account", lambda self: real)
    r = _login(client)
    assert r.status_code == 403
    assert "MT5_ALLOW_LIVE" in r.json()["detail"]
    assert client.get("/api/mt5/status").json()["connected"] is False

    monkeypatch.setenv("MT5_ALLOW_LIVE", "1")
    r = _login(client)
    assert r.status_code == 200
    assert r.json()["live"] is True


def test_order_endpoints_need_a_connection(client):
    r = client.post("/api/mt5/preview", json={"symbol": "EURUSD", "side": "buy", "volume": 0.01, "sl": 1.0}, headers=H)
    assert r.status_code == 409


# ---------------------------------------------------------------- rules


def test_stop_loss_is_required_by_default(client):
    _login(client)
    r = client.post("/api/mt5/preview", json={"symbol": "EURUSD", "side": "buy", "volume": 0.01}, headers=H)
    assert r.status_code == 422
    assert any("stop loss" in p.lower() for p in r.json()["detail"]["problems"])


def test_volume_above_the_limit_is_refused(client, monkeypatch):
    monkeypatch.setenv("MT5_MAX_VOLUME", "0.05")
    _login(client)
    ask = _ask(client)
    r = client.post("/api/mt5/preview", json={"symbol": "EURUSD", "side": "buy", "volume": 0.5, "sl": round(ask - 0.01, 5)}, headers=H)
    assert r.status_code == 422
    assert any("MT5_MAX_VOLUME" in p for p in r.json()["detail"]["problems"])


def test_stop_on_the_wrong_side_is_refused(client):
    _login(client)
    ask = _ask(client)
    r = client.post("/api/mt5/preview", json={"symbol": "EURUSD", "side": "buy", "volume": 0.01, "sl": round(ask + 0.01, 5)}, headers=H)
    assert r.status_code == 422
    assert any("below the current ask" in p for p in r.json()["detail"]["problems"])


def test_rules_module_checks_volume_step():
    spec = {"volume_min": 0.01, "volume_max": 10, "volume_step": 0.01, "digits": 5}
    tick = {"bid": 1.0, "ask": 1.0002}
    order = {"side": "buy", "volume": 0.015, "sl": 0.99}
    assert any("multiple of" in p for p in mt5_rules.check_order(order, spec, tick))


# ---------------------------------------------------------------- order flow


def test_buy_is_previewed_then_confirmed_once(client):
    _login(client)
    ask = _ask(client)
    body = {"symbol": "EURUSD", "side": "buy", "volume": 0.01, "sl": round(ask - 0.01, 5), "tp": round(ask + 0.02, 5)}
    p = client.post("/api/mt5/preview", json=body, headers=H)
    assert p.status_code == 200, p.text
    pid = p.json()["preview_id"]

    c = client.post("/api/mt5/order/confirm", json={"preview_id": pid}, headers=H)
    assert c.status_code == 200, c.text
    assert c.json()["result"]["ok"] is True
    positions = client.get("/api/mt5/positions", headers=H).json()["positions"]
    assert any(p["symbol"] == "EURUSD" and p["side"] == "buy" for p in positions)

    again = client.post("/api/mt5/order/confirm", json={"preview_id": pid}, headers=H)
    assert again.status_code == 410, "a preview must work once"


def test_expired_preview_is_refused(client):
    _login(client)
    ask = _ask(client)
    pid = client.post("/api/mt5/preview", json={"symbol": "EURUSD", "side": "buy", "volume": 0.01, "sl": round(ask - 0.01, 5)}, headers=H).json()["preview_id"]
    mt5_api._previews[pid]["expires"] = time.time() - 1
    r = client.post("/api/mt5/order/confirm", json={"preview_id": pid}, headers=H)
    assert r.status_code == 410


def test_close_is_previewed_then_confirmed(client):
    _login(client)
    ask = _ask(client)
    pid = client.post("/api/mt5/preview", json={"symbol": "EURUSD", "side": "buy", "volume": 0.01, "sl": round(ask - 0.01, 5)}, headers=H).json()["preview_id"]
    client.post("/api/mt5/order/confirm", json={"preview_id": pid}, headers=H)
    ticket = client.get("/api/mt5/positions", headers=H).json()["positions"][0]["ticket"]

    cp = client.post("/api/mt5/close/preview", json={"ticket": ticket}, headers=H)
    assert cp.status_code == 200
    cc = client.post("/api/mt5/close/confirm", json={"preview_id": cp.json()["preview_id"]}, headers=H)
    assert cc.status_code == 200 and cc.json()["result"]["ok"] is True
    assert client.get("/api/mt5/positions", headers=H).json()["positions"] == []


def test_a_close_preview_cannot_confirm_an_order(client):
    _login(client)
    ask = _ask(client)
    pid = client.post("/api/mt5/preview", json={"symbol": "EURUSD", "side": "buy", "volume": 0.01, "sl": round(ask - 0.01, 5)}, headers=H).json()["preview_id"]
    client.post("/api/mt5/order/confirm", json={"preview_id": pid}, headers=H)
    ticket = client.get("/api/mt5/positions", headers=H).json()["positions"][0]["ticket"]
    cp = client.post("/api/mt5/close/preview", json={"ticket": ticket}, headers=H).json()["preview_id"]
    r = client.post("/api/mt5/order/confirm", json={"preview_id": cp}, headers=H)
    assert r.status_code == 400


# ---------------------------------------------------------------- page and helpers


def test_trading_page_is_served(client):
    r = client.get("/trading")
    assert r.status_code == 200
    assert "SquidWings" in r.text
    assert "/static/trading/trading.js" in r.text


def test_simulator_bars_are_deterministic_and_valid():
    sim = SimBackend()
    a = sim.rates("EURUSD", "H1", 50)
    b = sim.rates("EURUSD", "H1", 50)
    assert a == b
    assert all(bar["l"] <= min(bar["o"], bar["c"]) and bar["h"] >= max(bar["o"], bar["c"]) for bar in a)


def test_unknown_backend_is_a_clear_error():
    with pytest.raises(TradingError):
        make_backend("alpaca")


def test_fingerprint_is_stable():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


# ---------------------------------------------------------------- SL/TP, cancel, risk


def _open_buy(client, sl_offset=0.01, tp_offset=0.02):
    _login(client)
    ask = _ask(client)
    body = {"symbol": "EURUSD", "side": "buy", "volume": 0.01,
            "sl": round(ask - sl_offset, 5), "tp": round(ask + tp_offset, 5)}
    pid = client.post("/api/mt5/preview", json=body, headers=H).json()["preview_id"]
    assert client.post("/api/mt5/order/confirm", json={"preview_id": pid}, headers=H).status_code == 200
    return client.get("/api/mt5/positions", headers=H).json()["positions"][0]


def test_stop_closer_than_the_broker_minimum_is_refused():
    spec = {"digits": 5, "stops_level": 0.0010}
    tick = {"bid": 1.08500, "ask": 1.08510}
    problems = mt5_rules.check_levels("buy", 1.08490, 1.08800, tick, spec)
    # 1.08510 - 1.08490 = 0.00020, below the 0.00100 minimum
    assert any("broker minimum" in p for p in problems)
    assert mt5_rules.check_levels("buy", 1.08400, 1.08800, tick, spec) == []


def test_modify_stop_and_target_is_previewed_then_confirmed(client):
    pos = _open_buy(client)
    ask = _ask(client)
    new_sl = round(ask - 0.005, 5)
    p = client.post("/api/mt5/modify/preview", json={"ticket": pos["ticket"], "sl": new_sl, "tp": pos["tp"]}, headers=H)
    assert p.status_code == 200, p.text
    assert p.json()["risk"]["money"] > 0
    c = client.post("/api/mt5/modify/confirm", json={"preview_id": p.json()["preview_id"]}, headers=H)
    assert c.status_code == 200 and c.json()["result"]["ok"] is True
    after = client.get("/api/mt5/positions", headers=H).json()["positions"][0]
    assert after["sl"] == new_sl


def test_modify_stop_on_the_wrong_side_is_refused(client):
    pos = _open_buy(client)
    ask = _ask(client)
    r = client.post("/api/mt5/modify/preview", json={"ticket": pos["ticket"], "sl": round(ask + 0.01, 5), "tp": pos["tp"]}, headers=H)
    assert r.status_code == 422
    assert any("below the current ask" in p for p in r.json()["detail"]["problems"])


def test_modify_needs_a_stop_when_required(client):
    pos = _open_buy(client)
    r = client.post("/api/mt5/modify/preview", json={"ticket": pos["ticket"], "sl": None, "tp": pos["tp"]}, headers=H)
    assert r.status_code == 422
    assert any("stop loss is required" in p.lower() for p in r.json()["detail"]["problems"])


def test_modify_a_closed_position_is_404(client):
    _login(client)
    r = client.post("/api/mt5/modify/preview", json={"ticket": 999999, "sl": 1.0, "tp": None}, headers=H)
    assert r.status_code == 404


def test_modify_preview_cannot_confirm_a_close(client):
    pos = _open_buy(client)
    pid = client.post("/api/mt5/close/preview", json={"ticket": pos["ticket"]}, headers=H).json()["preview_id"]
    r = client.post("/api/mt5/modify/confirm", json={"preview_id": pid}, headers=H)
    assert r.status_code == 400


def test_simulator_has_no_pending_orders_to_cancel(client):
    _login(client)
    r = client.post("/api/mt5/cancel/preview", json={"ticket": 1}, headers=H)
    assert r.status_code == 404


def test_risk_endpoint_gives_money_at_the_stop(client):
    _login(client)
    ask = _ask(client)
    # Simulator: contract size 100,000 for EURUSD, so 0.01 lot with a 0.0100 stop risks 10.00.
    r = client.get("/api/mt5/risk", params={"symbol": "EURUSD", "side": "buy", "volume": 0.01,
                                              "sl": round(ask - 0.01, 5)}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["risk"]["money"] == pytest.approx(10.0, abs=0.05)
    assert body["stop_distance"] == pytest.approx(0.01, abs=1e-5)


def test_risk_endpoint_without_a_stop_has_no_risk(client):
    _login(client)
    r = client.get("/api/mt5/risk", params={"symbol": "EURUSD", "side": "buy", "volume": 0.01}, headers=H)
    assert r.status_code == 200 and r.json()["risk"] is None


def test_order_preview_reports_risk(client):
    _login(client)
    ask = _ask(client)
    p = client.post("/api/mt5/preview", json={"symbol": "EURUSD", "side": "buy", "volume": 0.01,
                                              "sl": round(ask - 0.01, 5)}, headers=H)
    assert p.status_code == 200
    assert p.json()["risk"]["money"] == pytest.approx(10.0, abs=0.05)
    assert p.json()["equity"] > 0


def test_mt5_fill_policy_follows_the_symbol(monkeypatch):
    from types import SimpleNamespace

    from trading.mt5_adapter import Mt5Backend

    fake = SimpleNamespace(ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1, ORDER_FILLING_RETURN=2)

    def backend_with(mask):
        b = Mt5Backend.__new__(Mt5Backend)   # skip __init__: no terminal needed for this check
        fake.symbol_info = lambda symbol: SimpleNamespace(filling_mode=mask)
        b._mt5 = fake
        return b

    assert backend_with(2)._filling("EURUSD") == 1   # IOC allowed
    assert backend_with(1)._filling("EURUSD") == 0   # only FOK
    assert backend_with(0)._filling("EURUSD") == 2   # nothing listed: RETURN


def test_risk_at_stop_rounds_to_cents():
    assert mt5_rules.risk_at_stop(0.03, 123.456) == pytest.approx(3.70, abs=0.01)
