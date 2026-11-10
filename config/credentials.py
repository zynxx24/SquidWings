"""Keys and IDs for the flow's integrations, set on the web page instead of in ``.env``.

The team page's Kredensial tab writes here: the Telegram bot and chat, web search (Brave and
SearXNG), and the database URLs a flow can save to. Every reader asks ``get(name)``: a value
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
    Field("team_db_postgres_url", "PostgreSQL URL", "database", True, "TEAM_DB_POSTGRES_URL",
          "", r"postgres(ql)?://\S{3,500}", "postgresql://user:pass@host:5432/db"),
    Field("team_db_mysql_url", "MySQL URL", "database", True, "TEAM_DB_MYSQL_URL",
          "", r"mysql://\S{3,500}", "mysql://user:pass@host:3306/db"),
    Field("team_db_mongodb_url", "MongoDB URL", "database", True, "TEAM_DB_MONGODB_URL",
          "", r"mongodb(\+srv)?://\S{3,500}", "mongodb://user:pass@host:27017/db"),
)
BY_KEY = {f.key: f for f in FIELDS}
GROUPS = {
    "telegram": {"label": "Telegram", "description": "Kirim hasil ke Telegram dan jalankan alur dari pesan Telegram."},
    "search": {"label": "Web search", "description": "Sumber pencarian untuk tool web search agent. Tanpa ini memakai pencarian bawaan."},
    "database": {"label": "Database", "description": "Tujuan node Database. SQLite lokal selalu tersedia tanpa diisi."},
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


def _env_value(spec: Field) -> str:
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


def source(key: str) -> str:
    """Where the value in use comes from: "web", "env", or "" when it is not set."""
    spec = BY_KEY[key]
    if get_store().web_value(key):
        return "web"
    return "env" if _env_value(spec) else ""


def public() -> dict[str, Any]:
    """What the page may see: every field, whether it is set and where from, never a full secret."""
    fields = []
    for spec in FIELDS:
        value = get(spec.key)
        fields.append({"key": spec.key, "label": spec.label, "group": spec.group, "secret": spec.secret, "env": spec.env,
                       "hint": spec.hint, "example": spec.example, "set": bool(value), "source": source(spec.key),
                       "masked": mask(value, spec.secret)})
    return {"groups": [{"key": k, **v} for k, v in GROUPS.items()], "fields": fields}


__all__ = ["FIELDS", "GROUPS", "CredentialStore", "CredentialError", "get", "source", "public", "get_store", "mask"]
