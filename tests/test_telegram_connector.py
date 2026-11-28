"""Telegram's default chat is the harness: made once, then the owner switches it off or on on the Tim agent page."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_teams_flow import make


@pytest.fixture()
def telegram_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.config import credentials

    values = {"telegram_bot_token": "123:abc", "telegram_chat_id": "42"}
    monkeypatch.setattr(credentials, "get", lambda key: values.get(key, ""))
    monkeypatch.delenv("TELEGRAM_CONNECTOR", raising=False)


def test_the_harness_chat_is_made_once_and_is_the_default(tmp_path: Path, telegram_set: None) -> None:
    service, _ = make(tmp_path)
    team = service.ensure_telegram_harness()
    assert team is not None and team.active and service._is_harness_chat(team)
    assert service.ensure_telegram_harness().id == team.id, "never a second one"
    assert service._owner_flows_for(service._telegram_teams(), "buatkan web toko")[0].id == team.id


def test_switched_off_on_the_team_page_it_stays_off(tmp_path: Path, telegram_set: None) -> None:
    service, _ = make(tmp_path)
    team = service.ensure_telegram_harness()
    team.active = False
    service.store.put_team(team)
    service.ensure_telegram_harness()
    assert service.store.get_team(team.id).active is False, "the owner's choice is not undone at start"
    service.store.delete_team(team.id) if hasattr(service.store, "delete_team") else None
    if service.store.get_team(team.id) is None:
        assert service.ensure_telegram_harness() is None, "a deleted default is not made again"


def test_other_flows_still_take_their_trigger_word(tmp_path: Path, telegram_set: None) -> None:
    service, _ = make(tmp_path)
    harness = service.ensure_telegram_harness()
    members = [{"id": "jawab", "name": "Jawab", "role": "r", "model_id": "m", "depends_on": []}]
    other = service.create_team("Jadwal", "", members, None, None,
                                {"mode": "telegram", "keyword": "/jadwal", "task": ""}, style="flow", active=True)
    flows = service._telegram_teams()
    assert service._owner_flows_for(flows, "/jadwal besok")[0].id == other.id
    assert service._owner_flows_for(flows, "halo")[0].id == harness.id
    harness.active = False
    service.store.put_team(harness)
    assert all(t.id != harness.id for t in service._owner_flows_for(service._telegram_teams(), "halo"))


def test_without_the_bot_or_when_switched_off_nothing_is_made(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                              telegram_set: None) -> None:
    from brain.agent import telegram_connector

    monkeypatch.setenv("TELEGRAM_CONNECTOR", "off")
    service, _ = make(tmp_path)
    assert service.ensure_telegram_harness() is None
    assert "dimatikan" in telegram_connector.wanted()
