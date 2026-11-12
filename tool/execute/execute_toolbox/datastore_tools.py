"""Read and edit tables: Excel, CSV, JSON, SQLite on this computer, and Google Sheets, PostgreSQL, MySQL, MongoDB online.

A source is a name from the Data tab (``katalog``) or a data file in the workspace (``data/produk.csv``). A team agent
only reaches the sources its node was granted, with the rights it was granted; an agent talking to customers can never
change or delete a row, and never sees a column the owner hid (see ``utils.datastore.access``).

Every change returns what it did, so the agent can tell the owner exactly which rows moved.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from utils import datastore
from utils.datastore import DataError, access

from .catalog import group, param, spec

group("datastore", "Database and spreadsheets: read and edit",
      "database, basis data, data source, sumber data, katalog, produk, stok, harga, pesanan, order, inventory, tabel, "
      "baris, kolom, xlsx, excel, csv, json, sqlite, google sheets, spreadsheet, sheet, postgres, mysql, mongodb, toko")

_MAX_OUT_ROWS = 100
_MAX_CELL = 300
_MAX_WRITE_ROWS = 200


def _open(source: str, workspace_path: str | None) -> tuple[Any, str]:
    return datastore.open_source(str(source or "").strip(), workspace_path)


def _hidden(name: str, columns: list[str]) -> set[str]:
    return datastore.hidden_columns(name, columns)


def _cell(value: Any) -> Any:
    return value[:_MAX_CELL] + "…" if isinstance(value, str) and len(value) > _MAX_CELL else value


def _shape(rows: list[dict[str, Any]], hide: set[str], only: list[str] | None = None) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        values = {c: _cell(v) for c, v in row["values"].items() if c not in hide and (not only or c in only)}
        out.append({"key": row["key"], "values": values})
    return out


def _conditions(where: dict[str, Any] | None, conditions: list[dict[str, Any]] | None) -> Any:
    """``where`` ({column: value}) and ``conditions`` ([{column, op, value}]) together, as one list."""
    merged: list[dict[str, Any]] = [{"column": c, "op": "in" if isinstance(v, list) else "=", "value": v} for c, v in (where or {}).items()]
    merged.extend(conditions or [])
    return merged or None


def _check_columns(name: str, columns: list[str], used: list[str]) -> None:
    """A hidden column cannot even be named, so it cannot be probed with a condition or a sort."""
    hidden = _hidden(name, columns)
    for column in used:
        if column in hidden or column.lower() in {h.lower() for h in hidden}:
            raise DataError(f"Kolom '{column}' tidak tersedia untuk agent ini.")


def _used(where: Any, sort: str = "", only: list[str] | None = None) -> list[str]:
    names = [str(c.get("column")) for c in (where or []) if isinstance(c, dict)]
    return [*names, *([sort] if sort else []), *(only or [])]


@spec("db_sources", "List the data sources this agent can use: name, kind (file, google sheets, postgres, mysql, mongodb) and "
      "whether it is online. Start here, then db_schema to see a source's tables and columns.",
      "datastore", [], keywords="sumber data, data source, database apa, daftar database, katalog saya", lead=True)
def db_sources() -> dict[str, Any]:
    granted = access.current()
    out = []
    for source in datastore.get_registry().list():
        if granted is not None and source.name not in granted.grants:
            continue
        item = {"source": source.name, "label": source.label, "kind": source.kind, "online": source.online}
        if granted is not None:
            item["rights"] = sorted(granted.rights(source.name))
        out.append(item)
    return {"sources": out, "hint": "Pakai nama 'source' di db_schema, db_read, db_search, db_append, db_update, db_delete."}


@spec("db_schema", "Show the tables (sheets, collections) of a data source with their columns and row counts. With a table "
      "name it also shows the first rows. Call before reading or editing so column names are exact.",
      "datastore", [param("source", description="Source name from db_sources, or a data file in the workspace."),
                    param("table", description="Table or sheet name (optional).", required=False, default="")],
      keywords="lihat kolom, nama kolom, struktur tabel, schema, kolom apa saja, isi tabel", lead=True, workspace=True)
def db_schema(source: str, table: str = "", workspace_path: str | None = None) -> dict[str, Any]:
    backend, name = _open(source, workspace_path)
    # An agent that may only add rows still has to learn the columns, but it must not see what is in them.
    access.require_any(name, ("read", "append"))
    can_read = access.allows(name, "read")
    tables = []
    for info in backend.tables():
        if table and info.name.lower() != table.strip().lower():
            continue
        hide = _hidden(name, info.columns)
        tables.append({"table": info.name, "columns": [c for c in info.columns if c not in hide],
                       "rows": info.rows if can_read else None, "key": info.key or "nomor baris", "editable": info.editable})
    if table and not tables:
        raise DataError(f"Tabel '{table}' tidak ada. Tabel yang ada: {', '.join(t.name for t in backend.tables())}")
    out: dict[str, Any] = {"source": source, "kind": backend.kind, "tables": tables}
    if table and can_read:
        page = backend.read(table, 0, 3)
        out["sample"] = _shape(page.rows, _hidden(name, page.columns))
    return out


@spec("db_read", "Read rows from a table. Filter with where ({\"kode\": \"A1\"}) or conditions "
      "([{\"column\": \"stok\", \"op\": \">\", \"value\": 0}]; ops: = != > >= < <= contains startswith in), and/or search words "
      "that must all appear in the row. Returns each row's key and values. Use db_search to find a product by name.",
      "datastore", [param("source", description="Source name."), param("table", description="Table or sheet.", required=False, default=""),
                    param("where", "obj", "Column: value pairs that must all match.", required=False),
                    param("conditions", "list", "List of {column, op, value} conditions.", required=False, items="obj"),
                    param("search", description="Words that must all appear somewhere in the row.", required=False, default=""),
                    param("columns", "list", "Only these columns.", required=False),
                    param("sort", description="Column to sort by.", required=False, default=""),
                    param("desc", "bool", "Sort descending.", required=False, default=False),
                    param("limit", "int", "Rows to return (max 100).", required=False, default=20),
                    param("offset", "int", "Rows to skip.", required=False, default=0)],
      keywords="baca tabel, baca data, read table, lihat stok, cek harga, cek stok, tampilkan data, filter data tabel",
      lead=True, workspace=True)
def db_read(source: str, table: str = "", where: dict[str, Any] | None = None, conditions: list[dict[str, Any]] | None = None,
            search: str = "", columns: list[str] | None = None, sort: str = "", desc: bool = False, limit: int = 20,
            offset: int = 0, workspace_path: str | None = None) -> dict[str, Any]:
    backend, name = _open(source, workspace_path)
    access.require(name, "read")
    merged = _conditions(where, conditions)
    info = backend.table(table)
    _check_columns(name, info.columns, _used(merged, sort, columns))
    hide = _hidden(name, info.columns)
    limit = max(1, min(int(limit), _MAX_OUT_ROWS))
    if hide and search.strip():
        # The backend would also look inside hidden columns, so search only what may be seen.
        found = backend.all_rows(info.name, merged)
        needles = search.casefold().split()
        keep = [r for r in found.rows if all(n in " ".join(str(v) for c, v in r["values"].items() if c not in hide).casefold() for n in needles)]
        if sort:
            from utils.datastore.base import resolve_column, sort_key
            column = resolve_column(info.columns, sort)
            keep.sort(key=lambda r: sort_key(r["values"].get(column)), reverse=desc)
        page_rows, total = keep[int(offset): int(offset) + limit], len(keep)
    else:
        page = backend.read(info.name, int(offset), limit, sort, bool(desc), search, merged)
        page_rows, total = page.rows, page.total
    return {"source": source, "table": info.name, "total": total, "returned": len(page_rows),
            "rows": _shape(page_rows, hide, columns), "more": total > int(offset) + len(page_rows)}


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold())


@spec("db_search", "Find rows by free words, the way a customer asks: 'kaos hitam L', 'tote bag'. Ranks rows by how many words "
      "match (small typos still count) and returns the best few. Best tool to look up a product, price or stock.",
      "datastore", [param("source", description="Source name."), param("query", description="Words to look for."),
                    param("table", description="Table or sheet.", required=False, default=""),
                    param("limit", "int", "Best rows to return (max 20).", required=False, default=6)],
      keywords="cari produk, cari barang, cari di katalog, ada nggak, tersedia, produk apa, cari data", lead=True, workspace=True)
def db_search(source: str, query: str, table: str = "", limit: int = 6, workspace_path: str | None = None) -> dict[str, Any]:
    backend, name = _open(source, workspace_path)
    access.require(name, "read")
    info = backend.table(table)
    hide = _hidden(name, info.columns)
    tokens = list(dict.fromkeys(_words(query)))
    if not tokens:
        raise DataError("Tulis kata yang dicari")
    scored = []
    for row in backend.all_rows(info.name).rows:
        text = " ".join(str(v) for c, v in row["values"].items() if c not in hide and v is not None)
        present = set(_words(text))
        score = 0.0
        for token in tokens:
            if token in present or any(w.startswith(token) for w in present if len(token) >= 3):
                score += 1
            elif len(token) >= 4 and difflib.get_close_matches(token, present, n=1, cutoff=0.8):
                score += 0.6
        if score:
            scored.append((score + (0.5 if score >= len(tokens) else 0), row))
    scored.sort(key=lambda item: -item[0])
    best = [row for _, row in scored[: max(1, min(int(limit), 20))]]
    return {"source": source, "table": info.name, "query": query, "found": len(scored), "rows": _shape(best, hide),
            "note": "" if best else "Tidak ada yang cocok. Coba kata yang lebih umum, atau minta pelanggan memperjelas."}


@spec("db_append", "Add rows to the end of a table. Give each row as {column: value}. Max 50 rows per call. Use for new orders, "
      "new products, log entries.",
      "datastore", [param("source", description="Source name."), param("rows", "list", "Rows to add, each {column: value}.", items="obj"),
                    param("table", description="Table or sheet.", required=False, default="")],
      keywords="tambah baris, tambah produk, catat pesanan, simpan pesanan, tambahkan data, input data, append row",
      lead=True, workspace=True, read_only=False)
def db_append(source: str, rows: list[dict[str, Any]], table: str = "", workspace_path: str | None = None) -> dict[str, Any]:
    backend, name = _open(source, workspace_path)
    access.require(name, "append")
    if not rows or not all(isinstance(r, dict) and r for r in rows):
        raise DataError("rows harus berisi minimal satu baris berbentuk {kolom: nilai}")
    if len(rows) > 50:
        raise DataError("Maksimal 50 baris sekali tambah")
    info = backend.table(table)
    _check_columns(name, info.columns, [c for r in rows for c in r])
    added = []
    for row in rows:
        added.append(backend.insert(info.name, {c: access.defuse(v) for c, v in row.items()}))
    return {"source": source, "table": info.name, "added": len(added), "keys": added}


def _targets(backend: Any, name: str, table: str, where: dict[str, Any] | None, conditions: list[dict[str, Any]] | None,
             max_rows: int, verb: str) -> tuple[Any, list[dict[str, Any]]]:
    merged = _conditions(where, conditions)
    if not merged:
        raise DataError(f"{verb} butuh where atau conditions supaya tidak mengenai semua baris")
    info = backend.table(table)
    if not info.editable:
        raise DataError(f"Tabel '{info.name}' tidak punya primary key, jadi tidak bisa diubah.")
    _check_columns(name, info.columns, _used(merged))
    found = backend.all_rows(info.name, merged)
    limit = max(1, min(int(max_rows), _MAX_WRITE_ROWS))
    if found.total == 0:
        raise DataError("Tidak ada baris yang cocok dengan syarat itu. Cek dengan db_read dulu.")
    if found.total > limit:
        raise DataError(f"{found.total} baris cocok, lebih dari batas {limit}. Persempit syaratnya, atau naikkan max_rows "
                        f"(maksimal {_MAX_WRITE_ROWS}) bila memang benar semuanya.")
    return info, found.rows


@spec("db_update", "Change cells in the rows that match where/conditions. values is {column: new value}. Refuses when nothing "
      "matches or more than max_rows match (default 20), so one wrong filter cannot rewrite a table. Check with db_read first.",
      "datastore", [param("source", description="Source name."), param("values", "obj", "New values, {column: value}."),
                    param("where", "obj", "Column: value pairs that select the rows.", required=False),
                    param("conditions", "list", "List of {column, op, value} conditions.", required=False, items="obj"),
                    param("table", description="Table or sheet.", required=False, default=""),
                    param("max_rows", "int", "Most rows allowed to change (default 20).", required=False, default=20)],
      keywords="ubah stok, ganti harga, update harga, update stok, edit data, ubah data, perbarui data, update row",
      lead=True, workspace=True, read_only=False)
def db_update(source: str, values: dict[str, Any], where: dict[str, Any] | None = None, conditions: list[dict[str, Any]] | None = None,
              table: str = "", max_rows: int = 20, workspace_path: str | None = None) -> dict[str, Any]:
    backend, name = _open(source, workspace_path)
    access.require(name, "write")
    if not isinstance(values, dict) or not values:
        raise DataError("values harus berisi minimal satu {kolom: nilai baru}")
    info, rows = _targets(backend, name, table, where, conditions, max_rows, "Ubah")
    _check_columns(name, info.columns, list(values))
    for row in rows:
        backend.update(info.name, row["key"], values)
    changes = [{"key": row["key"], "changed": {c: {"from": _cell(row["values"].get(c)), "to": _cell(v)} for c, v in values.items()
                                              if c in row["values"]}} for row in rows[:10]]
    return {"source": source, "table": info.name, "changed_rows": len(rows), "changes": changes}


@spec("db_delete", "Delete the rows that match where/conditions. Refuses when nothing matches or more than max_rows match "
      "(default 5). Deleting cannot be undone from here (files keep a backup in .backups). Check with db_read first.",
      "datastore", [param("source", description="Source name."),
                    param("where", "obj", "Column: value pairs that select the rows.", required=False),
                    param("conditions", "list", "List of {column, op, value} conditions.", required=False, items="obj"),
                    param("table", description="Table or sheet.", required=False, default=""),
                    param("max_rows", "int", "Most rows allowed to delete (default 5).", required=False, default=5)],
      keywords="hapus baris, hapus data, hapus produk, delete row, buang data", lead=True, workspace=True, read_only=False)
def db_delete(source: str, where: dict[str, Any] | None = None, conditions: list[dict[str, Any]] | None = None,
              table: str = "", max_rows: int = 5, workspace_path: str | None = None) -> dict[str, Any]:
    backend, name = _open(source, workspace_path)
    access.require(name, "write")
    info, rows = _targets(backend, name, table, where, conditions, max_rows, "Hapus")
    gone = backend.delete(info.name, [r["key"] for r in rows])
    return {"source": source, "table": info.name, "deleted_rows": gone, "deleted": _shape(rows[:10], _hidden(name, info.columns))}


@spec("db_alter", "Change a table's structure: add_column, rename_column, drop_column, create_table, drop_table. "
      "Dropping removes data, so only do it when the owner asked.",
      "datastore", [param("source", description="Source name."),
                    param("action", description="What to do.", enum=["add_column", "rename_column", "drop_column", "create_table", "drop_table"]),
                    param("table", description="Table or sheet.", required=False, default=""),
                    param("column", description="Column to add, rename or drop.", required=False, default=""),
                    param("new_name", description="New column name (rename_column).", required=False, default=""),
                    param("columns", "list", "Column names for create_table.", required=False)],
      keywords="tambah kolom, hapus kolom, ganti nama kolom, buat tabel, buat sheet, ubah struktur tabel, alter table",
      workspace=True, read_only=False)
def db_alter(source: str, action: str, table: str = "", column: str = "", new_name: str = "", columns: list[str] | None = None,
             workspace_path: str | None = None) -> dict[str, Any]:
    backend, name = _open(source, workspace_path)
    access.require(name, "write")
    if action == "create_table":
        backend.create_table(table, list(columns or []))
    elif action == "drop_table":
        backend.drop_table(table)
    else:
        info = backend.table(table)
        table = info.name
        if action == "add_column":
            backend.add_column(table, column)
        elif action == "rename_column":
            backend.rename_column(table, column, new_name)
        elif action == "drop_column":
            backend.drop_column(table, column)
        else:
            raise DataError(f"Aksi '{action}' tidak dikenal")
    return {"source": source, "action": action, "table": table, "done": True}


@spec("owner_notify", "Send a short message to the shop owner's Telegram: an order that needs a human, a question you cannot "
      "answer, an angry customer. Max 3 per conversation. Say who and what is needed; the customer's name is added.",
      "datastore", [param("message", description="What the owner needs to know or do (max 600 characters).")],
      keywords="hubungi pemilik, kabari pemilik, beritahu pemilik, notifikasi pemilik, owner notify, eskalasi", network=True, read_only=False)
async def owner_notify(message: str) -> dict[str, Any]:
    from agent.team_outputs import OutputError, send_telegram

    text = " ".join(str(message).split())[:600]
    if not text:
        raise DataError("Pesannya kosong")
    granted = access.current()
    if granted is not None:
        if granted.notified >= access.MAX_NOTIFICATIONS:
            raise DataError("Sudah tiga kali mengabari pemilik di percakapan ini. Beri tahu pelanggan bahwa pemilik akan menghubungi.")
        granted.notified += 1
    prefix = f"🔔 Bot Telegram{(' — ' + granted.context) if granted and granted.context else ''}\n"
    try:
        await send_telegram(prefix + text)
    except OutputError as exc:
        raise DataError(f"Pemilik belum bisa dikabari: {exc}") from None
    return {"sent": True}
