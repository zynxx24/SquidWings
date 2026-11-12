"""The data layer: files, SQLite, Google Sheets (with a fake Google), MongoDB (fake), SQL dialects, access rules."""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote

import httpx
import pytest

from utils import datastore
from utils.datastore import access, registry
from utils.datastore.base import DataError, coerce, holds, normalize_where
from utils.datastore.files import (
    CsvFile,
    JsonFile,
    JsonLinesFile,
    XlsxFile,
    back_up,
    create_file,
    open_file,
)
from utils.datastore.mongo import MongoBackend
from utils.datastore.sheets import (
    SheetsBackend,
    column_letter,
    parse_service_account,
    parse_spreadsheet,
    sign_jwt,
)
from utils.datastore.sql import MysqlDialect, PostgresDialect, SqlBackend, sqlite_backend


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(registry, "DATA_DIR", tmp_path / "datastore")
    return tmp_path / "datastore"


def values(page: Any) -> list[dict[str, Any]]:
    return [row["values"] for row in page.rows]


# ── helpers ───────────────────────────────────────────────────────────────────

def test_coerce_keeps_the_type_the_column_already_has() -> None:
    assert coerce("50.000", [15000, 20000]) == 50000       # Indonesian thousands
    assert coerce("7,5", [1.5, 2.25]) == 7.5
    assert coerce("12", [1, 2]) == 12
    assert coerce("kaos", [1, 2]) == "kaos"                 # a word in a number column stays a word
    assert coerce("ya", [True]) is True
    assert coerce("", [1, 2]) is None
    assert coerce("007", ["A", "B"]) == "007"


def test_conditions_compare_numbers_as_numbers_and_text_without_case() -> None:
    cols = ["nama", "stok"]
    assert holds({"nama": "Kaos Hitam", "stok": 5}, normalize_where({"nama": "kaos hitam"}, cols)[0])
    assert holds({"stok": 12}, normalize_where([{"column": "stok", "op": ">", "value": 9}], cols)[0])
    assert not holds({"stok": "abc"}, normalize_where([{"column": "stok", "op": ">", "value": 1}], cols)[0])
    assert holds({"nama": "Tote Bag"}, normalize_where([{"column": "nama", "op": "contains", "value": "bag"}], cols)[0])
    with pytest.raises(DataError):
        normalize_where({"harga": 1}, cols)                  # unknown column
    with pytest.raises(DataError):
        normalize_where([{"column": "stok", "op": "drop", "value": 1}], cols)


# ── CSV ───────────────────────────────────────────────────────────────────────

def test_csv_round_trip_keeps_delimiter_and_edits_rows(tmp_path: Path) -> None:
    path = tmp_path / "produk.csv"
    path.write_text("kode;nama;harga\nA1;Kaos;50000\nB2;Topi;30000\n", encoding="utf-8")
    csv_file = open_file(path)
    assert isinstance(csv_file, CsvFile)
    assert csv_file.tables()[0].columns == ["kode", "nama", "harga"]
    csv_file.update("produk", 2, {"harga": "35000"})
    assert csv_file.insert("produk", {"kode": "C3", "nama": "Tas"}) == 3
    assert csv_file.delete("produk", [1]) == 1
    text = path.read_text(encoding="utf-8")
    assert text == "kode;nama;harga\nB2;Topi;35000\nC3;Tas;\n"
    csv_file.add_column("produk", "stok")
    csv_file.rename_column("produk", "stok", "jumlah")
    csv_file.drop_column("produk", "harga")
    assert path.read_text(encoding="utf-8").splitlines()[0] == "kode;nama;jumlah"
    with pytest.raises(DataError):
        csv_file.update("produk", 99, {"nama": "x"})
    with pytest.raises(DataError):
        csv_file.update("produk", 1, {"tidak_ada": "x"})


