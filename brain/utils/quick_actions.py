"""Menu cepat: ready-made jobs the web chat and Telegram offer by themselves, from what the person sends.

One list of actions (``ACTIONS``) serves both places:

* ``suggest(text, files)`` reads a message and its files and returns the actions that fit (a video link gets
  "Download video", a PDF "Analisa dokumen", a ZIP "Perbaiki ZIP"...): the auto menu.
* A **direct** action runs here without a model, so it is quick and free: ``download`` fetches a video, its
  audio or its subtitles with ``media_download`` (H.264 MP4 that every phone plays).
* An **agent** action becomes one clear instruction (``prompt``) that goes to the chat like a typed message,
  so the full harness does it with its tools, guards and taint rules unchanged.

Telegram buttons carry only a short token (``remember`` / ``recall``); what it stands for (a link, a file in the
chat's folder) stays here in a bounded cache, so a button can never smuggle a path or a command.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from brain.utils import cache
from brain.utils.logger import get_logger

logger = get_logger(__name__)
URL = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
VIDEO_HOSTS = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "x.com", "twitter.com", "vimeo.com",
               "facebook.com", "fb.watch", "dailymotion.com", "twitch.tv", "soundcloud.com", "bilibili.com", "reddit.com")
DOCUMENTS = frozenset({".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".pptx", ".ppt", ".txt", ".md", ".odt", ".ods",
                       ".odp", ".rtf", ".json", ".html", ".htm"})
OFFICE = frozenset({".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods", ".odp", ".rtf", ".md", ".html",
                    ".htm", ".txt", ".csv"})
MEDIA = frozenset({".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac", ".mp4", ".mov", ".mkv", ".webm", ".m4v"})
ARCHIVES = frozenset({".zip"})
HEIGHTS = (360, 480, 720, 1080)
TOKEN_TTL = 24 * 3600


@dataclass(frozen=True)
class Choice:
    id: str
    label: str


@dataclass(frozen=True)
class Action:
    id: str
    title: str
    icon: str                       # a name in the web page's icon set
    blurb: str
    needs: str                      # "url" or "file"
    mode: str                       # "direct" (no model) or "agent" (a chat turn)
    url_kind: str = ""              # "video" or "web" when needs == "url"
    files: frozenset[str] = frozenset()
    choices: tuple[Choice, ...] = ()
    note: str = ""                  # label of an optional free-text field, "" for none
    also_url: bool = False          # a file action that also takes a video link (transcribe)

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["files"] = sorted(self.files)
        return data


ACTIONS: tuple[Action, ...] = (
    Action("video", "Download video", "download", "Video, MP3, atau subtitle dari YouTube, TikTok, IG, X, dan lainnya",
           "url", "direct", url_kind="video",
           choices=(Choice("360", "360p"), Choice("480", "480p"), Choice("720", "720p"), Choice("1080", "1080p"),
                    Choice("mp3", "MP3 saja"), Choice("srt", "Subtitle"))),
    Action("dokumen", "Analisa dokumen", "doc", "Ringkasan, poin penting, angka, dan risiko dari PDF, Word, Excel, PPT",
           "file", "agent", files=DOCUMENTS,
           choices=(Choice("ringkas", "Ringkas"), Choice("lengkap", "Analisa lengkap"), Choice("risiko", "Cari risiko")),
           note="Fokus tertentu (opsional)"),
    Action("zip", "Perbaiki ZIP", "zip", "Ekstrak, cek, perbaiki, tes, lalu kembalikan sebagai ZIP baru",
           "file", "agent", files=ARCHIVES, note="Apa yang perlu diperbaiki (opsional)"),
    Action("link", "Ringkas halaman", "link", "Baca halaman web lalu ringkas dengan sumbernya",
           "url", "agent", url_kind="web", note="Yang mau dicari di halaman itu (opsional)"),
    Action("transkrip", "Transkrip suara", "wave", "Audio atau video jadi teks, atau subtitle SRT",
           "file", "agent", files=MEDIA, also_url=True,
           choices=(Choice("teks", "Teks"), Choice("srt", "Subtitle SRT"))),
    Action("pdf", "Jadikan PDF", "pdf", "Word, Excel, PowerPoint, atau Markdown jadi PDF",
           "file", "agent", files=OFFICE),
)
BY_ID = {action.id: action for action in ACTIONS}


class QuickActionError(ValueError):
    """A request the menu cannot carry out, in words the person can act on."""


# ── what fits a message ─────────────────────────────────────────────────────────
def urls(text: str) -> list[str]:
    return [u.rstrip(".,;:!?)]}") for u in URL.findall(text or "")]


def is_video_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS)


def only_a_link(text: str) -> str:
    """The link when a message is nothing but one link (what makes Telegram answer with a menu), else ''."""
    found = urls(text)
    if len(found) != 1:
        return ""
    rest = (text or "").replace(found[0], "", 1).strip(" \t\r\n.,;:!?()[]<>/")
    return "" if rest else found[0]


@dataclass
class Suggestion:
    action: str
    title: str
    icon: str
    url: str = ""
    file: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def suggest(text: str = "", files: list[str] | None = None) -> list[Suggestion]:
    """The actions that fit a message and its attached files, most specific first, each once per input."""
    out: list[Suggestion] = []
    for link in urls(text)[:3]:
        kinds = ("video", "transkrip", "link") if is_video_url(link) else ("link",)
        for action_id in kinds:
            action = BY_ID[action_id]
            out.append(Suggestion(action.id, action.title, action.icon, url=link))
    for name in (files or [])[:5]:
        suffix = Path(name).suffix.lower()
        for action in ACTIONS:
            if action.needs == "file" and suffix in action.files:
                out.append(Suggestion(action.id, action.title, action.icon, file=name))
    return out


# ── agent actions: one clear instruction for the chat ──────────────────────────
def _choice(action: Action, choice: str) -> Choice:
    if not action.choices:
        return Choice("", "")
    for item in action.choices:
        if item.id == choice:
            return item
    return action.choices[0]


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def label(action_id: str, inputs: dict[str, Any]) -> str:
    """What the chat shows for the request: 'Menu cepat · Analisa dokumen · laporan.pdf (Ringkas)'."""
    action = BY_ID[action_id]
    target = Path(str(inputs.get("file") or "")).name or _clean(inputs.get("url"), 80)
    picked = _choice(action, str(inputs.get("choice") or "")).label
    return f"Menu cepat · {action.title}" + (f" · {target}" if target else "") + (f" ({picked})" if picked else "")


def prompt(action_id: str, inputs: dict[str, Any]) -> str:
    """The message an agent action sends to the chat."""
    action = BY_ID.get(action_id)
    if action is None or action.mode != "agent":
        raise QuickActionError("Aksi ini tidak dikerjakan lewat chat.")
    file = _clean(inputs.get("file"), 300)
    url = _clean(inputs.get("url"), 2000)
    note = _clean(inputs.get("note"), 600)
    folder = _clean(inputs.get("folder"), 300)
    choice = _choice(action, str(inputs.get("choice") or "")).id
    if action.needs == "file" and not file and not (action.also_url and url):
        raise QuickActionError(f"{action.title}: lampirkan filenya dulu.")
    if action.needs == "url" and not url:
        raise QuickActionError(f"{action.title}: isi link-nya dulu.")
    focus = f" Fokus dari pengguna: {note}" if note else ""
    head = label(action_id, inputs)
    if action.id == "dokumen":
        depth = {"ringkas": "Ringkasan singkat: inti dalam 3 kalimat dan paling banyak 7 poin penting.",
                 "risiko": "Cari risiko, kejanggalan, angka yang tidak cocok, tenggat, dan kewajiban yang mudah terlewat.",
                 }.get(choice, "Analisa lengkap: inti dalam 3 kalimat, poin penting per bagian, angka dan tanggal yang "
                               "disebut, risiko atau hal yang perlu dicek, dan langkah lanjut yang disarankan.")
        body = (f"Analisa dokumen {file} yang ada di folder chat ini. {depth} Sebut halaman atau bagian untuk setiap "
                f"angka. Kutip isinya apa adanya dan jangan menambah yang tidak tertulis.{focus}")
    elif action.id == "zip":
        where = f" Hasil ekstraknya ada di {folder}." if folder else " Lihat catatan unggahan untuk folder hasil ekstraknya."
        stem = Path(file).stem or "proyek"
        body = (f"File ZIP {file} sudah diekstrak dan dipindai keamanannya.{where} Pahami dulu isinya, lalu jalankan tes, "
                "linter, atau build yang ada dan cari error serta bug. Perbaiki akar masalahnya di file, jalankan lagi "
                f"sampai bersih. Kemas folder hasil perbaikan jadi {stem}-diperbaiki.zip (tanpa node_modules, .git, "
                "venv, dan cache) lalu share_file ZIP itu. Jawaban akhir: apa yang rusak, apa yang diubah (file dan "
                f"baris), hasil tes terakhir, dan yang tidak bisa dicek.{focus}")
    elif action.id == "link":
        body = (f"Baca {url} dengan web_read lalu ringkas: inti dalam 3 kalimat, poin penting, angka dan tanggal, dan "
                f"siapa sumbernya. Sebut URL-nya. Jangan menambah yang tidak tertulis di halaman.{focus}")
    elif action.id == "transkrip":
        source = file or url
        fetch = "" if file else " Unduh audionya dulu dengan media_download kind=audio."
        result = ("Buat file subtitle .srt dan share_file file itu." if choice == "srt"
                  else "Tulis transkrip lengkap yang rapi per paragraf, lalu simpan juga sebagai .txt dan share_file.")
        body = (f"Transkripsikan {source} dengan tool transcribe (bahasa dideteksi otomatis).{fetch} {result} "
                "Di atasnya tulis ringkasan 3 kalimat.")
    else:  # pdf
        body = (f"Ubah {file} jadi PDF (office_to_pdf untuk Word, Excel, PowerPoint; format_convert untuk yang lain), "
                "periksa jumlah halamannya, lalu share_file PDF itu.")
    return f"{head}\n\n{body}"


# ── direct action: download ─────────────────────────────────────────────────────
def video_choices(info: dict[str, Any]) -> list[Choice]:
    """The download choices this video really has: the heights on offer (at least 360p), MP3, and subtitles."""
    heights = [int(h) for h in info.get("video_heights") or [] if isinstance(h, int | float)]
    top = max(heights, default=1080)
    out = [Choice(str(h), f"{h}p") for h in HEIGHTS if h <= max(top, HEIGHTS[0])]
    out.append(Choice("mp3", "MP3 saja"))
    if info.get("subtitles") or info.get("auto_subtitles"):
        out.append(Choice("srt", "Subtitle"))
    return out


async def video_info(url: str) -> dict[str, Any]:
    """Title, length and qualities of a video link (yt-dlp, no download)."""
    from tools.toolbox import media_tools

    if not urls(url):
        raise QuickActionError("Itu bukan link http atau https.")
    try:
        info: dict[str, Any] = await media_tools.media_info(url)
        return info
    except (media_tools.MediaError, ValueError) as exc:
        raise QuickActionError(f"Link ini tidak bisa dibaca: {exc}") from exc


async def download(url: str, choice: str, workspace: Path) -> dict[str, Any]:
    """Fetch the video (choice = height), its audio ('mp3') or its subtitles ('srt') into the chat's folder."""
    from tools.toolbox import media_tools

    if not urls(url):
        raise QuickActionError("Itu bukan link http atau https.")
    if choice == "mp3":
        args: dict[str, Any] = {"kind": "audio"}
    elif choice == "srt":
        args = {"kind": "subtitles"}
    elif choice.isdigit() and int(choice) in HEIGHTS:
        args = {"kind": "video", "max_height": int(choice)}
    else:
        raise QuickActionError("Pilih 360, 480, 720, 1080, mp3, atau srt.")
    try:
        result = await media_tools.media_download(url, folder="downloads", share=False, workspace_path=str(workspace), **args)
    except (media_tools.MediaError, ValueError) as exc:
        raise QuickActionError(str(exc)) from exc
    if not result.get("ok"):
        raise QuickActionError(str(result.get("error") or "download gagal"))
    main = str(result.get("main") or (result.get("files") or [{}])[0].get("path") or "")
    if not main:
        raise QuickActionError("Tidak ada file yang terunduh.")
    size = next((f.get("size_mb") for f in result.get("files") or [] if f.get("path") == main), None)
    return {"ok": True, "file": main, "files": [f["path"] for f in result.get("files") or []], "size_mb": size,
            "kind": args["kind"]}


