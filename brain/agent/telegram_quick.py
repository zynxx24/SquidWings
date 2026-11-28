"""Menu cepat in Telegram, for the owner: /menu, and buttons that appear by themselves for a lone link or a file.

* ``/menu`` lists the actions (``brain/utils/quick_actions.py``), also in the bot's own menu button.
* A message that is only a link gets a card: a video link its title, length and the qualities to download
  (360p to 1080p, MP3, subtitles) plus "Transkrip"; any other link "Ringkas halaman".
* A file sent without a caption is saved and scanned as before (``intake``), then gets the actions that fit it
  (a PDF "Ringkas", "Analisa lengkap", "Cari risiko", a ZIP "Perbaiki & kembalikan"...).
* Every card keeps "Tanya AI saja", which hands the link or file to the harness chat as before.

A download runs here without a model and comes back as the file itself (Telegram plays H.264 MP4s inline).
Agent actions become one message to the owner's harness chat, so they run with every tool and guard as usual.
A button carries only ``q:<token>:<action>:<choice>``; the link or file it stands for stays in the server's cache.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING, Any

from brain.agent.team_outputs import (
    TELEGRAM_FILE_LIMIT,
    OutputError,
    answer_callback,
    replace_buttons,
    send_buttons,
    send_chat_action,
    send_file,
    set_bot_commands,
)
from brain.utils import quick_actions as qa
from brain.utils.logger import get_logger

if TYPE_CHECKING:
    from brain.agent.team_orchestrator import TeamOrchestrator

logger = get_logger(__name__)

COMMANDS = [("menu", "Menu cepat: download video, analisa dokumen, perbaiki ZIP"), ("start", "Mulai dan lihat menu cepat")]
ICONS = {"video": "⬇️", "dokumen": "📄", "zip": "🗜", "link": "🔗", "transkrip": "🎙", "pdf": "📑"}
ASK = "ask"
WAITING = {
    "video": "Kirim link videonya (YouTube, TikTok, IG, X, dan lainnya). Pilihan resolusinya muncul setelah itu.",
    "dokumen": "Kirim dokumennya (PDF, Word, Excel, PowerPoint, CSV, atau teks) tanpa keterangan.",
    "zip": "Kirim file ZIP-nya tanpa keterangan. Isinya diperiksa keamanannya dulu sebelum dikerjakan.",
    "link": "Kirim link halaman yang mau diringkas.",
    "transkrip": "Kirim audio, voice note, atau video, atau link video.",
    "pdf": "Kirim file Word, Excel, PowerPoint, atau Markdown tanpa keterangan.",
}


def is_menu_command(text: str) -> bool:
    word = (text or "").strip().split(maxsplit=1)[0].lower() if (text or "").strip() else ""
    return word.split("@", 1)[0] in {"/menu", "/start"}


def _minutes(seconds: Any) -> str:
    if not isinstance(seconds, int | float) or seconds <= 0:
        return ""
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _rows(buttons: list[tuple[str, str]], per_row: int = 3) -> list[list[tuple[str, str]]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


async def register_commands() -> None:
    await set_bot_commands(COMMANDS)


async def show_menu(chat_id: str) -> None:
    buttons = [(f"{ICONS[a.id]} {a.title}", f"m:{a.id}") for a in qa.ACTIONS]
    await send_buttons(chat_id, "<b>Menu cepat</b>\nPilih yang mau dikerjakan. Bisa juga langsung kirim link atau file: "
                                "tombol yang cocok muncul sendiri.", _rows(buttons, 2))


# ── the auto menu ────────────────────────────────────────────────────────────────
async def offer_link(chat_id: str, team_id: str, url: str) -> None:
    """The card for a message that is only a link."""
    token = qa.remember({"chat": chat_id, "team": team_id, "url": url})
    if not qa.is_video_url(url):
        await send_buttons(chat_id, f"🔗 <b>Link</b>\n{html.escape(url[:300])}\nMau diapakan?",
                           [[("🔗 Ringkas halaman", f"q:{token}:link:"), ("💬 Tanya AI saja", f"q:{token}:{ASK}:")]])
        return
    await send_chat_action(chat_id)
    try:
        info = await qa.video_info(url)
    except qa.QuickActionError as exc:
        await send_buttons(chat_id, f"⚠️ Info video tidak terbaca: {html.escape(str(exc)[:300])}",
                           [[("💬 Tanya AI saja", f"q:{token}:{ASK}:")]])
        return
    details = " · ".join(x for x in (html.escape(str(info.get("channel") or "")), _minutes(info.get("duration_s"))) if x)
    choices = [(c.label, f"q:{token}:video:{c.id}") for c in qa.video_choices(info)]
    await send_buttons(chat_id, f"🎬 <b>{html.escape(str(info.get('title') or 'Video'))}</b>\n{details}\n\nDownload sebagai:",
                       [*_rows(choices), [("🎙 Transkrip", f"q:{token}:transkrip:teks"), ("💬 Tanya AI saja", f"q:{token}:{ASK}:")]])


def file_buttons(token: str, name: str) -> list[tuple[str, str]]:
    """Every action button that fits a file, by its kind."""
    suffix = Path(name).suffix.lower()
    out: list[tuple[str, str]] = []
    for action in qa.ACTIONS:
        if action.needs != "file" or suffix not in action.files:
            continue
        if action.id == "zip":
            out.append(("🗜 Perbaiki & kembalikan", f"q:{token}:zip:"))
        elif action.choices:
            out.extend((f"{ICONS[action.id]} {c.label}" if i == 0 else c.label, f"q:{token}:{action.id}:{c.id}")
                       for i, c in enumerate(action.choices))
        else:
            out.append((f"{ICONS[action.id]} {action.title}", f"q:{token}:{action.id}:"))
    return out


async def offer_file(chat_id: str, team_id: str, saved: str, extracted: str, note: str) -> bool:
    """Buttons for a file sent without a caption. False when no action fits it (the AI asks, as before)."""
    token = qa.remember({"chat": chat_id, "team": team_id, "file": saved, "folder": extracted, "note": note})
    buttons = file_buttons(token, saved)
    if not buttons:
        return False
    await send_buttons(chat_id, f"📎 <b>{html.escape(Path(saved).name)}</b> diterima.\nMau diapakan?",
                       [*_rows(buttons), [("💬 Tanya AI saja", f"q:{token}:{ASK}:")]])
    return True


# ── a pressed button ────────────────────────────────────────────────────────────
async def pressed(orch: TeamOrchestrator, callback: dict[str, Any], chat_id: str) -> None:
    """Carry out the owner's press. The message the buttons sat on becomes a note of what was picked."""
    data = str(callback.get("data") or "")
    callback_id = str(callback.get("id") or "")
    message = callback.get("message") or {}
    message_id = int(message.get("message_id") or 0)
    if data.startswith("m:"):
        action_id = data[2:]
        await answer_callback(callback_id)
        await orch._say(chat_id, WAITING.get(action_id, "Kirim link atau filenya."))
        return
    parts = data.split(":")
    payload = qa.recall(parts[1]) if len(parts) == 4 and parts[0] == "q" else None
    if payload is None or payload.get("chat") != chat_id:
        await answer_callback(callback_id, "Tombol ini sudah kedaluwarsa. Kirim lagi link atau filenya.")
        return
    action_id, choice = parts[2], parts[3]
    team = orch.store.get_team(str(payload.get("team") or ""))
    if team is None:
        await answer_callback(callback_id, "Chat harness-nya sudah tidak ada.")
        return
    target = Path(str(payload.get("file") or "")).name or str(payload.get("url") or "")
    picked = "Tanya AI" if action_id == ASK else qa.label(action_id, {**payload, "choice": choice}).removeprefix("Menu cepat · ")
    await answer_callback(callback_id, "Dikerjakan")
    if message_id:
        await replace_buttons(chat_id, message_id, f"✅ <b>{html.escape(picked)}</b>" if action_id != ASK
                              else f"💬 Diteruskan ke AI: {html.escape(target[:200])}")
    sender = {"from": callback.get("from") or {}}
    if action_id == ASK:
        ask = str(payload.get("note") or payload.get("url") or "")
        text = (f"Lihat file yang baru saya kirim, lalu tanya apa yang mau saya lakukan dengannya.\n\n[Unggahan dari Telegram] {ask}"
                if payload.get("file") else ask)
        orch._take_chat(team, chat_id, sender, text, owner=True)
        return
    if action_id == "video":
        await _download(orch, team, chat_id, str(payload.get("url") or ""), choice, payload)
        return
    try:
        text = qa.prompt(action_id, {**payload, "choice": choice})
    except qa.QuickActionError as exc:
        await orch._say(chat_id, str(exc))
        return
    orch._take_chat(team, chat_id, sender, text, owner=True)