def test_csv_search_sort_and_where(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    path.write_text("nama,harga\nKaos Hitam,50000\nKaos Putih,45000\nTopi,30000\n", encoding="utf-8")
    csv_file = open_file(path)
    assert [v["nama"] for v in values(csv_file.read("p", search="kaos"))] == ["Kaos Hitam", "Kaos Putih"]
    assert [v["nama"] for v in values(csv_file.read("p", sort="harga"))] == ["Topi", "Kaos Putih", "Kaos Hitam"]
    page = csv_file.read("p", where=[{"column": "harga", "op": ">=", "value": 45000}], sort="harga", desc=True)
    assert [v["nama"] for v in values(page)] == ["Kaos Hitam", "Kaos Putih"] and page.total == 2
    assert csv_file.read("p", limit=1, offset=2).rows[0]["key"] == 3


def test_backup_is_made_before_the_first_change_and_not_repeated(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    path.write_text("a\n1\n", encoding="utf-8")
    csv_file = open_file(path)
    csv_file.update("p", 1, {"a": "2"})
    csv_file.update("p", 1, {"a": "3"})
    backups = list((tmp_path / ".backups").glob("p.csv.*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "a\n1\n"
    assert back_up(tmp_path / "missing.csv") is None


# ── Excel ─────────────────────────────────────────────────────────────────────

def test_xlsx_edits_cells_without_losing_styles_or_formulas(tmp_path: Path) -> None:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font

    path = tmp_path / "katalog.xlsx"
    workbook = Workbook()
    ws = workbook.active
    ws.title = "Produk"
    ws.append(["kode", "nama", "harga", "total"])
    ws.append(["A1", "Kaos", 50000, "=C2*2"])
    ws.append(["B2", "Topi", 30000, "=C3*2"])
    ws["A1"].font = Font(bold=True)
    workbook.create_sheet("Lain").append(["x"])
    workbook.save(path)

    xlsx = open_file(path)
    assert isinstance(xlsx, XlsxFile)
    assert {t.name for t in xlsx.tables()} == {"Produk", "Lain"}
    # No cached value was saved, so the formula text shows.
    assert values(xlsx.read("Produk"))[0]["total"] == "=C2*2"
    xlsx.update("Produk", 1, {"harga": "55.000"})            # Indonesian thousands into a number column
    xlsx.insert("Produk", {"kode": "C3", "nama": "Tas", "harga": "20000"}, at=2)
    xlsx.delete("Produk", [3])
    rows = values(xlsx.read("Produk"))
    assert [r["kode"] for r in rows] == ["A1", "C3"] and rows[0]["harga"] == 55000
    xlsx.add_column("Produk", "stok")
    xlsx.rename_column("Produk", "stok", "jumlah")
    saved = load_workbook(path)
    assert saved["Produk"]["A1"].font.bold is True
    assert saved["Produk"]["E1"].value == "jumlah"
    assert saved["Lain"]["A1"].value == "x"
    xlsx.create_table("Baru", ["a", "b"])
    xlsx.drop_table("Lain")
    assert {t.name for t in xlsx.tables()} == {"Produk", "Baru"}
    with pytest.raises(DataError):
        xlsx.read("Hilang")


def test_a_new_xlsx_has_its_header(tmp_path: Path) -> None:
    path = tmp_path / "baru.xlsx"
    create_file(path, ["nama", "harga"])
    xlsx = open_file(path)
    assert xlsx.tables()[0].columns == ["nama", "harga"]
    xlsx.insert("", {"nama": "Kaos", "harga": "1000"})
    assert values(xlsx.read(""))[0] == {"nama": "Kaos", "harga": "1000"}
    with pytest.raises(DataError):
        create_file(path, ["x"])                               # never overwrites


# ── JSON ──────────────────────────────────────────────────────────────────────

def test_json_list_and_multi_table_object(tmp_path: Path) -> None:
    flat = tmp_path / "a.json"
    flat.write_text(json.dumps([{"nama": "Kaos", "tags": ["a"]}, {"nama": "Topi"}]), encoding="utf-8")
    store = open_file(flat)
    assert isinstance(store, JsonFile)
    assert store.tables()[0].columns == ["nama", "tags"]
    store.update("a", 1, {"tags": '["x", "y"]'})              # a list cell takes JSON text back as a list
    assert json.loads(flat.read_text(encoding="utf-8"))[0]["tags"] == ["x", "y"]

    multi = tmp_path / "b.json"
    multi.write_text(json.dumps({"versi": 2, "produk": [{"a": 1}], "pesanan": [{"b": 2}]}), encoding="utf-8")
    store = open_file(multi)
    assert {t.name for t in store.tables()} == {"produk", "pesanan"}
    store.insert("produk", {"a": "5"})
    store.create_table("baru", ["z"])
    saved = json.loads(multi.read_text(encoding="utf-8"))
    assert saved["versi"] == 2 and saved["produk"][1]["a"] == 5 and saved["baru"] == []

    bad = tmp_path / "c.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(DataError):
        open_file(bad).tables()


def test_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    path.write_text('{"a": 1}\n{"a": 2, "b": "x"}\n', encoding="utf-8")
    store = open_file(path)
    assert isinstance(store, JsonLinesFile)
    assert store.tables()[0].columns == ["a", "b"]
    store.delete("log", [1])
    assert path.read_text(encoding="utf-8").strip() == '{"a": 2, "b": "x"}'


# ── SQLite ────────────────────────────────────────────────────────────────────

@pytest.fixture
def lite(tmp_path: Path) -> SqlBackend:
    path = tmp_path / "toko.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE produk (id INTEGER PRIMARY KEY, nama TEXT, harga INTEGER)")
        db.execute("CREATE TABLE log (pesan TEXT)")
        db.executemany("INSERT INTO produk (nama, harga) VALUES (?, ?)", [("Kaos 100%", 50000), ("Topi", 30000)])
        db.execute("INSERT INTO log VALUES ('halo')")
        db.execute("CREATE VIEW murah AS SELECT * FROM produk WHERE harga < 40000")
    return sqlite_backend(path)


def test_sqlite_reads_with_search_sort_and_literal_percent(lite: SqlBackend) -> None:
    tables = {t.name: t for t in lite.tables()}
    assert tables["produk"].key == "id" and tables["produk"].rows == 2
    assert tables["log"].key == "" and tables["log"].editable            # no primary key: rowid does the job
    assert not tables["murah"].editable                                  # a view cannot be edited
    assert [v["nama"] for v in values(lite.read("produk", search="100%"))] == ["Kaos 100%"]
    assert [v["nama"] for v in values(lite.read("produk", search="%"))] == ["Kaos 100%"]   # % is not a wildcard
    page = lite.read("produk", sort="harga", where={"nama": ["Topi", "Kaos 100%"]})
    assert [v["nama"] for v in values(page)] == ["Topi", "Kaos 100%"]


def test_sqlite_edits(lite: SqlBackend) -> None:
    lite.update("produk", 1, {"harga": 55000})
    lite.insert("produk", {"nama": "Tas", "harga": 20000})
    page = lite.read("produk", where={"nama": "Tas"})
    assert page.rows[0]["values"]["harga"] == 20000
    assert lite.delete("produk", [page.rows[0]["key"]]) == 1
    lite.update("log", lite.read("log").rows[0]["key"], {"pesan": "baru"})
    assert values(lite.read("log"))[0]["pesan"] == "baru"
    lite.add_column("produk", "stok")
    lite.rename_column("produk", "stok", "jumlah")
    lite.drop_column("produk", "jumlah")
    lite.create_table("pesanan", ["nama", "jumlah"])
    assert "pesanan" in {t.name for t in lite.tables()}
    lite.drop_table("pesanan")
    with pytest.raises(DataError):
        lite.update("murah", 1, {"nama": "x"})
    with pytest.raises(DataError):
        lite.drop_column("produk", "id")


def test_sql_never_builds_statements_from_unchecked_names(lite: SqlBackend) -> None:
    with pytest.raises(DataError):
        lite.read("produk; DROP TABLE produk")
    with pytest.raises(DataError):
        lite.read("produk", sort='nama"; DROP TABLE produk; --')
    with pytest.raises(DataError):
        lite.add_column("produk", 'x" TEXT); DROP TABLE log; --')
    with pytest.raises(DataError):
        lite.read("produk", where={"nama; DROP": 1})
    assert {t.name for t in lite.tables()} >= {"produk", "log"}


class Recorder:
    """A DB-API connection that records SQL and answers schema questions, to check each dialect's statements."""

    def __init__(self, tables: list[str], columns: list[tuple[str, ...]], count: int = 0) -> None:
        self.sql: list[tuple[str, list[Any]]] = []
        self.tables, self.columns, self.count = tables, columns, count
        self.rowcount = 1
        self.last: list[Any] = []

    def cursor(self) -> Recorder:
        return self

    def execute(self, sql: str, params: Any = None) -> None:
        self.sql.append((sql, list(params or [])))
        text = sql.lower()
        if "information_schema.tables" in text:
            self.last = [(t,) for t in self.tables]
        elif "key_column_usage" in text:
            self.last = [("id",)]
        elif "information_schema.columns" in text:
            self.last = list(self.columns)
        elif text.startswith("select count"):
            self.last = [(self.count,)]
        else:
            self.last = []

    def fetchall(self) -> list[Any]:
        return self.last

    def fetchone(self) -> Any:
        return self.last[0] if self.last else None

    def close(self) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


def test_postgres_and_mysql_statements() -> None:
    pg = Recorder(["produk"], [("id",), ("nama",)], 3)
    backend = SqlBackend(PostgresDialect(), lambda: pg, "PostgreSQL", "postgres")
    backend.read("produk", limit=10, offset=20, sort="nama", desc=True, where={"nama": "x"}, search="kaos")
    select = [s for s, _ in pg.sql if s.startswith("SELECT \"id\"")][0]
    assert 'FROM "produk"' in select and "ILIKE %s" in select and 'ORDER BY "nama" DESC' in select and "LIMIT 10 OFFSET 20" in select
    pg.sql.clear()
    backend.update("produk", 7, {"nama": "Baru"})
    assert pg.sql[-1] == ('UPDATE "produk" SET "nama" = %s WHERE "id" = %s', ["Baru", 7])

    my = Recorder(["produk"], [("id", "PRI"), ("nama", "")], 1)
    backend = SqlBackend(MysqlDialect(), lambda: my, "MySQL", "mysql")
    backend.update("produk", 7, {"nama": "Baru"})
    assert my.sql[-1] == ("UPDATE `produk` SET `nama` = %s WHERE `id` = %s", ["Baru", 7])
    backend.delete("produk", [1, 2])
    assert my.sql[-1][0] == "DELETE FROM `produk` WHERE `id` IN (%s, %s)"


def test_a_driver_that_is_not_installed_says_how_to_install_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from utils.datastore import sql

    monkeypatch.setattr(sql.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(DataError, match=r"pip install .psycopg\[binary\]"):
        sql.postgres_backend("postgresql://u:SANDI@h/db").tables()


def test_a_connection_error_never_shows_the_password() -> None:
    def boom() -> Any:
        raise ConnectionError("could not connect with password SANDI-RAHASIA")

    with pytest.raises(DataError) as error:
        SqlBackend(PostgresDialect(), boom, "PostgreSQL", "postgres").tables()
    assert "SANDI-RAHASIA" not in str(error.value) and "ConnectionError" in str(error.value)


# ── MongoDB (fake client) ─────────────────────────────────────────────────────

class FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self._skip = 0
        self._limit = 0

    @staticmethod
    def _match(doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, want in query.items():
            if key == "$and":
                if not all(FakeCollection._match(doc, q) for q in want):
                    return False
            elif key == "$or":
                if not any(FakeCollection._match(doc, q) for q in want):
                    return False
            elif isinstance(want, dict) and "$regex" in want:
                if not re.search(want["$regex"], str(doc.get(key, "")), re.I):
                    return False
            elif isinstance(want, dict) and "$in" in want:
                if doc.get(key) not in want["$in"]:
                    return False
            elif isinstance(want, dict) or doc.get(key) != want:
                return False
        return True

    def find(self, query: dict[str, Any] | None = None) -> FakeCollection:
        found = FakeCollection([d for d in self.docs if self._match(d, query or {})])
        return found

    def sort(self, column: str, direction: int) -> FakeCollection:
        return FakeCollection(sorted(self.docs, key=lambda d: str(d.get(column)), reverse=direction < 0))

    def skip(self, n: int) -> FakeCollection:
        out = FakeCollection(self.docs)
        out._skip = n
        return out

    def limit(self, n: int) -> FakeCollection:
        out = FakeCollection(self.docs)
        out._skip, out._limit = self._skip, n
        return out

    def __iter__(self) -> Any:
        docs = self.docs[self._skip:]
        return iter(docs[: self._limit] if self._limit else docs)

    def count_documents(self, query: dict[str, Any]) -> int:
        return len([d for d in self.docs if self._match(d, query)])

    def insert_one(self, doc: dict[str, Any]) -> Any:
        doc = {"_id": f"id{len(self.docs) + 1}", **doc}
        self.docs.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})

    def update_one(self, query: dict[str, Any], change: dict[str, Any]) -> Any:
        hit = [d for d in self.docs if self._match(d, query)]
        for d in hit[:1]:
            d.update(change["$set"])
        return type("R", (), {"matched_count": len(hit[:1])})

    def delete_many(self, query: dict[str, Any]) -> Any:
        before = len(self.docs)
        self.docs[:] = [d for d in self.docs if not self._match(d, query)]
        return type("R", (), {"deleted_count": before - len(self.docs)})


class FakeMongo:
    def __init__(self) -> None:
        self.collections = {"produk": FakeCollection([{"_id": "p1", "nama": "Kaos", "harga": 50000}, {"_id": "p2", "nama": "Topi"}])}

    def get_default_database(self) -> FakeMongo:
        return self

    def list_collection_names(self) -> list[str]:
        return list(self.collections)

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections[name]

    def close(self) -> None: ...


def test_mongodb_reads_and_edits_documents() -> None:
    mongo = FakeMongo()
    backend = MongoBackend("mongodb://u:SANDI@h/db", client_factory=lambda url: mongo)
    info = backend.tables()[0]
    assert info.name == "produk" and info.columns == ["_id", "nama", "harga"] and info.rows == 2
    page = backend.read("produk", search="kaos")
    assert page.rows == [{"key": "p1", "values": {"_id": "p1", "nama": "Kaos", "harga": 50000}}]
    backend.update("produk", "p2", {"harga": "30.000"})
    assert mongo.collections["produk"].docs[1]["harga"] == 30000
    key = backend.insert("produk", {"nama": "Tas"})
    assert backend.delete("produk", [key]) == 1
    with pytest.raises(DataError):
        backend.update("produk", "hilang", {"nama": "x"})


# ── Google Sheets (fake Google) ───────────────────────────────────────────────

def make_account() -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return {"client_email": "bot@proyek.iam.gserviceaccount.com", "private_key": pem, "project_id": "proyek"}


class FakeGoogle:
    """Just enough of the Sheets API: tokens, sheet list, ranges, value writes and structure changes."""

    def __init__(self) -> None:
        self.sheets: dict[str, dict[str, Any]] = {
            "Produk": {"id": 11, "rows": [["kode", "nama", "harga"], ["A1", "Kaos", 50000], ["B2", "Topi", 30000]]},
        }
        self.tokens = 0
        self.calls: list[tuple[str, str]] = []
        self.allowed = True

    def title_of(self, ref: str) -> tuple[str, str]:
        match = re.match(r"^'(.*)'!(.*)$", ref)
        assert match, ref
        return match.group(1).replace("''", "'"), match.group(2)

    def cells(self, title: str, spec: str) -> list[list[Any]]:
        rows = self.sheets[title]["rows"]
        if re.fullmatch(r"\d+:\d+", spec):
            first, last = (int(x) for x in spec.split(":"))
            return [list(r) for r in rows[first - 1: last]]
        block = re.fullmatch(r"[A-Z]+(\d+):[A-Z]+(\d+)", spec)
        assert block, spec
        return [list(r) for r in rows[int(block.group(1)) - 1: int(block.group(2))]]

    def put(self, title: str, spec: str, value: Any) -> None:
        match = re.fullmatch(r"([A-Z]+)(\d+)", spec)
        assert match, spec
        col = sum((ord(c) - 64) * 26 ** i for i, c in enumerate(reversed(match.group(1))))
        row = int(match.group(2))
        rows = self.sheets[title]["rows"]
        while len(rows) < row:
            rows.append([])
        while len(rows[row - 1]) < col:
            rows[row - 1].append("")
        rows[row - 1][col - 1] = value

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.calls.append((request.method, url.path + ("?" + url.query.decode() if url.query else "")))
        if url.host == "oauth2.googleapis.com":
            self.tokens += 1
            assert b"assertion=" in request.content
            return httpx.Response(200, json={"access_token": f"tok{self.tokens}", "expires_in": 3600})
        assert request.headers["authorization"].startswith("Bearer tok")
        if not self.allowed:
            return httpx.Response(403, json={"error": {"message": "The caller does not have permission"}})
        path = url.path.split("/v4/spreadsheets/SHEETID")[1]
        if request.method == "GET" and path == "":
            return httpx.Response(200, json={"properties": {"title": "Toko"}, "sheets": [
                {"properties": {"sheetId": s["id"], "title": t, "gridProperties": {"rowCount": max(len(s["rows"]), 100), "columnCount": 26}}}
                for t, s in self.sheets.items()]})
        if path == "/values:batchGet":
            ranges = parse_qs(url.query.decode())["ranges"]
            out = []
            for ref in ranges:
                title, spec = self.title_of(unquote(ref))
                out.append({"values": self.cells(title, spec)})
            return httpx.Response(200, json={"valueRanges": out})
        if path == "/values:batchUpdate":
            body = json.loads(request.content)
            for item in body["data"]:
                title, spec = self.title_of(item["range"])
                self.put(title, spec, item["values"][0][0])
                self.last_mode = body["valueInputOption"]
            return httpx.Response(200, json={})
        if path == ":batchUpdate":
            for req in json.loads(request.content)["requests"]:
                (kind, arg), = req.items()
                if kind == "insertDimension":
                    rng = arg["range"]
                    rows = next(s for s in self.sheets.values() if s["id"] == rng["sheetId"])["rows"]
                    rows.insert(rng["startIndex"], [])
                elif kind == "deleteDimension" and arg["range"]["dimension"] == "ROWS":
                    rng = arg["range"]
                    rows = next(s for s in self.sheets.values() if s["id"] == rng["sheetId"])["rows"]
                    del rows[rng["startIndex"]]
                elif kind == "deleteDimension":
                    rng = arg["range"]
                    rows = next(s for s in self.sheets.values() if s["id"] == rng["sheetId"])["rows"]
                    for r in rows:
                        if len(r) > rng["startIndex"]:
                            del r[rng["startIndex"]]
                elif kind == "addSheet":
                    self.sheets[arg["properties"]["title"]] = {"id": 50 + len(self.sheets), "rows": []}
                elif kind == "deleteSheet":
                    for t in [t for t, s in self.sheets.items() if s["id"] == arg["sheetId"]]:
                        del self.sheets[t]
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": {"message": f"unhandled {path}"}})


@pytest.fixture
def google() -> tuple[SheetsBackend, FakeGoogle]:
    from utils.datastore import sheets

    sheets._tokens.clear()
    fake = FakeGoogle()
    backend = SheetsBackend("SHEETID", make_account(), client_factory=lambda: httpx.Client(transport=httpx.MockTransport(fake.handle)))
    return backend, fake


def test_service_account_and_links() -> None:
    account = make_account()
    assert parse_service_account(json.dumps({"type": "service_account", **account}))["client_email"] == account["client_email"]
    with pytest.raises(DataError):
        parse_service_account('{"type": "authorized_user"}')
    with pytest.raises(DataError):
        parse_service_account("bukan json")
    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    assert parse_spreadsheet(f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid=42") == (sid, 42)
    assert parse_spreadsheet(sid) == (sid, None)
    with pytest.raises(DataError):
        parse_spreadsheet("https://example.com/x")
    assert column_letter(1) == "A" and column_letter(27) == "AA" and column_letter(703) == "AAA"
    jwt = sign_jwt(account, now=1000)
    assert jwt.count(".") == 2 and "PRIVATE" not in jwt


def test_sheets_read_and_edit(google: tuple[SheetsBackend, FakeGoogle]) -> None:
    backend, fake = google
    assert backend.tables()[0].columns == ["kode", "nama", "harga"]
    assert backend.read("Produk", search="topi").rows == [{"key": 2, "values": {"kode": "B2", "nama": "Topi", "harga": 30000}}]
    backend.update("Produk", 2, {"harga": "35.000", "nama": "Topi Baru"})
    assert fake.sheets["Produk"]["rows"][2] == ["B2", "Topi Baru", 35000]
    assert backend.insert("Produk", {"kode": "C3", "nama": "Tas", "harga": "20000"}) == 3
    assert fake.sheets["Produk"]["rows"][3] == ["C3", "Tas", 20000]
    backend.insert("Produk", {"kode": "Z0", "nama": "Sisip"}, at=1)
    assert fake.sheets["Produk"]["rows"][1][0] == "Z0"
    assert backend.delete("Produk", [1, 2]) == 2
    backend.add_column("Produk", "stok")
    backend.rename_column("Produk", "stok", "jumlah")
    assert fake.sheets["Produk"]["rows"][0][-1] == "jumlah"
    backend.drop_column("Produk", "jumlah")
    backend.create_table("Pesanan", ["nama", "jumlah"])
    assert fake.sheets["Pesanan"]["rows"] == [["nama", "jumlah"]]
    backend.drop_table("Pesanan")
    assert "Pesanan" not in fake.sheets
    assert fake.tokens == 1                      # one token served all the calls


def test_sheets_keep_a_code_as_text_and_a_formula_as_a_formula(google: tuple[SheetsBackend, FakeGoogle]) -> None:
    backend, fake = google
    backend.update("Produk", 1, {"kode": "007"})
    assert fake.last_mode == "RAW" and fake.sheets["Produk"]["rows"][1][0] == "007"
    backend.update("Produk", 1, {"nama": "=UPPER(B1)"})
    assert fake.last_mode == "USER_ENTERED"


def test_sheets_explain_a_missing_share(google: tuple[SheetsBackend, FakeGoogle]) -> None:
    backend, fake = google
    fake.allowed = False
    with pytest.raises(DataError, match=r"Bagikan.*bot@proyek.iam.gserviceaccount.com"):
        backend.tables()


# ── registry ──────────────────────────────────────────────────────────────────

def test_registry_names_files_and_refuses_bad_ones() -> None:
    source = datastore.create_local("Katalog Saya.xlsx", ["nama", "harga"], label="Katalog")
    assert source.name == "katalog-saya" and source.kind == "file"
    ref, _ = datastore.open_source("katalog-saya")
    ref.insert("", {"nama": "Kaos", "harga": "1000"})
    assert datastore.get_registry().find("KATALOG-SAYA") is not None
    with pytest.raises(DataError):
        datastore.create_local("katalog-saya.xlsx", ["a"])                     # file exists
    with pytest.raises(DataError):
        datastore.safe_filename("../../etc/passwd")
    with pytest.raises(DataError):
        datastore.safe_filename("virus.exe")
    assert datastore.safe_filename("../x/laporan penjualan.csv") == "laporan penjualan.csv"
    with pytest.raises(DataError, match="Yang terdaftar"):
        datastore.open_source("tidak-ada")
    datastore.get_registry().remove("katalog-saya", delete_file=True)
    assert not (registry.files_dir() / "Katalog Saya.xlsx").exists()


def test_import_checks_the_file_opens_and_never_overwrites() -> None:
    good = base64.b64encode(b"nama,harga\nKaos,1000\n").decode()
    first = datastore.import_local("produk.csv", good)
    second = datastore.import_local("produk.csv", good)
    assert first.location == "produk.csv" and second.location == "produk-2.csv" and second.name != first.name
    with pytest.raises(DataError):
        datastore.import_local("rusak.json", base64.b64encode(b"{nope").decode())
    assert not (registry.files_dir() / "rusak.json").exists()


def test_sqlite_can_be_created_and_used_from_the_registry() -> None:
    datastore.create_local("toko.sqlite", ["nama", "harga"], name="db")
    backend, _ = datastore.open_source("db")
    backend.insert("data", {"nama": "Kaos", "harga": "1000"})
    assert values(backend.read("data"))[0]["nama"] == "Kaos"


def test_shop_starter_is_made_once_and_keeps_existing_data() -> None:
    made = datastore.ensure_shop_starter()
    assert {s.name for s in made} == {"katalog", "pesanan"}
    katalog, _ = datastore.open_source("katalog")
    assert katalog.read("").total == 2
    katalog.update("", 1, {"stok": 3})
    assert datastore.ensure_shop_starter() == []
    assert values(datastore.open_source("katalog")[0].read(""))[0]["stok"] == 3


def test_online_sources_need_their_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    source = datastore.get_registry().add("sheets", f"https://docs.google.com/spreadsheets/d/{sid}/edit", name="gudang")
    assert source.location == sid and source.online
    with pytest.raises(DataError, match="Kunci Google belum diisi"):
        datastore.backend_for(source)
    pg = datastore.get_registry().add("postgres")
    with pytest.raises(DataError, match="URL PostgreSQL belum diisi"):
        datastore.backend_for(pg)
    with pytest.raises(DataError):
        datastore.get_registry().add("postgres")                           # twice


# ── access ────────────────────────────────────────────────────────────────────

def test_an_agent_only_reaches_what_it_was_granted() -> None:
    access.require("katalog", "write")                                      # no restriction in normal chat
    with access.restrict(access.Access({"katalog": "read", "pesanan": "append"})):
        access.require("katalog", "read")
        access.require("pesanan", "append")
        access.require("", "write")                                         # the agent's own workspace file
        with pytest.raises(DataError, match="tidak boleh mengubah"):
            access.require("katalog", "write")
        with pytest.raises(DataError, match="tidak boleh membaca"):
            access.require("pesanan", "read")                               # append-only: orders of others stay unseen
        with pytest.raises(DataError, match="tidak diberi akses"):
            access.require("rahasia", "read")


def test_a_customer_facing_agent_can_never_change_or_delete() -> None:
    with access.restrict(access.Access({"katalog": "write"}, public=True)):
        access.require("katalog", "read")
        access.require("katalog", "append")
        with pytest.raises(DataError):
            access.require("katalog", "write")
        assert access.defuse("=IMPORTXML(\"http://x\")").startswith("'=")
        assert access.defuse("@SUM(1)").startswith("'@")
        assert access.defuse("-cmd|calc").startswith("'-")
        assert access.defuse("-5000") == "-5000" and access.defuse("Budi") == "Budi"
        assert access.defuse("-1+cmd|' /C calc'!A0").startswith("'-")         # starts like a number, runs in Excel
        assert access.defuse("+1+HYPERLINK(\"http://x\")").startswith("'+")
        assert access.defuse("+62 812-3456-7890") == "+62 812-3456-7890"       # a phone number stays as typed
    with access.restrict(access.Access({"katalog": "write"})):
        assert access.defuse("=SUM(A1:A2)") == "=SUM(A1:A2)"                # the owner's own agent may write formulas
