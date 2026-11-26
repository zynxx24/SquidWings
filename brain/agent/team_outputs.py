"""Where a team's result can go, and what reaches it from outside.

Telegram (sending, and reading messages that start a flow), a database of the
user's choice, and plain HTTP requests to public addresses.

Bot tokens and connection strings stay in .env and never enter a team's saved
settings. They also carry secrets in URLs, so errors here are written by hand and
never include the text of the underlying exception.
"""

from __future__ import annotations

import asyncio
import html as html_text
import importlib
import importlib.util
import ipaddress
import json
import re
import socket
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from brain.agent.team_flow import is_valid_table
from brain.config import credentials
from brain.utils.logger import get_logger

logger = get_logger(__name__)

_TELEGRAM_LIMIT = 4000
_TELEGRAM_MAX_WAIT = 30
_CONNECT_TIMEOUT = 10
_HTTP_MAX_BYTES = 200_000
_TELEGRAM_POLL_SECONDS = 25
_DATABASES: dict[str, tuple[str, str, str]] = {
    # id: (label, setting holding the connection string, driver to import)
    "sqlite": ("SQLite (file lokal)", "", "sqlite3"),
    "postgres": ("PostgreSQL", "team_db_postgres_url", "psycopg"),
    "mysql": ("MySQL / MariaDB", "team_db_mysql_url", "pymysql"),
    "mongodb": ("MongoDB", "team_db_mongodb_url", "pymongo"),
}
_INSTALL = {"psycopg": "psycopg[binary]", "pymysql": "pymysql", "pymongo": "pymongo"}
DATABASE_IDS = tuple(_DATABASES)


class OutputError(RuntimeError):
    """A delivery failed. The message is safe to show and to store."""


# ── Telegram ──────────────────────────────────────────────────────────────────
def _split_message(text: str) -> list[str]:
    """Telegram refuses long messages. Split on line breaks where possible."""
    parts: list[str] = []
    rest = text.strip()
    while len(rest) > _TELEGRAM_LIMIT:
        cut = rest.rfind("\n", 0, _TELEGRAM_LIMIT)
        cut = cut if cut > _TELEGRAM_LIMIT // 2 else _TELEGRAM_LIMIT
        parts.append(rest[:cut])
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    return parts


class _ParseError(OutputError):
    """Telegram could not read the HTML of a message."""


async def _post_message(client: httpx.AsyncClient, url: str, chat_id: str, text: str, extra: dict[str, Any] | None = None) -> None:
    for attempt in range(2):
        try:
            response = await client.post(url, json={"chat_id": chat_id, "text": text, **(extra or {})})
        except httpx.HTTPError as exc:
            raise OutputError(f"Tidak bisa menghubungi Telegram ({type(exc).__name__})") from None
        if response.status_code == 429 and attempt == 0:
            wait = response.json().get("parameters", {}).get("retry_after", 5)
            if wait <= _TELEGRAM_MAX_WAIT:
                await asyncio.sleep(wait)
                continue
        if response.status_code != 200:
            try:
                reason = str(response.json().get("description") or "")
            except ValueError:
                reason = ""
            if response.status_code == 400 and "parse entities" in reason:
                raise _ParseError(reason[:200])
            raise OutputError(f"Telegram menolak pesan ({response.status_code}): {reason[:200]}")
        return


def _target(chat_id: str) -> tuple[str, str]:
    token = credentials.get("telegram_bot_token")
    target = chat_id or credentials.get("telegram_chat_id")
    if not token:
        raise OutputError("Bot token Telegram belum diisi. Isi di Tim agent > Kredensial, atau TELEGRAM_BOT_TOKEN di .env.")
    if not target:
        raise OutputError("Chat ID kosong. Isi di node ini, di Tim agent > Kredensial, atau TELEGRAM_CHAT_ID di .env.")
    return f"https://api.telegram.org/bot{token}/sendMessage", target


async def send_telegram(text: str, chat_id: str = "") -> int:
    """Send a text to a chat. Returns the number of messages it took."""
    url, target = _target(chat_id)
    parts = _split_message(text)
    if not parts:
        raise OutputError("Tidak ada isi untuk dikirim")
    async with httpx.AsyncClient(timeout=20) as client:
        for part in parts:
            await _post_message(client, url, target, part)
    return len(parts)


