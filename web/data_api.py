"""HTTP API for the Data tab: the list of sources and an editable grid over any of them.

Reading is open like the rest of the team pages; every change needs the router PIN, like saving a credential.
The backends are synchronous (files, DB-API, plain HTTP), so these are plain ``def`` endpoints that FastAPI
runs in its thread pool and never block the event loop.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from config import credentials
from utils import datastore
from utils.datastore import DataError, registry
from utils.datastore.base import MAX_PAGE, Backend
from web.router_api import admin

router = APIRouter(prefix="/api/data", tags=["Data"])
MAX_UPLOAD_CHARS = 36_000_000      # 25 MB of file, as base64


def _guard[T](work: Callable[[], T]) -> T:
    """Run a data action and turn our plain-language errors into a 422 the page can show."""
    try:
        return work()
    except DataError as exc:
        raise HTTPException(422, str(exc)) from None


def _open(ref: str) -> tuple[registry.Source, Backend]:
    source = registry.get_registry().find(ref)
    if source is None:
        raise HTTPException(404, f"Sumber data '{ref}' tidak ada.")
    return source, _guard(lambda: registry.backend_for(source))


def _same(a: Any, b: Any) -> bool:
    return str("" if a is None else a).strip() == str("" if b is None else b).strip()


def _unchanged(backend: Backend, table: str, key: Any, expect: dict[str, Any] | None) -> None:
    """Rows of a file or sheet are found by number. If someone changed the data since the page loaded, say so
    instead of editing whatever row now has that number."""
    if not expect or not backend.positional:
        return
    try:
        number = int(key)
    except (TypeError, ValueError):
        return
    page = backend.read(table, number - 1, 1)
    row = page.rows[0] if page.rows else None
    if row is None or str(row["key"]) != str(number) or any(
        not _same(row["values"].get(column), value) for column, value in expect.items() if column in row["values"]
    ):
        raise HTTPException(409, "Isi tabel sudah berubah sejak halaman dimuat. Muat ulang, lalu ulangi perubahannya.")


# ── sources ───────────────────────────────────────────────────────────────────


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    """The registered sources, what kinds can be added, and whether the cloud logins are filled in."""
    return {
        "sources": [s.public() for s in registry.get_registry().list()],
        "online": registry.ONLINE_KINDS,
        "formats": sorted(registry.LOCAL_SUFFIXES),
        "ready": {
            "sheets": bool(credentials.get("google_service_account")),
            "postgres": bool(credentials.get("team_db_postgres_url")),
            "mysql": bool(credentials.get("team_db_mysql_url")),
            "mongodb": bool(credentials.get("team_db_mongodb_url")),
        },
        "google_email": credentials.google_email(),
    }


class SourceInput(BaseModel):
    kind: str = Field(max_length=20)
    location: str = Field(default="", max_length=300)
    name: str = Field(default="", max_length=60)
    label: str = Field(default="", max_length=80)


@router.post("/sources", status_code=201, dependencies=[Depends(admin)])
def add_source(payload: SourceInput) -> dict[str, Any]:
    """Register something that already exists: a file in the data folder, a Google spreadsheet, or a saved database."""
    source = _guard(lambda: registry.get_registry().add(payload.kind, payload.location, payload.name, payload.label))
    if source.online:
        # Say at once whether it can be reached, so a wrong link or a missing share shows up now, not at the first edit.
        try:
            registry.backend_for(source).check()
        except DataError as exc:
            registry.get_registry().remove(source.id)
            raise HTTPException(422, str(exc)) from None
    return source.public()


class NewFile(BaseModel):
    filename: str = Field(min_length=1, max_length=120)
    columns: list[str] = Field(default_factory=list, max_length=60)
    name: str = Field(default="", max_length=60)
    label: str = Field(default="", max_length=80)


@router.post("/sources/new-file", status_code=201, dependencies=[Depends(admin)])
def new_file(payload: NewFile) -> dict[str, Any]:
    return _guard(lambda: registry.create_local(payload.filename, payload.columns, payload.label, payload.name)).public()


class Upload(BaseModel):
    filename: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=MAX_UPLOAD_CHARS)
    name: str = Field(default="", max_length=60)
    label: str = Field(default="", max_length=80)


@router.post("/sources/import", status_code=201, dependencies=[Depends(admin)])
def import_file(payload: Upload) -> dict[str, Any]:
    return _guard(lambda: registry.import_local(payload.filename, payload.content, payload.label, payload.name)).public()


@router.post("/starter/shop", status_code=201, dependencies=[Depends(admin)])
def shop_starter() -> dict[str, Any]:
    """The product list and the orders sheet a shop bot needs. Existing ones are left alone."""
    made = _guard(datastore.ensure_shop_starter)
    return {"created": [s.public() for s in made], "sources": [s.public() for s in registry.get_registry().list()]}


class Rename(BaseModel):
    name: str = Field(default="", max_length=60)
    label: str = Field(default="", max_length=80)


@router.patch("/sources/{ref}", dependencies=[Depends(admin)])
def rename_source(ref: str, payload: Rename) -> dict[str, Any]:
    return _guard(lambda: registry.get_registry().rename(ref, payload.name, payload.label)).public()


@router.delete("/sources/{ref}", dependencies=[Depends(admin)])
def remove_source(ref: str, delete_file: bool = False) -> dict[str, Any]:
    """Stop using a source. A file stays on disk unless ``delete_file`` says to remove it; online data is never touched."""
    return _guard(lambda: registry.get_registry().remove(ref, delete_file)).public()


@router.post("/sources/{ref}/check")
def check_source(ref: str) -> dict[str, str]:
    _, backend = _open(ref)
    return {"message": _guard(backend.check)}


@router.get("/sources/{ref}/download")
def download(ref: str) -> FileResponse:
    source, _ = _open(ref)
    if source.kind != "file":
        raise HTTPException(400, "Hanya file di komputer ini yang bisa diunduh. Data online tetap di tempatnya.")
    path = registry.files_dir() / registry.safe_filename(source.location)
    if not path.is_file():
        raise HTTPException(404, "File-nya sudah tidak ada.")
    return FileResponse(path, filename=Path(source.location).name)


# ── tables and rows ───────────────────────────────────────────────────────────


@router.get("/sources/{ref}/tables")
def tables(ref: str) -> dict[str, Any]:
    source, backend = _open(ref)
    return {"source": source.public(), "positional": backend.positional,
            "tables": [t.to_dict() for t in _guard(backend.tables)]}


@router.get("/sources/{ref}/rows")
def read_rows(
    ref: str, table: str = "", offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=MAX_PAGE),
    sort: str = "", desc: bool = False, search: str = Query("", max_length=200),
) -> dict[str, Any]:
    source, backend = _open(ref)
    page = _guard(lambda: backend.read(table, offset, limit, sort, desc, search))
    return {**page.to_dict(), "table": backend.table(table).name, "positional": backend.positional, "kind": source.kind}


class NewRow(BaseModel):
    table: str = Field(default="", max_length=120)
    values: dict[str, Any] = Field(default_factory=dict)
    at: int | None = Field(default=None, ge=1)


@router.post("/sources/{ref}/rows", status_code=201, dependencies=[Depends(admin)])
def add_row(ref: str, payload: NewRow) -> dict[str, Any]:
    _, backend = _open(ref)
    key = _guard(lambda: backend.insert(payload.table, payload.values, payload.at))
    return {"key": key}


class RowChange(BaseModel):
    table: str = Field(default="", max_length=120)
    key: Any
    values: dict[str, Any] = Field(default_factory=dict)
    expect: dict[str, Any] | None = None        # what the page saw, to catch a change made meanwhile


@router.patch("/sources/{ref}/rows", dependencies=[Depends(admin)])
def update_row(ref: str, payload: RowChange) -> dict[str, bool]:
    _, backend = _open(ref)
    _guard(lambda: _unchanged(backend, payload.table, payload.key, payload.expect))
    _guard(lambda: backend.update(payload.table, payload.key, payload.values))
    return {"ok": True}


class RowDelete(BaseModel):
    table: str = Field(default="", max_length=120)
    keys: list[Any] = Field(min_length=1, max_length=200)
    expect: list[dict[str, Any]] | None = None  # one per key, in the same order


@router.post("/sources/{ref}/rows/delete", dependencies=[Depends(admin)])
def delete_rows(ref: str, payload: RowDelete) -> dict[str, int]:
    _, backend = _open(ref)
    for index, key in enumerate(payload.keys):
        expected = payload.expect[index] if payload.expect and index < len(payload.expect) else None
        _guard(partial(_unchanged, backend, payload.table, key, expected))
    return {"deleted": _guard(lambda: backend.delete(payload.table, payload.keys))}


class ColumnChange(BaseModel):
    table: str = Field(default="", max_length=120)
    action: str = Field(pattern=r"^(add|rename|drop)$")
    name: str = Field(min_length=1, max_length=64)
    new_name: str = Field(default="", max_length=64)


@router.post("/sources/{ref}/columns", dependencies=[Depends(admin)])
def change_column(ref: str, payload: ColumnChange) -> dict[str, Any]:
    _, backend = _open(ref)
    if payload.action == "add":
        _guard(lambda: backend.add_column(payload.table, payload.name))
    elif payload.action == "rename":
        if not payload.new_name.strip():
            raise HTTPException(422, "Nama kolom baru belum diisi.")
        _guard(lambda: backend.rename_column(payload.table, payload.name, payload.new_name))
    else:
        _guard(lambda: backend.drop_column(payload.table, payload.name))
    return {"columns": backend.table(payload.table).columns}


class TableCreate(BaseModel):
    table: str = Field(min_length=1, max_length=64)
    columns: list[str] = Field(default_factory=list, max_length=60)


@router.post("/sources/{ref}/tables", status_code=201, dependencies=[Depends(admin)])
def create_table(ref: str, payload: TableCreate) -> dict[str, Any]:
    _, backend = _open(ref)
    _guard(lambda: backend.create_table(payload.table, payload.columns))
    return {"tables": [t.to_dict() for t in backend.tables()]}


class TableDrop(BaseModel):
    table: str = Field(min_length=1, max_length=64)


@router.post("/sources/{ref}/tables/drop", dependencies=[Depends(admin)])
def drop_table(ref: str, payload: TableDrop) -> dict[str, Any]:
    _, backend = _open(ref)
    _guard(lambda: backend.drop_table(payload.table))
    return {"tables": [t.to_dict() for t in backend.tables()]}
