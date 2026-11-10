"""Every model request's tokens, kept in SQLite on this machine.

One row per request: provider, account, model, input, output, cached, cache write, total,
latency, ok or error. The Router page reads totals and a time series for the last hour, 24
hours, week, month, or everything, split by provider, account or model.

Token counts follow the OpenAI convention for every provider, so they add up:

* ``input``  every prompt token, cached ones included (Claude's input + cache read + cache write)
* ``cached`` prompt tokens read from the provider's cache (cheaper, faster)
* ``cache_write`` prompt tokens written to the cache (Claude only)
* ``output`` completion tokens, reasoning included
* ``total``  input + output

Requests that streamed without a usage report are still counted, with zero tokens, and the
page shows how many there were. Rows older than ROUTER_USAGE_KEEP_DAYS (default 400) are deleted.

Given a price lookup (``model.router.pricing``), the summary also estimates the cost in dollars:
per row, per time bucket, in total, and as a daily and 30-day rate at the period's pace. Models
without a price are listed, not counted as free.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from model.router.store import store_dir

PERIODS: dict[str, tuple[float | None, int]] = {
    # name: (seconds back, bucket size in seconds)
    "1h": (3600, 300),
    "24h": (86400, 3600),
    "7d": (7 * 86400, 6 * 3600),
    "30d": (30 * 86400, 86400),
    "all": (None, 86400),
}
GROUPS = {"provider": "provider", "account": "provider, account", "model": "model"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    ts REAL NOT NULL, provider TEXT NOT NULL, account TEXT NOT NULL DEFAULT '', account_label TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL, input INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0,
    cached INTEGER NOT NULL DEFAULT 0, cache_write INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0, ok INTEGER NOT NULL DEFAULT 1, reported INTEGER NOT NULL DEFAULT 1,
    stream INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', session TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS usage_ts ON usage (ts);
"""


def normalize(usage: dict[str, Any] | None) -> dict[str, int] | None:
    """OpenAI or Claude usage as input/output/cached/cache_write/total. None when there is none."""
    if not isinstance(usage, dict) or not usage:
        return None

    def num(*names: str) -> int:
        for name in names:
            value = usage.get(name)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
        return 0

    raw_details = usage.get("prompt_tokens_details")
    details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
    cached = int(details.get("cached_tokens") or 0) or num("cached_tokens", "cache_read_input_tokens")
    write = num("cache_creation_input_tokens")
    if "prompt_tokens" in usage or "completion_tokens" in usage:
        prompt = num("prompt_tokens")
        output = num("completion_tokens")
    else:   # Claude: input_tokens leaves out what was read from or written to the cache
        prompt = num("input_tokens") + cached + write
        output = num("output_tokens")
    total = num("total_tokens") or prompt + output
    return {"input": prompt, "output": output, "cached": min(cached, prompt) if prompt else cached,
            "cache_write": write, "total": total}


class UsageLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or store_dir() / "usage.db"
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._last_prune = 0.0

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5)
            with contextlib.suppress(sqlite3.DatabaseError):
                conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            with contextlib.suppress(OSError):
                os.chmod(self.path, 0o600)
            self._conn = conn
        return self._conn

    def record(self, *, provider: str, model: str, usage: dict[str, Any] | None, account: str = "", account_label: str = "",
               latency_ms: float = 0.0, ok: bool = True, error: str = "", stream: bool = False, session: str = "",
               ts: float | None = None) -> None:
        tokens = normalize(usage)
        row = tokens or {"input": 0, "output": 0, "cached": 0, "cache_write": 0, "total": 0}
        with self._lock:
            db = self._db()
            db.execute(
                "INSERT INTO usage (ts, provider, account, account_label, model, input, output, cached, cache_write, total,"
                " latency_ms, ok, reported, stream, error, session) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts or time.time(), provider, account, account_label, model, row["input"], row["output"], row["cached"],
                 row["cache_write"], row["total"], int(latency_ms), int(ok), int(tokens is not None or not ok), int(stream),
                 error[:300], session[:80]),
            )
            db.commit()
            self._prune()

    def _prune(self) -> None:
        now = time.time()
        if now - self._last_prune < 3600:
            return
        self._last_prune = now
        try:
            keep = float(os.getenv("ROUTER_USAGE_KEEP_DAYS", "400") or 400)
        except ValueError:
            keep = 400.0
        self._db().execute("DELETE FROM usage WHERE ts < ?", (now - keep * 86400,))
        self._db().commit()

    # ------------------------------------------------------------ reading

    @staticmethod
    def _window(period: str, now: float) -> tuple[float, int]:
        if period not in PERIODS:
            raise ValueError(f"period must be one of {', '.join(PERIODS)}")
        back, bucket = PERIODS[period]
        return (now - back if back else 0.0), bucket

    _SUMS = ("COUNT(*) AS requests, COALESCE(SUM(input),0) AS input, COALESCE(SUM(output),0) AS output, "
             "COALESCE(SUM(cached),0) AS cached, COALESCE(SUM(cache_write),0) AS cache_write, "
             "COALESCE(SUM(total),0) AS total, COALESCE(SUM(1-ok),0) AS errors, "
             "COALESCE(SUM(CASE WHEN ok=1 AND reported=0 THEN 1 ELSE 0 END),0) AS unreported, "
             "COALESCE(AVG(CASE WHEN ok=1 THEN latency_ms END),0) AS avg_latency_ms")

    def summary(self, period: str = "24h", group: str = "provider", now: float | None = None,
                price: Callable[[str], Any] | None = None) -> dict[str, Any]:
        now = now or time.time()
        since, bucket = self._window(period, now)
        if group not in GROUPS:
            raise ValueError(f"group must be one of {', '.join(GROUPS)}")
        with self._lock:
            db = self._db()
            db.row_factory = sqlite3.Row
            totals = dict(db.execute(f"SELECT {self._SUMS} FROM usage WHERE ts >= ?", (since,)).fetchone())
            label = ", MAX(account_label) AS account_label" if group == "account" else ""
            rows = [dict(r) for r in db.execute(
                f"SELECT {GROUPS[group]}{label}, {self._SUMS} FROM usage WHERE ts >= ? "
                f"GROUP BY {GROUPS[group]} ORDER BY total DESC, requests DESC LIMIT 500", (since,))]
            series = [dict(r) for r in db.execute(
                "SELECT CAST(ts / ? AS INTEGER) * ? AS bucket, COUNT(*) AS requests, COALESCE(SUM(input),0) AS input, "
                "COALESCE(SUM(output),0) AS output, COALESCE(SUM(cached),0) AS cached, COALESCE(SUM(total),0) AS total, "
                "COALESCE(SUM(1-ok),0) AS errors FROM usage WHERE ts >= ? GROUP BY bucket ORDER BY bucket",
                (bucket, bucket, since))]
            first = db.execute("SELECT MIN(ts) FROM usage").fetchone()[0]
            keys = "model" if group == "model" else f"{GROUPS[group]}, model"
            by_model = [dict(r) for r in db.execute(
                f"SELECT {keys}, COUNT(*) AS requests, COALESCE(SUM(input),0) AS input, COALESCE(SUM(output),0) AS output, "
                "COALESCE(SUM(cached),0) AS cached, COALESCE(SUM(cache_write),0) AS cache_write FROM usage "
                f"WHERE ts >= ? GROUP BY {keys}", (since,))] if price else []
            by_bucket = [dict(r) for r in db.execute(
                "SELECT CAST(ts / ? AS INTEGER) * ? AS bucket, model, COALESCE(SUM(input),0) AS input, "
                "COALESCE(SUM(output),0) AS output, COALESCE(SUM(cached),0) AS cached, COALESCE(SUM(cache_write),0) AS cache_write "
                "FROM usage WHERE ts >= ? GROUP BY bucket, model", (bucket, bucket, since))] if price else []
            db.row_factory = None
        for item in (totals, *rows):
            item["avg_latency_ms"] = round(item.get("avg_latency_ms") or 0)
            item["cache_hit_pct"] = round(item["cached"] / item["input"] * 100, 1) if item.get("input") else 0.0
        filled = _fill(series, since or (first or now), now, bucket)
        if price:
            _add_costs(price, group, totals, rows, filled, by_model, by_bucket, (now - (since or first or now)))
        return {"period": period, "group": group, "since": since or first, "until": now, "bucket_s": bucket,
                "totals": totals, "rows": rows, "series": filled}


