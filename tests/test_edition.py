"""The community edition runs without the Enterprise trading module. Offline."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config import edition


@pytest.fixture
def fresh():
    edition.trading_enabled.cache_clear()
    yield
    edition.trading_enabled.cache_clear()


def test_trading_is_off_when_its_files_are_missing(fresh, monkeypatch, tmp_path):
    monkeypatch.setattr(edition, "ROOT", tmp_path)
    assert edition.trading_enabled() is False and edition.edition() == "community"


def test_trading_is_on_only_with_every_file(fresh, monkeypatch, tmp_path):
    monkeypatch.delenv("ENTERPRISE_TRADING", raising=False)
    monkeypatch.setattr(edition, "ROOT", tmp_path)
    for name in edition.TRADING_FILES[:-1]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    assert edition.trading_enabled() is False, "a partial copy counts as absent"
    edition.trading_enabled.cache_clear()
    last = tmp_path / edition.TRADING_FILES[-1]
    last.parent.mkdir(parents=True, exist_ok=True)
    last.write_text("")
    assert edition.trading_enabled() is True and edition.edition() == "enterprise"


def test_the_switch_turns_an_installed_module_off(fresh, monkeypatch):
    monkeypatch.setenv("ENTERPRISE_TRADING", "0")
    assert edition.trading_enabled() is False


def test_the_tool_registry_skips_the_trading_tool_without_the_module(monkeypatch):
    from tool import register_tools
    from tool.registry.tool_registry import ToolRegistry

    monkeypatch.setattr(ToolRegistry, "_instance", None)   # a fresh registry, not the shared one
    monkeypatch.setattr(register_tools, "trading_enabled", lambda: False)
    names = set(register_tools.register_all_tools().get_all_names())
    assert "trading_snapshot" not in names and "web_search" in names


def test_the_web_shows_what_trading_is_instead_of_an_error(monkeypatch):
    from web import server

    monkeypatch.setattr(server, "trading_enabled", lambda: False)
    monkeypatch.setattr(server, "edition", lambda: "community")
    client = TestClient(server.app, client=("127.0.0.1", 50000))
    page = client.get("/trading?embed=1")
    assert page.status_code == 200 and "Enterprise" in page.text and "trading.js" not in page.text
    assert client.get("/trading").status_code == 200          # the chat page, with the link hidden
    assert client.get("/api/trading/panel").json()["external"] is False
    config = client.get("/api/config").json()
    assert config["edition"] == "community" and config["features"]["trading"] is False


def test_the_trading_command_explains_the_edition(monkeypatch):
    import sys

    import main

    monkeypatch.setattr(edition, "trading_enabled", lambda: False)
    monkeypatch.setattr(sys, "argv", ["main.py", "trading"])
    with pytest.raises(SystemExit) as stop:
        main.main()
    assert stop.value.code == 2
