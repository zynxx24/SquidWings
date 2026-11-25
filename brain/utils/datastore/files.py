"""Files on this computer: CSV, TSV, JSON, JSON Lines and Excel.

Every change is written to a temporary file next to the original and then moved over it, so a crash
never leaves half a file. Before the first change in a while the old file is copied to ``.backups/``
(the ten newest are kept), because an agent can edit a product list wrongly and the owner needs a way back.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .base import (
    MAX_ROWS,
    MAX_SCAN,
    Backend,
    DataError,
    Page,
    TableInfo,
    clean_columns,
    coerce,
    resolve_column,
    valid_name,
)

MAX_FILE_BYTES = 50 * 1024 * 1024
BACKUP_EVERY_SECONDS = 300
BACKUPS_KEPT = 10
CSV_SUFFIXES = {".csv", ".tsv"}
JSON_SUFFIXES = {".json", ".jsonl", ".ndjson"}
XLSX_SUFFIXES = {".xlsx", ".xlsm"}
FILE_SUFFIXES = CSV_SUFFIXES | JSON_SUFFIXES | XLSX_SUFFIXES

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(path.resolve()), threading.Lock())


def back_up(path: Path) -> Path | None:
    """Copy the file aside unless a copy was made in the last few minutes."""
    if not path.exists():
        return None
    folder = path.parent / ".backups"
    existing = sorted(folder.glob(f"{path.name}.*")) if folder.exists() else []
    if existing and time.time() - existing[-1].stat().st_mtime < BACKUP_EVERY_SECONDS:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, copy)
    for old in sorted(folder.glob(f"{path.name}.*"))[:-BACKUPS_KEPT]:
        old.unlink(missing_ok=True)
    return copy


def _replace(path: Path, write: Any) -> None:
    """Run ``write(temporary_path)``, then move the result over ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        write(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Table:
    def __init__(self, columns: list[str], rows: list[dict[str, Any]]) -> None:
        self.columns = columns
        self.rows = rows

    def samples(self, column: str) -> list[Any]:
        return [row.get(column) for row in self.rows[:60]]


class MemoryFile(Backend):
    """A file read whole, changed in memory and written back. Fine for lists of a few thousand rows."""

    kind = "file"
    positional = True
    typed = True          # False for CSV, where every cell is text

    def __init__(self, path: Path, label: str = "") -> None:
        self.path = Path(path)
        self.label = label or self.path.name

    # -- what a format provides
    def load(self) -> dict[str, Table]:
        raise NotImplementedError

    def dump(self, tables: dict[str, Table], target: Path) -> None:
        raise NotImplementedError

    # -- shared
    def _check_size(self) -> None:
        if self.path.exists() and self.path.stat().st_size > MAX_FILE_BYTES:
            raise DataError(f"{self.path.name} lebih dari 50 MB, terlalu besar untuk diedit di sini")

    def _tables(self) -> dict[str, Table]:
        if not self.path.exists():
            raise DataError(f"File {self.path.name} tidak ditemukan")
        self._check_size()
        return self.load()

    @contextlib.contextmanager
    def _edit(self) -> Iterator[dict[str, Table]]:
        with _lock_for(self.path):
            tables = self._tables()
            yield tables
            for table in tables.values():
                if len(table.rows) > MAX_ROWS:
                    raise DataError(f"Maksimal {MAX_ROWS} baris per tabel")
            back_up(self.path)
            _replace(self.path, lambda target: self.dump(tables, target))

    def _get(self, tables: dict[str, Table], name: str) -> tuple[str, Table]:
        for key in tables:
            if key == name or key.lower() == str(name).strip().lower():
                return key, tables[key]
        if not name and len(tables) == 1:
            key = next(iter(tables))
            return key, tables[key]
        raise DataError(f"Tabel '{name}' tidak ada. Tabel yang ada: {', '.join(tables) or '(kosong)'}")

    def tables(self) -> list[TableInfo]:
        return [TableInfo(n, t.columns, len(t.rows)) for n, t in self._tables().items()]

    def read(self, table: str, offset: int = 0, limit: int = 50, sort: str = "", desc: bool = False,
             search: str = "", where: Any = None) -> Page:
        _, found = self._get(self._tables(), table)
        keyed = [(i, row) for i, row in enumerate(found.rows, start=1)]
        picked, total = self.window(keyed, found.columns, offset, limit, sort, desc, search, where)
        return Page(found.columns, self.rows_out(picked, found.columns), total, max(0, int(offset)))

    def all_rows(self, table: str, where: Any = None, search: str = "", cap: int = MAX_SCAN) -> Page:
        _, found = self._get(self._tables(), table)
        chosen = self.pick([(i, row) for i, row in enumerate(found.rows, start=1)], found.columns, search, where)
        return Page(found.columns, self.rows_out(chosen[:cap], found.columns), len(chosen), 0)

    def _typed(self, table: Table, values: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for column, value in values.items():
            real = resolve_column(table.columns, column)
            if not self.typed and isinstance(value, str):
                out[real] = value
            else:
                out[real] = coerce(value, table.samples(real))
        return out

    def insert(self, table: str, values: dict[str, Any], at: int | None = None) -> int:
        with self._edit() as tables:
            _, found = self._get(tables, table)
            row = {c: ("" if not self.typed else None) for c in found.columns}
            row.update(self._typed(found, values))
            index = len(found.rows) if at is None else max(0, min(int(at) - 1, len(found.rows)))
            found.rows.insert(index, row)
            return index + 1

    def _row(self, found: Table, key: Any) -> dict[str, Any]:
        try:
            number = int(key)
        except (TypeError, ValueError):
            raise DataError(f"Nomor baris tidak valid: {key!r}") from None
        if not 1 <= number <= len(found.rows):
            raise DataError(f"Baris {number} tidak ada (jumlah baris sekarang {len(found.rows)}). Muat ulang tabelnya.")
        return found.rows[number - 1]

    def update(self, table: str, key: Any, values: dict[str, Any]) -> None:
        with self._edit() as tables:
            _, found = self._get(tables, table)
            self._row(found, key).update(self._typed(found, values))

    def delete(self, table: str, keys: list[Any]) -> int:
        with self._edit() as tables:
            _, found = self._get(tables, table)
            numbers = sorted({int(k) for k in keys}, reverse=True)
            for number in numbers:
                self._row(found, number)
            for number in numbers:
                del found.rows[number - 1]
            return len(numbers)

    def add_column(self, table: str, name: str) -> None:
        with self._edit() as tables:
            _, found = self._get(tables, table)
            label = str(name).strip()
            if not label:
                raise DataError("Nama kolom kosong")
            if label.lower() in (c.lower() for c in found.columns):
                raise DataError(f"Kolom '{label}' sudah ada")
            found.columns.append(label)
            for row in found.rows:
                row[label] = "" if not self.typed else None

    def rename_column(self, table: str, old: str, new: str) -> None:
        with self._edit() as tables:
            _, found = self._get(tables, table)
            real = resolve_column(found.columns, old)
            label = str(new).strip()
            if not label:
                raise DataError("Nama kolom kosong")
            if label.lower() != real.lower() and label.lower() in (c.lower() for c in found.columns):
                raise DataError(f"Kolom '{label}' sudah ada")
            found.columns[found.columns.index(real)] = label
            for row in found.rows:
                row[label] = row.pop(real, None)

    def drop_column(self, table: str, name: str) -> None:
        with self._edit() as tables:
            _, found = self._get(tables, table)
            real = resolve_column(found.columns, name)
            if len(found.columns) == 1:
                raise DataError("Tabel harus punya minimal satu kolom")
            found.columns.remove(real)
            for row in found.rows:
                row.pop(real, None)

    def create_table(self, table: str, columns: list[str]) -> None:
        raise DataError(f"{self.path.suffix} hanya punya satu tabel. Pakai file .xlsx atau .json berisi beberapa tabel.")

    def drop_table(self, table: str) -> None:
        raise DataError("File ini hanya punya satu tabel. Hapus filenya dari daftar sumber data bila tidak dipakai.")


def _rows_from(header: list[Any], body: list[list[Any]]) -> Table:
    columns = clean_columns([str(h) if h is not None else "" for h in header])
    width = max([len(columns), *(len(r) for r in body)])
    while len(columns) < width:
        columns.append(f"kolom_{len(columns) + 1}")
    rows = [{c: (r[i] if i < len(r) else "") for i, c in enumerate(columns)} for r in body]
    return Table(columns, rows)


class CsvFile(MemoryFile):
    typed = False

    def _delimiter(self, text: str) -> str:
        if self.path.suffix.lower() == ".tsv":
            return "\t"
        first = text.splitlines()[0] if text.strip() else ""
        return max((",", ";", "\t"), key=first.count) if any(first.count(d) for d in (",", ";", "\t")) else ","

    def load(self) -> dict[str, Table]:
        raw = self.path.read_bytes()
        self._bom = raw.startswith(b"\xef\xbb\xbf")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
        self._sep = self._delimiter(text)
        records = list(csv.reader(io.StringIO(text, newline=""), delimiter=self._sep))
        header = records[0] if records else []
        body = [r for r in records[1:] if any(cell.strip() for cell in r)]
        return {self.path.stem: _rows_from(header, body)}

    def dump(self, tables: dict[str, Table], target: Path) -> None:
        table = next(iter(tables.values()))
        sep = getattr(self, "_sep", ",")
        with open(target, "w", encoding="utf-8-sig" if getattr(self, "_bom", False) else "utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter=sep, lineterminator="\n")
            writer.writerow(table.columns)
            for row in table.rows:
                writer.writerow(["" if row.get(c) is None else row.get(c) for c in table.columns])


def _objects(items: Any, where: str) -> Table:
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise DataError(f"{where}: isinya harus daftar objek, misalnya [{{\"nama\": \"Kaos\", \"harga\": 50000}}]")
    columns: list[str] = []
    for item in items:
        for key in item:
            if key not in columns:
                columns.append(str(key))
    return Table(columns or ["kolom_1"], [{c: item.get(c) for c in columns} for item in items])


class JsonFile(MemoryFile):
    """A list of objects, or an object whose values are such lists (each one a table)."""

    def load(self) -> dict[str, Table]:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise DataError(f"{self.path.name} bukan JSON yang valid: {exc}") from None
        self._doc = doc
        if isinstance(doc, list):
            return {self.path.stem: _objects(doc, self.path.name)}
        if isinstance(doc, dict):
            lists = {k: v for k, v in doc.items() if isinstance(v, list) and all(isinstance(i, dict) for i in v)}
            if lists:
                return {str(k): _objects(v, str(k)) for k, v in lists.items()}
        raise DataError(f"{self.path.name}: bentuk JSON belum didukung. Pakai daftar objek, atau objek yang berisi daftar objek.")

    @staticmethod
    def _out(table: Table) -> list[dict[str, Any]]:
        return [{c: row.get(c) for c in table.columns} for row in table.rows]

    def dump(self, tables: dict[str, Table], target: Path) -> None:
        doc = getattr(self, "_doc", None)
        if isinstance(doc, list):
            out: Any = self._out(next(iter(tables.values())))
        else:
            out = dict(doc) if isinstance(doc, dict) else {}
            for name, table in tables.items():
                out[name] = self._out(table)
        target.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    def create_table(self, table: str, columns: list[str]) -> None:
        with self._edit() as tables:
            if not isinstance(self._doc, dict):
                raise DataError("File JSON berupa daftar hanya punya satu tabel")
            name = valid_name(table, "Nama tabel")
            if name in tables:
                raise DataError(f"Tabel '{name}' sudah ada")
            tables[name] = Table(clean_columns(columns or ["kolom_1"]), [])

    def drop_table(self, table: str) -> None:
        with self._edit() as tables:
            if not isinstance(self._doc, dict):
                raise DataError("File JSON berupa daftar hanya punya satu tabel")
            name, _ = self._get(tables, table)
            if len(tables) == 1:
                raise DataError("Tabel terakhir tidak bisa dihapus")
            del tables[name]
            self._doc.pop(name, None)

    def _typed(self, table: Table, values: dict[str, Any]) -> dict[str, Any]:
        out = super()._typed(table, values)
        # A cell that held a list or an object takes JSON text back as one.
        for column, value in out.items():
            samples = [s for s in table.samples(column) if s is not None]
            if isinstance(value, str) and samples and all(isinstance(s, dict | list) for s in samples):
                with contextlib.suppress(ValueError):
                    out[column] = json.loads(value)
        return out


class JsonLinesFile(JsonFile):
    def load(self) -> dict[str, Table]:
        items = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            if line.strip():
                try:
                    items.append(json.loads(line))
                except ValueError:
                    raise DataError(f"{self.path.name} baris {number} bukan JSON yang valid") from None
        self._doc = items
        return {self.path.stem: _objects(items, self.path.name)}

    def dump(self, tables: dict[str, Table], target: Path) -> None:
        table = next(iter(tables.values()))
        target.write_text("".join(json.dumps({c: row.get(c) for c in table.columns}, ensure_ascii=False, default=str) + "\n"
                                  for row in table.rows), encoding="utf-8")

    def create_table(self, table: str, columns: list[str]) -> None:
        raise DataError("File JSON Lines hanya punya satu tabel")

    def drop_table(self, table: str) -> None:
        raise DataError("File JSON Lines hanya punya satu tabel")


# ── Excel ────────────────────────────────────────────────────────────────────

def _sheet_table(formulas: Any, cached: Any) -> Table:
    """Header in row 1. A formula shows its last calculated value, or the formula when none was saved."""
    rows = list(formulas.iter_rows(values_only=True))
    values = list(cached.iter_rows(values_only=True)) if cached is not None else []
    while rows and all(c is None for c in rows[-1]):
        rows.pop()
    if not rows:
        return Table(["kolom_1"], [])
    header = list(rows[0])
    while header and header[-1] is None:
        header.pop()
    columns = clean_columns([str(h) if h is not None else "" for h in header])
    body = []
    for number, row in enumerate(rows[1:], start=1):
        cached_row = values[number] if number < len(values) else ()
        record = {}
        for index, column in enumerate(columns):
            cell = row[index] if index < len(row) else None
            if isinstance(cell, str) and cell.startswith("="):
                shown = cached_row[index] if index < len(cached_row) else None
                cell = shown if shown is not None else cell
            record[column] = cell
        body.append(record)
    return Table(columns, body)


class XlsxFile(Backend):
    """Excel workbooks, edited cell by cell so styles, widths and other sheets are left as they were."""

    kind = "file"
    positional = True

    def __init__(self, path: Path, label: str = "") -> None:
        self.path = Path(path)
        self.label = label or self.path.name

    def _open(self, data_only: bool = False) -> Any:
        from openpyxl import load_workbook

        if not self.path.exists():
            raise DataError(f"File {self.path.name} tidak ditemukan")
        if self.path.stat().st_size > MAX_FILE_BYTES:
            raise DataError(f"{self.path.name} lebih dari 50 MB, terlalu besar untuk diedit di sini")
        try:
            return load_workbook(self.path, data_only=data_only, keep_vba=self.path.suffix.lower() == ".xlsm")
        except Exception as exc:  # noqa: BLE001 - openpyxl raises many kinds for a damaged file
            raise DataError(f"{self.path.name} tidak bisa dibuka sebagai Excel ({type(exc).__name__})") from None

    def _read(self) -> dict[str, Table]:
        formulas, cached = self._open(), self._open(data_only=True)
        try:
            return {ws.title: _sheet_table(ws, cached[ws.title]) for ws in formulas.worksheets}
        finally:
            formulas.close()
            cached.close()

    @contextlib.contextmanager
    def _edit(self) -> Iterator[Any]:
        with _lock_for(self.path):
            workbook = self._open()
            try:
                yield workbook
                back_up(self.path)
                _replace(self.path, workbook.save)
            finally:
                workbook.close()

    @staticmethod
    def _sheet(workbook: Any, name: str) -> Any:
        for ws in workbook.worksheets:
            if ws.title == name or ws.title.lower() == str(name).strip().lower():
                return ws
        if not name and len(workbook.worksheets) == 1:
            return workbook.worksheets[0]
        raise DataError(f"Sheet '{name}' tidak ada. Sheet yang ada: {', '.join(w.title for w in workbook.worksheets)}")

    @staticmethod
    def _columns(ws: Any) -> list[str]:
        header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1), [])]
        while header and header[-1] is None:
            header.pop()
        return clean_columns([str(h) if h is not None else "" for h in header]) if header else []

    def tables(self) -> list[TableInfo]:
        return [TableInfo(n, t.columns, len(t.rows)) for n, t in self._read().items()]

    def _found(self, table: str) -> Table:
        tables = self._read()
        name = next((k for k in tables if k == table or k.lower() == str(table).strip().lower()), "")
        if not name:
            if not table and len(tables) == 1:
                name = next(iter(tables))
            else:
                raise DataError(f"Sheet '{table}' tidak ada. Sheet yang ada: {', '.join(tables)}")
        return tables[name]

    def all_rows(self, table: str, where: Any = None, search: str = "", cap: int = MAX_SCAN) -> Page:
        found = self._found(table)
        chosen = self.pick([(i, row) for i, row in enumerate(found.rows, start=1)], found.columns, search, where)
        return Page(found.columns, self.rows_out(chosen[:cap], found.columns), len(chosen), 0)

    def read(self, table: str, offset: int = 0, limit: int = 50, sort: str = "", desc: bool = False,
             search: str = "", where: Any = None) -> Page:
        found = self._found(table)
        keyed = [(i, row) for i, row in enumerate(found.rows, start=1)]
        picked, total = self.window(keyed, found.columns, offset, limit, sort, desc, search, where)
        return Page(found.columns, self.rows_out(picked, found.columns), total, max(0, int(offset)))

    def _convert(self, ws: Any, columns: list[str], values: dict[str, Any]) -> dict[int, Any]:
        """Column number (1-based) -> the value to write."""
        out = {}
        for column, value in values.items():
            real = resolve_column(columns, column)
            index = columns.index(real) + 1
            samples = [ws.cell(row=r, column=index).value for r in range(2, min(ws.max_row, 40) + 1)]
            out[index] = coerce(value, samples)
        return out

    def _check_row(self, ws: Any, key: Any) -> int:
        try:
            number = int(key)
        except (TypeError, ValueError):
            raise DataError(f"Nomor baris tidak valid: {key!r}") from None
        last = ws.max_row
        while last > 1 and all(c.value is None for c in ws[last]):
            last -= 1
        if not 1 <= number <= max(1, last - 1):
            raise DataError(f"Baris {number} tidak ada (jumlah baris sekarang {max(0, last - 1)}). Muat ulang tabelnya.")
        return number + 1

    def insert(self, table: str, values: dict[str, Any], at: int | None = None) -> int:
        with self._edit() as workbook:
            ws = self._sheet(workbook, table)
            columns = self._columns(ws)
            if not columns:
                raise DataError("Sheet ini belum punya baris judul kolom")
            last = ws.max_row
            while last > 1 and all(c.value is None for c in ws[last]):
                last -= 1
            count = last - 1
            if count >= MAX_ROWS:
                raise DataError(f"Maksimal {MAX_ROWS} baris per tabel")
            position = count + 1 if at is None else max(1, min(int(at), count + 1))
            if position <= count:
                ws.insert_rows(position + 1)
            for index, value in self._convert(ws, columns, values).items():
                ws.cell(row=position + 1, column=index).value = value
            return int(position)

    def update(self, table: str, key: Any, values: dict[str, Any]) -> None:
        with self._edit() as workbook:
            ws = self._sheet(workbook, table)
            row = self._check_row(ws, key)
            for index, value in self._convert(ws, self._columns(ws), values).items():
                ws.cell(row=row, column=index).value = value

    def delete(self, table: str, keys: list[Any]) -> int:
        with self._edit() as workbook:
            ws = self._sheet(workbook, table)
            rows = sorted({self._check_row(ws, k) for k in keys}, reverse=True)
            for row in rows:
                ws.delete_rows(row)
            return len(rows)

    def add_column(self, table: str, name: str) -> None:
        with self._edit() as workbook:
            ws = self._sheet(workbook, table)
            columns = self._columns(ws)
            label = str(name).strip()
            if not label:
                raise DataError("Nama kolom kosong")
            if label.lower() in (c.lower() for c in columns):
                raise DataError(f"Kolom '{label}' sudah ada")
            ws.cell(row=1, column=len(columns) + 1).value = label

    def rename_column(self, table: str, old: str, new: str) -> None:
        with self._edit() as workbook:
            ws = self._sheet(workbook, table)
            columns = self._columns(ws)
            real = resolve_column(columns, old)
            label = str(new).strip()
            if not label:
                raise DataError("Nama kolom kosong")
            if label.lower() != real.lower() and label.lower() in (c.lower() for c in columns):
                raise DataError(f"Kolom '{label}' sudah ada")
            ws.cell(row=1, column=columns.index(real) + 1).value = label

    def drop_column(self, table: str, name: str) -> None:
        with self._edit() as workbook:
            ws = self._sheet(workbook, table)
            columns = self._columns(ws)
            real = resolve_column(columns, name)
            if len(columns) == 1:
                raise DataError("Sheet harus punya minimal satu kolom")
            ws.delete_cols(columns.index(real) + 1)

    def create_table(self, table: str, columns: list[str]) -> None:
        with self._edit() as workbook:
            title = str(table).strip()[:31]
            if not title or any(ch in title for ch in "[]:*?/\\"):
                raise DataError("Nama sheet tidak boleh kosong atau memuat [ ] : * ? / \\")
            if title.lower() in (w.title.lower() for w in workbook.worksheets):
                raise DataError(f"Sheet '{title}' sudah ada")
            ws = workbook.create_sheet(title)
            for index, column in enumerate(clean_columns(columns or ["kolom_1"]), start=1):
                ws.cell(row=1, column=index).value = column

    def drop_table(self, table: str) -> None:
        with self._edit() as workbook:
            ws = self._sheet(workbook, table)
            if len(workbook.worksheets) == 1:
                raise DataError("Sheet terakhir tidak bisa dihapus")
            workbook.remove(ws)


