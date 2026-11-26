"""Telegram flows work in one folder, wake one flow per message, can run as the full harness, send files back,
and a gate can send the work back up the flow."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from brain.agent import team_flow, team_orchestrator
from brain.agent.orchestrator import AgentResponse
from brain.agent.team_orchestrator import TeamOrchestrator, TeamStore, team_templates
from brain.config.constants import ResponseStatus
from brain.config.settings import get_settings
from brain.utils import shared_files, workspace_context
from brain.utils.datastore import registry
from brain.utils.workspace_context import get_active_workspace
from tests.test_team_nodes import FakeOrchestrator, agent, node


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    current = get_settings()
    monkeypatch.setattr(current, "team_db_sqlite_path", str(tmp_path / "flow.sqlite3"))
    monkeypatch.setattr(current, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(current, "telegram_chat_id", "42")
    monkeypatch.setattr(registry, "DATA_DIR", tmp_path / "datastore")
    sent: list[tuple[str, str]] = []
    files: list[str] = []

    async def fake_send(text: str, chat_id: str = "") -> int:
        sent.append((text, chat_id))
        return 1

    async def fake_rich(parts: list[str], chat_id: str = "", **options: Any) -> int:
        fallback = options.get("fallback")
        sent.append((fallback[0] if fallback else "\n\n".join(parts), chat_id))
        return 1

    async def fake_file(path: Path, chat_id: str = "", caption: str = "") -> str:
        files.append(path.name)
        return path.name

    async def no_typing(_chat: str, _action: str = "typing") -> None:
        return None

    monkeypatch.setattr(team_orchestrator, "send_telegram", fake_send)
    monkeypatch.setattr(team_orchestrator, "send_rich", fake_rich)
    monkeypatch.setattr(team_orchestrator, "send_file", fake_file)
    monkeypatch.setattr(team_orchestrator, "send_chat_action", no_typing)
    return {"sent": sent, "files": files, "monkeypatch": monkeypatch}


def make(replies: dict[str, str] | None = None, fake: Any = None) -> tuple[TeamOrchestrator, Any]:
    fake = fake or FakeOrchestrator(replies)
    return TeamOrchestrator(fake, TeamStore(workspace_context.SESSIONS_ROOT.parent / "teams")), fake  # type: ignore[arg-type]


async def chat(manager: TeamOrchestrator, monkeypatch: pytest.MonkeyPatch, *texts: str) -> list[str]:
    batches = [[], [{"update_id": i, "message": {"chat": {"id": 42, "type": "private"}, "text": t, "from": {"first_name": "Zyn"}}}
                    for i, t in enumerate(texts, 1)]]

    async def fake_fetch(offset: int | None, wait: int = 25) -> list[dict[str, Any]]:
        return batches.pop(0) if batches else []

    monkeypatch.setattr(team_orchestrator, "fetch_telegram_updates", fake_fetch)
    manager._telegram_offset = None
    taken = await manager.poll_telegram(wait=0)
    await asyncio.wait_for(manager.drain_customers(), timeout=5)
    return taken


def flow(manager: TeamOrchestrator, name: str = "bot", keyword: str = "", folder: str = "team", **extra: Any) -> Any:
    return manager.create_team(name, "", [agent("a"), node("balas", "telegram", {}, "a")], None,
                               schedule={"mode": "telegram", "keyword": keyword}, style="flow", starts=["a"],
                               folder=folder, **extra)


# --- one folder, one flow ---------------------------------------------------


async def test_every_message_works_in_the_flows_own_folder(env: dict[str, Any]) -> None:
    manager, _ = make({"-a-": "siap"})
    team = flow(manager, "Asisten HP")
    await chat(manager, env["monkeypatch"], "halo", "buat catatan", "makasih")
    runs = manager.store.list_runs(team.id)
    assert len(runs) == 3 and {r.session for r in runs} == {team.id}
    folders = [p.name for p in workspace_context.SESSIONS_ROOT.iterdir() if p.is_dir()]
    assert folders == ["[team] Asisten HP"]


async def test_a_flow_can_have_a_new_folder_per_run(env: dict[str, Any]) -> None:
    manager, _ = make({"-a-": "siap"})
    team = flow(manager, "Bangun aplikasi", folder="run")
    await chat(manager, env["monkeypatch"], "satu", "dua")
    assert {r.session for r in manager.store.list_runs(team.id)} == {r.id for r in manager.store.list_runs(team.id)}


async def test_a_flow_can_work_in_a_web_chats_folder(env: dict[str, Any]) -> None:
    folder = workspace_context.SESSIONS_ROOT / "[web] Toko Kopi"
    folder.mkdir(parents=True)
    (folder / workspace_context.ID_FILE).write_text("sess-kopi123", encoding="utf-8")
    manager, _ = make({"-a-": "siap"})
    team = flow(manager, folder="s:[web] Toko Kopi")
    await chat(manager, env["monkeypatch"], "lanjutkan")
    [run] = manager.store.list_runs(team.id)
    assert run.session == "sess-kopi123"
    with pytest.raises(ValueError):
        flow(manager, "lain", folder="s:[web] Tidak Ada")
    with pytest.raises(ValueError):
        flow(manager, "lain", folder="/etc")


async def test_one_message_wakes_one_flow(env: dict[str, Any]) -> None:
    manager, _ = make({"-a-": "siap"})
    older = flow(manager, "lama")
    newer = flow(manager, "baru")
    admin = flow(manager, "admin", keyword="/atur")
    assert await chat(manager, env["monkeypatch"], "halo") == [newer.id]
    assert await chat(manager, env["monkeypatch"], "/atur harga") == [admin.id]
    assert manager.store.list_runs(older.id) == []
    manager.set_active(newer.id, False)
    assert await chat(manager, env["monkeypatch"], "halo lagi") == [older.id]


# --- the full harness, and files ---------------------------------------------


class Harness(FakeOrchestrator):
    """Records the calls; the harness agent writes and shares a file the first time."""

    def __init__(self) -> None:
        super().__init__({})
        self.turn = 0

    async def process(self, **kwargs: Any) -> AgentResponse:
        self.calls.append(kwargs)
        self.turn += 1
        if self.turn == 1:
            workspace = Path(get_active_workspace())
            (workspace / "laporan.pdf").write_bytes(b"%PDF-1.4 data")
            shared_files.add(workspace, "laporan.pdf")
        return AgentResponse(content=f"jawaban {self.turn}", status=ResponseStatus.SUCCESS)


async def test_the_harness_template_works_like_the_local_chat_and_sends_files(env: dict[str, Any]) -> None:
    fake = Harness()
    manager, _ = make(fake=fake)
    template = next(t for t in team_templates(["m"]) if t["key"] == "harness-telegram")
    members, starts = team_flow.to_flow(template["members"], None, None)
    team = manager.create_team(template["name"], "", members, None, schedule=template["schedule"], style="flow", starts=starts)
    await chat(manager, env["monkeypatch"], "buat laporan pdf")
    await chat(manager, env["monkeypatch"], "terima kasih")
    first, second = fake.calls
    # The chat's own pipeline: the real conversation, kept, in the flow's session, no step budget, Auto model.
    assert first["user_input"] == "buat laporan pdf" and first["persist"] is True and first["session_id"] == team.id
    assert first["override_model"] is None and "limits" not in first and "Telegram" in first["workspace_context"]
    assert second["session_id"] == team.id
    assert env["sent"] == [("jawaban 1", "42"), ("jawaban 2", "42")]
    assert env["files"] == ["laporan.pdf"]           # sent once, not again with the next message


# --- loops --------------------------------------------------------------------


class Writer(FakeOrchestrator):
    """The writer gets it right on the given attempt."""

    def __init__(self, good_on: int) -> None:
        super().__init__({})
        self.good_on = good_on
        self.attempts = 0

    async def process(self, **kwargs: Any) -> AgentResponse:
        self.calls.append(kwargs)
        if "-tulis-" in kwargs["session_id"]:
            self.attempts += 1
            return AgentResponse(content="OK final" if self.attempts >= self.good_on else "draf kasar", status=ResponseStatus.SUCCESS)
        return AgentResponse(content="lain", status=ResponseStatus.SUCCESS)


def loop_flow(manager: TeamOrchestrator, times: int = 3) -> Any:
    members = [
        agent("tulis"),
        node("cek", "gate", {"conditions": [{"mode": "contains", "value": "OK"}]}, "tulis"),
        node("hasil", "set", {"text": "Lolos: {{input}}"}, "cek", branch="A"),
    ]
    return manager.create_team("ulang", "", members, None, style="flow", starts=["tulis"],
                               loops=[{"from": "cek", "branch": "B", "to": "tulis", "max": times}])


async def test_a_gate_sends_the_work_back_until_it_passes(env: dict[str, Any]) -> None:
    fake = Writer(good_on=3)
    manager, _ = make(fake=fake)
    team = loop_flow(manager)
    run = await manager.start_run(team.id, "tulis puisi")
    await asyncio.wait_for(manager.run_tasks[run.id], timeout=5)
    run = manager.store.get_run(run.id)
    assert fake.attempts == 3 and run.loops == {"cek:B": 2}
    assert run.agents["cek"]["branch"] == "A" and run.result == "Lolos: OK final"
    retried = [c["workspace_context"] for c in fake.calls if "-tulis-" in c["session_id"]][1:]
    assert all("BELUM LOLOS" in text and "draf kasar" in text for text in retried)


async def test_a_loop_stops_at_its_limit(env: dict[str, Any]) -> None:
    fake = Writer(good_on=99)
    manager, _ = make(fake=fake)
    team = loop_flow(manager, times=2)
    run = await manager.start_run(team.id, "tulis")
    await asyncio.wait_for(manager.run_tasks[run.id], timeout=5)
    run = manager.store.get_run(run.id)
    assert fake.attempts == 3 and run.agents["cek"]["branch"] == "B" and "diulang 2x" in run.agents["cek"]["note"]
    assert run.agents["hasil"]["status"] == "skipped"


def test_loops_must_go_back_up_from_a_gate(env: dict[str, Any]) -> None:
    manager, _ = make()
    members = [agent("a"), node("cek", "gate", {"conditions": [{"mode": "empty"}]}, "a"), agent("b", "cek", branch="A")]
    with pytest.raises(ValueError, match="sebelum"):
        manager.create_team("x", "", members, None, style="flow", starts=["a"], loops=[{"from": "cek", "branch": "B", "to": "b"}])
    with pytest.raises(ValueError, match="IF atau Switch"):
        manager.create_team("x", "", members, None, style="flow", starts=["a"], loops=[{"from": "b", "branch": "A", "to": "a"}])
    with pytest.raises(ValueError, match="Jalur"):
        manager.create_team("x", "", members, None, style="flow", starts=["a"], loops=[{"from": "cek", "branch": "C", "to": "a"}])
    with pytest.raises(ValueError, match="kali"):
        manager.create_team("x", "", members, None, style="flow", starts=["a"], loops=[{"from": "cek", "branch": "B", "to": "a", "max": 99}])


# --- the database node writes to a Tabel data source --------------------------


async def test_the_database_node_logs_into_a_data_source(env: dict[str, Any]) -> None:
    registry.ensure_builtin()
    manager, _ = make({"-a-": "hasil kerja"})
    team = manager.create_team("log", "", [agent("a"), node("simpan", "database", {"source": "tabel", "table": "log_alur"}, "a")],
                               None, style="flow", starts=["a"])
    run = await manager.start_run(team.id, "kerjakan")
    await asyncio.wait_for(manager.run_tasks[run.id], timeout=5)
    run = manager.store.get_run(run.id)
    assert run.agents["simpan"]["status"] == "completed", run.agents["simpan"]["error"]
    backend = registry.backend_for(registry.ensure_builtin())
    rows = backend.all_rows("log_alur").rows
    assert rows[0]["values"]["content"] == "hasil kerja" and rows[0]["values"]["team_name"] == "log"
