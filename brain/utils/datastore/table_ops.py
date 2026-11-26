"""What a Data table node does to one table: get, insert, update, upsert or delete rows, the way n8n's does.

The node's settings come checked from ``brain.agent.team_flow``. Values and condition values are templates
(``{{input}}``, ``{{json.kode}}``) filled by the caller's ``fill`` before they reach a backend, and always travel as
bound values. The result is JSON text, so the next node can read fields with ``{{json.kolom}}``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .base import MAX_SCAN, Backend, DataError, holds, normalize_where, sort_key
from .relations import Relations, attach

Fill = Callable[[str], str]


def _conditions(config: dict[str, Any], fill: Fill) -> list[dict[str, Any]]:
    return [{"column": c["column"], "op": c["op"], "value": fill(c["value"])} for c in config.get("where") or []]


def _matching(backend: Backend, table: str, config: dict[str, Any], fill: Fill) -> list[dict[str, Any]]:
    """The rows the node's conditions pick. AND goes to the backend; OR is checked here, over every row."""
    conditions = _conditions(config, fill)
    if not conditions:
        return backend.all_rows(table, cap=MAX_SCAN).rows
    if config.get("combine", "and") == "and":
        return backend.all_rows(table, where=conditions, cap=MAX_SCAN).rows
    page = backend.all_rows(table, cap=MAX_SCAN)
    checked = normalize_where(conditions, page.columns)
    return [row for row in page.rows if any(holds(row["values"], c) for c in checked)]


def _values(config: dict[str, Any], fill: Fill) -> dict[str, Any]:
    return {item["column"]: fill(item["value"]) for item in config.get("values") or []}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, default=str)


def run(config: dict[str, Any], fill: Fill, open_backend: Callable[[str], Backend],
        relations: Relations | None = None) -> tuple[str, str]:
    """Do the node's operation. Returns (content as JSON text, a short note for the node)."""
    backend = open_backend(config["source"])
    info = backend.table(config.get("table") or "")
    table = info.name
    operation = config["operation"]
    if operation == "get":
        rows = _matching(backend, table, config, fill)
        if config.get("sort"):
            column = config["sort"]
            rows.sort(key=lambda r: sort_key(r["values"].get(column)), reverse=bool(config.get("desc")))
        if config.get("limit"):
            rows = rows[: int(config["limit"])]
        notes: list[str] = []
        include = config.get("include") or []
        if include and relations is not None:
            chosen = [r for r in relations.for_table(config["source"], table) if r.id in include or r.name in include]
            notes = attach(rows, config["source"], table, chosen, open_backend)
        found = [row["values"] for row in rows]
        note = f"{len(found)} baris dari {table}"
        return _dump(found), "; ".join([note, *notes])
    if operation == "insert":
        values = _values(config, fill)
        key = backend.insert(table, values)
        return _dump({**values, "_key": key}), f"1 baris ditambah ke {table}"
    if operation == "delete":
        rows = _matching(backend, table, config, fill)
        if not rows:
            return _dump([]), f"Tidak ada baris yang cocok di {table}"
        deleted = backend.delete(table, [row["key"] for row in rows])
        return _dump([row["values"] for row in rows]), f"{deleted} baris dihapus dari {table}"
    values = _values(config, fill)
    if operation == "upsert":
        where = [{"column": c, "op": "=", "value": values.get(c, "")} for c in config.get("match") or []]
        rows = backend.all_rows(table, where=where, cap=MAX_SCAN).rows
        if not rows:
            key = backend.insert(table, values)
            return _dump({**values, "_key": key}), f"Belum ada, 1 baris ditambah ke {table}"
    else:
        rows = _matching(backend, table, config, fill)
        if not rows:
            return _dump([]), f"Tidak ada baris yang cocok di {table}"
    # Positional rows (files, sheets) keep their numbers on update, so every key stays right.
    for row in rows:
        backend.update(table, row["key"], values)
    changed = [{**row["values"], **values} for row in rows]
    return _dump(changed), f"{len(rows)} baris diubah di {table}"


def check_config(config: dict[str, Any], open_backend: Callable[[str], Backend]) -> None:
    """Fail early, with the real names, when the table or a column the node uses is not there."""
    backend = open_backend(config["source"])
    info = backend.table(config.get("table") or "")
    columns = set(info.columns)
    used = [c["column"] for c in config.get("where") or []] + [v["column"] for v in config.get("values") or []]
    missing = [c for c in used if c not in columns]
    if missing:
        raise DataError(f"Kolom {', '.join(missing)} tidak ada di {info.name}. Kolom yang ada: {', '.join(info.columns)}")
