"""The full harness template: every kind of node in one Telegram chat flow, run path by path."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from brain.agent import team_flow, team_orchestrator
from brain.agent.team_orchestrator import TeamOrchestrator, TeamStore, team_templates
from brain.config.settings import get_settings
from brain.utils.datastore import registry
from tests.test_team_nodes import FakeOrchestrator

REPLIES = {
    "-rapikan-": '```json\n{"isi": "beli susu", "label": "belanja"}\n```',
    "-baca_kurs-": "1 USD = Rp16.250",
    "-berat-": "tidak",
    "-ngobrol-": "Halo juga!",
    "-rencana-": "1. tulis file",
    "-kerja-": "file dibuat: hello.py",
    "-periksa-": "Sudah jadi: hello.py",
}


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TeamOrchestrator, Any, list[tuple[str, str]]]:
    current = get_settings()
    monkeypatch.setattr(current, "team_db_sqlite_path", str(tmp_path / "flow.sqlite3"))
    monkeypatch.setattr(current, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(current, "telegram_chat_id", "42")
    monkeypatch.setattr(registry, "DATA_DIR", tmp_path / "datastore")
    sent: list[tuple[str, str]] = []

    async def fake_send(text: str, chat_id: str = "") -> int:
        sent.append((text, chat_id))
        return 1

    async def fake_rich(parts: list[str], chat_id: str = "", **options: Any) -> int:
        fallback = options.get("fallback")
        sent.append((fallback[0] if fallback else "\n\n".join(parts), chat_id))
        return 1

    async def no_typing(_chat: str, _action: str = "typing") -> None:
        return None

    async def fake_http(method: str, url: str, body: str = "") -> str:
        return json.dumps({"rates": {"IDR": 16250}, "time_last_update_utc": "Thu, 26 Nov 2026"})

    monkeypatch.setattr(team_orchestrator, "send_telegram", fake_send)
    monkeypatch.setattr(team_orchestrator, "send_rich", fake_rich)
    monkeypatch.setattr(team_orchestrator, "send_chat_action", no_typing)
    monkeypatch.setattr(team_orchestrator, "http_request", fake_http)
    fake = FakeOrchestrator(REPLIES)
    manager = TeamOrchestrator(fake, TeamStore(tmp_path / "teams"))  # type: ignore[arg-type]
    template = next(t for t in team_templates(["m"]) if t["key"] == "harness-full")
    registry.ensure_harness_starter()
    members, starts = team_flow.to_flow(template["members"], None, None)
    team = manager.create_team(template["name"], "", members, None, schedule=template["schedule"], style="flow", starts=starts)
    return manager, team, sent


async def say(manager: TeamOrchestrator, monkeypatch: pytest.MonkeyPatch, *texts: str) -> None:
    batches = [[], [{"update_id": i, "message": {"chat": {"id": 42, "type": "private"}, "text": t}} for i, t in enumerate(texts, 1)]]

    async def fake_fetch(offset: int | None, wait: int = 25) -> list[dict[str, Any]]:
        return batches.pop(0) if batches else []

    monkeypatch.setattr(team_orchestrator, "fetch_telegram_updates", fake_fetch)
    manager._telegram_offset = None
    await manager.poll_telegram(wait=0)
    await asyncio.wait_for(manager.drain_customers(), timeout=5)


def test_the_template_uses_every_kind_of_node() -> None:
    template = next(t for t in team_templates(["m"]) if t["key"] == "harness-full")
    assert {m.get("kind", "agent") for m in template["members"]} == set(team_flow.NODE_KINDS)
    assert template["starter"] == "harness"


def test_the_starter_makes_related_tables_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "DATA_DIR", tmp_path / "datastore")
    assert registry.ensure_harness_starter() == ["label", "catatan", "relasi catatan.label -> label.nama"]
    assert registry.ensure_harness_starter() == []
    backend = registry.backend_for(registry.ensure_builtin())
    assert backend.table("label").rows == 4


async def test_notes_are_saved_then_listed_with_their_label(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    manager, team, sent = setup
    await say(manager, monkeypatch, "/catat beli susu ya")
    assert sent[-1][0].startswith("Dicatat (") and "belanja" in sent[-1][0] and "beli susu" in sent[-1][0]
    await say(manager, monkeypatch, "/lihat")
    listed = sent[-1][0]
    assert "beli susu" in listed and "Barang yang perlu dibeli" in listed     # the label's meaning, through the relation


async def test_rates_come_from_the_http_node(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    manager, team, sent = setup
    await say(manager, monkeypatch, "/kurs")
    assert sent[-1] == ("1 USD = Rp16.250", "42")
    run = manager.store.list_runs(team.id)[0]
    assert run.agents["kurs"]["status"] == "completed" and run.agents["lihat"]["status"] == "skipped"


async def test_a_chat_goes_to_the_assistant_and_work_goes_through_plan_do_check(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    manager, team, sent = setup
    await say(manager, monkeypatch, "halo")
    assert sent[-1] == ("Halo juga!", "42")
    await say(manager, monkeypatch, "/kerja buat hello.py")
    assert sent[-1] == ("Sudah jadi: hello.py", "42")
    runs = sorted(manager.store.list_runs(team.id), key=lambda r: r.created_at)
    assert [runs[1].agents[x]["status"] for x in ("rencana", "kerja", "periksa")] == ["completed"] * 3
    # The answering agents talk with the owner as in a chat, though a merge stands between them and the reply.
    prompts = {c["session_id"].split("-")[-2]: c["user_input"] for c in manager.orchestrator.calls}
    assert "with the owner of this app" in prompts["periksa"] and "with the owner of this app" not in prompts["rencana"]


async def test_a_focus_session_frees_the_chat_while_it_waits(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    manager, team, sent = setup
    bell = asyncio.Event()
    real_sleep = asyncio.sleep

    async def sleep(seconds: float) -> None:
        if seconds == 1500:
            await bell.wait()
        else:
            await real_sleep(seconds)

    monkeypatch.setattr(team_orchestrator.asyncio, "sleep", sleep)
    batches = [[], [{"update_id": 1, "message": {"chat": {"id": 42, "type": "private"}, "text": "/fokus"}}],
               [{"update_id": 2, "message": {"chat": {"id": 42, "type": "private"}, "text": "halo"}}]]

    async def fake_fetch(offset: int | None, wait: int = 25) -> list[dict[str, Any]]:
        return batches.pop(0) if batches else []

    monkeypatch.setattr(team_orchestrator, "fetch_telegram_updates", fake_fetch)
    await manager.poll_telegram(wait=0)
    for _ in range(200):
        if any("Fokus 25 menit dimulai" in text for text, _ in sent):
            break
        await real_sleep(0.01)
    await manager.poll_telegram(wait=0)                  # "halo" while the focus run sleeps
    for _ in range(200):
        if ("Halo juga!", "42") in sent:
            break
        await real_sleep(0.01)
    assert ("Halo juga!", "42") in sent and not any("Waktu fokus habis" in text for text, _ in sent)
    bell.set()
    await asyncio.wait_for(manager.drain_customers(), timeout=5)
    assert "Waktu fokus habis" in sent[-1][0]
    assert not manager._chat_released