def keyboard(buttons: list[dict[str, str]], private: bool) -> dict[str, Any] | None:
    """Telegram's inline keyboard, two buttons a row. A "web" button opens inside Telegram (a Mini App), which
    Telegram allows only in a private chat; elsewhere it becomes an ordinary link."""
    keys = []
    for button in buttons:
        label, url = button.get("label", "").strip()[:64], button.get("url", "")
        if not label or not url.startswith(("https://", "http://", "tg://")):
            continue
        web = button.get("kind") == "web" and private and url.startswith("https://")
        keys.append({"text": label, "web_app": {"url": url}} if web else {"text": label, "url": url})
    if not keys:
        return None
    return {"inline_keyboard": [keys[i:i + 2] for i in range(0, len(keys), 2)]}


async def send_rich(parts: list[str], chat_id: str = "", *, html: bool = True, buttons: list[dict[str, str]] | None = None,
                    silent: bool = False, fallback: list[str] | None = None) -> int:
    """Send prepared messages: HTML from ``telegram_format``, buttons under the last one, optionally without a sound.

    When Telegram cannot read the HTML, ``fallback`` (plain text) goes instead, so a formatting slip never loses
    the message.
    """
    url, target = _target(chat_id)
    parts = [p for p in parts if p.strip()]
    if not parts:
        raise OutputError("Tidak ada isi untuk dikirim")
    private = bool(re.fullmatch(r"\d{1,20}", target))
    markup = keyboard(buttons or [], private)
    base: dict[str, Any] = {"disable_notification": True} if silent else {}
    async with httpx.AsyncClient(timeout=20) as client:
        for index, part in enumerate(parts):
            extra = dict(base)
            if html:
                extra.update(parse_mode="HTML", link_preview_options={"is_disabled": True})
            if markup and index == len(parts) - 1:
                extra["reply_markup"] = markup
            try:
                await _post_message(client, url, target, part, extra)
            except _ParseError:
                # Nothing sent yet: the plain version of the whole thing. Halfway: the rest without its tags.
                rest = fallback if index == 0 and fallback else [html_text.unescape(re.sub(r"<[^>]+>", "", p)) for p in parts[index:]]
                logger.info("[teams] Telegram could not read the formatted message; sent it as plain text")
                for spare_index, spare in enumerate(rest):
                    spare_extra = dict(base)
                    if markup and spare_index == len(rest) - 1:
                        spare_extra["reply_markup"] = markup
                    for chunk in _split_message(spare):
                        await _post_message(client, url, target, chunk, spare_extra)
                return len(rest)
    return len(parts)


# Bots may upload up to 50 MB; a photo up to 10 MB, shown in the chat itself.
TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024
_PHOTO_LIMIT = 10 * 1024 * 1024
_FILE_METHODS = {
    ".jpg": ("sendPhoto", "photo"), ".jpeg": ("sendPhoto", "photo"), ".png": ("sendPhoto", "photo"), ".webp": ("sendPhoto", "photo"),
    ".mp4": ("sendVideo", "video"), ".mov": ("sendVideo", "video"),
    ".mp3": ("sendAudio", "audio"), ".m4a": ("sendAudio", "audio"),
}


async def send_file(path: Path, chat_id: str = "", caption: str = "") -> str:
    """Send one file to a chat: a picture, video or song plays there, anything else is a document.
    Returns how it went, as a short line for the node's note. Raises OutputError when Telegram refuses it."""
    _, target = _target(chat_id)
    token = credentials.get("telegram_bot_token")
    size = path.stat().st_size
    if size > TELEGRAM_FILE_LIMIT:
        raise OutputError(f"{path.name} {size // 2**20} MB, lebih dari batas bot Telegram (50 MB).")
    method, field_name = _FILE_METHODS.get(path.suffix.lower(), ("sendDocument", "document"))
    if method == "sendPhoto" and size > _PHOTO_LIMIT:
        method, field_name = "sendDocument", "document"
    data = {"chat_id": target, **({"caption": caption[:1000]} if caption else {})}
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            with path.open("rb") as handle:
                response = await client.post(f"https://api.telegram.org/bot{token}/{method}", data=data,
                                             files={field_name: (path.name, handle)})
    except httpx.HTTPError as exc:
        raise OutputError(f"Tidak bisa mengirim {path.name} ({type(exc).__name__})") from None
    if response.status_code != 200:
        try:
            reason = str(response.json().get("description") or "")
        except ValueError:
            reason = ""
        raise OutputError(f"Telegram menolak {path.name} ({response.status_code}): {reason[:200]}")
    return path.name


