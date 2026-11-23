"""The chat agent's read-only trading tool, against the real trading app served in-process."""

from __future__ import annotations

import asyncio
import urllib.error

import pytest
from fastapi.testclient import TestClient

from brain import skills
from tool.execute.execute_trading import snapshot_tool as T


@pytest.fixture()
def server(monkeypatch):
    """The trading app in-process. The tool's HTTP call goes to it instead of a socket."""
    from trading.mt5_adapter import SimBackend
    from web import mt5_api
    from web.trading_server import app

    backend = SimBackend()
    backend.connect(1, "", "", None)
    mt5_api._state.update(backend=backend, live=False, view_only="")
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1")

    def fake_get(path, timeout=T._TIMEOUT_S):
        r = client.get(path)
        body = r.json()
        return r.status_code, (body if r.status_code == 200 else {"error": body.get("detail")})

    monkeypatch.setattr(T, "_get", fake_get)
    yield
    mt5_api._state.update(backend=None, live=False, view_only="")


def test_snapshot_reads_the_account_and_positions(server):
    out = asyncio.run(T.trading_snapshot(sections=["status", "account", "positions", "risk_state"]))
    assert out["ok"] is True, out
    assert out["status"]["connected"] is True
    assert "equity" in out["account"] and "positions" in out["positions"]
    assert "cannot place" in out["note"]


def test_snapshot_needs_a_symbol_for_the_proposal(server):
    out = asyncio.run(T.trading_snapshot(sections=["proposal"]))
    assert out["ok"] is False and "symbol" in out["error"]


def test_a_stopped_server_is_reported_with_the_command_to_start_it(monkeypatch):
    def refused(path, timeout=0):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(T, "_get", refused)
    out = asyncio.run(T.trading_snapshot(sections=["status"]))
    assert out["ok"] is False and "python main.py trading" in out["error"]


def test_only_this_machine_can_be_read(monkeypatch):
    monkeypatch.setenv("TRADING_SERVER_URL", "http://example.com")
    out = asyncio.run(T.trading_snapshot())
    assert out["ok"] is False and "this machine" in out["error"]


def test_tool_is_registered_read_only_and_the_skill_wakes_on_trading_talk():
    from tool.register_tools import register_all_tools
    from tool.registry.tool_registry import is_read_only

    assert register_all_tools().has_tool("trading_snapshot") and is_read_only("trading_snapshot")
    skills.load_all.cache_clear()
    assert "analis-trading" in skills.woken("posisi xauusd saya floating, sl-nya harus digeser?")
    assert "analis-trading" not in skills.woken("buat tombol buy di halaman toko online")
    assert "trading_snapshot" in skills.tools_for(["analis-trading"])