# ── Telegram button tokens ──────────────────────────────────────────────────────
# Kept in memory and in a small file, so a button still works after a restart or when another process (the
# terminal, then the web server) has taken over polling the bot.
MAX_TOKENS = 500
_tokens = cache.cache("quick_menu_tokens", max_entries=MAX_TOKENS, ttl=TOKEN_TTL)
_token_lock = threading.Lock()


def _token_file() -> Path:
    from brain.config.paths import data_dir

    return data_dir() / "quick-menu-tokens.json"


def _load_tokens(now: float) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(_token_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict) and now - float(v.get("at") or 0) < TOKEN_TTL}


def remember(payload: dict[str, Any]) -> str:
    """A short token for a button (Telegram allows 64 bytes of callback data)."""
    token = secrets.token_urlsafe(9)
    _tokens.set(token, dict(payload))
    now = time.time()
    with _token_lock:
        kept = _load_tokens(now)
        kept[token] = {"at": now, "payload": dict(payload)}
        newest = dict(sorted(kept.items(), key=lambda item: item[1]["at"])[-MAX_TOKENS:])
        target = _token_file()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(newest, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, target)
        except OSError as exc:
            logger.warning("[quick] Button tokens not saved to disk, they last until a restart: %s", exc)
    return token


def recall(token: str) -> dict[str, Any] | None:
    found = _tokens.get(token)
    if isinstance(found, dict):
        return dict(found)
    with _token_lock:
        stored = _load_tokens(time.time()).get(token)
    payload = stored.get("payload") if stored else None
    if isinstance(payload, dict):
        _tokens.set(token, payload)
        return dict(payload)
    return None


def catalog() -> list[dict[str, Any]]:
    return [action.public() for action in ACTIONS]
