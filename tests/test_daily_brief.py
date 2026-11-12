"""The daily brief: what was made, what is hanging, one file a day, shown once per place. No model, no network."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brain.memory.conversation_store import ConversationStore, Message
from utils import daily_brief

NOW = dt.datetime(2026, 11, 12, 8, 0, tzinfo=dt.UTC)


def at(days_ago: float, hour: int = 10) -> float:
    return (NOW - dt.timedelta(days=days_ago)).replace(hour=hour, minute=0).timestamp()


class FakeStore:
    def __init__(self, chats: dict[str, tuple[str, list[Message]]]) -> None:
        self.chats = chats

    def list_sessions(self) -> list[dict[str, Any]]:
        return [{"session_id": sid, "title": title, "updated_at": msgs[-1].timestamp} for sid, (title, msgs) in self.chats.items()]

    def get_history(self, session_id: str) -> list[Message]:
        return list(self.chats[session_id][1])


def user(text: str, ts: float) -> Message:
    return Message(role="user", content=text, timestamp=ts)


def agent(text: str, ts: float, trace: str = "", tokens: int = 100, model: str = "m1") -> Message:
    return Message(role="assistant", content=text, timestamp=ts, tool_trace=trace, token_count=tokens, model_used=model)


@pytest.fixture()
def store() -> FakeStore:
    return FakeStore({
        "a": ("[web] Landing kopi", [
            user("buat landing page kedai kopi", at(1)),
            agent("Sudah jadi, ada di index.html.", at(1, 11), "file_write index.html; file_write style.css; read_file notes.md"),
        ]),
        "b": ("[terminal] Perbaiki login", [
            user("login gagal terus", at(1, 14)),
            agent("Sudah kuubah.", at(1, 15), "file_patch auth.py (failed); run_checks"),
            user("coba lagi dong", at(0, 7)),
        ]),
        "c": ("[web] Tanya resep", [
            user("resep rendang", at(0, 6)),
            agent("Mau yang pedas atau sedang?", at(0, 6)),
        ]),
        "d": ("[web] Riset python", [
            user("riset python", at(1, 9)),
            agent("Ringkasannya begini. Selanjutnya saya perlu membandingkan versi 3.13.", at(1, 9)),
        ]),
        "old": ("[web] Lama", [user("halo", at(5)), agent("hai", at(5))]),
    })


def test_only_chats_since_yesterday_count(store: FakeStore) -> None:
    brief = daily_brief.build(store, NOW)
    assert {c["label"] for c in brief["chats"]} == {"[web] Landing kopi", "[terminal] Perbaiki login", "[web] Tanya resep", "[web] Riset python"}
    assert brief["chat_count"] == 4 and brief["empty"] is False
    assert brief["day"] == "2026-11-12" and brief["weekday"] == "Kamis"


def test_files_come_from_successful_writes_only(store: FakeStore) -> None:
    brief = daily_brief.build(store, NOW)
    assert brief["files"] == ["index.html", "style.css"]            # read_file is not a result, the failed patch is not made
    assert "auth.py" not in brief["files"] and brief["file_count"] == 2


def test_what_is_hanging_is_found_in_four_ways(store: FakeStore) -> None:
    hanging = {h["chat"]: h["text"] for h in daily_brief.build(store, NOW)["hanging"]}
    assert hanging["[terminal] Perbaiki login"].startswith("belum dijawab: coba lagi dong")
    assert hanging["[web] Tanya resep"].startswith("menunggu jawabanmu: Mau yang pedas")
    assert hanging["[web] Riset python"].startswith("masih ada lanjutan")
    assert "[web] Landing kopi" not in hanging                      # finished: nothing to chase


def test_a_failed_step_in_the_newest_answer_is_hanging() -> None:
    chat = FakeStore({"x": ("[web] Uji", [user("jalankan", at(0, 7)), agent("Selesai.", at(0, 7), "run_checks; file_patch app.py (failed)")])})
    texts = [h["text"] for h in daily_brief.build(chat, NOW)["hanging"]]
    assert texts == ["langkah gagal: file_patch app.py"]


def test_a_failure_that_a_later_answer_fixed_is_not_hanging() -> None:
    chat = FakeStore({"x": ("[web] Uji", [
        user("ubah", at(0, 6)), agent("Gagal.", at(0, 6), "file_patch app.py (failed)"),
        user("lagi", at(0, 7)), agent("Beres.", at(0, 7), "file_patch app.py"),
    ])})
    assert daily_brief.build(chat, NOW)["hanging"] == []


def test_an_empty_window_says_so() -> None:
    brief = daily_brief.build(FakeStore({"o": ("[web] Lama", [user("x", at(9)), agent("y", at(9))])}), NOW)
    assert brief["empty"] and brief["chats"] == []
    assert "belum ada obrolan" in daily_brief.render(brief)


def test_render_lists_each_section(store: FakeStore) -> None:
    text = daily_brief.render(daily_brief.build(store, NOW))
    for needle in ("# Brief Kamis, 2026-11-12", "## Masih menggantung", "## Hasil kerja", "`index.html`", "## Chat", "[web] Landing kopi"):
        assert needle in text


def test_long_lists_are_cut_and_counted() -> None:
    many = {f"s{i}": (f"[web] Chat {i}", [user(f"tugas {i}", at(0, 6)), agent("Mau lanjut?", at(0, 6))]) for i in range(12)}
    brief = daily_brief.build(FakeStore(many), NOW)
    assert brief["chat_count"] == 12 and len(brief["chats"]) == daily_brief.MAX_CHATS
    assert len(brief["hanging"]) == daily_brief.MAX_HANGING and brief["hanging_count"] == 12
    assert "dan 6 lagi" in daily_brief.render(brief)


def test_one_file_per_day_and_force_rebuilds(store: FakeStore, tmp_path: Path) -> None:
    first = daily_brief.ensure_today(store, tmp_path, NOW)
    assert (tmp_path / "2026-11-12.md").read_text(encoding="utf-8").startswith("# Brief Kamis")
    assert json.loads((tmp_path / "2026-11-12.json").read_text(encoding="utf-8"))["chat_count"] == first["chat_count"]
    store.chats["e"] = ("[web] Baru", [user("baru", at(0, 7)), agent("ok", at(0, 7))])
    assert daily_brief.ensure_today(store, tmp_path, NOW)["chat_count"] == first["chat_count"]            # saved one is reused
    assert daily_brief.ensure_today(store, tmp_path, NOW, force=True)["chat_count"] == first["chat_count"] + 1


def test_a_damaged_saved_brief_is_rebuilt(store: FakeStore, tmp_path: Path) -> None:
    (tmp_path / "2026-11-12.json").write_text("{not json", encoding="utf-8")
    assert daily_brief.ensure_today(store, tmp_path, NOW)["chat_count"] == 4


def test_each_place_shows_it_once_a_day(tmp_path: Path) -> None:
    assert daily_brief.due(tmp_path, NOW, "terminal") and daily_brief.due(tmp_path, NOW, "web")
    daily_brief.mark_seen("2026-11-12", tmp_path, "terminal")
    assert not daily_brief.due(tmp_path, NOW, "terminal")
    assert daily_brief.due(tmp_path, NOW, "web")                     # reading it in the terminal does not hide the web card
    assert daily_brief.due(tmp_path, NOW + dt.timedelta(days=1), "terminal")


def test_old_days_are_pruned(tmp_path: Path) -> None:
    for day in range(1, 41):
        stamp = f"2026-10-{day:02d}" if day <= 31 else f"2026-11-{day - 31:02d}"
        (tmp_path / f"{stamp}.json").write_text("{}", encoding="utf-8")
        (tmp_path / f"{stamp}.md").write_text("x", encoding="utf-8")
    daily_brief.prune(tmp_path, keep=30)
    assert len(list(tmp_path.glob("*.json"))) == 30 and len(list(tmp_path.glob("*.md"))) == 30
    assert not (tmp_path / "2026-10-01.json").exists() and (tmp_path / "2026-11-09.json").exists()


def test_schedule_waits_for_the_next_occurrence() -> None:
    assert daily_brief.seconds_until(dt.time(9, 0), NOW) == 3600
    assert daily_brief.seconds_until(dt.time(7, 0), NOW) == 23 * 3600       # already past today: tomorrow


@pytest.mark.parametrize(("raw", "expected"), [("", dt.time(8, 0)), ("06:30", dt.time(6, 30)), ("pagi", dt.time(8, 0)), ("25:00", dt.time(8, 0))])
def test_brief_time_setting(monkeypatch: pytest.MonkeyPatch, raw: str, expected: dt.time) -> None:
    monkeypatch.setenv("BRIEF_TIME", raw)
    assert daily_brief.brief_time() == expected


@pytest.mark.parametrize(("raw", "expected"), [("", True), ("true", True), ("false", False), ("0", False), ("off", False)])
def test_automatic_brief_can_be_turned_off(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv("DAILY_BRIEF", raw)
    assert daily_brief.enabled() is expected


def test_works_on_the_real_conversation_store(tmp_path: Path) -> None:
    real = ConversationStore(storage_dir=tmp_path / "chats")
    real.add_message("s1", "user", "buat file hello.py")
    real.add_message("s1", "assistant", "Sudah.", tool_trace="file_write hello.py")
    brief = daily_brief.build(real, dt.datetime.now().astimezone())
    assert brief["chat_count"] == 1 and brief["files"] == ["hello.py"]


# ── the web endpoints ───────────────────────────────────────────────────────

@pytest.fixture()
def web(store: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from web import server

    monkeypatch.setattr(server, "get_orchestrator", lambda: SimpleNamespace(conversation_store=store))
    monkeypatch.setattr(daily_brief, "default_dir", lambda: tmp_path)
    monkeypatch.setenv("DAILY_BRIEF", "true")
    return TestClient(server.app)


def test_api_returns_the_brief_and_remembers_it_was_shown(web: TestClient) -> None:
    today = dt.datetime.now().astimezone()
    body = web.get("/api/brief").json()
    assert body["enabled"] is True and body["shown"] is False
    assert body["markdown"].startswith("# Brief")
    assert web.post("/api/brief/seen").json()["day"] == today.strftime("%Y-%m-%d")
    assert web.get("/api/brief").json()["shown"] is True


def test_api_refresh_rebuilds(web: TestClient, store: FakeStore) -> None:
    web.get("/api/brief")
    store.chats["z"] = ("[web] Segar", [user("baru", dt.datetime.now().timestamp() - 5), agent("ok", dt.datetime.now().timestamp() - 4)])
    assert web.get("/api/brief?refresh=true").json()["brief"]["chat_count"] >= 1
