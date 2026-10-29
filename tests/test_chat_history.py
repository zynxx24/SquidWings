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
    store.add_message(session_id, "assistant", "print('hello')", model_used="ag/gemini-3.8-flash-high")

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
        orch.conversation_store.add_message(sid, "assistant", "Halo juga!", model_used="ag/gemini-3.8-flash-high")

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
