"""The daily graphify and aislop update check. Offline: the network and the upgrade commands are replaced."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tool.execute.execute_code_intel import tool_updates


@pytest.mark.parametrize(
    ("latest", "installed", "expected"),
    [
        ("0.19.2", "0.19.1", True),
        ("0.20.0", "0.19.9", True),
        ("0.19.1", "0.19.1", False),
        ("0.19.0", "0.19.1", False),
        (None, "0.19.1", False),        # unknown latest: do nothing
        ("1.0", None, False),           # not installed: do not install here
        ("1.0", "1.0.0", False),        # same release written differently
    ],
)
def test_is_newer_only_when_both_versions_are_known(latest, installed, expected):
    assert tool_updates.is_newer(latest, installed) is expected


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("TOOLS_AUTO_UPDATE", "false")
    assert tool_updates.enabled() is False
    assert tool_updates.start_background(Path("/nonexistent/x.json")) is False


def test_check_is_skipped_within_a_day(tmp_path, monkeypatch):
    state = tmp_path / "tool_updates.json"
    state.write_text(json.dumps({"checked_at": 1_000.0, "results": []}), encoding="utf-8")
    called = []
    monkeypatch.setattr(tool_updates, "latest_pypi", lambda *_: called.append("pypi") or "9.9")
    out = tool_updates.run_check(state, now=1_000.0 + 3600)
    assert out["skipped"] is True and called == []


def test_upgrades_only_the_tool_that_is_behind(tmp_path, monkeypatch):
    state = tmp_path / "tool_updates.json"
    monkeypatch.setattr(tool_updates, "latest_pypi", lambda *_: "0.9.99")
    monkeypatch.setattr(tool_updates, "latest_npm", lambda *_: "0.19.1")
    versions = {"graphify": "0.9.91", "aislop": "0.19.1", "hyperframes": "0.19.1"}
    monkeypatch.setattr(tool_updates, "installed_version", lambda cmd: versions[cmd])
    monkeypatch.setattr(tool_updates, "_ytdlp_version", lambda: "0.9.99")
    monkeypatch.setattr(tool_updates.shutil, "which", lambda name: f"/usr/bin/{name}")
    ran = []

    def fake_upgrade(argv):
        ran.append(argv)
        versions["graphify"] = "0.9.99"
        return True, ""

    monkeypatch.setattr(tool_updates, "_run_upgrade", fake_upgrade)
    out = tool_updates.run_check(state, force=True, now=5.0)
    actions = {r["tool"]: r["action"] for r in out["results"]}
    assert actions == {"graphify": "upgraded", "aislop": "none", "yt-dlp": "none", "hyperframes": "none"}
    assert ran == [["uv", "tool", "upgrade", "graphifyy"]]
    assert json.loads(state.read_text(encoding="utf-8"))["checked_at"] == 5.0


def test_missing_upgrader_is_reported_not_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_updates, "installed_version", lambda cmd: "0.1.0")
    monkeypatch.setattr(tool_updates.shutil, "which", lambda name: None)
    result = tool_updates._update_one("aislop", "aislop", "0.2.0", ["npm", "install", "-g", "aislop@latest"])
    assert result["action"].startswith("newer version available")


def test_ytdlp_is_upgraded_inside_this_apps_python(tmp_path, monkeypatch):
    """A yt-dlp a few months old stops working on YouTube, so it is kept current where the app runs it."""
    import sys

    monkeypatch.setattr(tool_updates, "latest_pypi", lambda name: "2026.11.1" if name == "yt-dlp" else None)
    monkeypatch.setattr(tool_updates, "latest_npm", lambda *_: None)
    monkeypatch.setattr(tool_updates, "installed_version", lambda cmd: None)
    version = {"now": "2026.1.1"}
    monkeypatch.setattr(tool_updates, "_ytdlp_version", lambda: version["now"])
    ran = []

    def fake_upgrade(argv):
        ran.append(argv)
        version["now"] = "2026.11.1"
        return True, ""

    monkeypatch.setattr(tool_updates, "_run_upgrade", fake_upgrade)
    out = tool_updates.run_check(tmp_path / "state.json", force=True, now=1.0)
    ytdlp = next(r for r in out["results"] if r["tool"] == "yt-dlp")
    assert ytdlp["action"] == "upgraded" and ytdlp["installed_after"] == "2026.11.1"
    assert ran and ran[0][0] in (sys.executable, "uv") and ran[0][-1] == "yt-dlp[default]"
