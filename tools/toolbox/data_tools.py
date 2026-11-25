"""Tables and structured data: CSV, Excel, JSON, YAML, XML, TOML, SQLite. Files stay inside the workspace."""

from __future__ import annotations

import io
import json
import re
import sqlite3
import tomllib
from pathlib import Path
from typing import Any

import yaml

from . import _files
from .catalog import group, param, spec

group("data", "Tables and structured data",
      "csv, excel, xlsx, xls, spreadsheet, tabel, table, kolom, column, baris, row, data, dataset, json, yaml, yml, "
      "xml, toml, sqlite, database, db, sql, pivot, group by, rekap, rekapitulasi, filter data, urutkan data")

_MAX_ROWS_OUT = 200


def _frame(path: str | None, text: str | None, workspace_path: str | None, sheet: str | int | None = None) -> Any:
    import pandas as pd

    if text:
        stripped = text.strip()
        if stripped.startswith(("[", "{")):
            data = json.loads(stripped)
            return pd.json_normalize(data if isinstance(data, list) else [data])
        sep = "\t" if "\t" in stripped.splitlines()[0] else (";" if stripped.splitlines()[0].count(";") > stripped.splitlines()[0].count(",") else ",")
        return pd.read_csv(io.StringIO(stripped), sep=sep)
    if not path:
        raise ValueError("give path (a file in the workspace) or text (CSV or JSON)")
    file = _files.source(path, workspace_path)
    ext = file.suffix.lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(file, sheet_name=sheet if sheet not in (None, "") else 0)
    if ext == ".json":
        data = json.loads(file.read_text(encoding="utf-8-sig"))
        return pd.json_normalize(data if isinstance(data, list) else [data])
    if ext == ".jsonl":
        return pd.read_json(file, lines=True)
    if ext == ".parquet":
        return pd.read_parquet(file)
    raw = file.read_text(encoding="utf-8-sig", errors="replace")
    first = raw.splitlines()[0] if raw else ""
    sep = "\t" if ext == ".tsv" or "\t" in first else (";" if first.count(";") > first.count(",") else ",")
    return pd.read_csv(io.StringIO(raw), sep=sep)


def _records(df: Any, limit: int = _MAX_ROWS_OUT) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(df.head(limit).to_json(orient="records", date_format="iso", force_ascii=False))
    return rows


@spec("table_inspect", "Open a CSV/TSV/Excel/JSON table (a workspace file or pasted text) and describe it: rows, columns, "
      "types, missing values, sample rows, and summary statistics of numeric columns. Start here with any dataset.",
      "data", [param("path", required=False), param("text", description="Pasted CSV or JSON instead of a file.", required=False),
               param("sheet", description="Excel sheet name or index.", required=False), param("sample", "int", required=False, default=5)],
      keywords="csv, excel, xlsx, spreadsheet, dataset, tabel, buka data, lihat data, inspect data", lead=True, workspace=True)
def table_inspect(path: str | None = None, text: str | None = None, sheet: str | None = None, sample: int = 5,
                  workspace_path: str | None = None) -> dict[str, Any]:
    df = _frame(path, text, workspace_path, sheet)
    columns = []
    for col in df.columns:
        s = df[col]
        info: dict[str, Any] = {"name": str(col), "type": str(s.dtype), "missing": int(s.isna().sum()),
                                "unique": int(s.nunique(dropna=True))}
        if s.dtype.kind in "if":
            info.update(min=_num(s.min()), max=_num(s.max()), mean=_num(s.mean()), sum=_num(s.sum()))
        else:
            info["top_values"] = {str(k): int(v) for k, v in s.astype(str).value_counts().head(5).items()}
        columns.append(info)
    out: dict[str, Any] = {"rows": int(len(df)), "columns": columns, "sample": _records(df, int(sample))}
    if path and _files.source(path, workspace_path).suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        import pandas as pd

        out["sheets"] = pd.ExcelFile(_files.source(path, workspace_path)).sheet_names
    return out


def _num(v: Any) -> Any:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f.is_integer() else round(f, 6)


_OPS = {"=", "==", "!=", ">", ">=", "<", "<=", "contains", "not_contains", "startswith", "endswith", "in", "not_in",
        "is_null", "not_null", "regex"}


