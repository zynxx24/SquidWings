"""MongoDB: a collection is a table and a document is a row.

Documents need not share fields, so the columns are the fields seen in the first few hundred documents.
The row key is the document's ``_id`` as text.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator
from typing import Any

from .base import (
    MAX_PAGE,
    Backend,
    DataError,
    Page,
    TableInfo,
    coerce,
    jsonable,
    normalize_where,
    resolve_column,
    valid_name,
)
from .sql import CONNECT_TIMEOUT, need_driver

SAMPLE_DOCS = 300
_HEX24 = re.compile(r"[0-9a-fA-F]{24}")
_OPS = {">": "$gt", ">=": "$gte", "<": "$lt", "<=": "$lte", "!=": "$ne"}


class MongoBackend(Backend):
    kind = "mongodb"

    def __init__(self, url: str, label: str = "MongoDB", client_factory: Callable[[str], Any] | None = None) -> None:
        self.url = url
        self.label = label
        self._factory = client_factory

    @contextlib.contextmanager
    def _db(self) -> Iterator[Any]:
        try:
            if self._factory is not None:
                client = self._factory(self.url)
            else:
                client = need_driver("pymongo").MongoClient(self.url, serverSelectionTimeoutMS=CONNECT_TIMEOUT * 1000)
        except DataError:
            raise
        except Exception as exc:  # noqa: BLE001 - the URL holds the password, so only the type is shown
            raise DataError(f"Tidak bisa tersambung ke MongoDB ({type(exc).__name__}).") from None
        try:
            try:
                yield client.get_default_database()
            except DataError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise DataError(f"MongoDB menolak perintah ({type(exc).__name__}). Cek URL dan nama database di Kredensial.") from None
        finally:
            with contextlib.suppress(Exception):
                client.close()

    @staticmethod
    def _id(key: Any) -> Any:
        if isinstance(key, str) and _HEX24.fullmatch(key):
            return need_driver("bson").ObjectId(key)
        if isinstance(key, str) and key.lstrip("-").isdigit():
            return int(key)
        return key

    def _collection(self, db: Any, table: str) -> tuple[str, Any]:
        names = sorted(n for n in db.list_collection_names() if not n.startswith("system."))
        real = next((n for n in names if n == table), None) or next((n for n in names if n.lower() == str(table).strip().lower()), None)
        if real is None:
            if not table and len(names) == 1:
                real = names[0]
            else:
                raise DataError(f"Koleksi '{table}' tidak ada. Koleksi yang ada: {', '.join(names) or '(kosong)'}")
        return real, db[real]

    @staticmethod
    def _columns(collection: Any) -> list[str]:
        columns: list[str] = ["_id"]
        for document in collection.find().limit(SAMPLE_DOCS):
            for field in document:
                if field not in columns:
                    columns.append(str(field))
        return columns

    def tables(self) -> list[TableInfo]:
        with self._db() as db:
            out = []
            for name in sorted(n for n in db.list_collection_names() if not n.startswith("system.")):
                collection = db[name]
                out.append(TableInfo(name, self._columns(collection), int(collection.count_documents({})), "_id"))
            return out

    def _filter(self, columns: list[str], where: Any, search: str) -> dict[str, Any]:
        clauses: list[dict[str, Any]] = []
        for cond in normalize_where(where, columns):
            if cond.op == "=":
                clauses.append({cond.column: cond.value})
            elif cond.op == "in":
                clauses.append({cond.column: {"$in": cond.value if isinstance(cond.value, list) else [cond.value]}})
            elif cond.op in ("contains", "startswith"):
                pattern = re.escape(str(cond.value))
                clauses.append({cond.column: {"$regex": ("^" if cond.op == "startswith" else "") + pattern, "$options": "i"}})
            else:
                clauses.append({cond.column: {_OPS[cond.op]: cond.value}})
        for word in [w for w in search.split() if w]:
            pattern = re.escape(word)
            clauses.append({"$or": [{c: {"$regex": pattern, "$options": "i"}} for c in columns if c != "_id"]})
        return {"$and": clauses} if clauses else {}

    def read(self, table: str, offset: int = 0, limit: int = 50, sort: str = "", desc: bool = False,
             search: str = "", where: Any = None) -> Page:
        with self._db() as db:
            _, collection = self._collection(db, table)
            columns = self._columns(collection)
            query = self._filter(columns, where, search)
            total = int(collection.count_documents(query))
            cursor = collection.find(query)
            if sort:
                cursor = cursor.sort(resolve_column(columns, sort), -1 if desc else 1)
            cursor = cursor.skip(max(0, int(offset))).limit(max(1, min(int(limit), MAX_PAGE)))
            rows = []
            for document in cursor:
                values = {c: jsonable(document.get(c)) for c in columns}
                rows.append({"key": str(document.get("_id")), "values": values})
            return Page(columns, rows, total, max(0, int(offset)), "_id", True)

    def _typed(self, collection: Any, columns: list[str], values: dict[str, Any]) -> dict[str, Any]:
        sample = list(collection.find().limit(30))
        out = {}
        for column, value in values.items():
            real = resolve_column(columns, column) if column in columns or column.lower() in (c.lower() for c in columns) else str(column)
            if real == "_id":
                continue
            out[real] = coerce(value, [d.get(real) for d in sample])
        return out

    def insert(self, table: str, values: dict[str, Any], at: int | None = None) -> Any:
        with self._db() as db:
            _, collection = self._collection(db, table)
            columns = self._columns(collection)
            document = {k: v for k, v in self._typed(collection, columns, values).items() if v is not None}
            return str(collection.insert_one(document).inserted_id)

    def update(self, table: str, key: Any, values: dict[str, Any]) -> None:
        with self._db() as db:
            _, collection = self._collection(db, table)
            columns = self._columns(collection)
            typed = self._typed(collection, columns, values)
            if not typed:
                return
            result = collection.update_one({"_id": self._id(key)}, {"$set": typed})
            if result.matched_count == 0:
                raise DataError("Dokumen itu sudah tidak ada. Muat ulang tabelnya.")

    def delete(self, table: str, keys: list[Any]) -> int:
        with self._db() as db:
            _, collection = self._collection(db, table)
            return int(collection.delete_many({"_id": {"$in": [self._id(k) for k in keys]}}).deleted_count)

    def add_column(self, table: str, name: str) -> None:
        label = str(name).strip()
        if not label or label.startswith("$") or "." in label:
            raise DataError("Nama kolom tidak boleh kosong, diawali $, atau memuat titik")
        with self._db() as db:
            _, collection = self._collection(db, table)
            collection.update_many({label: {"$exists": False}}, {"$set": {label: None}})

    def rename_column(self, table: str, old: str, new: str) -> None:
        label = str(new).strip()
        if not label or label.startswith("$") or "." in label:
            raise DataError("Nama kolom tidak boleh kosong, diawali $, atau memuat titik")
        with self._db() as db:
            _, collection = self._collection(db, table)
            source = resolve_column(self._columns(collection), old)
            if source == "_id":
                raise DataError("Kolom _id tidak bisa diganti namanya")
            collection.update_many({source: {"$exists": True}}, {"$rename": {source: label}})

    def drop_column(self, table: str, name: str) -> None:
        with self._db() as db:
            _, collection = self._collection(db, table)
            source = resolve_column(self._columns(collection), name)
            if source == "_id":
                raise DataError("Kolom _id tidak bisa dihapus")
            collection.update_many({source: {"$exists": True}}, {"$unset": {source: ""}})

    def create_table(self, table: str, columns: list[str]) -> None:
        label = valid_name(table, "Nama koleksi")
        with self._db() as db:
            if label in db.list_collection_names():
                raise DataError(f"Koleksi '{label}' sudah ada")
            db.create_collection(label)

    def drop_table(self, table: str) -> None:
        with self._db() as db:
            real, _ = self._collection(db, table)
            db.drop_collection(real)
