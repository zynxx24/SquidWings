"""What every data source can do, whether it is a file on this computer or a table in the cloud.

A source is one thing that holds tables: a CSV (one table), an Excel or JSON file, a SQLite
file, a Google Sheets spreadsheet (a sheet is a table), or a PostgreSQL, MySQL or MongoDB
database. They all speak the same small language: list the tables, read a page of rows, insert,
update and delete rows, change columns. The web editor and the agent tools use only this.

A row is ``{"key": ..., "values": {column: value}}``. The key says which row: the primary key for
SQL and Mongo, the row number (first data row = 1) for files and spreadsheets. Values are always
plain JSON: numbers, text, true/false, null. Dates become ISO text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

MAX_PAGE = 200
MAX_ROWS = 100_000
MAX_SCAN = 5_000
MAX_CELL_CHARS = 20_000
_NAME_RE = re.compile(r"[^\W\d]\w{0,62}", re.UNICODE)


class DataError(ValueError):
    """A problem the user can act on. The message is safe to show and never holds a secret."""


@dataclass
class TableInfo:
    name: str
    columns: list[str]
    rows: int | None = None
    key: str = ""             # the key column's name; "" means the row number
    editable: bool = True     # False for a SQL table with no primary key

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "columns": self.columns, "rows": self.rows, "key": self.key, "editable": self.editable}


@dataclass
class Page:
    columns: list[str]
    rows: list[dict[str, Any]]
    total: int
    offset: int = 0
    key: str = ""
    editable: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"columns": self.columns, "rows": self.rows, "total": self.total, "offset": self.offset,
                "key": self.key, "editable": self.editable, "notes": self.notes}


@dataclass(frozen=True)
class Condition:
    column: str
    op: str
    value: Any

OPS = ("=", "!=", ">", ">=", "<", "<=", "contains", "startswith", "in")
_OP_ALIASES = {"==": "=", "eq": "=", "ne": "!=", "<>": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
               "like": "contains", "includes": "contains", "prefix": "startswith"}


def jsonable(value: Any) -> Any:
    """A cell as plain JSON."""
    if value is None or isinstance(value, bool | int | str):
        return value if not isinstance(value, str) else value[:MAX_CELL_CHARS]
    if isinstance(value, float):
        return None if value != value or value in (float("inf"), float("-inf")) else value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<{len(bytes(value))} byte>"
    if isinstance(value, dict | list | tuple):
        import json

        try:
            return json.dumps(value, ensure_ascii=False, default=str)[:MAX_CELL_CHARS]
        except (TypeError, ValueError):
            return str(value)[:MAX_CELL_CHARS]
    return str(value)[:MAX_CELL_CHARS]


def valid_name(name: str, what: str = "Nama") -> str:
    """A table or column name we create: letters, digits and underscores, so it is safe in SQL."""
    text = str(name or "").strip().replace(" ", "_")
    if not _NAME_RE.fullmatch(text):
        raise DataError(f"{what} hanya boleh huruf, angka, dan garis bawah, dan tidak diawali angka: {name!r}")
    return text


def clean_columns(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    cleaned = []
    for index, raw in enumerate(columns, start=1):
        label = str(raw if raw is not None else "").strip() or f"kolom_{index}"
        seen[label] = seen.get(label, 0) + 1
        cleaned.append(label if seen[label] == 1 else f"{label}_{seen[label]}")
    return cleaned


def resolve_column(columns: list[str], name: str) -> str:
    """The real column for a name that may differ in case or spaces, else a clear error."""
    if name in columns:
        return name
    wanted = str(name).strip().lower().replace(" ", "_")
    for column in columns:
        if column.lower().replace(" ", "_") == wanted:
            return column
    raise DataError(f"Kolom '{name}' tidak ada. Kolom yang ada: {', '.join(columns) or '(kosong)'}")


def normalize_where(where: Any, columns: list[str]) -> list[Condition]:
    """Turn what a person or a model wrote into checked conditions.

    ``{"sku": "A1"}`` means equals, a list value means "any of". A list of
    ``{"column", "op", "value"}`` covers the rest. Every column must really exist.
    """
    if not where:
        return []
    items: list[Condition] = []
    if isinstance(where, dict):
        for column, value in where.items():
            items.append(Condition(resolve_column(columns, str(column)), "in" if isinstance(value, list) else "=", value))
        return items
    if isinstance(where, list):
        for entry in where:
            if not isinstance(entry, dict) or "column" not in entry:
                raise DataError("Setiap syarat berbentuk {\"column\": ..., \"op\": ..., \"value\": ...}")
            op = _OP_ALIASES.get(str(entry.get("op") or "=").lower(), str(entry.get("op") or "=").lower())
            if op not in OPS:
                raise DataError(f"Operator '{entry.get('op')}' tidak dikenal. Pilih: {', '.join(OPS)}")
            items.append(Condition(resolve_column(columns, str(entry["column"])), op, entry.get("value")))
        return items
    raise DataError("where harus berupa objek {kolom: nilai} atau daftar syarat")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace(" ", "")
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return float(text)
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", text):
        return float(text.replace(".", ""))
    if re.fullmatch(r"-?\d+,\d+", text):
        return float(text.replace(",", "."))
    return None


def _equal(cell: Any, wanted: Any) -> bool:
    if cell is None or wanted is None:
        return cell in (None, "") and wanted in (None, "")
    left, right = _number(cell), _number(wanted)
    if left is not None and right is not None and not isinstance(cell, bool):
        return left == right
    return str(cell).strip().casefold() == str(wanted).strip().casefold()


def holds(values: dict[str, Any], condition: Condition) -> bool:
    """Whether one row meets one condition. Text compares without case, numbers as numbers."""
    cell = values.get(condition.column)
    op, wanted = condition.op, condition.value
    if op == "=":
        return _equal(cell, wanted)
    if op == "!=":
        return not _equal(cell, wanted)
    if op == "in":
        return any(_equal(cell, item) for item in (wanted if isinstance(wanted, list) else [wanted]))
    if op in ("contains", "startswith"):
        text = "" if cell is None else str(cell).casefold()
        needle = str(wanted).casefold()
        return needle in text if op == "contains" else text.startswith(needle)
    left, right = _number(cell), _number(wanted)
    if left is None or right is None:
        return False
    return {">": left > right, ">=": left >= right, "<": left < right, "<=": left <= right}[op]


def matches(values: dict[str, Any], conditions: list[Condition]) -> bool:
    return all(holds(values, c) for c in conditions)


def text_of(values: dict[str, Any]) -> str:
    return " ".join("" if v is None else str(v) for v in values.values())


def sort_key(value: Any) -> tuple[int, Any]:
    """Empty last, numbers before text, text without case."""
    if value is None or value == "":
        return (2, 0)
    number = _number(value)
    if number is not None:
        return (0, number)
    return (1, str(value).casefold())


def coerce(value: Any, samples: list[Any] | None = None) -> Any:
    """What a person typed, as the type the column already holds.

    The grid sends text. A column of numbers should stay numbers, so "50.000" into a column
    of whole numbers is 50000 (Indonesian thousands), and "7,5" is 7.5. Text stays text.
    Empty text clears the cell.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text == "":
        return None
    kinds = [s for s in (samples or []) if s not in (None, "")][:30]
    if not kinds:
        return text
    if all(isinstance(s, datetime | date) and not isinstance(s, bool) for s in kinds):
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return text
        return moment if any(isinstance(s, datetime) for s in kinds) else moment.date()
    if all(isinstance(s, bool) for s in kinds):
        low = text.lower()
        if low in ("true", "ya", "iya", "yes", "1"):
            return True
        if low in ("false", "tidak", "no", "0"):
            return False
        return text
    if all(isinstance(s, int | float) and not isinstance(s, bool) for s in kinds):
        whole = all(isinstance(s, int) or float(s).is_integer() for s in kinds)
        compact = text.replace(" ", "")
        if re.fullmatch(r"-?\d+", compact):
            return int(compact)
        if whole and re.fullmatch(r"-?\d{1,3}(\.\d{3})+", compact):
            return int(compact.replace(".", ""))
        if re.fullmatch(r"-?\d+\.\d+", compact):
            return float(compact)
        if re.fullmatch(r"-?\d+,\d+", compact):
            return float(compact.replace(",", "."))
        return text
    return text