def _cost(price: Callable[[str], Any], cache: dict[str, Any], row: dict[str, Any]) -> float | None:
    model = row["model"]
    if model not in cache:
        cache[model] = price(model)
    found = cache[model]
    if found is None:
        return None
    return float(found.cost(prompt=int(row["input"]), output=int(row["output"]), cached=int(row["cached"]),
                            cache_write=int(row["cache_write"])))


def _add_costs(price: Callable[[str], Any], group: str, totals: dict[str, Any], rows: list[dict[str, Any]],
               series: list[dict[str, Any]], by_model: list[dict[str, Any]], by_bucket: list[dict[str, Any]],
               span_s: float) -> None:
    """Dollars per row, per bucket and in total, from the per-model sums. Unpriced tokens stay out."""
    cache: dict[str, Any] = {}
    columns = [c.strip() for c in GROUPS[group].split(",")]
    per_row: dict[tuple[Any, ...], list[float]] = {}
    unpriced: dict[str, int] = {}
    for item in by_model:
        key = tuple(item[c] for c in columns)
        slot = per_row.setdefault(key, [0.0, 0])
        cost = _cost(price, cache, item)
        if cost is None:
            slot[1] += item["requests"]
            if item["input"] or item["output"]:
                unpriced[item["model"]] = unpriced.get(item["model"], 0) + int(item["input"]) + int(item["output"])
        else:
            slot[0] += cost
    for row in rows:
        cost, missing = per_row.get(tuple(row[c] for c in columns), [0.0, 0])
        row["cost_usd"] = round(cost, 6)
        row["unpriced_requests"] = int(missing)
    per_bucket: dict[int, float] = {}
    for item in by_bucket:
        cost = _cost(price, cache, item)
        if cost:
            per_bucket[int(item["bucket"])] = per_bucket.get(int(item["bucket"]), 0.0) + cost
    for point in series:
        point["cost_usd"] = round(per_bucket.get(int(point["bucket"]), 0.0), 6)
    total = sum(slot[0] for slot in per_row.values())
    totals["cost_usd"] = round(total, 6)
    totals["unpriced_requests"] = int(sum(slot[1] for slot in per_row.values()))
    totals["unpriced_models"] = [m for m, _ in sorted(unpriced.items(), key=lambda kv: -kv[1])][:20]
    days = max(span_s, 3600.0) / 86400        # at least an hour, so one early request is not a month's bill
    totals["cost_per_day_usd"] = round(total / days, 6) if total else 0.0
    totals["cost_30d_usd"] = round(total / days * 30, 4) if total else 0.0


def _fill(series: list[dict[str, Any]], since: float, now: float, bucket: int) -> list[dict[str, Any]]:
    """Every bucket in the window, empty ones included, so a chart's time axis is even."""
    by = {int(row["bucket"]): row for row in series}
    start = int(since // bucket) * bucket
    end = int(now // bucket) * bucket
    if (end - start) // bucket > 400:      # "all" over years: keep the buckets that have data
        return series
    empty = {"requests": 0, "input": 0, "output": 0, "cached": 0, "total": 0, "errors": 0}
    return [by.get(t, {"bucket": t, **empty}) for t in range(start, end + 1, bucket)]


_log: UsageLog | None = None
_log_lock = threading.Lock()


def get_usage_log() -> UsageLog:
    global _log
    with _log_lock:
        if _log is None or _log.path != store_dir() / "usage.db":
            _log = UsageLog()
        return _log


def record(**kwargs: Any) -> None:
    """Record one request. A broken database must never break a chat, so errors are swallowed."""
    with contextlib.suppress(Exception):
        get_usage_log().record(**kwargs)


__all__ = ["UsageLog", "PERIODS", "GROUPS", "normalize", "get_usage_log", "record"]
