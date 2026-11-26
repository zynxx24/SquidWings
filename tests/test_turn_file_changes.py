"""A web chat turn writes down the files it changed, tells the page, and the page can undo them."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from web import live_turns


class Orch:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    async def process(self, **kwargs: Any) -> Any:
        (self.folder / "app.py").write_text("print('halo')\n", encoding="utf-8")
        return SimpleNamespace(
            content="Sudah.", thinking="", model_used="m", task_type="code", difficulty="easy", confidence=1.0,
            prompt_tokens=1, completion_tokens=1, total_tokens=2, iterations=1, status="success",
            fallback_used=False, harness={},
        )


async def test_a_turn_reports_its_files_before_it_is_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from web import server

    monkeypatch.setenv("SQUIDWINGS_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(server, "_start_naming", lambda *a, **k: None)

    async def no_title(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(server, "_finish_naming", no_title)
    live = live_turns.start("sess-filetest01", "buat app")
    await server._run_live_turn(Orch(tmp_path), live, "sess-filetest01", "buat app", None,
                                locked=False, images=[], classification=None, difficulty="easy")
    kinds = [item["type"] for item in live.log]
    assert "files" in kinds and kinds.index("files") < kinds.index("done")
    turn = next(item["turn"] for item in live.log if item["type"] == "files")
    assert turn["prompt"] == "buat app" and [c["path"] for c in turn["changes"]] == ["app.py"]

    client = TestClient(server.app, client=("127.0.0.1", 50000))
    listed = client.get("/api/session/sess-filetest01/changes").json()
    assert [t["id"] for t in listed["turns"]] == [turn["id"]]
    undone = client.post("/api/session/sess-filetest01/changes/restore", json={"turn": turn["id"]})
    assert undone.status_code == 200 and not (tmp_path / "app.py").exists()
    assert client.post("/api/session/sess-filetest01/changes/restore", json={"turn": "nope"}).status_code == 404
