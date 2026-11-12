"""The list of data sources, and how to open one.

A source has a short name (``katalog``) that flows, agents and the web editor all use. Nothing here is read from
``.env``: files live in the data folder, the Google key and the database URLs come from the web page's Kredensial
tab (``config.credentials``), and the list itself is ``sources.json`` next to them.

Kinds: ``file`` (CSV, Excel, JSON, SQLite in the data folder), ``sheets`` (a Google spreadsheet),
``postgres``, ``mysql`` and ``mongodb`` (the URL saved in Kredensial).
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .base import Backend, DataError
from .files import FILE_SUFFIXES, MAX_FILE_BYTES, create_file, open_file
from .mongo import MongoBackend
from .sheets import SheetsBackend, parse_service_account, parse_spreadsheet
from .sql import mysql_backend, postgres_backend, sqlite_backend

# Where files and the list are kept. Tests point it at a temporary folder; it is not a setting.
DATA_DIR = Path(__file__).resolve().parents[2] / "web" / "data" / "datastore"
SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
LOCAL_SUFFIXES = FILE_SUFFIXES | SQLITE_SUFFIXES
ONLINE_KINDS = {
    "sheets": "Google Sheets",
    "postgres": "PostgreSQL",
    "mysql": "MySQL / MariaDB",
    "mongodb": "MongoDB",
}
CREDENTIAL_FOR = {"postgres": "team_db_postgres_url", "mysql": "team_db_mysql_url", "mongodb": "team_db_mongodb_url"}
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
MAX_SOURCES = 50
MAX_IMPORT_BYTES = 25 * 1024 * 1024


@dataclass
class Source:
    id: str
    name: str                 # what flows and tools call it
    kind: str                 # file | sheets | postgres | mysql | mongodb
    location: str = ""        # file name in the data folder, or the spreadsheet id
    label: str = ""
    created_at: float = 0.0

    @property
    def online(self) -> bool:
        return self.kind != "file"

    def public(self) -> dict[str, Any]:
        out = asdict(self)
        out["online"] = self.online
        out["format"] = Path(self.location).suffix.lower().lstrip(".") if self.kind == "file" else self.kind
        return out


def slug(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40]
    return base or "data"


def files_dir() -> Path:
    return DATA_DIR / "files"


def safe_filename(name: str) -> str:
    """A file name for the data folder: no folders, no odd characters, and a type we can open."""
    base = Path(str(name).replace("\\", "/")).name.strip().lstrip(".")
    base = re.sub(r"[^\w.\- ]", "_", base).strip()
    if not base or Path(base).suffix.lower() not in LOCAL_SUFFIXES:
        raise DataError(f"Nama file harus berakhiran {', '.join(sorted(LOCAL_SUFFIXES))}")
    return base[:120]


class Registry:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "sources.json"
        self._lock = threading.RLock()

    def _load(self) -> list[Source]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = []
        for item in raw if isinstance(raw, list) else []:
            with contextlib.suppress(TypeError, KeyError):
                out.append(Source(**{k: v for k, v in item.items() if k in Source.__dataclass_fields__}))
        return out

    def _save(self, items: list[Source]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps([asdict(s) for s in items], ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, self.path)

    def list(self) -> list[Source]:
        with self._lock:
            return self._load()

    def find(self, ref: str) -> Source | None:
        wanted = str(ref or "").strip().lower()
        for source in self.list():
            if wanted in (source.id.lower(), source.name.lower()):
                return source
        return None

    def add(self, kind: str, location: str = "", name: str = "", label: str = "") -> Source:
        with self._lock:
            items = self._load()
            if len(items) >= MAX_SOURCES:
                raise DataError(f"Maksimal {MAX_SOURCES} sumber data")
            if kind != "file" and kind not in ONLINE_KINDS:
                raise DataError(f"Jenis sumber tidak dikenal: {kind}")
            if kind == "sheets":
                location = parse_spreadsheet(location)[0]
            elif kind == "file":
                location = safe_filename(location)
                if not (files_dir() / location).is_file():
                    raise DataError(f"File {location} belum ada di folder data")
            else:
                location = ""
            chosen = slug(name or label or (Path(location).stem if kind == "file" else ONLINE_KINDS.get(kind, kind)))
            if not _NAME_RE.fullmatch(chosen):
                raise DataError("Nama sumber hanya boleh huruf kecil, angka, - dan _")
            if any(s.name == chosen for s in items):
                raise DataError(f"Sumber bernama '{chosen}' sudah ada. Pilih nama lain.")
            if any(s.kind == kind and s.location == location and kind != "file" and kind in CREDENTIAL_FOR for s in items):
                raise DataError(f"{ONLINE_KINDS[kind]} sudah ditambahkan")
            source = Source(id=uuid.uuid4().hex[:10], name=chosen, kind=kind, location=location,
                            label=(label or chosen).strip()[:80], created_at=time.time())
            self._save([*items, source])
            return source

    def rename(self, ref: str, name: str = "", label: str = "") -> Source:
        with self._lock:
            items = self._load()
            source = next((s for s in items if ref.lower() in (s.id.lower(), s.name.lower())), None)
            if source is None:
                raise DataError(f"Sumber '{ref}' tidak ada")
            if name:
                chosen = slug(name)
                if any(s.name == chosen and s.id != source.id for s in items):
                    raise DataError(f"Sumber bernama '{chosen}' sudah ada")
                source.name = chosen
            if label:
                source.label = label.strip()[:80]
            self._save(items)
            return source

    def remove(self, ref: str, delete_file: bool = False) -> Source:
        with self._lock:
            items = self._load()
            source = next((s for s in items if ref.lower() in (s.id.lower(), s.name.lower())), None)
            if source is None:
                raise DataError(f"Sumber '{ref}' tidak ada")
            self._save([s for s in items if s.id != source.id])
            if delete_file and source.kind == "file":
                (files_dir() / source.location).unlink(missing_ok=True)
            return source


def get_registry() -> Registry:
    return Registry(DATA_DIR)


def _google_account() -> dict[str, str]:
    from config import credentials

    text = credentials.get("google_service_account")
    if not text:
        raise DataError("Kunci Google belum diisi. Isi di Tim agent > Kredensial > Google Workspace.")
    return parse_service_account(text)


def backend_for(source: Source) -> Backend:
    """The live connection for a registered source."""
    from config import credentials

    if source.kind == "file":
        path = files_dir() / safe_filename(source.location)
        if path.suffix.lower() in SQLITE_SUFFIXES:
            return sqlite_backend(path, source.label or source.name)
        return open_file(path, source.label or source.name)
    if source.kind == "sheets":
        return SheetsBackend(source.location, _google_account(), source.label or "Google Sheets")
    key = CREDENTIAL_FOR.get(source.kind)
    if key is None:
        raise DataError(f"Jenis sumber tidak dikenal: {source.kind}")
    url = credentials.get(key)
    if not url:
        raise DataError(f"URL {ONLINE_KINDS[source.kind]} belum diisi. Isi di Tim agent > Kredensial > Database.")
    if source.kind == "postgres":
        return postgres_backend(url, source.label or "PostgreSQL")
    if source.kind == "mysql":
        return mysql_backend(url, source.label or "MySQL")
    return MongoBackend(url, source.label or "MongoDB")


def open_source(ref: str, workspace_path: str | None = None) -> tuple[Backend, str]:
    """A registered source by name, or else a data file inside the session's workspace.

    Returns the backend and the name to use for access checks ("" for a workspace file, which is the agent's own).
    """
    source = get_registry().find(ref)
    if source is not None:
        return backend_for(source), source.name
    if workspace_path or str(ref).lower().endswith(tuple(LOCAL_SUFFIXES)):
        from tool.execute.execute_toolbox import _files

        path = _files.source(str(ref), workspace_path)
        if path.suffix.lower() in SQLITE_SUFFIXES:
            return sqlite_backend(path), ""
        return open_file(path), ""
    names = ", ".join(s.name for s in get_registry().list()) or "(belum ada, tambahkan di tab Data)"
    raise DataError(f"Sumber data '{ref}' tidak ada. Yang terdaftar: {names}")


# ── Creating and importing ────────────────────────────────────────────────────

def create_local(filename: str, columns: list[str], label: str = "", name: str = "") -> Source:
    """Make a new empty file in the data folder and register it."""
    safe = safe_filename(filename)
    path = files_dir() / safe
    if path.suffix.lower() in SQLITE_SUFFIXES:
        import sqlite3

        if path.exists():
            raise DataError(f"File {safe} sudah ada")
        path.parent.mkdir(parents=True, exist_ok=True)
        from .base import valid_name

        names = [valid_name(c, "Nama kolom") for c in columns if str(c).strip()] or ["nama"]
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE data (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                               + ", ".join(f'"{c}" TEXT' for c in names if c.lower() != "id") + ")")
    else:
        create_file(path, columns)
    return get_registry().add("file", safe, name=name or Path(safe).stem, label=label or Path(safe).stem)


def import_local(filename: str, content_base64: str, label: str = "", name: str = "") -> Source:
    """Save an uploaded file in the data folder, check that it opens, and register it."""
    safe = safe_filename(filename)
    try:
        raw = base64.b64decode(content_base64, validate=False)
    except ValueError:
        raise DataError("Isi file tidak bisa dibaca") from None
    if len(raw) > min(MAX_IMPORT_BYTES, MAX_FILE_BYTES):
        raise DataError(f"File lebih dari {MAX_IMPORT_BYTES // 2**20} MB")
    target = files_dir() / safe
    stem, suffix, n = target.stem, target.suffix, 1
    while target.exists():
        n += 1
        target = files_dir() / f"{stem}-{n}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    try:
        backend = sqlite_backend(target) if target.suffix.lower() in SQLITE_SUFFIXES else open_file(target)
        backend.tables()
    except DataError:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - a broken upload is removed again
        target.unlink(missing_ok=True)
        raise DataError(f"File tidak bisa dibuka ({type(exc).__name__})") from None
    return get_registry().add("file", target.name, name=name or target.stem, label=label or target.stem)


SHOP_STARTER = {
    "katalog": (
        "katalog.xlsx", "Katalog produk",
        ["kode", "nama", "kategori", "harga", "stok", "ukuran", "warna", "deskripsi", "aktif"],
        [
            {"kode": "KS-001", "nama": "Kaos Polos Premium", "kategori": "Kaos", "harga": 75000, "stok": 24, "ukuran": "S, M, L, XL",
             "warna": "Hitam, Putih", "deskripsi": "Katun combed 30s, nyaman dipakai harian.", "aktif": "ya"},
            {"kode": "TS-002", "nama": "Tote Bag Kanvas", "kategori": "Tas", "harga": 55000, "stok": 12, "ukuran": "All size",
             "warna": "Krem", "deskripsi": "Kanvas tebal, ada resleting dan saku dalam.", "aktif": "ya"},
        ],
    ),
    "pesanan": (
        "pesanan.xlsx", "Pesanan masuk",
        ["waktu", "chat_id", "nama", "produk", "jumlah", "alamat", "catatan", "status"],
        [],
    ),
}


def ensure_shop_starter() -> list[Source]:
    """The two files a shop bot needs, made if missing: the product list and the orders. Existing ones are kept."""
    registry = get_registry()
    made: list[Source] = []
    for name, (filename, label, columns, rows) in SHOP_STARTER.items():
        if registry.find(name) is not None:
            continue
        path = files_dir() / filename
        if not path.exists():
            create_file(path, columns)
            if rows:
                backend = open_file(path)
                for row in rows:
                    backend.insert("", row)
        made.append(registry.add("file", filename, name=name, label=label))
    return made


__all__ = ["Registry", "Source", "backend_for", "create_local", "ensure_shop_starter", "get_registry", "import_local",
           "open_source", "safe_filename", "files_dir", "LOCAL_SUFFIXES", "ONLINE_KINDS"]