def _apply_filters(df: Any, filters: list[dict[str, Any]] | None) -> Any:
    for f in filters or []:
        col, op, value = f.get("column"), str(f.get("op", "=")).lower(), f.get("value")
        if col not in df.columns:
            raise ValueError(f"no column '{col}'; columns are {', '.join(map(str, df.columns))}")
        if op not in _OPS:
            raise ValueError(f"unknown op {op}; use {', '.join(sorted(_OPS))}")
        s = df[col]
        if op in ("=", "=="):
            mask = s.astype(str).str.lower() == str(value).lower() if s.dtype == object else s == _cast(value, s)
        elif op == "!=":
            mask = s.astype(str).str.lower() != str(value).lower() if s.dtype == object else s != _cast(value, s)
        elif op in (">", ">=", "<", "<="):
            v = _cast(value, s)
            mask = {">": s > v, ">=": s >= v, "<": s < v, "<=": s <= v}[op]
        elif op == "contains":
            mask = s.astype(str).str.contains(str(value), case=False, regex=False, na=False)
        elif op == "not_contains":
            mask = ~s.astype(str).str.contains(str(value), case=False, regex=False, na=False)
        elif op == "startswith":
            mask = s.astype(str).str.lower().str.startswith(str(value).lower())
        elif op == "endswith":
            mask = s.astype(str).str.lower().str.endswith(str(value).lower())
        elif op in ("in", "not_in"):
            values = {str(v).lower() for v in (value if isinstance(value, list) else str(value).split(","))}
            mask = s.astype(str).str.lower().isin(values)
            mask = ~mask if op == "not_in" else mask
        elif op == "is_null":
            mask = s.isna()
        elif op == "not_null":
            mask = s.notna()
        else:
            mask = s.astype(str).str.contains(str(value), regex=True, na=False)
        df = df[mask]
    return df


def _cast(value: Any, series: Any) -> Any:
    if series.dtype.kind in "if":
        return float(value)
    if series.dtype.kind == "M":
        import pandas as pd

        return pd.to_datetime(value)
    return value


_AGGS = {"sum", "mean", "median", "min", "max", "count", "nunique", "std", "first", "last"}


@spec("table_query", "Query a table without writing code: filter rows ([{column, op, value}] with = != > >= < <= contains in "
      "regex is_null), choose columns, group by with aggregates (sum, mean, count, ...), sort, and limit. "
      "Optionally save the result as CSV/XLSX/JSON in the workspace.",
      "data", [param("path", required=False), param("text", required=False),
               param("filters", "list", "e.g. [{\"column\": \"kota\", \"op\": \"=\", \"value\": \"Bandung\"}]", required=False, items="obj"),
               param("columns", "list", "Columns to keep.", required=False, items="str"),
               param("group_by", "list", "Columns to group by.", required=False, items="str"),
               param("aggregate", "obj", "e.g. {\"harga\": \"sum\", \"id\": \"count\"}", required=False),
               param("sort_by", "list", "Columns to sort by; prefix '-' for descending.", required=False, items="str"),
               param("limit", "int", required=False, default=50), param("sheet", required=False),
               param("save_as", description="Optional output path (.csv, .xlsx, .json).", required=False)],
      keywords="filter, saring, group by, kelompokkan, pivot, rekap, total per, jumlah per, sum per, rata-rata per, "
               "query data, top 10, urutkan data", lead=True, workspace=True, read_only=False)
