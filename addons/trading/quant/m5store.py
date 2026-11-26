"""The local M5 warehouse: every M5 bar MT4 has ever shown us, kept on disk and only ever added to.

Sources, merged by bar time (a later source wins on the same bar):
  1. MT4's own history files (``history/<server>/<SYMBOL>1.hst``), M1 resampled to M5, to fill holes;
  2. MT4's ``<SYMBOL>5.hst`` files (what the terminal downloaded when a chart was opened or scrolled back);
  3. the bars the EA streamed to this app (``addons/trading/data/bars/<SYMBOL>-M1/M5.csv``).

Symbols that are the same instrument under another name (``XAUUSD`` and ``XAUUSDcheck``) go into one file named by
the instrument (the first six capital letters). Only one trade server's files are used, the one with the most bars,
because two brokers keep different clocks and prices.

The result is ``addons/trading/data/m5store/<INSTRUMENT>.csv`` (t,o,h,l,c,v; server time, oldest first). ``sync`` can run
as often as you like: it rewrites the file only when bars were added or changed.

Older history: MT4 downloads more when ``Tools > Options > Charts > Max bars in history`` is large and you press
Home on an M5 chart. The next ``sync`` picks those bars up from the .hst file.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import glob
import json
import os
import re
import struct
import threading
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "data"
STORE = ROOT / "m5store"
RECORDED = ROOT / "bars"
M5 = 300
HST_HEADER, HST_RECORD = 148, 60          # MT4 build 600+ history format (version 401)
_lock = threading.Lock()


def instrument(symbol: str) -> str:
    match = re.match(r"[A-Z]{6}", symbol.upper())
    return match.group(0) if match else re.sub(r"[^A-Za-z0-9]", "", symbol).upper()


def mt4_history_dirs() -> list[Path]:
    """Every trade-server folder of every MT4 terminal under Wine (or MT4_HISTORY_DIR when set)."""
    custom = os.getenv("MT4_HISTORY_DIR", "").strip()
    if custom:
        return [p for p in Path(custom).iterdir() if p.is_dir()] if Path(custom).is_dir() else []
    pattern = str(Path.home() / ".wine/drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/*/history/*")
    return [Path(p) for p in glob.glob(pattern) if Path(p).is_dir()]


def read_hst(path: Path) -> list[tuple[int, float, float, float, float, float]]:
    data = path.read_bytes()
    if len(data) < HST_HEADER or struct.unpack_from("<i", data)[0] != 401:
        return []
    out = []
    for offset in range(HST_HEADER, len(data) - HST_RECORD + 1, HST_RECORD):
        t, o, h, low, c, v, _spread, _real = struct.unpack_from("<qddddqiq", data, offset)
        if t > 0 and min(o, h, low, c) > 0 and h >= low:
            out.append((int(t), o, h, low, c, float(v)))
    return out


def read_recorded(path: Path) -> list[tuple[int, float, float, float, float, float]]:
    rows: dict[int, tuple[int, float, float, float, float, float]] = {}
    with contextlib.suppress(OSError):
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split(",")
            if len(parts) == 6:
                with contextlib.suppress(ValueError):
                    t, o, h, low, c, v = (float(p) for p in parts)
                    rows[int(t)] = (int(t), o, h, low, c, v)
    return [rows[t] for t in sorted(rows)]


def to_m5(bars: list[tuple[int, float, float, float, float, float]]) -> list[tuple[int, float, float, float, float, float]]:
    """M1 bars into M5 bars (a bucket is kept even when some of its minutes are missing)."""
    out: dict[int, list[float]] = {}
    for t, o, h, low, c, v in sorted(bars):
        key = t - t % M5
        b = out.get(key)
        if b is None:
            out[key] = [o, h, low, c, v]
        else:
            b[1], b[2], b[3], b[4] = max(b[1], h), min(b[2], low), c, b[4] + v
    return [(t, out[t][0], out[t][1], out[t][2], out[t][3], out[t][4]) for t in sorted(out)]


def _pick_server(name: str) -> Path | None:
    counts: Counter[Path] = Counter()
    for folder in mt4_history_dirs():
        for f in folder.glob("*.hst"):
            m = re.match(r"(.*?)(\d+)$", f.stem)
            if m and instrument(m.group(1)) == name and m.group(2) in ("1", "5"):
                n = max(0, (f.stat().st_size - HST_HEADER) // HST_RECORD)
                counts[folder] += n // 5 if m.group(2) == "1" else n
    server = os.getenv("MT4_HISTORY_SERVER", "").strip()
    if server:
        return next((f for f in counts if f.name == server), None)
    return counts.most_common(1)[0][0] if counts else None


def path_for(symbol: str) -> Path:
    return STORE / f"{instrument(symbol)}.csv"


def load(symbol: str) -> list[tuple[int, float, float, float, float, float]]:
    return read_recorded(path_for(symbol))


def sync(symbol: str = "XAUUSD") -> dict[str, Any]:
    """Merge every source into the store. Returns what it holds and what was added."""
    name = instrument(symbol)
    with _lock:
        merged: dict[int, tuple[int, float, float, float, float, float]] = {b[0]: b for b in load(name)}
        before = dict(merged)
        sources: dict[str, int] = {}
        server = _pick_server(name)
        if server is not None:
            hst = sorted(server.glob("*.hst"))
            for period, convert in (("1", to_m5), ("5", lambda b: b)):
                for f in hst:
                    m = re.match(r"(.*?)(\d+)$", f.stem)
                    if m and m.group(2) == period and instrument(m.group(1)) == name:
                        bars = convert(read_hst(f))
                        sources[f"{server.name}/{f.name}"] = len(bars)
                        merged.update({b[0]: b for b in bars})
        for tf, convert in (("M1", to_m5), ("M5", lambda b: b)):
            for f in sorted(RECORDED.glob(f"*-{tf}.csv")) if RECORDED.is_dir() else []:
                if instrument(f.stem.rpartition("-")[0]) == name:
                    bars = convert(read_recorded(f))
                    sources[f"recorded/{f.name}"] = len(bars)
                    merged.update({b[0]: b for b in bars})
        added = sum(1 for t in merged if t not in before)
        changed = sum(1 for t, b in merged.items() if t in before and before[t] != b)
        path = path_for(name)
        if added or changed or not path.exists():
            STORE.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")       # the trainer and the server may sync at once
            with open(tmp, "w", encoding="utf-8") as out:
                out.writelines(f"{t},{o},{h},{low},{c},{v:g}\n" for t, o, h, low, c, v in (merged[k] for k in sorted(merged)))
            tmp.replace(path)
        report = {"instrument": name, "server": server.name if server else None, "bars": len(merged),
                  "added": added, "changed": changed, "sources": sources, **coverage(sorted(merged))}
        with contextlib.suppress(OSError):
            (STORE / f"{name}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        return report


def coverage(times: list[int]) -> dict[str, Any]:
    if not times:
        return {"first": None, "last": None, "days": 0, "one_year": False}
    first, last = times[0], times[-1]
    days = (last - first) / 86400
    fmt = lambda t: dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%d %H:%M")   # noqa: E731
    return {"first": fmt(first), "last": fmt(last), "days": round(days, 1), "one_year": days >= 365,
            "missing_for_one_year_days": max(0, round(365 - days, 1))}


def sync_in_background(symbol: str) -> None:
    threading.Thread(target=_safe_sync, args=(symbol,), name="m5-sync", daemon=True).start()


def _safe_sync(symbol: str) -> None:
    with contextlib.suppress(Exception):
        sync(symbol)


COLLECT_EVERY_S = 900
_collector: threading.Thread | None = None


def start_collector(symbols: Any, every_s: float = COLLECT_EVERY_S) -> None:
    """A daemon thread that syncs each instrument of ``symbols()`` now and then every ``every_s`` seconds."""
    global _collector
    if _collector is not None and _collector.is_alive():
        return

    def loop() -> None:
        import time

        while True:
            with contextlib.suppress(Exception):
                for name in sorted({instrument(s) for s in symbols()}):
                    _safe_sync(name)
            time.sleep(every_s)

    _collector = threading.Thread(target=loop, name="m5-collector", daemon=True)
    _collector.start()


if __name__ == "__main__":
    import sys

    print(json.dumps(sync(sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"), indent=1))