def open_file(path: Path, label: str = "") -> Backend:
    suffix = Path(path).suffix.lower()
    if suffix in XLSX_SUFFIXES:
        return XlsxFile(path, label)
    if suffix in CSV_SUFFIXES:
        return CsvFile(path, label)
    if suffix in (".jsonl", ".ndjson"):
        return JsonLinesFile(path, label)
    if suffix == ".json":
        return JsonFile(path, label)
    raise DataError(f"Jenis file {suffix or '(tanpa ekstensi)'} belum didukung. Pakai {', '.join(sorted(FILE_SUFFIXES))} atau .sqlite")


def create_file(path: Path, columns: list[str], table: str = "") -> None:
    """A new, empty file of the type its name says, with the columns as the header."""
    path = Path(path)
    if path.exists():
        raise DataError(f"File {path.name} sudah ada")
    names = clean_columns(columns or ["kolom_1"])
    suffix = path.suffix.lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix in XLSX_SUFFIXES:
        from openpyxl import Workbook
        from openpyxl.styles import Font

        workbook = Workbook()
        ws = workbook.active
        ws.title = (table or path.stem)[:31] or "Sheet1"
        ws.append(names)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        _replace(path, workbook.save)
    elif suffix in CSV_SUFFIXES:
        _replace(path, lambda t: t.write_text(("\t" if suffix == ".tsv" else ",").join(names) + "\n", encoding="utf-8"))
    elif suffix in JSON_SUFFIXES:
        # A JSON list with no rows has no keys, so the columns would be lost: start with one empty row.
        blank = {c: None for c in names}
        text = json.dumps([blank], ensure_ascii=False, indent=2) + "\n" if suffix == ".json" else json.dumps(blank, ensure_ascii=False) + "\n"
        _replace(path, lambda t: t.write_text(text, encoding="utf-8"))
    else:
        raise DataError(f"Jenis file {suffix or '(tanpa ekstensi)'} belum didukung")


__all__ = ["FILE_SUFFIXES", "MemoryFile", "CsvFile", "JsonFile", "JsonLinesFile", "XlsxFile", "open_file", "create_file", "back_up"]
