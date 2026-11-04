"""The standalone trading backend: what it serves, what it does not, and its port errors. Offline."""

from __future__ import annotations

import socket

from fastapi.testclient import TestClient

from web import trading_server


def test_serves_the_panel_and_the_trading_apis():
    client = TestClient(trading_server.app)
    assert client.get("/trading").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/static/trading/trading.js").status_code == 200
    assert client.get("/api/mt5/status").status_code == 200


def test_the_agent_is_not_exposed_here():
    client = TestClient(trading_server.app)
    assert client.get("/api/models").status_code == 404
    assert client.get("/api/sessions").status_code == 404


def test_foreign_host_is_refused():
    resp = TestClient(trading_server.app).get("/trading", headers={"Host": "evil.example"})
    assert resp.status_code == 403


def test_bridge_still_needs_the_secret():
    assert TestClient(trading_server.app).get("/api/mt4/commands", params={"ea": "x"}).status_code in {401, 403, 503}


def test_bridge_works_at_the_bare_address_and_still_needs_the_secret(monkeypatch):
    monkeypatch.setenv("MT4_BRIDGE_SECRET", "s3cret")
    client = TestClient(trading_server.app)
    assert client.get("/commands", params={"ea": "x"}).status_code == 401
    ok = client.get("/commands", params={"ea": "x"}, headers={"X-Bridge-Secret": "s3cret"})
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/plain")
    posted = client.post("/ingest", json=[], headers={"X-Bridge-Secret": "s3cret"})
    assert posted.status_code == 200


def test_one_ea_fits_inside_the_rate_limit():
    ea_requests_per_minute = 300 + 30 + 60      # ticks every 200 ms, account every 2 s, commands every 1 s
    assert ea_requests_per_minute * 2 < trading_server.RATE_LIMIT_RPM


def test_port_problem_explains_a_busy_port():
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        assert "already in use" in trading_server.port_problem("127.0.0.1", port)


def test_port_problem_gives_the_command_when_permission_is_denied(monkeypatch):
    class Refused(socket.socket):
        def bind(self, address):
            raise PermissionError

    monkeypatch.setattr(trading_server.socket, "socket", Refused)
    assert "ip_unprivileged_port_start=80" in trading_server.port_problem("127.0.0.1", 80)