async def send_chat_action(chat_id: str, action: str = "typing") -> None:
    """Show "typing…" in a chat while the answer is being made. Best effort: a failure changes nothing."""
    token = credentials.get("telegram_bot_token")
    if not token or not chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            await client.post(f"https://api.telegram.org/bot{token}/sendChatAction", json={"chat_id": chat_id, "action": action})
    except httpx.HTTPError:
        return


async def fetch_telegram_updates(offset: int | None, wait: int = _TELEGRAM_POLL_SECONDS) -> list[dict[str, Any]]:
    """New messages sent to the bot, by long polling. Empty when Telegram is not set up."""
    token = credentials.get("telegram_bot_token")
    if not token:
        return []
    params: dict[str, Any] = {"timeout": wait, "allowed_updates": '["message"]'}
    if offset is not None:
        params["offset"] = offset
    try:
        async with httpx.AsyncClient(timeout=wait + 10) as client:
            response = await client.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params)
    except httpx.HTTPError as exc:
        raise OutputError(f"Tidak bisa menghubungi Telegram ({type(exc).__name__})") from None
    if response.status_code != 200:
        raise OutputError(f"Telegram menolak getUpdates ({response.status_code})")
    return list(response.json().get("result") or [])


# ── HTTP ──────────────────────────────────────────────────────────────────────
def _public_url(url: str) -> None:
    """Refuse addresses on this machine or its network.

    A flow can be built from a template someone shared, so without this it could
    reach the router, other local services, or a cloud metadata endpoint.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OutputError("Hanya alamat http atau https")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise OutputError(f"Alamat {parsed.hostname} tidak ditemukan") from None
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]))
        if not address.is_global or address.is_multicast:
            raise OutputError(f"{parsed.hostname} bukan alamat publik")


async def http_request(method: str, url: str, body: str = "") -> str:
    """Call a public URL and return the reply as text. Redirects are not followed."""
    await asyncio.to_thread(_public_url, url)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            if method == "POST":
                try:
                    payload: Any = {"json": json.loads(body)} if body.strip() else {"json": {}}
                except ValueError:
                    payload = {"content": body.encode(), "headers": {"Content-Type": "text/plain; charset=utf-8"}}
                response = await client.post(url, **payload)
            else:
                response = await client.get(url)
    except httpx.HTTPError as exc:
        raise OutputError(f"Permintaan HTTP gagal ({type(exc).__name__})") from None
    if response.status_code >= 400:
        raise OutputError(f"Server menjawab {response.status_code}")
    return response.content[:_HTTP_MAX_BYTES].decode(response.encoding or "utf-8", errors="replace")


# ── Databases ─────────────────────────────────────────────────────────────────
def database_options() -> list[dict[str, Any]]:
    """Which databases can be chosen, and what each still needs."""
    options: list[dict[str, Any]] = []
    for database, (label, setting, driver) in _DATABASES.items():
        reason = ""
        if setting and not credentials.get(setting):
            reason = f"Isi URL-nya di Tim agent > Kredensial, atau {setting.upper()} di .env"
        elif driver != "sqlite3" and importlib.util.find_spec(driver) is None:
            reason = f'Jalankan pip install "{_INSTALL[driver]}"'
        options.append({"id": database, "label": label, "ready": not reason, "reason": reason})
    return options


def _save_sqlite(table: str, record: dict[str, Any]) -> None:
    path = Path(credentials.get("team_db_sqlite_path"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(_create_table(table, "INTEGER PRIMARY KEY AUTOINCREMENT"))
        connection.execute(_insert(table, record, "?"), list(record.values()))


def _create_table(table: str, id_column: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {table} (id {id_column}, run_id VARCHAR(40), team_id VARCHAR(40), "
        "team_name VARCHAR(100), node_id VARCHAR(40), task TEXT, content TEXT, created_at VARCHAR(40))"
    )


def _insert(table: str, record: dict[str, Any], placeholder: str) -> str:
    # Column names are ours and the table name was validated. Values stay bound.
    return f"INSERT INTO {table} ({', '.join(record)}) VALUES ({', '.join([placeholder] * len(record))})"


def _save_sql(driver: Any, connection: Any, table: str, record: dict[str, Any], id_column: str) -> None:
    try:
        with connection.cursor() as cursor:
            cursor.execute(_create_table(table, id_column))
            cursor.execute(_insert(table, record, "%s"), list(record.values()))
        connection.commit()
    finally:
        connection.close()


def _check_blocking(database: str) -> None:
    """Open a connection and close it again. Writes nothing."""
    if database == "postgres":
        psycopg = importlib.import_module("psycopg")
        psycopg.connect(credentials.get("team_db_postgres_url"), connect_timeout=_CONNECT_TIMEOUT).close()
    elif database == "mysql":
        pymysql = importlib.import_module("pymysql")
        parsed = httpx.URL(credentials.get("team_db_mysql_url"))
        pymysql.connect(host=parsed.host, port=parsed.port or 3306, user=parsed.username, password=parsed.password,
                        database=parsed.path.lstrip("/"), connect_timeout=_CONNECT_TIMEOUT).close()
    elif database == "mongodb":
        pymongo = importlib.import_module("pymongo")
        client = pymongo.MongoClient(credentials.get("team_db_mongodb_url"), serverSelectionTimeoutMS=_CONNECT_TIMEOUT * 1000)
        try:
            client.admin.command("ping")
        finally:
            client.close()
    elif database != "sqlite":
        raise OutputError(f"Database tidak dikenal: {database}")


async def check_database(database: str) -> None:
    """Can a flow reach this database now? Raises OutputError with a reason that names no secret."""
    option = next((o for o in database_options() if o["id"] == database), None)
    if option is None:
        raise OutputError(f"Database tidak dikenal: {database}")
    if not option["ready"]:
        raise OutputError(option["reason"])
    try:
        await asyncio.to_thread(_check_blocking, database)
    except OutputError:
        raise
    except Exception as exc:  # noqa: BLE001 - the driver's message can hold the URL, so only its type is shown
        raise OutputError(f"Tidak bisa tersambung ke {option['label']} ({type(exc).__name__})") from None


def _save_blocking(database: str, table: str, record: dict[str, Any]) -> None:
    if database == "sqlite":
        _save_sqlite(table, record)
    elif database == "postgres":
        psycopg = importlib.import_module("psycopg")
        connection = psycopg.connect(credentials.get("team_db_postgres_url"), connect_timeout=_CONNECT_TIMEOUT)
        _save_sql(psycopg, connection, table, record, "BIGSERIAL PRIMARY KEY")
    elif database == "mysql":
        pymysql = importlib.import_module("pymysql")
        parsed = httpx.URL(credentials.get("team_db_mysql_url"))
        connection = pymysql.connect(
            host=parsed.host, port=parsed.port or 3306, user=parsed.username, password=parsed.password,
            database=parsed.path.lstrip("/"), connect_timeout=_CONNECT_TIMEOUT, charset="utf8mb4",
        )
        _save_sql(pymysql, connection, table, record, "BIGINT AUTO_INCREMENT PRIMARY KEY")
    elif database == "mongodb":
        pymongo = importlib.import_module("pymongo")
        client = pymongo.MongoClient(credentials.get("team_db_mongodb_url"), serverSelectionTimeoutMS=_CONNECT_TIMEOUT * 1000)
        try:
            client.get_default_database()[table].insert_one(dict(record))
        finally:
            client.close()
    else:
        raise OutputError(f"Database tidak dikenal: {database}")


async def save_to_database(database: str, table: str, record: dict[str, Any]) -> None:
    """Save one record to the chosen database. The table is created if it is missing."""
    if not is_valid_table(table):
        raise OutputError("Nama tabel tidak valid")
    option = next((o for o in database_options() if o["id"] == database), None)
    if option is None:
        raise OutputError(f"Database tidak dikenal: {database}")
    if not option["ready"]:
        raise OutputError(f"{option['label']} belum siap: {option['reason']}")
    try:
        await asyncio.to_thread(_save_blocking, database, table, record)
    except OutputError:
        raise
    except Exception as exc:  # noqa: BLE001 - drivers raise many types, some quoting the password
        logger.warning("[teams] Saving to %s failed: %s", database, type(exc).__name__)
        raise OutputError(f"Gagal menyimpan ke {option['label']} ({type(exc).__name__})") from None
