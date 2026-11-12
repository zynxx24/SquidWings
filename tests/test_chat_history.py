"""
Tests for Chat History persistence and web endpoints.
"""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from brain.memory.conversation_store import ConversationStore
from web.server import app, get_orchestrator


def test_conversation_store_persistence(tmp_path: Path):
    store = ConversationStore(storage_dir=tmp_path)
    session_id = "test-session-123"

    # Add messages
    store.add_message(session_id, "user", "Tuliskan kode Python sederhana")
    store.add_message(session_id, "assistant", "print('hello')", model_used="prov/gemini-3.8-flash-high")

    # Verify session listed
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == session_id
    assert "Tuliskan kode Python" in sessions[0]["title"]
    assert sessions[0]["message_count"] == 2

    # Verify disk persistence
    file_path = tmp_path / f"{session_id}.json"
    assert file_path.exists()

    # Create new store from same dir to verify disk reloading
    store2 = ConversationStore(storage_dir=tmp_path)
    history = store2.get_history(session_id)
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[1].role == "assistant"

    # Test title update
    store.set_title(session_id, "Judul Kustom")
    assert store.get_session_meta(session_id)["title"] == "Judul Kustom"

    # Test delete session
    assert store.delete_session(session_id) is True
    assert not file_path.exists()
    assert len(store.list_sessions()) == 0


@pytest.mark.asyncio
async def test_web_chat_history_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create session
        res = await client.get("/api/session/new")
        assert res.status_code == 200
        sid = res.json()["session_id"]

        orch = get_orchestrator()
        orch.conversation_store.add_message(sid, "user", "Halo dari unit test")
        orch.conversation_store.add_message(sid, "assistant", "Halo juga!", model_used="prov/gemini-3.8-flash-high")

        # List sessions
        res = await client.get("/api/sessions")
        assert res.status_code == 200
        found = [s for s in res.json()["sessions"] if s["session_id"] == sid]
        assert len(found) == 1
        assert found[0]["message_count"] == 2

        # Get messages
        res = await client.get(f"/api/session/{sid}/messages")
        assert res.status_code == 200
        body = res.json()
        assert len(body["messages"]) == 2
        assert body["session_id"] == sid

        # Rename title
        res = await client.post(f"/api/session/{sid}/title", json={"title": "Judul Baru"})
        assert res.status_code == 200
        assert res.json()["title"] == "Judul Baru"

        # Delete session
        res = await client.delete(f"/api/session/{sid}")
        assert res.status_code == 200
        assert res.json()["deleted"] is True

        # Verify not in sessions list
        res = await client.get("/api/sessions")
        assert res.status_code == 200
        assert not any(s["session_id"] == sid for s in res.json()["sessions"])


def test_web_and_cli_stores_see_each_others_chats(tmp_path: Path):
    """The web server and the CLI keep one store each over the same folder."""
    web = ConversationStore(storage_dir=tmp_path)
    cli = ConversationStore(storage_dir=tmp_path)

    web.add_message("sess-web", "user", "halo dari web")
    web.set_source("sess-web", "web")
    web.set_title("sess-web", "Chat web", ai=True)
    # Made after the CLI started: it still shows up there, named and tagged.
    listed = {item["session_id"]: item for item in cli.list_sessions()}
    assert listed["sess-web"]["source"] == "web"
    assert cli.needs_ai_title("sess-web") is False

    # The CLI picks the chat up while the web adds a turn: nothing is lost.
    cli.get_history("sess-web")
    web.add_message("sess-web", "assistant", "jawaban web")
    cli.add_message("sess-web", "user", "lanjut dari terminal")
    contents = [m.content for m in ConversationStore(storage_dir=tmp_path).get_history("sess-web")]
    assert contents == ["halo dari web", "jawaban web", "lanjut dari terminal"]


def test_a_failed_turn_stays_in_the_chat_but_never_reaches_the_model(tmp_path: Path):
    from types import SimpleNamespace

    from agent.orchestrator import AgentResponse, Orchestrator
    from config.constants import ResponseStatus

    store = ConversationStore(storage_dir=tmp_path)
    store.add_message("sess-x", "user", "halo")
    store.add_message("sess-x", "assistant", "hai")
    owner = SimpleNamespace(_conversation_store=store)
    owner._keep_failed_turn = lambda *a: Orchestrator._keep_failed_turn(owner, *a)
    failed = AgentResponse(content="Error: model down", status=ResponseStatus.ERROR)
    Orchestrator._failed_turn(owner, failed, "sess-x", "buat laporan", True, None)
    Orchestrator._failed_turn(owner, failed, "sess-x", "agent tim", True, "tugas tim")   # team agents: never kept

    reopened = ConversationStore(storage_dir=tmp_path)
    assert [m.content for m in reopened.get_history("sess-x")] == ["halo", "hai", "buat laporan", "⚠️ Error: model down"]
    assert [m["content"] for m in reopened.get_history_as_api_messages("sess-x")] == ["halo", "hai"]