def table_query(path: str | None = None, text: str | None = None, filters: list[dict[str, Any]] | None = None,
                columns: list[str] | None = None, group_by: list[str] | None = None, aggregate: dict[str, str] | None = None,
                sort_by: list[str] | None = None, limit: int = 50, sheet: str | None = None, save_as: str | None = None,
                workspace_path: str | None = None) -> dict[str, Any]:
    df = _frame(path, text, workspace_path, sheet)
    total = len(df)
    df = _apply_filters(df, filters)
    if group_by:
        missing = [c for c in group_by if c not in df.columns]
        if missing:
            raise ValueError(f"no column {missing}")
        agg = aggregate or {c: "count" for c in df.columns if c not in group_by}
        bad = [a for a in agg.values() if a not in _AGGS]
        if bad:
            raise ValueError(f"unknown aggregate {bad}; use {', '.join(sorted(_AGGS))}")
        df = df.groupby(group_by, dropna=False).agg(agg).reset_index()
    if columns:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise ValueError(f"no column {missing}; columns are {', '.join(map(str, df.columns))}")
        df = df[columns]
    if sort_by:
        keys = [c.lstrip("-") for c in sort_by]
        df = df.sort_values(keys, ascending=[not c.startswith("-") for c in sort_by])
    matched = len(df)
    out: dict[str, Any] = {"total_rows": total, "matched_rows": matched}
    if save_as:
        dest = _files.destination(save_as, workspace_path)
        _write(df, dest)
        out["saved"] = _files.relative(dest, workspace_path)
    out["rows"] = _records(df, min(int(limit), _MAX_ROWS_OUT))
    return out


def _write(df: Any, dest: Path) -> None:
    ext = dest.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        df.to_excel(dest, index=False)
    elif ext == ".json":
        dest.write_text(df.to_json(orient="records", force_ascii=False, indent=2, date_format="iso"), encoding="utf-8")
    elif ext == ".md":
        dest.write_text(_markdown(df), encoding="utf-8")
    elif ext == ".html":
        dest.write_text(df.to_html(index=False), encoding="utf-8")
    else:
        df.to_csv(dest, index=False)


def _markdown(df: Any) -> str:
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join("" if v is None or v != v else str(v).replace("|", "\\|") for v in row) + " |")
    return "\n".join(lines)


@spec("table_convert", "Convert a table between CSV, TSV, Excel, JSON, JSON Lines, Markdown and HTML. Writes the new file "
      "into the workspace, or returns the text when no output path is given.",
      "data", [param("path", required=False), param("text", required=False),
               param("to", enum=["csv", "tsv", "xlsx", "json", "jsonl", "markdown", "html"]),
               param("output", description="Output path. Leave out to get the text back.", required=False),
               param("sheet", required=False)],
      keywords="convert csv, csv ke excel, excel ke csv, csv to json, json to csv, ubah ke excel, xlsx", workspace=True,
      read_only=False)
def table_convert(to: str, path: str | None = None, text: str | None = None, output: str | None = None,
                  sheet: str | None = None, workspace_path: str | None = None) -> dict[str, Any]:
    df = _frame(path, text, workspace_path, sheet)
    if output:
        dest = _files.destination(output, workspace_path)
        if to == "tsv":
            df.to_csv(dest, index=False, sep="\t")
        elif to == "jsonl":
            dest.write_text(df.to_json(orient="records", lines=True, force_ascii=False), encoding="utf-8")
        else:
            _write(df, dest.with_suffix({"markdown": ".md"}.get(to, f".{to}")) if dest.suffix == "" else dest)
        return {"saved": _files.relative(dest, workspace_path), "rows": len(df)}
    if to == "xlsx":
        raise ValueError("xlsx needs an output path")
    rendered = {"csv": lambda: df.to_csv(index=False), "tsv": lambda: df.to_csv(index=False, sep="\t"),
                "json": lambda: df.to_json(orient="records", force_ascii=False, indent=2),
                "jsonl": lambda: df.to_json(orient="records", lines=True, force_ascii=False),
                "markdown": lambda: _markdown(df), "html": lambda: df.to_html(index=False)}[to]()
    return {"result": rendered[:200_000], "rows": len(df)}


@spec("markdown_table", "Build a neat Markdown table from rows (list of objects or list of lists with headers). "
      "Columns of numbers are right-aligned.",
      "data", [param("rows", "list", items="obj"), param("headers", "list", "Needed when rows are lists.", required=False, items="str")],
      keywords="tabel markdown, markdown table, buat tabel, make a table")
