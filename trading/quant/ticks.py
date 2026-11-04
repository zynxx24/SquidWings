"""Records the live quotes the EA sends, because MT4 keeps no second-level history.

A 30-second backtest needs prices inside the minute, and M1 bars cannot show them. So every quote
goes into one CSV per symbol and day under trading/data/ticks, and ``load_bars`` turns those files
into short bars. The history starts the day recording starts. There is no way to fetch it backward.
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path

TICK_DIR = Path(__file__).resolve().parents[2] / "trading" / "data" / "ticks"
BAR_SECONDS = 5


def _safe(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", symbol)[:32]


def append(symbol: str, seen: float, bid: float, ask: float, folder: Path | None = None) -> None:
    root = folder or TICK_DIR
    root.mkdir(parents=True, exist_ok=True)
    day = int(seen // 86400)
    with open(root / f"{_safe(symbol)}-{day}.csv", "a", encoding="utf-8") as out:
        out.write(f"{seen:.3f},{bid},{ask}\n")


def load_bars(symbol: str, seconds: int = BAR_SECONDS, folder: Path | None = None) -> tuple[list[dict[str, float]], float]:
    """Bars built from the recorded mid prices, and the median spread. Empty when nothing is recorded."""
    root = folder or TICK_DIR
    bars: list[dict[str, float]] = []
    spreads: list[float] = []
    for path in sorted(root.glob(f"{_safe(symbol)}-*.csv"), key=lambda p: int(p.stem.rsplit("-", 1)[1])):
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split(",")
            if len(parts) != 3:
                continue      # a line cut short by a crash
            seen, bid, ask = float(parts[0]), float(parts[1]), float(parts[2])
            mid = (bid + ask) / 2
            spreads.append(ask - bid)
            t = seen // seconds * seconds
            if bars and bars[-1]["t"] == t:
                last = bars[-1]
                last.update(h=max(last["h"], mid), l=min(last["l"], mid), c=mid, v=last["v"] + 1)
            else:
                bars.append({"t": t, "o": mid, "h": mid, "l": mid, "c": mid, "v": 1.0})
    return bars, statistics.median(spreads) if spreads else 0.0


__all__ = ["append", "load_bars", "BAR_SECONDS", "TICK_DIR"]
