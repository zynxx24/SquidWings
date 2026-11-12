"""SQL databases: a SQLite file, or PostgreSQL and MySQL/MariaDB in the cloud.

The three differ only in quoting, placeholders, how a schema is read, and a few statements, so one class
drives them through a small ``Dialect``. A table or column name from a caller is only ever used after it has
been matched against what the database itself reports, and values always travel as bound parameters.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .base import (
    MAX_PAGE,
    Backend,
    DataError,
    Page,
    TableInfo,
    jsonable,
    normalize_where,
    resolve_column,
    valid_name,
)

CONNECT_TIMEOUT = 10
INSTALL = {"psycopg": "psycopg[binary]", "pymysql": "pymysql", "pymongo": "pymongo"}
_ROWID = "__rowid__"


def need_driver(module: str) -> Any:
    if importlib.util.find_spec(module) is None:
        raise DataError(f'Driver {module} belum terpasang. Jalankan: pip install "{INSTALL.get(module, module)}"')
    return importlib.import_module(module)


class Dialect:
    name = "sqlite"
    placeholder = "?"
    auto_key = "INTEGER PRIMARY KEY AUTOINCREMENT"

    def q(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def text(self, column: str) -> str:
        return f"CAST({self.q(column)} AS TEXT)"

    def like(self, column: str) -> str:
        return f"{self.text(column)} LIKE {self.placeholder} ESCAPE '\\'"

    def list_tables(self, cursor: Any) -> list[str]:
        cursor.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name")
        return [r[0] for r in cursor.fetchall()]

    def describe(self, cursor: Any, table: str) -> tuple[list[str], str, bool]:
        """(columns, key column, whether the key is the real primary key)."""
        cursor.execute(f"PRAGMA table_info({self.q(table)})")
        info = cursor.fetchall()
        columns = [r[1] for r in info]
        cursor.execute("SELECT type FROM sqlite_master WHERE name = ?", (table,))
        kind = cursor.fetchone()
        if kind and kind[0] == "view":
            return columns, "", False
        keys = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
        if len(keys) == 1:
            return columns, keys[0], True
        return columns, _ROWID, False

    def key_select(self, key: str) -> str:
        return f"rowid AS {self.q(_ROWID)}" if key == _ROWID else ""

    def rename_column_sql(self, table: str, old: str, new: str) -> str:
        return f"ALTER TABLE {self.q(table)} RENAME COLUMN {self.q(old)} TO {self.q(new)}"

    def column_type(self) -> str:
        return "TEXT"


class PostgresDialect(Dialect):
    name = "postgres"
    placeholder = "%s"
    auto_key = "BIGSERIAL PRIMARY KEY"

    def text(self, column: str) -> str:
        return f"CAST({self.q(column)} AS TEXT)"

    def like(self, column: str) -> str:
        return f"{self.text(column)} ILIKE {self.placeholder}"

    def list_tables(self, cursor: Any) -> list[str]:
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() "
                       "AND table_type IN ('BASE TABLE', 'VIEW') ORDER BY table_name")
        return [r[0] for r in cursor.fetchall()]

    def describe(self, cursor: Any, table: str) -> tuple[list[str], str, bool]:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
                       "AND table_name = %s ORDER BY ordinal_position", (table,))
        columns = [r[0] for r in cursor.fetchall()]
        cursor.execute("SELECT kcu.column_name FROM information_schema.table_constraints tc "
                       "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
                       "AND tc.table_schema = kcu.table_schema WHERE tc.constraint_type = 'PRIMARY KEY' "
                       "AND tc.table_schema = current_schema() AND tc.table_name = %s", (table,))
        keys = [r[0] for r in cursor.fetchall()]
        return (columns, keys[0], True) if len(keys) == 1 else (columns, "", False)

    def key_select(self, key: str) -> str:
        return ""

    def rename_column_sql(self, table: str, old: str, new: str) -> str:
        return f"ALTER TABLE {self.q(table)} RENAME COLUMN {self.q(old)} TO {self.q(new)}"


class MysqlDialect(Dialect):
    name = "mysql"
    placeholder = "%s"
    auto_key = "BIGINT AUTO_INCREMENT PRIMARY KEY"

    def q(self, identifier: str) -> str:
        return "`" + identifier.replace("`", "``") + "`"

    def text(self, column: str) -> str:
        return f"CAST({self.q(column)} AS CHAR)"

    def like(self, column: str) -> str:
        return f"{self.text(column)} LIKE {self.placeholder}"

    def list_tables(self, cursor: Any) -> list[str]:
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY table_name")
        return [r[0] for r in cursor.fetchall()]

    def describe(self, cursor: Any, table: str) -> tuple[list[str], str, bool]:
        cursor.execute("SELECT column_name, column_key FROM information_schema.columns "
                       "WHERE table_schema = DATABASE() AND table_name = %s ORDER BY ordinal_position", (table,))
        rows = cursor.fetchall()
        columns = [r[0] for r in rows]
        keys = [r[0] for r in rows if str(r[1]).upper() == "PRI"]
        return (columns, keys[0], True) if len(keys) == 1 else (columns, "", False)

    def key_select(self, key: str) -> str:
        return ""

    def rename_column_sql(self, table: str, old: str, new: str) -> str:
        return f"ALTER TABLE {self.q(table)} RENAME COLUMN {self.q(old)} TO {self.q(new)}"


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlBackend(Backend):
    """One SQL database. ``connect`` returns a fresh DB-API connection; each call opens and closes its own."""

    def __init__(self, dialect: Dialect, connect: Callable[[], Any], label: str, kind: str) -> None:
        self.dialect = dialect
        self._connect = connect
        self.label = label
        self.kind = kind

    @contextlib.contextmanager
    def _cursor(self, write: bool = False) -> Iterator[Any]:
        try:
            connection = self._connect()
        except DataError:
            raise
        except Exception as exc:  # noqa: BLE001 - a driver's message can hold the password, so only its type is shown
            raise DataError(f"Tidak bisa tersambung ke {self.label} ({type(exc).__name__}). Cek URL-nya di Kredensial.") from None
        try:
            cursor = connection.cursor()
            try:
                yield cursor
                if write:
                    connection.commit()
            except DataError:
                connection.rollback()
                raise
            except Exception as exc:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    connection.rollback()
                raise DataError(f"{self.label} menolak perintah ({type(exc).__name__}): {str(exc)[:160]}") from None
            finally:
                with contextlib.suppress(Exception):
                    cursor.close()
        finally:
            with contextlib.suppress(Exception):
                connection.close()

    def _info(self, cursor: Any, table: str) -> tuple[str, list[str], str, bool]:
        names = self.dialect.list_tables(cursor)
        real = next((n for n in names if n == table), None) or next((n for n in names if n.lower() == str(table).strip().lower()), None)
        if real is None:
            if not table and len(names) == 1:
                real = names[0]
            else:
                raise DataError(f"Tabel '{table}' tidak ada. Tabel yang ada: {', '.join(names) or '(belum ada tabel)'}")
        columns, key, real_key = self.dialect.describe(cursor, real)
        return real, columns, key, real_key

    def tables(self) -> list[TableInfo]:
        with self._cursor() as cursor:
            out = []
            for name in self.dialect.list_tables(cursor):
                columns, key, real_key = self.dialect.describe(cursor, name)
                cursor.execute(f"SELECT COUNT(*) FROM {self.dialect.q(name)}")
                out.append(TableInfo(name, columns, int(cursor.fetchone()[0]), key if real_key else "", bool(key)))
            return out

    def _where(self, columns: list[str], where: Any, search: str) -> tuple[str, list[Any]]:
        d = self.dialect
        clauses: list[str] = []
        params: list[Any] = []
        for cond in normalize_where(where, columns):
            col = d.q(cond.column)
            if cond.op == "in":
                items = cond.value if isinstance(cond.value, list) else [cond.value]
                if not items:
                    clauses.append("1 = 0")
                    continue
                clauses.append(f"{col} IN ({', '.join([d.placeholder] * len(items))})")
                params.extend(items)
            elif cond.op in ("contains", "startswith"):
                pattern = _escape_like(str(cond.value)) + "%" if cond.op == "startswith" else "%" + _escape_like(str(cond.value)) + "%"
                clauses.append(d.like(cond.column))
                params.append(pattern)
            elif cond.value is None and cond.op in ("=", "!="):
                clauses.append(f"{col} IS {'NOT ' if cond.op == '!=' else ''}NULL")
            else:
                clauses.append(f"{col} {cond.op} {d.placeholder}")
                params.append(cond.value)
        for word in [w for w in search.split() if w]:
            clauses.append("(" + " OR ".join(d.like(c) for c in columns) + ")")
            params.extend([f"%{_escape_like(word)}%"] * len(columns))
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def read(self, table: str, offset: int = 0, limit: int = 50, sort: str = "", desc: bool = False,
             search: str = "", where: Any = None) -> Page:
        d = self.dialect
        with self._cursor() as cursor:
            name, columns, key, real_key = self._info(cursor, table)
            clause, params = self._where(columns, where, search)
            order = f" ORDER BY {d.q(resolve_column(columns, sort))} {'DESC' if desc else 'ASC'}" if sort else (
                f" ORDER BY {d.q(key)}" if key and key != _ROWID else "")
            limit = max(1, min(int(limit), MAX_PAGE))
            extra = d.key_select(key)
            select = ", ".join([*(d.q(c) for c in columns), *([extra] if extra else [])])
            cursor.execute(f"SELECT COUNT(*) FROM {d.q(name)}{clause}", params)
            total = int(cursor.fetchone()[0])
            cursor.execute(f"SELECT {select} FROM {d.q(name)}{clause}{order} LIMIT {limit} OFFSET {max(0, int(offset))}", params)
            rows = []
            for record in cursor.fetchall():
                values = {c: jsonable(record[i]) for i, c in enumerate(columns)}
                row_key = jsonable(record[len(columns)]) if extra else (values.get(key) if key else None)
                rows.append({"key": row_key, "values": values})
            return Page(columns, rows, total, max(0, int(offset)), key if real_key else "", bool(key))

    def _need_key(self, key: str, name: str) -> None:
        if not key:
            raise DataError(f"Tabel '{name}' tidak punya primary key, jadi barisnya tidak bisa diubah dari sini.")

    def insert(self, table: str, values: dict[str, Any], at: int | None = None) -> Any:
        d = self.dialect
        with self._cursor(write=True) as cursor:
            name, columns, key, _ = self._info(cursor, table)
            real = {resolve_column(columns, c): v for c, v in values.items() if v is not None and v != ""}
            if real:
                cursor.execute(f"INSERT INTO {d.q(name)} ({', '.join(d.q(c) for c in real)}) "
                               f"VALUES ({', '.join([d.placeholder] * len(real))})", list(real.values()))
            else:
                cursor.execute(f"INSERT INTO {d.q(name)} DEFAULT VALUES" if d.name != "mysql" else f"INSERT INTO {d.q(name)} () VALUES ()")
            if d.name == "sqlite":
                return cursor.lastrowid
            if key and key in real:
                return real[key]
            return getattr(cursor, "lastrowid", None) or None

    def update(self, table: str, key: Any, values: dict[str, Any]) -> None:
        d = self.dialect
        with self._cursor(write=True) as cursor:
            name, columns, key_col, _ = self._info(cursor, table)
            self._need_key(key_col, name)
            real = {resolve_column(columns, c): (None if v == "" else v) for c, v in values.items()}
            if not real:
                return
            target = "rowid" if key_col == _ROWID else d.q(key_col)
            cursor.execute(f"UPDATE {d.q(name)} SET {', '.join(d.q(c) + ' = ' + d.placeholder for c in real)} "
                           f"WHERE {target} = {d.placeholder}", [*real.values(), key])
            if cursor.rowcount == 0:
                raise DataError("Baris itu sudah tidak ada. Muat ulang tabelnya.")

    def delete(self, table: str, keys: list[Any]) -> int:
        d = self.dialect
        if not keys:
            return 0
        with self._cursor(write=True) as cursor:
            name, _, key_col, _ = self._info(cursor, table)
            self._need_key(key_col, name)
            target = "rowid" if key_col == _ROWID else d.q(key_col)
            cursor.execute(f"DELETE FROM {d.q(name)} WHERE {target} IN ({', '.join([d.placeholder] * len(keys))})", list(keys))
            return int(cursor.rowcount)

    def add_column(self, table: str, name: str) -> None:
        d = self.dialect
        label = valid_name(name, "Nama kolom")
        with self._cursor(write=True) as cursor:
            real, columns, _, _ = self._info(cursor, table)
            if label.lower() in (c.lower() for c in columns):
                raise DataError(f"Kolom '{label}' sudah ada")
            cursor.execute(f"ALTER TABLE {d.q(real)} ADD COLUMN {d.q(label)} {d.column_type()}")

    def rename_column(self, table: str, old: str, new: str) -> None:
        label = valid_name(new, "Nama kolom")
        with self._cursor(write=True) as cursor:
            real, columns, _, _ = self._info(cursor, table)
            source = resolve_column(columns, old)
            if label.lower() != source.lower() and label.lower() in (c.lower() for c in columns):
                raise DataError(f"Kolom '{label}' sudah ada")
            cursor.execute(self.dialect.rename_column_sql(real, source, label))

    def drop_column(self, table: str, name: str) -> None:
        d = self.dialect
        with self._cursor(write=True) as cursor:
            real, columns, key, _ = self._info(cursor, table)
            source = resolve_column(columns, name)
            if source == key:
                raise DataError("Kolom kunci (primary key) tidak bisa dihapus")
            cursor.execute(f"ALTER TABLE {d.q(real)} DROP COLUMN {d.q(source)}")

    def create_table(self, table: str, columns: list[str]) -> None:
        d = self.dialect
        label = valid_name(table, "Nama tabel")
        names = [valid_name(c, "Nama kolom") for c in columns if str(c).strip()]
        with self._cursor(write=True) as cursor:
            if label.lower() in (t.lower() for t in d.list_tables(cursor)):
                raise DataError(f"Tabel '{label}' sudah ada")
            body = ", ".join([f"id {d.auto_key}", *(f"{d.q(c)} {d.column_type()}" for c in names if c.lower() != "id")])
            cursor.execute(f"CREATE TABLE {d.q(label)} ({body})")

    def drop_table(self, table: str) -> None:
        with self._cursor(write=True) as cursor:
            real, _, _, _ = self._info(cursor, table)
            cursor.execute(f"DROP TABLE {self.dialect.q(real)}")


def sqlite_backend(path: Path, label: str = "") -> SqlBackend:
    path = Path(path)

    def connect() -> Any:
        if not path.exists():
            raise DataError(f"File {path.name} tidak ditemukan")
        connection = sqlite3.connect(path, timeout=CONNECT_TIMEOUT)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    return SqlBackend(Dialect(), connect, label or path.name, "sqlite")


def postgres_backend(url: str, label: str = "PostgreSQL") -> SqlBackend:
    def connect() -> Any:
        return need_driver("psycopg").connect(url, connect_timeout=CONNECT_TIMEOUT)

    return SqlBackend(PostgresDialect(), connect, label, "postgres")


def mysql_backend(url: str, label: str = "MySQL") -> SqlBackend:
    def connect() -> Any:
        parsed = urlparse(url)
        return need_driver("pymysql").connect(
            host=parsed.hostname, port=parsed.port or 3306, user=unquote(parsed.username or ""), password=unquote(parsed.password or ""),
            database=parsed.path.lstrip("/"), connect_timeout=CONNECT_TIMEOUT, charset="utf8mb4")

    return SqlBackend(MysqlDialect(), connect, label, "mysql")
