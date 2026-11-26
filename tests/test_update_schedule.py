"""The repository, the GitHub skills and the local tools update every 12 hours, and only when online."""

from __future__ import annotations

import pytest

from brain.utils import online, repo_update
from tools.code_intel import tool_updates


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Set to empty, not removed: the repository reads .env, which never overrides a variable that is already set.
    for name in ("UPDATE_EVERY_HOURS", "AUTO_UPDATE_EVERY_MIN", "SKILL_SOURCES_UPDATE_HOURS", "TOOLS_UPDATE_HOURS"):
        monkeypatch.setenv(name, "")


def test_everything_defaults_to_twelve_hours() -> None:
    assert repo_update.interval_s() == 12 * 3600
    assert tool_updates.check_every_s() == 12 * 3600
    assert online.every_hours("SKILL_SOURCES_UPDATE_HOURS") == 12


def test_one_setting_moves_all_and_each_can_differ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDATE_EVERY_HOURS", "6")
    assert repo_update.interval_s() == 6 * 3600 and tool_updates.check_every_s() == 6 * 3600
    monkeypatch.setenv("TOOLS_UPDATE_HOURS", "24")
    monkeypatch.setenv("AUTO_UPDATE_EVERY_MIN", "30")
    assert tool_updates.check_every_s() == 24 * 3600 and repo_update.interval_s() == 30 * 60
    monkeypatch.setenv("UPDATE_EVERY_HOURS", "nonsense")
    assert online.every_hours("") == 12


def test_offline_start_does_not_wait_for_a_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    said: list[str] = []
    monkeypatch.setattr(repo_update, "enabled", lambda: True)
    monkeypatch.delenv(repo_update.RESTARTED_ENV, raising=False)
    monkeypatch.setattr(online, "is_online", lambda: False)
    monkeypatch.setattr(repo_update, "update", lambda root=None: pytest.fail("fetched while offline"))
    repo_update.update_at_start(said.append)
    assert said and "offline" in said[0]

