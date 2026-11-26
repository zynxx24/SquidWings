"""An answer keeps being written when the page goes away (a phone screen turning off), and a page
that comes back follows it from the start. Only the stop button cancels. Offline."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from brain.config.constants import ResponseStatus, TaskType
from web import live_turns, server


class _Orch:
    """Writes two chunks, waits until the test lets it finish, then answers."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.cancelled = False
        self.finished = False
        self.classifier = MagicMock()
        self.classifier.classify_with_difficulty.return_value = (
            SimpleNamespace(task_type=TaskType.REASON, confidence=0.9), "easy")
        self.registry = MagicMock()
        self.registry.get.return_value = None
        self.conversation_store = MagicMock()
        self.conversation_store.get_last_used_model.return_value = None

    async def process(self, **kwargs):
        on_content = kwargs["on_content_chunk"]
        on_content("Halo, ")
        try:
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        on_content("ini jawabannya.")
        self.finished = True
        return SimpleNamespace(
            content="Halo, ini jawabannya.", thinking="", model_used="m", task_type="reason", difficulty="easy",
            confidence=0.9, prompt_tokens=1, completion_tokens=1, total_tokens=2, iterations=1,
            status=ResponseStatus.SUCCESS, fallback_used=False, harness={},
        )


@pytest.fixture
def orch(monkeypatch: pytest.MonkeyPatch) -> _Orch:
    fake = _Orch()
    monkeypatch.setattr(server, "get_orchestrator", lambda: fake)
    monkeypatch.setattr(server, "default_model_id", lambda: "auto")
    monkeypatch.setattr(server, "_start_naming", lambda *a, **k: None)

    async def no_title(*_a, **_k):
        return None

    monkeypatch.setattr(server, "_finish_naming", no_title)
    monkeypatch.setattr(server.session_stack, "enter", lambda *_a, **_k: None)
    return fake


def _wait(predicate, seconds: float = 5.0) -> None:
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_a_dropped_page_does_not_stop_the_answer_and_a_new_one_gets_all_of_it(orch: _Orch):
    session = "sess-abcdef123456"
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"message": "halo", "session_id": session})
            seen = []
            while not any(m.get("type") == "content" for m in seen):
                seen.append(ws.receive_json())
        # The phone went to sleep: the socket is gone, the answer is not.
        _wait(lambda: live_turns.running(session))
        orch.release.set()
        _wait(lambda: orch.finished)
        assert not orch.cancelled

        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"type": "attach", "session_id": session, "include_finished": True})
            first = ws.receive_json()
            assert first == {"type": "attach", "running": True, "session_id": session, "message": "halo",
                             "started": first["started"]}
            replay = []
            while True:
                m = ws.receive_json()
                replay.append(m)
                if m["type"] in {"done", "error", "stopped"}:
                    break
    text = "".join(m["content"] for m in replay if m["type"] == "content")
    assert text == "Halo, ini jawabannya." and replay[-1]["type"] == "done"


def test_the_stop_button_still_cancels(orch: _Orch):
    session = "sess-0123456789ab"
    with TestClient(server.app) as client, client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "halo", "session_id": session})
        while ws.receive_json().get("type") != "content":
            pass
        ws.send_json({"type": "stop"})
        while ws.receive_json()["type"] != "stopped":
            pass
    _wait(lambda: orch.cancelled)
    assert not orch.finished


def test_attach_without_a_running_answer_says_so(orch: _Orch):
    with TestClient(server.app) as client, client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "attach", "session_id": "sess-ffffffffffff"})
        assert ws.receive_json() == {"type": "attach", "running": False, "session_id": "sess-ffffffffffff"}
