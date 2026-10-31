"""Flow nodes: delay, OR gates, schedules, Telegram, and the choice of database."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agent import team_flow, team_orchestrator, team_outputs
from agent.orchestrator import AgentResponse
from agent.team_orchestrator import TeamOrchestrator, TeamStore, team_templates
from agent.team_outputs import OutputError
from config.constants import ResponseStatus
from config.settings import get_settings

TOKEN = "123:RAHASIA-BOT"
PASSWORD = "sandi-rahasia"


class FakeOrchestrator:
    def __init__(self, replies: dict[str, str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.replies = replies or {}
        self.registry = SimpleNamespace(get=lambda _id: object())

    async def process(self, **kwargs: Any) -> AgentResponse:
        self.calls.append(kwargs)
        for marker, reply in self.replies.items():
            if marker in kwargs["session_id"]:
                return AgentResponse(content=reply, status=ResponseStatus.SUCCESS, prompt_tokens=7)
        return AgentResponse(content=f"laporan {kwargs['session_id']}", status=ResponseStatus.SUCCESS)


def agent(id_: str, *after: str, branch: str = "") -> dict[str, Any]:
    return {"id": id_, "name": id_, "role": "r", "model_id": "m", "depends_on": list(after), "branch": branch}


def node(id_: str, kind: str, config: dict[str, Any], *after: str, branch: str = "") -> dict[str, Any]:
    return {"id": id_, "name": id_, "kind": kind, "config": config, "depends_on": list(after), "branch": branch}


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    current = get_settings()
    monkeypatch.setattr(current, "team_db_sqlite_path", str(tmp_path / "flow.sqlite3"))
    monkeypatch.setattr(current, "telegram_bot_token", "")
    monkeypatch.setattr(current, "telegram_chat_id", "")
    return current


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []

    async def fake_send(text: str, chat_id: str = "") -> int:
        messages.append((text, chat_id))
        return 1

    monkeypatch.setattr(team_orchestrator, "send_telegram", fake_send)
    return messages


def make(tmp_path: Path, replies: dict[str, str] | None = None) -> tuple[TeamOrchestrator, FakeOrchestrator]:
    fake = FakeOrchestrator(replies)
    return TeamOrchestrator(fake, TeamStore(tmp_path / "teams")), fake  # type: ignore[arg-type]


async def finish(manager: TeamOrchestrator, team_id: str, task: str = "tugas") -> Any:
    run = await manager.start_run(team_id, task)
    await asyncio.wait_for(manager.run_tasks[run.id], timeout=5)
    return manager.store.get_run(run.id)


def saved_rows(path: Path, table: str) -> list[tuple[Any, ...]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(f"SELECT task, content, node_id FROM {table}").fetchall()  # noqa: S608


# --- schedules -----------------------------------------------------------


def test_daily_schedule_fires_at_the_next_matching_time() -> None:
    schedule = {"mode": "daily", "time": "07:00", "days": team_flow.ALL_DAYS}
    before = datetime(2026, 11, 2, 6, 0).timestamp()
    after = datetime(2026, 11, 2, 8, 0).timestamp()
    assert team_flow.next_run_after(schedule, before) == datetime(2026, 11, 2, 7, 0).timestamp()
    assert team_flow.next_run_after(schedule, after) == datetime(2026, 11, 3, 7, 0).timestamp()


def test_schedule_skips_days_that_are_not_chosen() -> None:
    # 2 November 2026 is a Monday; only Friday is chosen.
    schedule = {"mode": "daily", "time": "07:00", "days": [4]}
    assert team_flow.next_run_after(schedule, datetime(2026, 11, 2, 6, 0).timestamp()) == datetime(2026, 11, 6, 7, 0).timestamp()


def test_interval_schedule_and_off() -> None:
    assert team_flow.next_run_after({"mode": "interval", "minutes": 10}, 1000.0) == 1600.0
    assert team_flow.next_run_after({"mode": "off"}, 1000.0) is None


@pytest.mark.parametrize(
    "schedule",
    [
        {"mode": "nanti"},
        {"mode": "daily", "time": "25:00", "task": "x"},
        {"mode": "daily", "time": "07:00"},
        {"mode": "interval", "minutes": 1, "task": "x"},
        {"mode": "daily", "time": "07:00", "days": [9], "task": "x"},
    ],
)
def test_bad_schedules_are_refused(schedule: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        team_flow.normalize_schedule(schedule)


async def test_due_schedule_starts_a_run_once(tmp_path: Path, settings: Any) -> None:
    manager, fake = make(tmp_path)
    team = manager.create_team(
        "t", "", [agent("a"), agent("b")], "a", schedule={"mode": "interval", "minutes": 5, "task": "cek cuaca"},
    )
    assert await manager.run_due_schedules(team.updated_at + 60) == []
    started = await manager.run_due_schedules(team.updated_at + 301)
    assert len(started) == 1
    await asyncio.wait_for(manager.run_tasks[started[0]], timeout=5)
    assert fake.calls[0]["task_hint"] == "cek cuaca"
    assert await manager.run_due_schedules(team.updated_at + 302) == []


async def test_a_daily_schedule_missed_while_off_does_not_fire_late(tmp_path: Path, settings: Any) -> None:
    manager, _ = make(tmp_path)
    team = manager.create_team(
        "t", "", [agent("a"), agent("b")], "a",
        schedule={"mode": "daily", "time": "07:00", "days": team_flow.ALL_DAYS, "task": "x"},
    )
    due = team_flow.next_run_after(team.schedule, team.updated_at)
    assert due is not None
    assert await manager.run_due_schedules(due + 3600) == []
    assert manager.store.get_team(team.id).last_scheduled_at == due + 3600  # type: ignore[union-attr]


# --- gates ---------------------------------------------------------------


def test_time_window_inside_outside_and_across_midnight() -> None:
    day = {"start": "07:00", "end": "22:00", "days": team_flow.ALL_DAYS}
    night = {"start": "22:00", "end": "06:00", "days": [0]}  # opens on Monday night
    assert team_flow.in_time_window(day, datetime(2026, 11, 2, 12, 0))
    assert not team_flow.in_time_window(day, datetime(2026, 11, 2, 23, 0))
    assert team_flow.in_time_window(night, datetime(2026, 11, 2, 23, 0))
    assert team_flow.in_time_window(night, datetime(2026, 11, 3, 5, 0))
    assert not team_flow.in_time_window(night, datetime(2026, 11, 4, 5, 0))


def test_keywords_and_decisions() -> None:
    assert team_flow.text_matches("banjir, gempa", "Ada BANJIR di utara")
    assert not team_flow.text_matches("gempa", "cerah")
    assert team_flow.parse_decision("YA, karena hujan") is True
    assert team_flow.parse_decision("**Tidak** ada") is False
    assert team_flow.parse_decision("mungkin") is None
    assert team_flow.parse_decision("Yakin sekali") is None


@pytest.mark.parametrize(
    ("kind", "config"),
    [
        ("delay", {"seconds": 0}),
        ("delay", {"seconds": 99999}),
        ("gate", {"mode": "contains", "value": " "}),
        ("gate", {"mode": "time", "start": "7", "end": "8"}),
        ("gate", {"mode": "sihir", "value": "x"}),
        ("telegram", {"chat_id": "bukan chat"}),
        ("database", {"database": "oracle"}),
        ("database", {"database": "sqlite", "table": "x; DROP TABLE y"}),
    ],
)
def test_bad_node_settings_are_refused(kind: str, config: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        team_flow.normalize_node_config(kind, config, team_outputs.DATABASE_IDS)


async def test_only_the_taken_branch_runs_and_supplies_the_answer(tmp_path: Path, settings: Any, sent: list[Any]) -> None:
    manager, fake = make(tmp_path, {"-cari-": "ada banjir besar", "-a1-": "PERINGATAN", "-b1-": "SANTAI"})
    team = manager.create_team("t", "", [
        agent("cari"),
        node("cek", "gate", {"mode": "contains", "value": "banjir"}, "cari"),
        agent("a1", "cek", branch="A"),
        agent("b1", "cek", branch="B"),
        node("kirim", "telegram", {"chat_id": "42"}, "a1", "b1"),
    ], None)
    run = await finish(manager, team.id)
    assert run.status == "completed"
    assert run.agents["cek"]["branch"] == "A"
    assert run.agents["a1"]["status"] == "completed"
    assert run.agents["b1"]["status"] == "skipped"
    assert not any("-b1-" in c["session_id"] for c in fake.calls)
    assert sent == [("PERINGATAN", "42")]
    assert run.result == "PERINGATAN"


async def test_ai_gate_uses_no_tools_and_reads_yes_or_no(tmp_path: Path, settings: Any) -> None:
    manager, fake = make(tmp_path, {"-cek-gate": "TIDAK, semua aman"})
    team = manager.create_team("t", "", [
        agent("cari"),
        {**node("cek", "gate", {"mode": "ai", "value": "Mendesak?"}, "cari"), "model_id": "m"},
        agent("a1", "cek", branch="A"),
        agent("b1", "cek", branch="B"),
    ], None)
    run = await finish(manager, team.id)
    assert run.agents["cek"]["branch"] == "B"
    assert run.agents["a1"]["status"] == "skipped"
    gate_call = next(c for c in fake.calls if "-cek-gate" in c["session_id"])
    assert gate_call["limits"].max_tools == 0
    assert run.total_tokens >= 7


async def test_failed_gate_takes_neither_branch(tmp_path: Path, settings: Any) -> None:
    manager, fake = make(tmp_path)
    original = fake.process

    async def process(**kwargs: Any) -> AgentResponse:
        if "-gate" in kwargs["session_id"]:
            raise RuntimeError("model mati")
        return await original(**kwargs)

    fake.process = process  # type: ignore[method-assign]
    team = manager.create_team("t", "", [
        agent("cari"),
        {**node("cek", "gate", {"mode": "ai", "value": "x"}, "cari"), "model_id": "m"},
        agent("a1", "cek", branch="A"),
    ], None)
    run = await finish(manager, team.id)
    assert run.agents["cek"]["status"] == "failed"
    assert run.agents["a1"]["status"] == "skipped"
    assert run.status == "completed"


# --- delay ---------------------------------------------------------------


async def test_delay_waits_and_passes_the_text_on(tmp_path: Path, settings: Any, sent: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(team_orchestrator.asyncio, "sleep", sleep)
    manager, _ = make(tmp_path, {"-cari-": "isi"})
    team = manager.create_team("t", "", [
        agent("cari"), node("jeda", "delay", {"seconds": 30}, "cari"), node("kirim", "telegram", {}, "jeda"),
    ], None)
    run = await finish(manager, team.id)
    assert 30 in slept
    assert run.waited_seconds == 30
    assert sent[0][0] == "isi"


async def test_time_in_delays_does_not_shrink_the_budget(tmp_path: Path, settings: Any) -> None:
    manager, _ = make(tmp_path)
    run = team_orchestrator.TeamRun(id="r", team_id="t", task="x", started_at=time.time())
    before = manager._workers_until(run)
    run.waited_seconds = 100
    assert manager._workers_until(run) == before + 100


# --- outputs in a run ----------------------------------------------------


async def test_a_failed_output_keeps_the_answer_and_the_next_output_still_runs(tmp_path: Path, settings: Any) -> None:
    manager, _ = make(tmp_path, {"-cari-": "hasil nyata"})
    team = manager.create_team("t", "", [
        agent("cari"),
        node("kirim", "telegram", {}, "cari"),
        node("simpan", "database", {"database": "sqlite", "table": "asisten"}, "kirim"),
    ], None)
    run = await finish(manager, team.id, task="apa kabar")
    assert run.status == "completed"
    assert run.result == "hasil nyata"
    assert "TELEGRAM_BOT_TOKEN" in run.agents["kirim"]["error"]
    assert run.agents["simpan"]["status"] == "completed"
    assert saved_rows(Path(settings.team_db_sqlite_path), "asisten") == [("apa kabar", "hasil nyata", "simpan")]


async def test_unconnected_output_delivers_the_leads_answer(tmp_path: Path, settings: Any, sent: list[Any]) -> None:
    manager, _ = make(tmp_path, {"-lead-": "jawaban akhir"})
    team = manager.create_team("t", "", [agent("lead"), agent("a"), node("kirim", "telegram", {})], "lead")
    run = await finish(manager, team.id)
    assert sent == [("jawaban akhir", "")]
    assert run.result == "jawaban akhir"


async def test_outputs_never_send_the_task_when_every_agent_failed(tmp_path: Path, settings: Any, sent: list[Any]) -> None:
    manager, fake = make(tmp_path)

    async def process(**_kwargs: Any) -> AgentResponse:
        raise RuntimeError("semua model mati")

    fake.process = process  # type: ignore[method-assign]
    team = manager.create_team("t", "", [
        agent("cari"), node("jeda", "delay", {"seconds": 1}, "cari"), node("kirim", "telegram", {}, "jeda"),
    ], None)
    run = await finish(manager, team.id, task="TUGAS RAHASIA")
    assert sent == []
    assert run.agents["kirim"]["status"] == "failed"


def test_flow_needs_an_agent_and_at_most_six(tmp_path: Path, settings: Any) -> None:
    manager, _ = make(tmp_path)
    with pytest.raises(ValueError, match="minimal satu agent"):
        manager.create_team("t", "", [node("a", "delay", {"seconds": 1}), node("b", "delay", {"seconds": 1})], None)
    with pytest.raises(ValueError, match="Maksimal 6"):
        manager.create_team("t", "", [agent(str(i)) for i in range(7)], "0")


def test_secrets_never_enter_saved_teams(tmp_path: Path, settings: Any) -> None:
    manager, _ = make(tmp_path)
    team = manager.create_team("t", "", [
        agent("a"), node("k", "telegram", {"chat_id": "42", "token": TOKEN}, "a"),
    ], None)
    assert team.members[1].config == {"chat_id": "42"}
    assert TOKEN not in (tmp_path / "teams" / "teams.json").read_text()


def test_all_agent_teams_still_default_to_a_lead(tmp_path: Path, settings: Any) -> None:
    manager, _ = make(tmp_path)
    assert manager.create_team("t", "", [agent("a"), agent("b")], None).lead_id == "a"


def test_assistant_template_is_valid(tmp_path: Path, settings: Any) -> None:
    manager, _ = make(tmp_path)
    template = next(t for t in team_templates(["ag/gemini-flash", "ag/claude-sonnet"]) if t["key"] == "assistant")
    team = manager.create_team(template["name"], "", template["members"], template["lead_id"], template["reviewer_id"], template["schedule"])
    assert team.lead_id == ""
    assert {m.kind for m in team.members} == {"agent", "delay", "gate", "telegram", "database"}
    assert "web_research" in team.members[0].instructions


def test_teams_saved_before_this_still_load(tmp_path: Path) -> None:
    directory = tmp_path / "teams"
    directory.mkdir()
    (directory / "teams.json").write_text(json.dumps([{
        "id": "x", "name": "lama", "description": "", "lead_id": "a",
        "members": [{"id": "a", "name": "A", "role": "r", "model_id": "m"}],
    }]), encoding="utf-8")
    team = TeamStore(directory).get_team("x")
    assert team is not None and team.members[0].kind == "agent" and team.schedule == {"mode": "off"}


# --- Telegram ------------------------------------------------------------


def use_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real = httpx.AsyncClient
    monkeypatch.setattr(team_outputs.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_long_messages_are_split_on_lines() -> None:
    parts = team_outputs._split_message("baris\n" * 2000)
    assert len(parts) > 1 and all(len(part) <= 4000 for part in parts)
    assert sum(part.count("baris") for part in parts) == 2000


async def test_telegram_sends_every_part_to_the_right_chat(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.telegram_bot_token = TOKEN
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert f"/bot{TOKEN}/sendMessage" in str(request.url)
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    use_transport(monkeypatch, handler)
    assert await team_outputs.send_telegram("x\n" * 3000, "42") == 2
    assert {item["chat_id"] for item in seen} == {"42"}


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"description": "Unauthorized"}),
        httpx.Response(500, text="gagal"),
        httpx.ConnectError("cannot connect to https://api.telegram.org/bot123:RAHASIA-BOT/sendMessage"),
    ],
)
async def test_telegram_errors_never_contain_the_token(settings: Any, monkeypatch: pytest.MonkeyPatch, response: Any) -> None:
    settings.telegram_bot_token = TOKEN

    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

    use_transport(monkeypatch, handler)
    with pytest.raises(OutputError) as caught:
        await team_outputs.send_telegram("halo", "42")
    assert TOKEN not in str(caught.value)
    assert caught.value.__cause__ is None


async def test_telegram_waits_out_a_rate_limit_once(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.telegram_bot_token = TOKEN
    replies = [httpx.Response(429, json={"parameters": {"retry_after": 0}}), httpx.Response(200, json={"ok": True})]
    use_transport(monkeypatch, lambda _request: replies.pop(0))
    assert await team_outputs.send_telegram("halo", "42") == 1
    assert not replies


# --- databases -----------------------------------------------------------

RECORD = {"run_id": "r", "team_id": "t", "team_name": "n", "node_id": "k", "task": "x", "content": "y", "created_at": "z"}


async def test_sqlite_values_are_bound_not_pasted(settings: Any) -> None:
    hostile = "'); DROP TABLE hasil; --"
    await team_outputs.save_to_database("sqlite", "hasil", {**RECORD, "content": hostile})
    assert saved_rows(Path(settings.team_db_sqlite_path), "hasil")[0][1] == hostile


async def test_table_names_are_checked_again_when_saving(settings: Any) -> None:
    with pytest.raises(OutputError, match="tidak valid"):
        await team_outputs.save_to_database("sqlite", "x; DROP TABLE y", RECORD)


def test_options_say_what_each_database_still_needs(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "team_db_postgres_url", "")
    options = {o["id"]: o for o in team_outputs.database_options()}
    assert set(options) == {"sqlite", "postgres", "mysql", "mongodb"}
    assert options["sqlite"]["ready"]
    assert not options["postgres"]["ready"] and "TEAM_DB_POSTGRES_URL" in options["postgres"]["reason"]


class FakeCursor:
    def __init__(self, log: list[Any]) -> None:
        self.log = log

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str, values: Any = None) -> None:
        self.log.append((sql, values))


class FakeConnection:
    def __init__(self, log: list[Any]) -> None:
        self.log = log

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.log)

    def commit(self) -> None:
        self.log.append("commit")

    def close(self) -> None:
        self.log.append("close")


def fake_driver(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    monkeypatch.setattr(team_outputs.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(team_outputs.importlib, "import_module", lambda _name: module)


async def test_postgres_creates_the_table_and_binds_values(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[Any] = []
    monkeypatch.setattr(settings, "team_db_postgres_url", f"postgresql://u:{PASSWORD}@db/app")
    fake_driver(monkeypatch, SimpleNamespace(connect=lambda *_a, **_k: FakeConnection(log)))
    await team_outputs.save_to_database("postgres", "hasil", RECORD)
    assert "BIGSERIAL" in log[0][0]
    assert "VALUES (%s, %s" in log[1][0] and log[1][1] == list(RECORD.values())
    assert log[2:] == ["commit", "close"]


async def test_mysql_reads_the_connection_string(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[Any] = []
    captured: dict[str, Any] = {}

    def connect(**kwargs: Any) -> FakeConnection:
        captured.update(kwargs)
        return FakeConnection(log)

    monkeypatch.setattr(settings, "team_db_mysql_url", f"mysql://u:{PASSWORD}@db:3307/app")
    fake_driver(monkeypatch, SimpleNamespace(connect=connect))
    await team_outputs.save_to_database("mysql", "hasil", RECORD)
    assert (captured["host"], captured["port"], captured["user"], captured["database"]) == ("db", 3307, "u", "app")
    assert captured["password"] == PASSWORD
    assert "AUTO_INCREMENT" in log[0][0]


async def test_mongodb_inserts_into_the_collection(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    inserted: list[Any] = []
    collection = SimpleNamespace(insert_one=inserted.append)
    client = SimpleNamespace(get_default_database=lambda: {"hasil": collection}, close=lambda: None)
    monkeypatch.setattr(settings, "team_db_mongodb_url", "mongodb://db/app")
    fake_driver(monkeypatch, SimpleNamespace(MongoClient=lambda *_a, **_k: client))
    await team_outputs.save_to_database("mongodb", "hasil", RECORD)
    assert inserted == [RECORD]


async def test_database_errors_never_contain_the_password(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def connect(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(f"could not connect to postgresql://u:{PASSWORD}@db/app")

    monkeypatch.setattr(settings, "team_db_postgres_url", f"postgresql://u:{PASSWORD}@db/app")
    fake_driver(monkeypatch, SimpleNamespace(connect=connect))
    with pytest.raises(OutputError) as caught:
        await team_outputs.save_to_database("postgres", "hasil", RECORD)
    assert PASSWORD not in str(caught.value)
    assert caught.value.__cause__ is None


def test_settings_hide_their_secrets(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", TOKEN)
    monkeypatch.setattr(settings, "team_db_postgres_url", f"postgresql://u:{PASSWORD}@db/app")
    assert TOKEN not in repr(settings) and PASSWORD not in repr(settings)
