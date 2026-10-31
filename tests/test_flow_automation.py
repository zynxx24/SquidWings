"""n8n-style flow pieces: AND/OR gates, switch, merge, text, HTTP, cron, and Telegram triggers."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from agent import team_flow, team_orchestrator, team_outputs
from agent.team_orchestrator import TeamOrchestrator, TeamStore, team_templates
from agent.team_outputs import OutputError
from config.settings import get_settings
from tests.test_team_nodes import FakeOrchestrator, agent, node

MONDAY_8 = datetime(2026, 11, 2, 8, 0)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    current = get_settings()
    monkeypatch.setattr(current, "team_db_sqlite_path", str(tmp_path / "flow.sqlite3"))
    monkeypatch.setattr(current, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(current, "telegram_chat_id", "42")
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


# --- triggers ------------------------------------------------------------


def test_daily_schedule_fires_at_each_of_several_times() -> None:
    schedule = team_flow.normalize_schedule({"mode": "daily", "time": "05:00, 12:00,17:00", "task": "x"})
    assert schedule["times"] == ["05:00", "12:00", "17:00"]
    assert team_flow.next_run_after(schedule, MONDAY_8.timestamp()) == datetime(2026, 11, 2, 12, 0).timestamp()
    assert team_flow.next_run_after(schedule, datetime(2026, 11, 2, 18, 0).timestamp()) == datetime(2026, 11, 3, 5, 0).timestamp()


def test_cron_follows_crontab_rules() -> None:
    weekdays = {"mode": "cron", "cron": "30 6 * * 1-5"}
    # Saturday 31 October: the next weekday is Monday.
    assert team_flow.next_run_after(weekdays, datetime(2026, 10, 31, 7, 0).timestamp()) == datetime(2026, 11, 2, 6, 30).timestamp()
    every_15 = {"mode": "cron", "cron": "*/15 * * * *"}
    assert team_flow.next_run_after(every_15, datetime(2026, 11, 2, 8, 7).timestamp()) == datetime(2026, 11, 2, 8, 15).timestamp()
    # Both day fields restricted: either one fires, as in crontab. 2 Nov is a Monday.
    either = {"mode": "cron", "cron": "0 9 1 * 1"}
    assert team_flow.next_run_after(either, datetime(2026, 10, 31, 10, 0).timestamp()) == datetime(2026, 11, 1, 9, 0).timestamp()
    sunday = {"mode": "cron", "cron": "0 9 * * 7"}
    assert team_flow.next_run_after(sunday, MONDAY_8.timestamp()) == datetime(2026, 11, 8, 9, 0).timestamp()


def test_once_fires_one_time_only() -> None:
    schedule = team_flow.normalize_schedule({"mode": "once", "at": "2026-11-02 09:00", "task": "x"})
    moment = datetime(2026, 11, 2, 9, 0).timestamp()
    assert team_flow.next_run_after(schedule, MONDAY_8.timestamp()) == moment
    assert team_flow.next_run_after(schedule, moment) is None


@pytest.mark.parametrize(
    "schedule",
    [
        {"mode": "cron", "cron": "0 7 * *", "task": "x"},
        {"mode": "cron", "cron": "61 7 * * *", "task": "x"},
        {"mode": "cron", "cron": "0 7 * * senin", "task": "x"},
        {"mode": "once", "at": "besok", "task": "x"},
        {"mode": "once", "at": "2026-02-30 07:00", "task": "x"},
        {"mode": "daily", "time": "07:00, 7", "task": "x"},
        {"mode": "telegram", "keyword": "dua kata"},
    ],
)
def test_bad_triggers_are_refused(schedule: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        team_flow.normalize_schedule(schedule)


def test_telegram_messages_become_tasks() -> None:
    assert team_flow.telegram_task({"keyword": ""}, "halo") == "halo"
    assert team_flow.telegram_task({"keyword": "/jadwal"}, "/jadwal besok rapat") == "besok rapat"
    assert team_flow.telegram_task({"keyword": "/jadwal"}, "/jadwal@bot_saya") == "/jadwal@bot_saya"
    assert team_flow.telegram_task({"keyword": "/jadwal"}, "halo") is None
    assert team_flow.telegram_task({"keyword": "", "task": "Konteks"}, "hai") == "Konteks\n\nPesan: hai"


async def test_only_the_allowed_chat_can_start_a_flow(tmp_path: Path, settings: Any, sent: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    manager, _ = make(tmp_path, {"-a-": "balasan"})
    team = manager.create_team("bot", "", [agent("a"), node("balas", "telegram", {}, "a")], None, schedule={"mode": "telegram"})
    batches = [
        [{"update_id": 9, "message": {"chat": {"id": 42}, "text": "lama"}}],
        [
            {"update_id": 10, "message": {"chat": {"id": 666}, "text": "orang asing"}},
            {"update_id": 11, "message": {"chat": {"id": 42}, "text": "cek jadwal"}},
        ],
    ]
    offsets: list[int | None] = []

    async def fake_fetch(offset: int | None, wait: int = 25) -> list[dict[str, Any]]:
        offsets.append(offset)
        return batches.pop(0)

    monkeypatch.setattr(team_orchestrator, "fetch_telegram_updates", fake_fetch)
    started = await manager.poll_telegram(wait=0)
    assert offsets == [-1, 10]  # the message waiting since before startup is dropped
    assert len(started) == 1
    await asyncio.wait_for(manager.run_tasks[started[0]], timeout=5)
    run = manager.store.get_run(started[0])
    assert run is not None and run.task == "cek jadwal"
    assert run.trigger == {"source": "telegram", "chat_id": "42"}
    assert sent == [("balasan", "42")]
    assert manager._telegram_offset == 12
    assert team.id == run.team_id


async def test_telegram_trigger_is_off_without_an_allowed_chat(tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_chat_id", "")
    manager, _ = make(tmp_path)
    manager.create_team("bot", "", [agent("a"), agent("b")], "a", schedule={"mode": "telegram"})

    async def fake_fetch(offset: int | None, wait: int = 25) -> list[dict[str, Any]]:
        return [] if offset == -1 else [{"update_id": 1, "message": {"chat": {"id": 42}, "text": "hai"}}]

    monkeypatch.setattr(team_orchestrator, "fetch_telegram_updates", fake_fetch)
    assert await manager.poll_telegram(wait=0) == []


# --- conditions ----------------------------------------------------------


@pytest.mark.parametrize(
    ("condition", "text", "expected"),
    [
        ({"mode": "contains", "value": "hujan, badai"}, "Besok BADAI", True),
        ({"mode": "not_contains", "value": "libur"}, "hari kerja", True),
        ({"mode": "equals", "value": "ya"}, "  YA ", True),
        ({"mode": "regex", "value": r"^/catat\b"}, "/catat beli susu", True),
        ({"mode": "regex", "value": r"^/catat\b"}, "/catatan", False),
        ({"mode": "empty"}, "  ", True),
        ({"mode": "time", "start": "07:00", "end": "09:00", "days": [0]}, "", True),
        ({"mode": "ai", "value": "?"}, "", None),
    ],
)
def test_conditions(condition: dict[str, Any], text: str, expected: bool | None) -> None:
    assert team_flow.check_condition(condition, text, MONDAY_8) is expected


@pytest.mark.parametrize(
    ("kind", "config"),
    [
        ("gate", {"combine": "xor", "conditions": [{"mode": "empty"}]}),
        ("gate", {"conditions": []}),
        ("gate", {"conditions": [{"mode": "empty"}] * 6}),
        ("gate", {"conditions": [{"mode": "regex", "value": "("}]}),
        ("switch", {"rules": []}),
        ("switch", {"rules": [{"mode": "empty"}] * 4}),
        ("merge", {"mode": "sebagian"}),
        ("set", {"text": " "}),
        ("http", {"url": "ftp://x"}),
        ("http", {"url": "https://x.id", "method": "DELETE"}),
    ],
)
def test_bad_new_node_settings_are_refused(kind: str, config: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        team_flow.normalize_node_config(kind, config, team_outputs.DATABASE_IDS)


def test_old_single_condition_gates_still_load() -> None:
    config = team_flow.normalize_node_config("gate", {"mode": "contains", "value": "banjir"}, ())
    assert config == {"combine": "and", "conditions": [{"mode": "contains", "value": "banjir"}]}


def test_templates_fill_known_names_and_keep_typos() -> None:
    assert team_flow.render_template("{{ day }} {{input}} {{salah}}", {"day": "Senin", "input": "x"}) == "Senin x {{salah}}"


async def test_and_gate_skips_the_model_when_a_plain_condition_fails(tmp_path: Path, settings: Any) -> None:
    manager, fake = make(tmp_path, {"-cari-": "cerah sekali"})
    team = manager.create_team("t", "", [
        agent("cari"),
        node("cek", "gate", {"combine": "and", "conditions": [
            {"mode": "ai", "value": "Perlu payung?"}, {"mode": "contains", "value": "hujan"},
        ]}, "cari") | {"model_id": "m"},
        agent("a", "cek", branch="A"),
        agent("b", "cek", branch="B"),
    ], None)
    run = await finish(manager, team.id)
    assert run.agents["cek"]["branch"] == "B"
    assert not any("gate" in call["session_id"] for call in fake.calls)


async def test_or_gate_asks_the_model_only_when_needed(tmp_path: Path, settings: Any) -> None:
    manager, fake = make(tmp_path, {"-cari-": "cerah", "gate0": "YA, perlu"})
    team = manager.create_team("t", "", [
        agent("cari"),
        node("cek", "gate", {"combine": "or", "conditions": [
            {"mode": "ai", "value": "Perlu payung?"}, {"mode": "contains", "value": "hujan"},
        ]}, "cari") | {"model_id": "m"},
        agent("a", "cek", branch="A"),
        agent("b", "cek", branch="B"),
    ], None)
    run = await finish(manager, team.id)
    assert run.agents["cek"]["branch"] == "A"
    assert sum("gate" in call["session_id"] for call in fake.calls) == 1


async def test_switch_takes_the_first_matching_rule_or_d(tmp_path: Path, settings: Any) -> None:
    manager, _ = make(tmp_path)
    members = [
        node("pilah", "switch", {"rules": [{"mode": "contains", "value": "cuaca"}, {"mode": "contains", "value": "jadwal"}]}),
        agent("a", "pilah", branch="A"),
        agent("b", "pilah", branch="B"),
        agent("d", "pilah", branch="D"),
    ]
    team = manager.create_team("t", "", members, None)
    run = await finish(manager, team.id, task="jadwal dan cuaca")
    assert run.agents["pilah"]["branch"] == "A"
    assert [run.agents[i]["status"] for i in "abd"] == ["completed", "skipped", "skipped"]
    run = await finish(manager, team.id, task="halo")
    assert run.agents["pilah"]["branch"] == "D"
    assert run.agents["d"]["status"] == "completed"


async def test_merge_all_waits_for_every_path(tmp_path: Path, settings: Any) -> None:
    manager, _ = make(tmp_path)
    team = manager.create_team("t", "", [
        node("cek", "gate", {"conditions": [{"mode": "contains", "value": "x"}]}),
        agent("a", "cek", branch="A"),
        agent("b"),
        node("semua", "merge", {"mode": "all"}, "a", "b"),
        node("salah_satu", "merge", {"mode": "any"}, "a", "b"),
    ], None)
    run = await finish(manager, team.id, task="tanpa huruf itu")
    assert run.agents["semua"]["status"] == "skipped"
    assert run.agents["salah_satu"]["status"] == "completed"
    assert run.agents["salah_satu"]["content"] == "laporan " + run.id + "-b-worker"


async def test_set_node_fills_the_template(tmp_path: Path, settings: Any, sent: list[Any]) -> None:
    manager, _ = make(tmp_path, {"-a-": "isi"})
    team = manager.create_team("t", "", [
        agent("a"),
        node("teks", "set", {"text": "{{team}}: {{input}} / {{node.a}} / {{task}}"}, "a"),
        node("kirim", "telegram", {"chat_id": "7"}, "teks"),
    ], None)
    await finish(manager, team.id, task="T")
    assert sent == [("t: isi / isi / T", "7")]


async def test_http_node_refuses_private_addresses() -> None:
    with pytest.raises(OutputError, match="bukan alamat publik"):
        await team_outputs.http_request("GET", "http://127.0.0.1:8080/api/teams")


async def test_http_node_passes_the_reply_on(tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str]] = []

    async def fake_http(method: str, url: str, body: str = "") -> str:
        calls.append((method, url, body))
        return '{"suhu": 31}'

    monkeypatch.setattr(team_orchestrator, "http_request", fake_http)
    manager, fake = make(tmp_path)
    team = manager.create_team("t", "", [
        node("api", "http", {"method": "POST", "url": "https://contoh.id/x", "body": '{"q": "{{task}}"}'}),
        agent("a", "api"),
    ], None)
    run = await finish(manager, team.id, task="Jakarta")
    assert calls == [("POST", "https://contoh.id/x", '{"q": "Jakarta"}')]
    assert run.agents["api"]["content"] == '{"suhu": 31}'
    assert '{"suhu": 31}' in fake.calls[0]["workspace_context"]


# --- templates -----------------------------------------------------------


@pytest.mark.parametrize("key", ["reminder", "telegram-bot"])
def test_new_templates_are_valid(tmp_path: Path, settings: Any, key: str) -> None:
    manager, _ = make(tmp_path)
    template = next(t for t in team_templates(["m"]) if t["key"] == key)
    team = manager.create_team(
        template["name"], "", template["members"], template["lead_id"], template["reviewer_id"], template["schedule"],
    )
    assert team.schedule["mode"] == template["schedule"]["mode"]


async def test_reminder_wakes_in_the_morning_then_rings_again(tmp_path: Path, settings: Any, sent: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    class Morning(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return MONDAY_8.replace(hour=5)

    monkeypatch.setattr(team_orchestrator, "datetime", Morning)

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(team_orchestrator.asyncio, "sleep", no_wait)
    manager, _ = make(tmp_path, {"-bangun-": "Bangun! 05:15 olahraga"})
    template = next(t for t in team_templates(["m"]) if t["key"] == "reminder")
    team = manager.create_team(template["name"], "", template["members"], None, None, template["schedule"])
    run = await finish(manager, team.id, task="Tidak ada catatan khusus.")
    assert run.status == "completed"
    assert run.agents["pagi"]["branch"] == "A"
    assert run.agents["ingat"]["status"] == "skipped"
    assert sent[0][0] == "Bangun! 05:15 olahraga"
    assert sent[1][0].startswith("Alarm kedua, jam 05:00")
    assert run.agents["catat"]["status"] == "completed"


# --- folder skills -------------------------------------------------------


def test_folder_skills_load_with_guides_and_requirements(monkeypatch: pytest.MonkeyPatch) -> None:
    from brain import skills

    skills.load_all.cache_clear()
    animate = skills.load_all()["animate"]
    assert animate.root is not None and "craft.md" in animate.guides
    assert animate.requires == ("node", "ffmpeg")
    assert "SKILL.md" not in animate.guides
    assert skills.select("tolong buat video animasi tentang sejarah internet")[0].name == "animate"
    monkeypatch.setenv("SKILLS_DISABLED", "animate, fix-bug")
    skills.load_all.cache_clear()
    try:
        assert "animate" not in skills.load_all() and "fix-bug" not in skills.load_all()
    finally:
        monkeypatch.delenv("SKILLS_DISABLED")
        skills.load_all.cache_clear()


async def test_load_skill_reads_only_listed_guides_and_installs_once_per_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from brain import skills
    from tool.execute.execute_skills import skill_tool

    skills.load_all.cache_clear()
    escape = await skill_tool.load_skill("animate", file="../../../.env")
    assert escape["ok"] is False
    craft = await skill_tool.load_skill("animate", file="craft.md")
    assert craft["ok"] and "Craft" in craft["guide"]
    monkeypatch.setattr(skill_tool, "get_active_workspace", lambda: str(tmp_path))
    monkeypatch.setattr(skill_tool, "get_active_session", lambda: "chat-a")
    first = await skill_tool.load_skill("animate", install_files=True)
    assert first["installed_at"] == ".skills/chat-a/animate"
    assert (tmp_path / ".skills/chat-a/animate/tools/build.mjs").is_file()
    (tmp_path / ".skills/chat-a/animate/craft.md").write_text("milik pengguna")
    await skill_tool.load_skill("animate", install_files=True)
    assert (tmp_path / ".skills/chat-a/animate/craft.md").read_text() == "milik pengguna"
    # Another chat in the same project gets a clean copy of its own.
    monkeypatch.setattr(skill_tool, "get_active_session", lambda: "../chat-b")
    second = await skill_tool.load_skill("animate", install_files=True)
    assert second["installed_at"] == ".skills/chat-b/animate"
    assert "Craft" in (tmp_path / ".skills/chat-b/animate/craft.md").read_text()


async def test_web_page_cache_is_kept_per_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from tool.execute.execute_web_search import web_reader
    from utils.workspace_context import set_active_session

    downloads: list[str] = []

    async def fake_download(url: str) -> tuple[str, str, bytes]:
        downloads.append(url)
        return url, "text/plain", f"isi untuk unduhan {len(downloads)}".encode()

    monkeypatch.setattr(web_reader, "_download", fake_download)
    monkeypatch.setattr(web_reader, "_cache", {})

    async def read_as(session: str) -> str:
        set_active_session(session)
        return (await web_reader.fetch_page("https://contoh.id/a")).text

    assert await asyncio.create_task(read_as("chat-a")) == "isi untuk unduhan 1"
    assert await asyncio.create_task(read_as("chat-a")) == "isi untuk unduhan 1"
    assert await asyncio.create_task(read_as("chat-b")) == "isi untuk unduhan 2"


def test_contextual_skills_stay_dormant_without_context() -> None:
    from brain import skills

    skills.load_all.cache_clear()
    assert skills.load_all()["animate"].scope == "contextual"
    assert skills.load_all()["fix-bug"].scope == "core"
    assert "animate" not in skills.index_text()
    assert "fix-bug" in skills.index_text()
    # "animasi" for a CSS hover is not a video: the context words must match too.
    assert skills.woken("tambah animasi hover di tombol") == []
    assert "animate" not in [s.name for s in skills.select("tambah animasi hover di tombol")]
    assert skills.woken("bikin video animasi 30 detik") == ["animate"]


def test_a_woken_skill_stays_for_that_session_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.harness import briefing
    from config.constants import TaskType

    chat_a, chat_b = tmp_path / "a", tmp_path / "b"
    current = {"workspace": str(chat_a)}
    monkeypatch.setattr(briefing, "get_active_workspace", lambda: current["workspace"])
    guides, used = briefing.skill_guidance("buat video animasi tentang fotosintesis", TaskType.CODE, 40_000)
    assert "animate" in used and "- animate:" in guides
    # Later turns without the word keep it, so the work does not lose its guide.
    guides, _ = briefing.skill_guidance("lanjutkan storyboard panel 3", TaskType.CODE, 40_000)
    assert "- animate:" in guides
    current["workspace"] = str(chat_b)
    guides, _ = briefing.skill_guidance("lanjutkan storyboard panel 3", TaskType.CODE, 40_000)
    assert "- animate:" not in guides


async def test_loading_a_dormant_skill_by_name_wakes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tool.execute.execute_skills import skill_tool
    from utils import session_settings

    monkeypatch.setattr(skill_tool, "get_active_workspace", lambda: str(tmp_path))
    assert (await skill_tool.load_skill("animate"))["ok"]
    assert session_settings.load(tmp_path).skills == ["animate"]