def markdown_table(rows: list[Any], headers: list[str] | None = None) -> dict[str, Any]:
    if not rows:
        raise ValueError("rows is empty")
    if isinstance(rows[0], dict):
        cols = headers or list(dict.fromkeys(k for r in rows for k in r))
        data = [[r.get(c, "") for c in cols] for r in rows]
    else:
        if not headers:
            headers, rows = [str(h) for h in rows[0]], rows[1:]
        cols, data = headers, [list(r) for r in rows]
    numeric = [all(isinstance(r[i], (int, float)) or re.fullmatch(r"-?[\d.,]+", str(r[i]) or "x") for r in data if i < len(r))
               for i in range(len(cols))]
    lines = ["| " + " | ".join(map(str, cols)) + " |",
             "|" + "|".join("---:" if n else "---" for n in numeric) + "|"]
    lines += ["| " + " | ".join(str(v).replace("|", "\\|") for v in r) + " |" for r in data]
    return {"markdown": "\n".join(lines)}


@spec("json_tool", "Work with JSON text or a .json file: validate (with the exact error position), pretty-print, minify, "
      "sort keys, list every key path, or pick a value by path like 'data.items[0].name'.",
      "data", [param("operation", enum=["validate", "pretty", "minify", "sort_keys", "paths", "get"]),
               param("text", required=False), param("path", description="A .json file in the workspace.", required=False),
               param("query", description="For get: dotted path with [index].", required=False)],
      keywords="json, validasi json, validate json, format json, pretty json, json error, minify", lead=True, workspace=True)
def json_tool(operation: str, text: str | None = None, path: str | None = None, query: str | None = None,
              workspace_path: str | None = None) -> dict[str, Any]:
    raw = text if text is not None else (_files.source(path, workspace_path).read_text(encoding="utf-8-sig") if path else None)
    if raw is None:
        raise ValueError("give text or path")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        line = raw.splitlines()[exc.lineno - 1] if exc.lineno - 1 < len(raw.splitlines()) else ""
        return {"ok": True, "valid": False, "error": exc.msg, "line": exc.lineno, "column": exc.colno,
                "context": line[max(0, exc.colno - 40): exc.colno + 40]}
    if operation == "validate":
        return {"valid": True, "type": type(data).__name__,
                "size": len(data) if isinstance(data, (list, dict)) else None}
    if operation == "pretty":
        return {"result": json.dumps(data, ensure_ascii=False, indent=2)[:200_000]}
    if operation == "minify":
        return {"result": json.dumps(data, ensure_ascii=False, separators=(",", ":"))[:200_000]}
    if operation == "sort_keys":
        return {"result": json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)[:200_000]}
    if operation == "paths":
        paths: list[str] = []
        _walk(data, "", paths)
        return {"paths": paths[:1000], "count": len(paths)}
    if operation == "get":
        return {"value": json_get(data, query or "")}
    raise ValueError("unknown operation")


