"""Menu cepat: which actions fit a message, the instruction an agent action sends, the direct download (H.264),
the web API and chat turn, and the Telegram auto menu and buttons (owner only). Offline."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brain.agent import team_orchestrator, telegram_quick
from brain.utils import quick_actions as qa
from tests.test_harness_template import setup  # noqa: F401 - the owner's Telegram harness flow
from tests.test_live_turns import orch  # noqa: F401 - a chat orchestrator that needs no model

YT = "https://youtu.be/aqz-KE-bpKQ"
INFO = {"title": "Big Buck Bunny", "channel": "Blender", "duration_s": 635, "video_heights": [144, 360, 720],
        "subtitles": [], "auto_subtitles": ["en"]}


def test_the_auto_menu_offers_what_fits() -> None:
    assert [s.action for s in qa.suggest(f"tolong {YT}")] == ["video", "transkrip", "link"]
    assert [s.action for s in qa.suggest("baca https://example.com/berita")] == ["link"]
    assert [s.action for s in qa.suggest("", ["laporan.pdf"])] == ["dokumen"]
    assert [s.action for s in qa.suggest("", ["memo.docx"])] == ["dokumen", "pdf"]
    assert [s.action for s in qa.suggest("", ["app.zip", "rekaman.mp3"])] == ["zip", "transkrip"]
    assert qa.suggest("halo apa kabar", ["foto.jpg"]) == []
    assert qa.only_a_link(f"  {YT}  ") == YT and qa.only_a_link(f"{YT}.") == YT
    assert qa.only_a_link(f"unduh {YT}") == "" and qa.only_a_link(f"{YT} https://x.test") == ""


def test_agent_actions_become_one_clear_instruction() -> None:
    zip_text = qa.prompt("zip", {"file": "uploads/app.zip", "folder": "uploads/app", "note": "login error"})
    assert zip_text.startswith("Menu cepat · Perbaiki ZIP · app.zip")
    assert "uploads/app" in zip_text and "app-diperbaiki.zip" in zip_text and "share_file" in zip_text and "login error" in zip_text
    assert "Cari risiko" in qa.label("dokumen", {"file": "x.pdf", "choice": "risiko"})
    assert "kejanggalan" in qa.prompt("dokumen", {"file": "x.pdf", "choice": "risiko"})
    assert "media_download kind=audio" in qa.prompt("transkrip", {"url": YT, "choice": "srt"})
    with pytest.raises(qa.QuickActionError, match="lampirkan"):
        qa.prompt("dokumen", {})
    with pytest.raises(qa.QuickActionError):
        qa.prompt("video", {"url": YT})


def test_video_choices_follow_what_the_video_has() -> None:
    assert [c.id for c in qa.video_choices(INFO)] == ["360", "480", "720", "mp3", "srt"]
    assert [c.id for c in qa.video_choices({"video_heights": [240]})] == ["360", "mp3"]


async def test_download_maps_the_choice_and_asks_for_h264(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.toolbox import media_tools

    argv_seen: list[list[str]] = []

    async def fake_run(argv: list[str], cwd: Path, timeout_s: float, env: Any = None, low_priority: bool = False):
        argv_seen.append(argv)
        target = Path(argv[argv.index("-P") + 1])
        target.mkdir(parents=True, exist_ok=True)
        (target / "Big Buck Bunny [aqz].mp4").write_bytes(b"\x00" * 2048)
        return 0, "", False

    monkeypatch.setattr(media_tools, "_run", fake_run)
    monkeypatch.setattr(media_tools, "_public_url", lambda url: url)
    result = await qa.download(YT, "720", tmp_path)
    assert result["file"].endswith(".mp4") and result["kind"] == "video"
    argv = argv_seen[0]
    assert argv[argv.index("-S") + 1] == "res:720,vcodec:h264,acodec:aac", "an MP4 every phone plays"
    with pytest.raises(qa.QuickActionError):
        await qa.download(YT, "999", tmp_path)
    with pytest.raises(qa.QuickActionError):
        await qa.download("file:///etc/passwd", "720", tmp_path)


def test_the_web_api(monkeypatch: pytest.MonkeyPatch) -> None:
    from web.server import app

    async def fake_info(url: str) -> dict[str, Any]:
        return INFO

    monkeypatch.setattr(qa, "video_info", fake_info)
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert {a["id"] for a in client.get("/api/quick/actions").json()["actions"]} == {"video", "dokumen", "zip", "link", "transkrip", "pdf"}
    found = client.post("/api/quick/suggest", json={"text": YT, "files": ["a.zip"]}).json()["suggestions"]
    assert [s["action"] for s in found] == ["video", "transkrip", "link", "zip"]
    made = client.post("/api/quick/prompt", json={"action": "dokumen", "inputs": {"file": "uploads/a.pdf", "choice": "ringkas", "evil": "x"}}).json()
    assert made["label"] == "Menu cepat · Analisa dokumen · a.pdf (Ringkas)" and "evil" not in made["message"]
    assert client.post("/api/quick/prompt", json={"action": "zip", "inputs": {}}).status_code == 422
    info = client.post("/api/quick/video-info", json={"url": YT}).json()
    assert info["title"] == "Big Buck Bunny" and [c["id"] for c in info["choices"]][-1] == "srt"


def test_a_download_is_a_chat_turn_without_a_model(orch: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from web import server

    monkeypatch.setattr(server.session_stack, "enter", lambda *_a, **_k: type("Stack", (), {"workspace": str(tmp_path)})())

    async def fake_info(url: str) -> dict[str, Any]:
        return INFO

    async def fake_download(url: str, choice: str, workspace: Path) -> dict[str, Any]:
        (workspace / "downloads").mkdir()
        (workspace / "downloads" / "bunny.mp4").write_bytes(b"\x00" * 4096)
        return {"ok": True, "file": "downloads/bunny.mp4", "files": ["downloads/bunny.mp4"], "size_mb": 0.0, "kind": "video"}

    monkeypatch.setattr(qa, "video_info", fake_info)
    monkeypatch.setattr(qa, "download", fake_download)
    session = "sess-abcdef123456"
    with TestClient(server.app) as client, client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "quick", "session_id": session, "inputs": {"url": YT, "choice": "360"}})
        seen = []
        while True:
            event = ws.receive_json()
            seen.append(event)
            if event["type"] in {"done", "error"}:
                break
    kinds = [e["type"] for e in seen]
    assert kinds[-1] == "done" and seen[-1]["model_used"] == "menu-cepat"
    answer = next(e["content"] for e in seen if e["type"] == "content")
    assert "**Big Buck Bunny**" in answer and "bunny.mp4" in answer and "10:35" in answer
    saved = [c.args[1:3] for c in orch.conversation_store.add_message.call_args_list]
    assert saved[0] == ("user", "Menu cepat · Download video · https://youtu.be/aqz-KE-bpKQ (360p)") and saved[1][0] == "assistant"


# ── Telegram ────────────────────────────────────────────────────────────────────
def _updates(*items: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"update_id": i, **item} for i, item in enumerate(items, 1)]


def _owner(text: str = "", **extra: Any) -> dict[str, Any]:
    return {"message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42, "first_name": "Zyn"}, "date": 0, "text": text, **extra}}


async def test_telegram_offers_a_menu_for_a_lone_link_and_for_menu(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    manager, team, _sent = setup
    calls: list[tuple[str, ...]] = []

    async def offer_link(chat_id: str, team_id: str, url: str) -> None:
        calls.append(("link", chat_id, team_id, url))

    async def show_menu(chat_id: str) -> None:
        calls.append(("menu", chat_id))

    monkeypatch.setattr(telegram_quick, "offer_link", offer_link)
    monkeypatch.setattr(telegram_quick, "show_menu", show_menu)
    taken = manager._handle_updates(_updates(_owner(YT), _owner("/menu"), _owner("/start@squidbot")))
    await asyncio.wait_for(manager.drain_customers(), timeout=5)
    assert calls == [("link", "42", team.id, YT), ("menu", "42"), ("menu", "42")]
    assert taken == [team.id], "the link went to the menu, not to the AI"


async def test_only_the_owner_may_press_a_button(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    manager, _team, _sent = setup
    answered: list[str] = []
    pressed: list[str] = []

    async def answer(callback_id: str, text: str = "") -> None:
        answered.append(text)

    async def fake_pressed(manager_: Any, callback: dict[str, Any], chat_id: str) -> None:
        pressed.append(chat_id)

    monkeypatch.setattr(team_orchestrator, "answer_callback", answer)
    monkeypatch.setattr(telegram_quick, "pressed", fake_pressed)
    stranger = {"callback_query": {"id": "1", "data": "m:video", "from": {"id": 7}, "message": {"chat": {"id": 42, "type": "private"}}}}
    owner = {"callback_query": {"id": "2", "data": "m:video", "from": {"id": 42}, "message": {"chat": {"id": 42, "type": "private"}}}}
    manager._handle_updates(_updates(stranger, owner))
    await asyncio.wait_for(manager.drain_customers(), timeout=5)
    assert answered == ["Tombol ini hanya untuk pemilik bot."] and pressed == ["42"]


def test_file_buttons_fit_the_file() -> None:
    labels = lambda name: [label for label, _ in telegram_quick.file_buttons("tok", name)]  # noqa: E731
    assert labels("a.pdf") == ["📄 Ringkas", "Analisa lengkap", "Cari risiko"]
    assert labels("a.docx")[-1] == "📑 Jadikan PDF"
    assert labels("a.zip") == ["🗜 Perbaiki & kembalikan"]
    assert labels("a.ogg") == ["🎙 Teks", "Subtitle SRT"]
    assert labels("a.jpg") == []
    assert all(len(data.encode()) <= 64 for _, data in telegram_quick.file_buttons(qa.remember({}), "laporan.xlsx"))


async def test_a_pressed_button_downloads_or_asks_the_harness(setup: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    manager, team, _sent = setup
    notes: list[str] = []
    files: list[str] = []
    taken: list[str] = []

    async def note(*args: Any, **kwargs: Any) -> None:
        notes.append(str(args[-1]) if args else "")

    async def fake_send_file(path: Path, chat_id: str = "", caption: str = "") -> str:
        files.append(path.name)
        return path.name

    async def fake_download(url: str, choice: str, workspace: Path) -> dict[str, Any]:
        (workspace / "downloads").mkdir(parents=True, exist_ok=True)
        (workspace / "downloads" / "bunny.mp4").write_bytes(b"\x00" * 100)
        return {"ok": True, "file": "downloads/bunny.mp4", "files": ["downloads/bunny.mp4"], "size_mb": 0.0, "kind": "video"}

    for name in ("answer_callback", "replace_buttons", "send_chat_action"):
        monkeypatch.setattr(telegram_quick, name, note)
    monkeypatch.setattr(telegram_quick, "send_file", fake_send_file)
    monkeypatch.setattr(qa, "download", fake_download)
    monkeypatch.setattr(manager, "_flow_folder", lambda t: tmp_path)
    monkeypatch.setattr(manager, "_take_chat", lambda t, chat, message, text, owner: taken.append(text) or True)

    def press(token: str, action: str, choice: str) -> dict[str, Any]:
        return {"id": "9", "data": f"q:{token}:{action}:{choice}", "from": {"id": 42}, "message": {"message_id": 5, "chat": {"id": 42}}}

    video = qa.remember({"chat": "42", "team": team.id, "url": YT})
    await telegram_quick.pressed(manager, press(video, "video", "360"), "42")
    assert files == ["bunny.mp4"]
    doc = qa.remember({"chat": "42", "team": team.id, "file": "uploads/laporan.pdf"})
    await telegram_quick.pressed(manager, press(doc, "dokumen", "risiko"), "42")
    assert taken and taken[0].startswith("Menu cepat · Analisa dokumen · laporan.pdf (Cari risiko)")
    await telegram_quick.pressed(manager, press(doc, "dokumen", "risiko"), "99")
    assert len(taken) == 1 and "kedaluwarsa" in notes[-1], "a token is bound to its chat"


def test_a_button_token_survives_a_restart(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qa, "_token_file", lambda: tmp_path / "tokens.json")
    token = qa.remember({"chat": "42", "url": YT})
    qa._tokens.clear()                                  # another process, or after a restart
    assert qa.recall(token) == {"chat": "42", "url": YT}
    assert qa.recall("nope") is None
    monkeypatch.setattr(qa.time, "time", lambda: 10**10)
    qa._tokens.clear()
    assert qa.recall(token) is None, "a day later the button is gone"
