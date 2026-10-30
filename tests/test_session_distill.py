"""Tests for session distillation into vault notes."""

from __future__ import annotations

import asyncio
from pathlib import Path

from brain.memory.conversation_store import ConversationStore
from tool.execute.execute_local_memory import session_distill


def _store(tmp_path: Path) -> ConversationStore:
    store = ConversationStore(storage_dir=tmp_path / "sessions")
    return store


def _seed(store: ConversationStore, session_id: str, turns: int = 3) -> None:
    for i in range(turns):
        store.add_message(session_id, "user", f"tolong perbaiki file agent/orchestrator.py nomor {i}")
        store.add_message(
            session_id,
            "assistant",
            "Decision: pakai patch kecil.\nNext step: jalankan pytest.",
            model_used="vendor/model-1",
            token_count=10,
        )


def test_distill_writes_session_note(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    session_distill.clear_cache()
    store = _store(tmp_path)
    _seed(store, "sess-1234")

    result = asyncio.run(
        session_distill.distill_session(store, "sess-1234", workspace_path=str(tmp_path))
    )

    assert result["ok"] is True
    assert result["written"] is True
    assert result["path"].startswith("10-sessions/")
    note = tmp_path / "vault" / result["path"]
    text = note.read_text(encoding="utf-8")
    assert "type: session" in text
    assert "agent/orchestrator.py" in text


def test_distill_skips_when_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    session_distill.clear_cache()
    store = _store(tmp_path)
    _seed(store, "sess-5678")

    first = asyncio.run(
        session_distill.distill_session(store, "sess-5678", workspace_path=str(tmp_path))
    )
    second = asyncio.run(
        session_distill.distill_session(store, "sess-5678", workspace_path=str(tmp_path))
    )

    assert first["written"] is True
    assert second["written"] is False
    assert second["reason"] == "unchanged"


def test_distill_ignores_short_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    session_distill.clear_cache()
    store = _store(tmp_path)
    store.add_message("tiny", "user", "halo")

    result = asyncio.run(
        session_distill.distill_session(store, "tiny", workspace_path=str(tmp_path))
    )

    assert result["ok"] is True
    assert result["written"] is False
    assert result["reason"] == "too-short"


def test_distill_path_is_stable_across_turns(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    session_distill.clear_cache()
    store = _store(tmp_path)
    _seed(store, "sess-9999")

    first = asyncio.run(
        session_distill.distill_session(store, "sess-9999", workspace_path=str(tmp_path))
    )
    store.add_message("sess-9999", "user", "lanjut")
    store.add_message("sess-9999", "assistant", "ok", model_used="vendor/model-2")
    second = asyncio.run(
        session_distill.distill_session(store, "sess-9999", workspace_path=str(tmp_path))
    )

    assert first["path"] == second["path"]
    assert second["written"] is True


def test_distill_never_raises_on_broken_store(tmp_path):
    class Broken:
        def get_history(self, session_id, limit=None):
            raise RuntimeError("boom")

    result = asyncio.run(
        session_distill.distill_session(Broken(), "x", workspace_path=str(tmp_path))
    )

    assert result["ok"] is False
    assert result["written"] is False