def _walk(node: Any, prefix: str, out: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.append(p)
            _walk(v, p, out)
    elif isinstance(node, list) and node:
        p = f"{prefix}[0]"
        out.append(f"{p}  (list of {len(node)})")
        _walk(node[0], p, out)


def json_get(data: Any, query: str) -> Any:
    node = data
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", query):
        if part.startswith("["):
            node = node[int(part[1:-1])]
        elif isinstance(node, dict):
            if part not in node:
                raise ValueError(f"no key '{part}'; keys here: {', '.join(list(node)[:30])}")
            node = node[part]
        elif isinstance(node, list) and part.isdigit():
            node = node[int(part)]
        else:
            raise ValueError(f"cannot read '{part}' from a {type(node).__name__}")
    return node


@spec("json_diff", "Structural difference between two JSON values: added, removed, and changed keys with their paths.",
      "data", [param("a", description="First JSON text."), param("b", description="Second JSON text.")],
      keywords="json diff, bandingkan json, compare json, perbedaan json")
def json_diff(a: str, b: str) -> dict[str, Any]:
    left, right = json.loads(a), json.loads(b)
    changes: list[dict[str, Any]] = []
    _diff(left, right, "", changes)
    return {"equal": not changes, "changes": changes[:500], "count": len(changes)}


def _diff(x: Any, y: Any, path: str, out: list[dict[str, Any]]) -> None:
    if isinstance(x, dict) and isinstance(y, dict):
        for k in x.keys() - y.keys():
            out.append({"path": f"{path}.{k}".lstrip("."), "change": "removed", "old": x[k]})
        for k in y.keys() - x.keys():
            out.append({"path": f"{path}.{k}".lstrip("."), "change": "added", "new": y[k]})
        for k in x.keys() & y.keys():
            _diff(x[k], y[k], f"{path}.{k}", out)
    elif isinstance(x, list) and isinstance(y, list):
        for i in range(max(len(x), len(y))):
            p = f"{path}[{i}]"
            if i >= len(x):
                out.append({"path": p.lstrip("."), "change": "added", "new": y[i]})
            elif i >= len(y):
                out.append({"path": p.lstrip("."), "change": "removed", "old": x[i]})
            else:
                _diff(x[i], y[i], p, out)
    elif x != y:
        out.append({"path": path.lstrip(".") or "$", "change": "changed", "old": x, "new": y})


@spec("format_convert", "Convert structured text between JSON, YAML, TOML (read only), XML (read), and Python-style dicts. "
      "Good for config files.",
      "data", [param("text"), param("from_format", enum=["json", "yaml", "toml", "xml", "auto"], required=False, default="auto"),
               param("to_format", enum=["json", "yaml"], required=False, default="json")],
      keywords="yaml, yml, toml, xml, yaml ke json, json ke yaml, yaml to json, json to yaml, xml to json, config", lead=True)
def format_convert(text: str, to_format: str = "json", from_format: str = "auto") -> dict[str, Any]:
    src = from_format
    stripped = text.strip()
    if src == "auto":
        looks_toml = bool(re.search(r"^\s*\[[\w.\"-]+\]\s*$", stripped, re.M)) and "=" in stripped
        if stripped.startswith("{"):
            src = "json"
        elif stripped.startswith("["):
            # "[section]" opens a TOML file as well as a JSON array.
            src = "toml" if looks_toml and not _is_json(stripped) else "json"
        elif stripped.startswith("<"):
            src = "xml"
        else:
            src = "toml" if looks_toml or re.match(r"^[\w.-]+\s*=\s*\S", stripped) else "yaml"
    if src == "json":
        data = json.loads(stripped)
    elif src == "yaml":
        data = yaml.safe_load(stripped)
    elif src == "toml":
        data = tomllib.loads(stripped)
    elif src == "xml":
        data = _xml(stripped)
    else:
        raise ValueError("unknown from_format")
    result = (json.dumps(data, ensure_ascii=False, indent=2, default=str) if to_format == "json"
              else yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return {"detected": src, "result": result[:200_000]}


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _xml(text: str) -> Any:
    from defusedxml import ElementTree as SafeET

    def node(el: Any) -> Any:
        children = list(el)
        item: dict[str, Any] = {f"@{k}": v for k, v in el.attrib.items()}
        for c in children:
            value = node(c)
            tag = c.tag.split("}")[-1]
            if tag in item:
                item[tag] = item[tag] if isinstance(item[tag], list) else [item[tag]]
                item[tag].append(value)
            else:
                item[tag] = value
        body = (el.text or "").strip()
        if not children and not el.attrib:
            return body
        if body:
            item["#text"] = body
        return item

    root = SafeET.fromstring(text)
    return {root.tag.split("}")[-1]: node(root)}


@spec("sqlite_query", "Run a read-only SQL query on a SQLite database file in the workspace, or list its tables and columns "
      "when no query is given. Writes are refused.",
      "data", [param("path", description="The .db / .sqlite file."), param("query", description="SELECT ... (optional).", required=False),
               param("limit", "int", required=False, default=100)],
      keywords="sqlite, sql, database, db, query sql, select from, tabel database", lead=True, workspace=True)
def sqlite_query(path: str, query: str | None = None, limit: int = 100, workspace_path: str | None = None) -> dict[str, Any]:
    file = _files.source(path, workspace_path)
    conn = sqlite3.connect(f"file:{file}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if not query:
            tables = {}
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"):
                cols = [f"{r['name']} {r['type']}" for r in conn.execute(f"PRAGMA table_info({json.dumps(name)})")]
                count = conn.execute(f"SELECT COUNT(*) FROM {json.dumps(name)}").fetchone()[0]
                tables[name] = {"columns": cols, "rows": count}
            return {"tables": tables}
        if not re.match(r"\s*(select|with|pragma|explain)\b", query, re.I):
            raise ValueError("only read queries (SELECT, WITH, PRAGMA, EXPLAIN) are allowed")
        cursor = conn.execute(query)
        rows = [dict(r) for r in cursor.fetchmany(min(int(limit), 1000))]
        return {"columns": [d[0] for d in cursor.description or []], "rows": rows, "returned": len(rows)}
    except sqlite3.Error as exc:
        raise ValueError(f"SQL error: {exc}") from exc
    finally:
        conn.close()


@spec("sql_check", "Check SQL syntax against a schema without touching any real database: runs the CREATE statements and "
      "then EXPLAINs the query in a throwaway in-memory SQLite. Reports the exact error.",
      "data", [param("query"), param("schema", description="CREATE TABLE statements the query needs.", required=False, default="")],
      keywords="cek sql, check sql, sql error, sql syntax, query salah, validasi sql")
def sql_check(query: str, schema: str = "") -> dict[str, Any]:
    conn = sqlite3.connect(":memory:")
    try:
        if schema:
            conn.executescript(schema)
        conn.execute(f"EXPLAIN {query}")
        return {"valid": True, "dialect": "sqlite"}
    except sqlite3.Error as exc:
        return {"ok": True, "valid": False, "error": str(exc), "dialect": "sqlite",
                "note": "checked with SQLite; MySQL/PostgreSQL-only syntax may be reported as an error"}
    finally:
        conn.close()


@spec("json_schema_infer", "Infer a JSON Schema (types, required keys, nested objects and arrays) from example JSON. "
      "Useful for writing validators, API docs, or pydantic models.",
      "data", [param("text", description="Example JSON value.")],
      keywords="json schema, skema json, infer schema, pydantic, tipe data json")
def json_schema_infer(text: str) -> dict[str, Any]:
    def infer(v: Any) -> dict[str, Any]:
        if isinstance(v, bool):
            return {"type": "boolean"}
        if isinstance(v, int):
            return {"type": "integer"}
        if isinstance(v, float):
            return {"type": "number"}
        if isinstance(v, str):
            fmt = ("date-time" if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?.*", v) else
                   "date" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else
                   "email" if re.fullmatch(r"[^@\s]+@[^@\s]+\.\w+", v) else "uri" if v.startswith(("http://", "https://")) else None)
            return {"type": "string", **({"format": fmt} if fmt else {})}
        if v is None:
            return {"type": "null"}
        if isinstance(v, list):
            return {"type": "array", "items": infer(v[0]) if v else {}}
        return {"type": "object", "properties": {k: infer(x) for k, x in v.items()}, "required": sorted(v)}

    return {"schema": {"$schema": "https://json-schema.org/draft/2020-12/schema", **infer(json.loads(text))}}


@spec("env_inspect", "Read a .env or config file in the workspace and list its keys with values masked, flag empty or "
      "duplicate keys, and compare with .env.example if present. Never prints secret values.",
      "data", [param("path", required=False, default=".env.example")],
      keywords="env, .env, environment variable, variabel lingkungan, dotenv, konfigurasi env", workspace=True)
def env_inspect(path: str = ".env.example", workspace_path: str | None = None) -> dict[str, Any]:
    file = _files.source(path, workspace_path)
    keys: dict[str, str] = {}
    duplicates, empty = [], []
    for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip().strip("'\"")
        if k in keys:
            duplicates.append(k)
        if not v:
            empty.append(k)
        keys[k] = v
    masked = {k: (f"set ({len(v)} chars)" if v else "") for k, v in keys.items()}
    out: dict[str, Any] = {"keys": masked, "count": len(keys), "duplicates": duplicates, "empty": empty}
    example = file.parent / ".env.example"
    if example.exists() and example != file:
        ex_keys = set(re.findall(r"^\s*(?:export\s+)?([A-Za-z_]\w*)\s*=", example.read_text(encoding="utf-8"), re.M))
        out["missing_vs_example"] = sorted(ex_keys - set(keys))
        out["extra_vs_example"] = sorted(set(keys) - ex_keys)
    return out
