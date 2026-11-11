"""Chats and their folders are named "[terminal] / [web] / [team] <title of at most 28 characters>"."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from brain import skills
from brain.memory.conversation_store import ConversationStore
from utils import session_names, workspace_context
from utils.workspace_context import (
    TITLE_MAX,
    folder_label,
    name_session_folder,
    rebuild_session_names,
    reserve_session_name,
    session_workspace,
)


def test_titles_are_cut_at_a_word_and_never_longer_than_28():
    title = session_names.quick_title("tolong fix bug login di halaman admin yang error 500 terus")
    assert title == "Fix bug login di halaman"
    assert len(title) <= TITLE_MAX
    assert session_names.label("terminal", title) == "[terminal] Fix bug login di halaman"
    assert session_names.quick_title("") == "Chat baru"


def test_the_models_answer_is_cleaned_into_a_title():
    raw = 'Judul: "[web] Perbaikan Login Admin Error 500 Halaman."'
    assert session_names.clean_ai_title(raw) == "Perbaikan Login Admin Error"
    assert session_names.split_label("[Team] Video kopi") == ("team", "Video kopi")


def test_folder_names_are_safe_on_any_file_system():
    assert folder_label("web", "a/b: c*d?") == "[web] a b c d"
    assert folder_label("nope", "x") == "[web] x"
    assert folder_label("team", "   ") == "[team]"


def test_ai_title_without_a_model_is_none():
    assert asyncio.run(session_names.ai_title(object(), None, "halo")) is None


def test_ai_title_reads_the_cheap_models_reply():
    class Client:
        async def chat_completion(self, **kwargs):
            assert kwargs["model"] == "cheap"
            return {"choices": [{"message": {"content": "Landing Page Warung Kopi Susu Gula Aren"}}]}

    title = asyncio.run(session_names.ai_title(Client(), lambda: "cheap", "buatkan landing page"))
    assert title == "Landing Page Warung Kopi"


def test_a_new_chat_folder_takes_the_reserved_name():
    reserve_session_name("sess-aaa111", "web", "Fix bug login")
    folder = session_workspace("sess-aaa111")
    assert folder.name == "[web] Fix bug login"
    assert (folder / ".session-id").read_text() == "sess-aaa111"
    assert session_workspace("sess-aaa111", create=False) == folder


def test_an_old_folder_is_renamed_and_still_found_by_id():
    old = session_workspace("sess-bbb222")
    (old / "index.html").write_text("<h1>hi</h1>")
    renamed = name_session_folder("sess-bbb222", "web", "Landing page kopi")
    assert renamed is not None and renamed.name == "[web] Landing page kopi"
    assert not old.exists()
    assert (session_workspace("sess-bbb222", create=False) / "index.html").read_text() == "<h1>hi</h1>"


def test_two_chats_with_the_same_title_get_two_folders():
    name_session_folder("sess-c1", "web", "Same")
    session_workspace("sess-c1")
    session_workspace("sess-c2")
    second = name_session_folder("sess-c2", "web", "Same")
    assert second is not None and second.name == "[web] Same 2"


def test_a_folder_renamed_by_hand_is_found_again():
    reserve_session_name("sess-ddd444", "team", "Video promosi")
    folder = session_workspace("sess-ddd444")
    moved = folder.with_name("[team] Video promosi final")
    folder.rename(moved)
    assert session_workspace("sess-ddd444", create=False) == moved
    assert rebuild_session_names()["sess-ddd444"] == moved.name


def test_the_store_keeps_the_source_and_shows_the_tag(tmp_path):
    store = ConversationStore(storage_dir=tmp_path / "chats")
    store.set_source("sess-eee555", "terminal")
    store.add_message("sess-eee555", "user", "tolong refactor modul pembayaran biar rapi")
    assert store.needs_ai_title("sess-eee555")
    assert store.label("sess-eee555") == "[terminal] Refactor modul pembayaran"
    store.set_title("sess-eee555", "Refactor Modul Pembayaran", ai=True)
    assert not store.needs_ai_title("sess-eee555")
    listed = store.list_sessions()[0]
    assert listed["title"] == "[terminal] Refactor Modul Pembayaran"
    assert listed["source"] == "terminal"
    again = ConversationStore(storage_dir=tmp_path / "chats")
    assert again.label("sess-eee555") == "[terminal] Refactor Modul Pembayaran"


def test_a_typed_title_keeps_its_tag_out_of_the_name(tmp_path):
    store = ConversationStore(storage_dir=tmp_path / "chats")
    store.add_message("sess-fff666", "user", "halo")
    store.set_title("sess-fff666", "[web] Ngobrol santai")
    assert store.get_session_meta("sess-fff666")["title"] == "Ngobrol santai"
    assert store.label("sess-fff666") == "[web] Ngobrol santai"


def test_the_team_run_folder_is_named_before_the_run_works_in_it(monkeypatch):
    from agent.team_orchestrator import TeamOrchestrator, TeamRun

    async def title_for(text):
        return "Video promosi kopi"

    orch = TeamOrchestrator.__new__(TeamOrchestrator)
    orch.orchestrator = SimpleNamespace(title_for=title_for)
    orch.store = SimpleNamespace(put_run=lambda run: None)
    run = TeamRun(id="run-abc123", team_id="team-1", task="buat video promosi kopi 15 detik")
    asyncio.run(orch._name_run(run))
    assert run.title == "Video promosi kopi"
    assert session_workspace(run.id).name == "[team] Video promosi kopi"


@pytest.mark.parametrize("request_text, task", [
    ("buatkan landing page warung kopi", "code"),
    ("buat video animasi promosi 15 detik", "activity"),
    ("buat gambar poster konser", "generate_img"),
])
def test_aislop_comes_with_every_digital_work(request_text, task):
    active = ("animate",) if "video" in request_text else ()
    assert "aislop" in [s.name for s in skills.select(request_text, active=active, task=task)]


def test_aislop_stays_out_of_plain_coding_and_research():
    assert "aislop" not in [s.name for s in skills.select("fix bug di parser csv", task="code")]
    assert "aislop" not in [s.name for s in skills.select("cari berita hari ini", task="reason")]


def test_an_image_request_still_gets_the_aislop_guide():
    from agent.harness.briefing import skill_guidance
    from config.constants import TaskType

    text, chosen = skill_guidance("buat gambar poster konser jazz", TaskType.GENERATE_IMG, 8000)
    assert chosen == ["aislop"]
    assert "AI slop" in text


def test_sessions_root_is_the_patched_one():
    assert "workspace" in str(workspace_context.SESSIONS_ROOT)
