"""The web edge: origin, host, token, and session-id checks."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from brain.memory.conversation_store import ConversationStore
from security import web_access
from security.web_access import WebSecurityMiddleware
from utils.helpers import is_valid_session_id


def make_client(**options) -> TestClient:
    app = FastAPI()
    app.add_middleware(WebSecurityMiddleware, allowed_hosts=("testserver",), max_users=1000, requests_per_minute=10_000, **options)

    @app.get("/")
    async def index() -> dict[str, str]:
        return {"page": "app"}

    @app.post("/api/thing")
    async def thing() -> dict[str, bool]:
        return {"ok": True}

    @app.websocket("/ws/chat")
    async def chat(ws: WebSocket) -> None:
        await ws.accept()
        await ws.close()

    return TestClient(app, follow_redirects=False)




def test_foreign_origin_websocket_is_refused() -> None:
    client = make_client()
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/chat", headers={"Origin": "https://evil.example"}):
        pass


def test_same_origin_websocket_is_accepted() -> None:
    client = make_client()
    with client.websocket_connect("/ws/chat", headers={"Origin": "http://testserver"}):
        pass


def test_foreign_origin_post_is_refused() -> None:
    response = make_client().post("/api/thing", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_dns_rebinding_host_is_refused() -> None:
    response = make_client().get("/", headers={"Host": "evil.example:8080"})
    assert response.status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:8080", "localhost:8080", "[::1]:8080"])
def test_loopback_hosts_work(host: str) -> None:
    assert make_client().get("/", headers={"Host": host}).status_code == 200
















@pytest.mark.parametrize("bad", ["../../escaped", "a/b", "..", ".hidden", "", "a" * 200, None, 7, "x\ny"])
def test_unsafe_session_ids(bad: object) -> None:
    assert not is_valid_session_id(bad)


@pytest.mark.parametrize("good", ["sess-1b57b9157019", "test_turn", "run-abc-lead.1"])
def test_safe_session_ids(good: str) -> None:
    assert is_valid_session_id(good)


def test_store_never_writes_outside_its_folder(tmp_path) -> None:
    store_dir = tmp_path / "a" / "store"
    store_dir.mkdir(parents=True)
    store = ConversationStore(storage_dir=store_dir)
    store.add_message("../../escaped", "user", "hi")
    assert not (tmp_path / "escaped.json").exists()
    assert store.get_history("../../escaped")  # still usable in memory


def test_chat_stream_carries_progress_and_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: the event's own "type" used to overwrite "progress"."""
    from unittest.mock import MagicMock

    from agent.orchestrator import AgentResponse
    from web import server

    async def process(**kwargs):
        kwargs["on_event"]({"type": "tool", "phase": "start", "name": "file_write"})
        kwargs["on_content_chunk"]("draft")
        kwargs["on_reset"]()
        kwargs["on_content_chunk"]("final")
        return AgentResponse(content="final", harness={"tier": "lite"})

    orchestrator = MagicMock()
    orchestrator.process = process
    orchestrator.classifier.classify_with_difficulty.return_value = (
        MagicMock(task_type=MagicMock(value="code"), confidence=0.9), "easy",
    )
    monkeypatch.setattr(server, "get_orchestrator", lambda: orchestrator)
    monkeypatch.setattr(server, "default_model_id", lambda: "m")

    with TestClient(server.app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "hi", "session_id": "sess-test"})
        seen = []
        while not seen or seen[-1]["type"] != "done":
            seen.append(ws.receive_json())

    progress = next(m for m in seen if m["type"] == "progress")
    assert progress["kind"] == "tool" and progress["name"] == "file_write"
    assert [m["type"] for m in seen].index("reset") < [m.get("content") for m in seen].index("final")
    assert seen[-1]["harness"] == {"tier": "lite"}


@pytest.mark.parametrize("bad", ["not json", "[1]", '{"message": 5}', '{"message": "x", "session_id": "../../x"}'])
def test_chat_rejects_malformed_requests_without_dropping_the_connection(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    from unittest.mock import MagicMock

    from web import server

    monkeypatch.setattr(server, "get_orchestrator", lambda: MagicMock())
    with TestClient(server.app).websocket_connect("/ws/chat") as ws:
        ws.send_text(bad)
        assert ws.receive_json()["type"] == "error"
        ws.send_text("still alive?")
        assert ws.receive_json()["type"] == "error"


def test_there_is_no_login() -> None:
    client = make_client()
    assert client.get("/", headers={"Accept": "text/html"}).status_code == 200
    assert client.post("/api/thing").status_code == 200


def test_public_mode_accepts_any_host_but_still_blocks_other_sites() -> None:
    web_access.set_public_mode(True)
    try:
        client = make_client()
        host = {"Host": "abc.trycloudflare.com"}
        assert client.get("/", headers=host).status_code == 200
        assert client.post("/api/thing", headers={**host, "Origin": "https://abc.trycloudflare.com"}).status_code == 200
        assert client.post("/api/thing", headers={**host, "Origin": "https://evil.example"}).status_code == 403
    finally:
        web_access.set_public_mode(False)
