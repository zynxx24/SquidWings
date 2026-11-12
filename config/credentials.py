"""Keys and IDs for the flow's integrations, set on the web page instead of in ``.env``.

The team page's Kredensial tab writes here: the Telegram bot and chat, web search (Brave and
SearXNG), the ElevenLabs voice key, the Google Workspace service-account key, the database URLs a flow can save to,
and the time, round and token limits of a team run. Every reader asks ``get(name)``: a value
typed on the page wins, else the one from ``.env`` (``Settings``), else empty. Nothing needs a
restart: the Telegram poller, the search tool and the database node read it on every use.

The file holds secrets, so it is written owner-only (0600) in ``web/data/`` (ignored by git), and
the page never gets a secret back, only whether it is set and a masked tail. ``CREDENTIALS_FILE``
moves it.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "web" / "data" / "credentials.json"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    secret: bool
    env: str
    hint: str = ""
    pattern: str = ""          # a value that does not match is refused
    example: str = ""
    multiline: bool = False    # a pasted file, not a one-line value


FIELDS: tuple[Field, ...] = (
    Field("telegram_bot_token", "Bot token", "telegram", True, "TELEGRAM_BOT_TOKEN",
          "Dari @BotFather: /newbot, lalu salin token-nya.", r"\d{5,15}:[A-Za-z0-9_-]{20,}", "123456789:AA..."),
    Field("telegram_chat_id", "Chat ID", "telegram", False, "TELEGRAM_CHAT_ID",
          "Chat yang boleh memicu alur dan menerima pesan. Kirim pesan ke bot, lalu buka "
          "api.telegram.org/bot<token>/getUpdates untuk melihat id-nya.", r"-?\d{1,20}|@\w{1,64}", "123456789 atau @channel"),
    Field("brave_search_api_key", "Brave Search API key", "search", True, "BRAVE_SEARCH_API_KEY",
          "Dari api-dashboard.search.brave.com. Ada paket gratis.", r"[A-Za-z0-9_-]{10,200}", "BSA..."),
    Field("searxng_url", "URL SearXNG", "search", False, "SEARXNG_URL",
          "Instance SearXNG milikmu, dengan format JSON aktif.", r"https?://\S{3,300}", "http://127.0.0.1:8888"),
    Field("elevenlabs_api_key", "ElevenLabs API key", "voice", True, "ELEVENLABS_API_KEY",
          "Dari elevenlabs.io → Developers → API Keys. Ada paket gratis. Dipakai tool voice_speak untuk voice-over.",
          r"[A-Za-z0-9_-]{20,200}", "sk_..."),
    Field("elevenlabs_voice_id", "Voice ID bawaan", "voice", False, "ELEVENLABS_VOICE_ID",
          "Opsional. Suara yang dipakai bila agent tidak memilih. Lihat daftarnya dengan tool voice_list.",
          r"[A-Za-z0-9]{10,40}", "21m00Tcm4TlvDq8ikWAM"),
    Field("google_service_account", "Kunci akun layanan (JSON)", "google", True, "",
          "Tempel seluruh isi file .json dari Google Cloud > IAM > Akun layanan > Kunci. Lalu bagikan tiap spreadsheet "
          "ke email akun layanan itu sebagai Editor.", "", '{"type": "service_account", "client_email": "...", ...}', True),
    Field("team_db_sqlite_path", "Lokasi file SQLite", "database", False, "TEAM_DB_SQLITE_PATH",
          "Tempat node Database menyimpan hasil ke SQLite. Folder dibuat otomatis. Tanpa diisi dipakai lokasi bawaan.",
          r"[^\r\n\x00]{1,300}", "web/data/teams/flow.sqlite3"),
    Field("team_db_postgres_url", "PostgreSQL URL", "database", True, "TEAM_DB_POSTGRES_URL",
          "", r"postgres(ql)?://\S{3,500}", "postgresql://user:pass@host:5432/db"),
    Field("team_db_mysql_url", "MySQL URL", "database", True, "TEAM_DB_MYSQL_URL",
          "", r"mysql://\S{3,500}", "mysql://user:pass@host:3306/db"),
    Field("team_db_mongodb_url", "MongoDB URL", "database", True, "TEAM_DB_MONGODB_URL",
          "", r"mongodb(\+srv)?://\S{3,500}", "mongodb://user:pass@host:27017/db"),
    Field("team_run_seconds", "Waktu satu permintaan (detik)", "limits", False, "TEAM_RUN_SECONDS",
          "Batas waktu seluruh alur untuk satu permintaan.", r"[1-9]\d{0,6}", "600"),
    Field("team_member_seconds", "Waktu tiap agent (detik)", "limits", False, "TEAM_MEMBER_SECONDS",
          "Batas waktu satu agent menyelesaikan bagiannya.", r"[1-9]\d{0,6}", "240"),
    Field("team_member_rounds", "Putaran tiap agent", "limits", False, "TEAM_MEMBER_ROUNDS",
          "Berapa kali satu agent boleh memanggil tool dan berpikir ulang.", r"[1-9]\d{0,6}", "10"),
    Field("team_member_tokens", "Token tiap agent", "limits", False, "TEAM_MEMBER_TOKENS",
          "Batas token satu agent. Naikkan bila tugas besar berhenti terlalu cepat.", r"[1-9]\d{0,6}", "120000"),
    Field("team_video_run_seconds", "Mode video: waktu satu permintaan (detik)", "limits", False, "TEAM_VIDEO_RUN_SECONDS",
          "Dipakai tim yang mencentang Mode video.", r"[1-9]\d{0,6}", "3600"),
    Field("team_video_member_seconds", "Mode video: waktu tiap agent (detik)", "limits", False, "TEAM_VIDEO_MEMBER_SECONDS",
          "", r"[1-9]\d{0,6}", "1800"),
    Field("team_video_member_rounds", "Mode video: putaran tiap agent", "limits", False, "TEAM_VIDEO_MEMBER_ROUNDS",
          "", r"[1-9]\d{0,6}", "40"),
    Field("team_video_member_tokens", "Mode video: token tiap agent", "limits", False, "TEAM_VIDEO_MEMBER_TOKENS",
          "", r"[1-9]\d{0,6}", "400000"),
)
BY_KEY = {f.key: f for f in FIELDS}
GROUPS = {
    "telegram": {"label": "Telegram", "description": "Kirim hasil ke Telegram dan jalankan alur dari pesan Telegram."},
    "search": {"label": "Web search", "description": "Sumber pencarian untuk tool web search agent. Tanpa ini memakai pencarian bawaan."},
    "voice": {"label": "ElevenLabs (suara)", "description": "Voice-over dan text to speech. Tanpa ini tool suara menjelaskan cara menyambungkannya."},
    "google": {"label": "Google Workspace (Sheets)", "description": "Database online di Google Sheets: dibaca dan diedit agent dan tab Data."},
    "database": {"label": "Database", "description": "Tujuan node Database dan sumber data PostgreSQL, MySQL, MongoDB. SQLite lokal selalu tersedia tanpa diisi."},
    "limits": {"label": "Batas tim", "description": "Batas waktu, putaran, dan token untuk satu permintaan tim. Kosongkan untuk memakai nilai bawaan."},
}


class CredentialError(ValueError):
    """A value that cannot be saved. The message is for the user."""


def mask(value: str, secret: bool) -> str:
    if not value:
        return ""
    if not secret:
        return value
    return f"…{value[-4:]}" if len(value) > 8 else "••••"


class CredentialStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(os.getenv("CREDENTIALS_FILE", "").strip() or DEFAULT_PATH)
        self._lock = threading.Lock()
        self._values: dict[str, str] | None = None
        self._mtime = 0.0

    def _load(self) -> dict[str, str]:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if self._values is None or mtime != self._mtime:
            data: Any = {}
            with contextlib.suppress(OSError, ValueError):
                data = json.loads(self.path.read_text(encoding="utf-8"))
            self._values = {k: str(v) for k, v in (data if isinstance(data, dict) else {}).items() if k in BY_KEY and v}
            self._mtime = mtime
        return self._values

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._load(), indent=1, sort_keys=True), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        with contextlib.suppress(OSError):
            self._mtime = self.path.stat().st_mtime

    def web_value(self, key: str) -> str:
        with self._lock:
            return self._load().get(key, "")

    def update(self, values: dict[str, Any]) -> None:
        """Set the given keys. An empty value removes the web copy, so ``.env`` applies again."""
        cleaned: dict[str, str] = {}
        for key, raw in values.items():
            spec = BY_KEY.get(key)
            if spec is None:
                raise CredentialError(f"Kredensial tidak dikenal: {key}")
            value = str(raw or "").strip()
            if value and spec.key == "google_service_account":
                value = _clean_google_key(value)
            if value and spec.pattern and not re.fullmatch(spec.pattern, value):
                raise CredentialError(f"{spec.label} tidak sesuai format. Contoh: {spec.example}")
            cleaned[key] = value
        with self._lock:
            current = self._load()
            for key, value in cleaned.items():
                if value:
                    current[key] = value
                else:
                    current.pop(key, None)
            self._save()


_store: CredentialStore | None = None
_store_lock = threading.Lock()


def get_store() -> CredentialStore:
    global _store
    with _store_lock:
        wanted = Path(os.getenv("CREDENTIALS_FILE", "").strip() or DEFAULT_PATH)
        if _store is None or _store.path != wanted:
            _store = CredentialStore(wanted)
        return _store


def _clean_google_key(text: str) -> str:
    """Keep only what a service account needs, so the rest of the downloaded file is never stored."""
    from utils.datastore.base import DataError
    from utils.datastore.sheets import parse_service_account

    try:
        account = parse_service_account(text)
    except DataError as exc:
        raise CredentialError(str(exc)) from None
    return json.dumps({"type": "service_account", **account}, separators=(",", ":"))


def google_email() -> str:
    """The robot's email to share spreadsheets with, or empty."""
    try:
        return str(json.loads(get("google_service_account")).get("client_email") or "")
    except (ValueError, AttributeError):
        return ""