async def _download(orch: TeamOrchestrator, team: Any, chat_id: str, url: str, choice: str, payload: dict[str, Any]) -> None:
    folder = orch._flow_folder(team)
    action = {"mp3": "upload_voice", "srt": "upload_document"}.get(choice, "upload_video")
    await send_chat_action(chat_id, action)
    try:
        result = await qa.download(url, choice, folder)
    except qa.QuickActionError as exc:
        await orch._say(chat_id, f"Download tidak jadi: {exc}")
        return
    sent = 0
    for relative in result["files"]:
        path = folder / relative
        if not path.is_file():
            continue
        if path.stat().st_size > TELEGRAM_FILE_LIMIT:
            smaller = [c for c in ("720", "480", "360") if choice.isdigit() and int(c) < int(choice)]
            token = qa.remember({**payload, "chat": chat_id})
            await send_buttons(chat_id, f"📦 <b>{html.escape(path.name)}</b> {path.stat().st_size // 2**20} MB, lebih dari batas "
                                        "kirim bot Telegram (50 MB). File tersimpan di folder chat. Coba yang lebih kecil:",
                               [[(f"{c}p", f"q:{token}:video:{c}") for c in smaller] + [("MP3 saja", f"q:{token}:video:mp3")]])
            continue
        await send_chat_action(chat_id, action)
        try:
            await send_file(path, chat_id, caption=path.name)
            sent += 1
        except OutputError as exc:
            await orch._say(chat_id, f"File tersimpan tapi tidak terkirim: {exc}")
    if sent:
        logger.info("[teams] Menu cepat: sent %s file(s) of %s to the owner", sent, url)
