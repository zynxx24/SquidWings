"""What the trading server sees right now, for the chat agent. Read-only: it cannot send, close or change anything.

The trading backend runs as its own process (``python main.py trading``, port 80 by default) and
holds the MT4 EA's stream. This tool asks its read-only endpoints over loopback, so the agent can
reason about real numbers (account, positions, the rule's proposal and its reasons, stop
suggestions, a walk-forward backtest, and with ``levels`` the trader's checklist: pivot and
Fibonacci zones, the market's clocks, each hour's seasonality and the cycle, with their tests)
instead of guessing. Orders stay with the panel's
preview and confirm.

TRADING_SERVER_URL overrides the address (default http://127.0.0.1).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SECTIONS = ("status", "account", "positions", "orders", "history", "proposal", "manage", "risk_state", "bridge", "backtest",
            "levels")
DEFAULT_SECTIONS = ("status", "account", "positions", "proposal", "manage", "risk_state")
_SYMBOL = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_TIMEOUT_S = 8.0
_BACKTEST_TIMEOUT_S = 120.0


def _base() -> str:
    url = os.getenv("TRADING_SERVER_URL", "").strip() or "http://127.0.0.1"
    parts = urllib.parse.urlsplit(url)
    # Loopback only: this tool reads a local service, it is not a way to fetch arbitrary URLs.
    if parts.scheme not in ("http", "https") or (parts.hostname or "") not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("TRADING_SERVER_URL must point at this machine (127.0.0.1 or localhost)")
    return url.rstrip("/")


def _get(path: str, timeout: float = _TIMEOUT_S) -> tuple[int, Any]:
    request = urllib.request.Request(_base() + path, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback, checked in _base
            return response.status, json.loads(response.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail")
        except (ValueError, AttributeError):
            detail = exc.reason
        return exc.code, {"error": detail}


def _paths(symbol: str, days: int) -> dict[str, tuple[str, float]]:
    q = urllib.parse.quote(symbol)
    return {
        "status": ("/api/mt5/status", _TIMEOUT_S),
        "account": ("/api/mt5/account", _TIMEOUT_S),
        "positions": ("/api/mt5/positions", _TIMEOUT_S),
        "orders": ("/api/mt5/orders", _TIMEOUT_S),
        "history": (f"/api/mt5/history?days={days}", _TIMEOUT_S),
        "proposal": (f"/api/quant/propose?symbol={q}", 60.0),
        "manage": ("/api/quant/manage", _TIMEOUT_S * 2),
        "risk_state": ("/api/quant/state", _TIMEOUT_S),
        "bridge": ("/api/quant/bridge", _TIMEOUT_S),
        "backtest": (f"/api/quant/backtest?symbol={q}", _BACKTEST_TIMEOUT_S),
        "levels": (f"/api/quant/considerations?symbol={q}&timeframe=H1", 30.0),
    }


def _trim_levels(view: dict[str, Any]) -> dict[str, Any]:
    """The checklist without what only the chart needs: the session times, every hour's row, every far zone."""
    out = {k: v for k, v in view.items() if k not in ("session_bands", "seasonality", "levels")}
    levels = dict(view.get("levels") or {})
    levels.pop("zones", None)
    out["levels"] = levels
    season = view.get("seasonality") or {}
    out["seasonality"] = {k: season.get(k) for k in ("ok", "days", "next_hour", "today", "significant", "verdict", "reason")
                          if k in season}
    return out


def _snapshot_sync(symbol: str, sections: list[str], days: int) -> dict[str, Any]:
    try:
        _base()
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    wanted = [s for s in sections if s in SECTIONS] or list(DEFAULT_SECTIONS)
    if any(s in ("proposal", "backtest", "levels") for s in wanted) and not _SYMBOL.match(symbol or ""):
        return {"ok": False, "error": "A symbol such as XAUUSD or EURUSD is needed for the proposal and the backtest."}
    paths = _paths(symbol, days)
    out: dict[str, Any] = {"ok": True, "server": _base(), "symbol": symbol or None}
    for name in wanted:
        path, timeout = paths[name]
        try:
            code, body = _get(path, timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"ok": False, "error": (f"The trading server at {_base()} is not answering ({exc}). "
                                           "Start it with: python main.py trading")}
        out[name] = body if code == 200 else {"error": (body or {}).get("error"), "http_status": code}
    if isinstance(out.get("levels"), dict) and "levels" in out["levels"]:
        out["levels"] = _trim_levels(out["levels"])
    if isinstance(out.get("backtest"), dict) and "last_trades" in out["backtest"]:
        out["backtest"]["last_trades"] = out["backtest"]["last_trades"][-10:]
    out["note"] = ("Read-only data. You cannot place, close or change orders; the user does that in the "
                   "trading panel with preview and confirm. Never promise profit.")
    return out


async def trading_snapshot(symbol: str = "", sections: list[str] | None = None, history_days: int = 7) -> dict[str, Any]:
    """Account, positions, the rule's proposal and stop suggestions from the local trading server."""
    days = max(1, min(int(history_days or 7), 90))
    return await asyncio.to_thread(_snapshot_sync, (symbol or "").strip().upper(), list(sections or []), days)


__all__ = ["trading_snapshot", "SECTIONS", "DEFAULT_SECTIONS"]
