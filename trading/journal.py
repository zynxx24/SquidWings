"""Trade journal and loss report.

Every order that the broker accepts and every close is appended to a JSON-lines file.
The report reads that file and says where the money went: win rate, net result, the
worst trades with the reasons they were taken, losing streaks, and which setups lost.

The path comes from ``TRADE_JOURNAL`` (default ``trading/data/journal.jsonl``). It is
user data, so keep it out of git (the folder is ignored).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_lock = threading.Lock()
DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "journal.jsonl"


def path() -> Path:
    return Path(os.getenv("TRADE_JOURNAL", str(DEFAULT_PATH))).expanduser()


def record(event: dict[str, Any]) -> None:
    """Append one event. ``event`` is e.g. {"event": "order", "symbol": ..., "ticket": ...}."""
    row = {"ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), **event}
    target = path()
    with _lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read(since_days: int | None = None) -> list[dict[str, Any]]:
    target = path()
    if not target.exists():
        return []
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if since_days is not None:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=since_days)
        rows = [r for r in rows if _ts(r) >= cutoff]
    return rows


def _ts(row: dict[str, Any]) -> dt.datetime:
    try:
        t = dt.datetime.fromisoformat(row["ts"])
        return t if t.tzinfo else t.replace(tzinfo=dt.UTC)
    except (KeyError, ValueError):
        return dt.datetime.min.replace(tzinfo=dt.UTC)


def trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair each open with its close by ticket. Open trades are left out."""
    opened: dict[int, dict[str, Any]] = {}
    closed: list[dict[str, Any]] = []
    for r in rows:
        if r.get("event") == "order" and r.get("ticket") is not None:
            opened[int(r["ticket"])] = r
        elif r.get("event") == "close" and r.get("ticket") is not None:
            o = opened.pop(int(r["ticket"]), None)
            closed.append({
                "ticket": int(r["ticket"]), "symbol": r.get("symbol") or (o or {}).get("symbol"),
                "side": (o or {}).get("side"), "volume": r.get("volume"),
                "profit": float(r.get("profit") or 0.0), "opened": (o or {}).get("ts"), "closed": r.get("ts"),
                "reason": (o or {}).get("reason", ""), "source": (o or {}).get("source", "manual"),
            })
    return closed


def report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = trades(rows)
    n = len(done)
    if n == 0:
        return {"trades": 0, "note": "no closed trades in the journal yet"}
    wins = [t for t in done if t["profit"] > 0]
    losses = [t for t in done if t["profit"] <= 0]
    gross_win = sum(t["profit"] for t in wins)
    gross_loss = -sum(t["profit"] for t in losses)
    streak = best = 0
    for t in done:
        streak = streak + 1 if t["profit"] <= 0 else 0
        best = max(best, streak)
    by_symbol: dict[str, float] = defaultdict(float)
    by_reason_loss: Counter[str] = Counter()
    for t in done:
        by_symbol[t["symbol"] or "?"] += t["profit"]
        if t["profit"] <= 0:
            by_reason_loss[t["reason"] or "(no reason recorded)"] += 1
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n, 3),
        "net": round(sum(t["profit"] for t in done), 2),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "largest_loss": round(min(t["profit"] for t in done), 2),
        "max_losing_streak": best,
        "net_by_symbol": {k: round(v, 2) for k, v in sorted(by_symbol.items())},
        "losses_by_reason": by_reason_loss.most_common(5),
        "worst_trades": sorted(done, key=lambda t: t["profit"])[:5],
    }


def markdown(rep: dict[str, Any]) -> str:
    """The loss report as text, for the user and for the LLM advisor."""
    if rep.get("trades", 0) == 0:
        return "# Laporan trading\n\nBelum ada trade yang sudah ditutup.\n"
    lines = [
        "# Laporan trading",
        "",
        f"- Trade ditutup: {rep['trades']} (menang {rep['wins']}, rugi {rep['losses']})",
        f"- Win rate: {rep['win_rate'] * 100:.1f}%",
        f"- Hasil bersih: {rep['net']}",
        f"- Profit factor: {rep['profit_factor'] if rep['profit_factor'] is not None else 'tanpa kerugian'}",
        f"- Rata-rata rugi: {rep['avg_loss']}   Rugi terbesar: {rep['largest_loss']}",
        f"- Kerugian beruntun terpanjang: {rep['max_losing_streak']}",
        "",
        "## Hasil per symbol",
    ]
    lines += [f"- {k}: {v}" for k, v in rep["net_by_symbol"].items()] or ["- (kosong)"]
    lines += ["", "## Alasan yang paling sering rugi"]
    lines += [f"- {reason}: {count} kali" for reason, count in rep["losses_by_reason"]] or ["- (tidak ada)"]
    lines += ["", "## Lima trade terburuk"]
    for t in rep["worst_trades"]:
        lines.append(f"- {t['symbol']} {t['side']} {t['volume']} lot: {t['profit']} (dibuka {t['opened']}, alasan: {t['reason'] or '-'})")
    return "\n".join(lines) + "\n"


__all__ = ["record", "read", "trades", "report", "markdown", "path"]