def _env_value(spec: Field) -> str:
    if not spec.env:
        return ""      # configured on the page only
    from config.settings import get_settings

    with contextlib.suppress(Exception):
        value = getattr(get_settings(), spec.key, None)
        if value:
            return str(value).strip()
    return os.getenv(spec.env, "").strip()


def get(key: str) -> str:
    """The value to use: typed on the page, else ``.env``, else empty."""
    spec = BY_KEY[key]
    return get_store().web_value(key) or _env_value(spec)


def number(key: str) -> int:
    """A numeric setting: typed on the page, else ``.env``, else the built-in default.

    Unlike ``get`` this keeps a 0 from ``.env`` (a limit a test or an owner set on purpose)."""
    from config.settings import get_settings

    web = get_store().web_value(key)
    if web.isdigit():
        return int(web)
    return int(getattr(get_settings(), key))


def default_of(key: str) -> str:
    """The built-in default of a setting that has one, as text, else empty."""
    from config.settings import get_settings

    with contextlib.suppress(Exception):
        info = type(get_settings()).model_fields.get(key)
        if info is not None and info.default not in (None, ""):
            return str(info.default)
    return ""


def source(key: str) -> str:
    """Where the value in use comes from: "web", "env", "default" (the built-in value), or "" when it is not set."""
    spec = BY_KEY[key]
    if get_store().web_value(key):
        return "web"
    value = _env_value(spec)
    if not value:
        return ""
    return "default" if value == default_of(key) else "env"


def public() -> dict[str, Any]:
    """What the page may see: every field, whether it is set and where from, never a full secret."""
    fields = []
    for spec in FIELDS:
        value = get(spec.key)
        shown = google_email() if spec.key == "google_service_account" and value else mask(value, spec.secret)
        fields.append({"key": spec.key, "label": spec.label, "group": spec.group, "secret": spec.secret, "env": spec.env,
                       "hint": spec.hint, "example": spec.example, "set": bool(value), "source": source(spec.key),
                       "masked": shown, "multiline": spec.multiline, "default": default_of(spec.key)})
    return {"groups": [{"key": k, **v} for k, v in GROUPS.items()], "fields": fields}


__all__ = ["FIELDS", "GROUPS", "CredentialStore", "CredentialError", "get", "number", "default_of", "source", "public", "get_store", "mask", "google_email"]
