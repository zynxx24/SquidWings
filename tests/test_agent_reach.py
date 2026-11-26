"""Agent Reach in the harness: argument building, result shaping, which links it takes, and that tests never run it."""

from __future__ import annotations

from typing import Any

import pytest

from brain.utils import agent_reach

CHANNELS = [{"name": "youtube", "actions": [
    {"name": "search", "params": [{"name": "query", "required": True}, {"name": "limit", "required": False}]},
]}]


def test_tests_never_run_the_real_program(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_REACH", raising=False)
    assert agent_reach.enabled() is False and agent_reach.installed() is False
    assert agent_reach.run(["channels"])["error"]["code"] == "NOT_INSTALLED"


def test_required_params_go_last_after_a_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_reach, "channels", lambda: CHANNELS)
    argv = agent_reach.action_argv("youtube", "search", {"query": "--looks like a flag", "limit": 5, "empty": ""})
    assert argv == ["youtube", "search", "--limit", "5", "--", "--looks like a flag"]
    # An action the cache does not know yet still passes its query.
    monkeypatch.setattr(agent_reach, "channels", lambda: [])
    assert agent_reach.action_argv("reddit", "search", {"query": "hp murah"}) == ["reddit", "search", "--", "hp murah"]


def test_results_drop_the_raw_copy_and_errors_say_what_to_do() -> None:
    ok: dict[str, Any] = {"ok": True, "items": [{"title": "t", "text": "isi", "raw": {"big": "x" * 100}}]}
    assert agent_reach.explain(ok)["items"] == [{"title": "t", "text": "isi"}]
    failed = agent_reach.explain({"ok": False, "error": {"code": "NEED_LOGIN", "message": "login"}})
    assert "agent-reach login" in failed["advice"] and "tidak pernah login" in failed["advice"]


@pytest.mark.parametrize(("url", "handles", "prefers"), [
    ("https://www.youtube.com/watch?v=abc", True, True),
    ("https://youtu.be/abc", True, True),
    ("https://old.reddit.com/r/indonesia", True, True),
    ("https://github.com/Panniantong/Agent-Reach/issues/1", True, False),
    ("https://kompas.com/berita", False, False),
    ("https://notyoutube.com.evil.io/x", False, False),
])
def test_which_links_it_takes(url: str, handles: bool, prefers: bool) -> None:
    assert agent_reach.handles(url) is handles and agent_reach.prefers(url) is prefers


def test_parse_finds_the_json_after_a_greeting() -> None:
    assert agent_reach._parse('hello\n{"ok": true, "items": []}') == {"ok": True, "items": []}
    assert agent_reach._parse("no json here") is None