class Backend:
    """Base for every source. Subclasses fill in what they can; the rest says so plainly."""

    kind = "data"
    label = "Data"
    # True when rows are found by position, so a person who edits while the data changes could hit the wrong row.
    positional = False

    def tables(self) -> list[TableInfo]:
        raise NotImplementedError

    def table(self, name: str) -> TableInfo:
        """One table by name (case-insensitive), or an error that lists the real ones."""
        found = self.tables()
        for info in found:
            if info.name == name:
                return info
        for info in found:
            if info.name.lower() == str(name).strip().lower():
                return info
        if not name and len(found) == 1:
            return found[0]
        names = ", ".join(t.name for t in found) or "(belum ada tabel)"
        raise DataError(f"Tabel '{name}' tidak ada. Tabel yang ada: {names}")

    def read(self, table: str, offset: int = 0, limit: int = 50, sort: str = "", desc: bool = False,
             search: str = "", where: Any = None) -> Page:
        raise NotImplementedError

    def all_rows(self, table: str, where: Any = None, search: str = "", cap: int = MAX_SCAN) -> Page:
        """Up to ``cap`` rows that meet the condition, from every page."""
        first = self.read(table, 0, MAX_PAGE, "", False, search, where)
        rows = list(first.rows)
        while len(rows) < min(first.total, cap):
            more = self.read(table, len(rows), MAX_PAGE, "", False, search, where).rows
            if not more:
                break
            rows.extend(more)
        first.rows = rows[:cap]
        return first

    def insert(self, table: str, values: dict[str, Any], at: int | None = None) -> Any:
        raise DataError(f"{self.label} belum bisa menambah baris")

    def update(self, table: str, key: Any, values: dict[str, Any]) -> None:
        raise DataError(f"{self.label} belum bisa mengubah baris")

    def delete(self, table: str, keys: list[Any]) -> int:
        raise DataError(f"{self.label} belum bisa menghapus baris")

    def add_column(self, table: str, name: str) -> None:
        raise DataError(f"{self.label} belum bisa menambah kolom")

    def rename_column(self, table: str, old: str, new: str) -> None:
        raise DataError(f"{self.label} belum bisa mengganti nama kolom")

    def drop_column(self, table: str, name: str) -> None:
        raise DataError(f"{self.label} belum bisa menghapus kolom")

    def create_table(self, table: str, columns: list[str]) -> None:
        raise DataError(f"{self.label} belum bisa membuat tabel")

    def drop_table(self, table: str) -> None:
        raise DataError(f"{self.label} belum bisa menghapus tabel")

    def check(self) -> str:
        """Prove the source can be reached. Returns a short sentence."""
        count = len(self.tables())
        return f"Tersambung, {count} tabel."

    # Shared by the sources that hold their rows in memory.
    @staticmethod
    def pick(rows: list[tuple[Any, dict[str, Any]]], columns: list[str], search: str, where: Any) -> list[tuple[Any, dict[str, Any]]]:
        """The rows that meet every condition and hold every word searched for."""
        conditions = normalize_where(where, columns)
        picked = [(k, v) for k, v in rows if matches(v, conditions)]
        if search.strip():
            needles = [w for w in search.casefold().split() if w]
            picked = [(k, v) for k, v in picked if all(n in text_of(v).casefold() for n in needles)]
        return picked

    @classmethod
    def window(cls, rows: list[tuple[Any, dict[str, Any]]], columns: list[str], offset: int, limit: int, sort: str,
               desc: bool, search: str, where: Any) -> tuple[list[tuple[Any, dict[str, Any]]], int]:
        picked = cls.pick(rows, columns, search, where)
        if sort:
            column = resolve_column(columns, sort)
            picked.sort(key=lambda item: sort_key(item[1].get(column)), reverse=desc)
        total = len(picked)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), MAX_PAGE))
        return picked[offset: offset + limit], total

    @staticmethod
    def rows_out(picked: list[tuple[Any, dict[str, Any]]], columns: list[str]) -> list[dict[str, Any]]:
        return [{"key": key, "values": {c: jsonable(values.get(c)) for c in columns}} for key, values in picked]
