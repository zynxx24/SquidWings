"""Load M5 bars from a CSV export (MT5 "History Center" or a simple time,open,high,low,close,volume file).

Accepted headers, case-insensitive: time or <DATE> + <TIME>, open, high, low, close, and
tick_volume / volume / <TICKVOL> (optional). Times are read as UTC; the timestamp is what
matters for the UTC-day VWAP reset, so a broker-time export shifts the reset.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path


def _num(v: str | None, default: float = 0.0) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def load_csv(path: str | Path) -> list[dict[str, float]]:
    text = Path(path).read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    rows = list(csv.DictReader(text.splitlines(), dialect=dialect))
    out: list[dict[str, float]] = []
    for raw in rows:
        r = {k.strip().lower().strip("<>"): v for k, v in raw.items() if k}
        if "date" in r and "time" in r:          # MT5 export: <DATE> and <TIME> are separate columns
            t = _parse_time(f"{r['date']} {r['time']}")
        elif "time" in r:                         # a single timestamp column
            t = _parse_time(r["time"])
        elif "date" in r:
            t = _parse_time(r["date"])
        else:
            t = None
        if t is None:
            continue
        out.append({
            "t": t, "o": _num(r.get("open")), "h": _num(r.get("high")), "l": _num(r.get("low")),
            "c": _num(r.get("close")), "v": int(_num(r.get("tick_volume") or r.get("volume") or r.get("tickvol"), 1.0)) or 1,
        })
    out.sort(key=lambda b: b["t"])
    return [b for b in out if b["h"] >= b["l"] > 0]


def _parse_time(s: str | None) -> int | None:
    if not s:
        return None
    s = s.strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            return int(dt.datetime.strptime(s, fmt).replace(tzinfo=dt.UTC).timestamp())
        except ValueError:
            continue
    try:
        return int(float(s))
    except ValueError:
        return None


__all__: list[str] = ["load_csv"]
