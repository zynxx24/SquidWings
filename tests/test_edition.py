"""Everything runs without the optional trading module. Offline."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brain.config import edition


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
    monkeypatch.delenv("ADDON_TRADING", raising=False)
    monkeypatch.setenv("ENTERPRISE_TRADING", "0")
    assert edition.trading_enabled() is False


def test_the_tool_registry_skips_the_trading_tool_without_the_module(monkeypatch):
    from tools import register_tools
    from tools.registry.tool_registry import ToolRegistry

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
    assert page.status_code == 200 and "tidak terpasang" in page.text and "trading.js" not in page.text
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


def test_addons_are_off_until_switched_on(fresh, monkeypatch, tmp_path):
    monkeypatch.delenv("ADDON_TRADING", raising=False)
    monkeypatch.delenv("ENTERPRISE_TRADING", raising=False)
    monkeypatch.setattr(edition, "STATE_FILE", tmp_path / "enabled.json")
    assert edition.files_complete("trading")
    assert edition.trading_enabled() is False, "a fresh clone has trading off"
    edition.write_state("trading", True)
    assert edition.trading_enabled() is True
    monkeypatch.setenv("ADDON_TRADING", "0")
    edition.clear_cache()
    assert edition.trading_enabled() is False, "the environment wins over the saved switch"


def test_addon_manager_switches_on_and_off_without_touching_files(fresh, monkeypatch, tmp_path):
    from addons import manager

    monkeypatch.delenv("ADDON_STUDIO", raising=False)
    monkeypatch.setattr(edition, "STATE_FILE", tmp_path / "enabled.json")
    result = manager.install("studio", deps=False)
    assert result["ok"] and result["on"] and result["manual"], result
    assert manager.remove("studio")["on"] is False
    assert edition.files_complete("studio")
    assert manager.install("nope")["ok"] is False


def test_every_addon_manifest_lists_real_files():
    assert set(edition.names()) >= {"trading", "studio"}
    for name in edition.names():
        spec = edition.manifest(name)
        assert spec["files"] and all((edition.ROOT / f).is_file() for f in spec["files"]), name
        assert set(spec["manual"]) == {"windows", "linux", "macos"}, name
