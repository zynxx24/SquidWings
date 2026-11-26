"""Relations between tables: a column in one table that points at a column in another.

``pesanan.kode_produk -> katalog.kode`` says every order names a product by its code. The tables can live in
different sources (an orders sheet and a product SQLite file), so a relation is kept here, beside ``sources.json``,
and the rows are joined by reading, not by the database. Reading a table can then bring along, for each row, the row
it points at (many to one) and the rows that point at it (one to many).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from .base import MAX_SCAN, Backend, DataError

_FIELD_RE = re.compile(r"[^\W\d]\w{0,62}", re.UNICODE)


@dataclass
class Relation:
    id: str
    source: str           # the table that holds the pointing column
    table: str
    column: str
    target_source: str    # the table it points at
    target_table: str
    target_column: str
    name: str = ""        # field added to a row of ``table``: the row it points at
    reverse: str = ""     # field added to a row of ``target_table``: the rows that point at it
    created_at: float = 0.0

    def public(self) -> dict[str, Any]:
        return asdict(self)

    def touches(self, source: str, table: str) -> bool:
        return (self.source, self.table) == (source, table) or (self.target_source, self.target_table) == (source, table)


def _field(text: str, fallback: str) -> str:
    value = re.sub(r"\W+", "_", str(text or "").strip()).strip("_")[:63] or fallback
    if not _FIELD_RE.fullmatch(value):
        value = f"r_{value}"[:63]
    return value


class Relations:
    def __init__(self, directory: Any) -> None:
        self.path = directory / "relations.json"
        self._lock = threading.RLock()

    def _load(self) -> list[Relation]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = []
        for item in raw if isinstance(raw, list) else []:
            with contextlib.suppress(TypeError, KeyError):
                out.append(Relation(**{k: v for k, v in item.items() if k in Relation.__dataclass_fields__}))
        return out

    def _save(self, items: list[Relation]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps([asdict(r) for r in items], ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, self.path)

    def all(self) -> list[Relation]:
        with self._lock:
            return self._load()

    def find(self, ref: str) -> Relation | None:
        return next((r for r in self.all() if ref in (r.id, r.name, r.reverse)), None)

    def for_table(self, source: str, table: str) -> list[Relation]:
        return [r for r in self.all() if r.touches(source, table)]

    def add(self, source: str, table: str, column: str, target_source: str, target_table: str, target_column: str,
            name: str = "", reverse: str = "") -> Relation:
        if not all(str(x or "").strip() for x in (source, table, column, target_source, target_table, target_column)):
            raise DataError("Relasi butuh tabel dan kolom di kedua sisi.")
        if (source, table) == (target_source, target_table) and column == target_column:
            raise DataError("Kolom tidak bisa berelasi dengan dirinya sendiri.")
        with self._lock:
            items = self._load()
            if any((r.source, r.table, r.column, r.target_source, r.target_table, r.target_column)
                   == (source, table, column, target_source, target_table, target_column) for r in items):
                raise DataError("Relasi itu sudah ada.")
            relation = Relation(
                id=uuid.uuid4().hex[:10], source=source, table=table, column=column,
                target_source=target_source, target_table=target_table, target_column=target_column,
                name=_field(name, target_table), reverse=_field(reverse, table), created_at=time.time(),
            )
            self._save([*items, relation])
            return relation

    def remove(self, ref: str) -> Relation:
        with self._lock:
            items = self._load()
            relation = next((r for r in items if r.id == ref), None)
            if relation is None:
                raise DataError("Relasi itu tidak ada.")
            self._save([r for r in items if r.id != ref])
            return relation

    def forget_source(self, source: str) -> int:
        """Drop the relations of a source that was removed. Returns how many went."""
        with self._lock:
            items = self._load()
            kept = [r for r in items if source not in (r.source, r.target_source)]
            if len(kept) != len(items):
                self._save(kept)
            return len(items) - len(kept)


def attach(rows: list[dict[str, Any]], source: str, table: str, relations: list[Relation],
           open_backend: Any) -> list[str]:
    """Add the related rows to each row's values, in place. Returns notes for what could not be read.

    ``rows`` are ``{"key", "values"}`` dicts. ``open_backend(source_name) -> Backend`` opens the other side.
    """
    notes: list[str] = []
    for relation in relations:
        outgoing = (relation.source, relation.table) == (source, table)
        here, there = (relation.column, relation.target_column) if outgoing else (relation.target_column, relation.column)
        other_source, other_table = ((relation.target_source, relation.target_table) if outgoing
                                     else (relation.source, relation.table))
        label = relation.name if outgoing else relation.reverse
        # The raw values, not text, so a number column in PostgreSQL still compares; one of each.
        keys = list({str(v): v for v in (r["values"].get(here) for r in rows) if v not in (None, "")}.values())
        if not keys:
            for r in rows:
                r["values"][label] = None if outgoing else []
            continue
        try:
            backend: Backend = open_backend(other_source)
            page = backend.all_rows(other_table, where={there: keys}, cap=MAX_SCAN)
        except DataError as exc:
            notes.append(f"Relasi {label}: {exc}")
            continue
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in page.rows:
            grouped.setdefault(str(item["values"].get(there)).strip().casefold(), []).append(item["values"])
        for r in rows:
            found = grouped.get(str(r["values"].get(here)).strip().casefold(), [])
            r["values"][label] = (found[0] if found else None) if outgoing else found
    return notes
