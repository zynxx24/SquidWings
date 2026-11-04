"""Receives the MT4 EA's stream and carries its orders back (see trading/mt4/Mt4Bridge.mq4).

The EA pushes ticks, closed M5 bars, account and positions to ``/api/mt4/ingest`` and polls
``/api/mt4/commands`` for orders to run. Results come back on ``/api/mt4/result``.

Shared secret: every call from the EA must carry ``X-Bridge-Secret`` equal to
``MT4_BRIDGE_SECRET``. Without that variable the bridge is off. The panel reaches the
bridge through ``trading.mt4_backend``, which uses the same order rules and the
same preview/confirm flow as MT5.
"""

from __future__ import annotations

import contextlib
import hmac
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/mt4", tags=["mt4"])

BAR_KEEP = 6000
EA_TIMEOUT = 15          # seconds without a message before the EA counts as gone
RESULT_WAIT = 8          # seconds to wait for the EA to report an order's outcome


class Store:
    """Everything the EA has sent, plus the queue of commands waiting for it. Thread-safe."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ticks: dict[str, dict[str, Any]] = {}
        self.bars: dict[str, deque[dict[str, Any]]] = {}
        self.account: dict[str, Any] | None = None
        self.account_seen = 0.0
        self.positions: list[dict[str, Any]] = []
        self.commands: deque[dict[str, Any]] = deque()
        self.results: dict[str, str] = {}

    def ingest(self, items: list[dict[str, Any]]) -> int:
        with self.lock:
            for item in items:
                kind = item.get("type")
                if kind == "tick":
                    self.ticks[item["symbol"]] = {**item, "seen": time.time()}
                elif kind == "bar":
                    series = self.bars.setdefault(item["symbol"], deque(maxlen=BAR_KEEP))
                    bar = {"t": int(item["time"]), "o": float(item["o"]), "h": float(item["h"]),
                           "l": float(item["l"]), "c": float(item["c"]), "v": int(item.get("v", 0))}
                    if not series or series[-1]["t"] < bar["t"]:
                        series.append(bar)
                    elif series[-1]["t"] == bar["t"]:
                        series[-1] = bar
                elif kind == "account":
                    self.account = {k: v for k, v in item.items() if k not in ("type", "positions")}
                    self.account_seen = time.time()
                    self.positions = list(item.get("positions", []))
            return len(items)

    def alive(self) -> bool:
        return self.account is not None and time.time() - self.account_seen < EA_TIMEOUT

    def pop_commands(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            out = []
            while self.commands and len(out) < limit:
                out.append(self.commands.popleft())
            return out

    def enqueue(self, cmd: dict[str, Any]) -> str:
        cid = secrets.token_hex(6)
        with self.lock:
            self.commands.append({**cmd, "id": cid})
        return cid

    def wait_result(self, cid: str, timeout: float) -> str | None:
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if cid in self.results:
                    return self.results.pop(cid)
            time.sleep(0.1)
        return None

    def set_results(self, lines: list[str]) -> None:
        with self.lock:
            for line in lines:
                cid, _, outcome = line.partition("|")
                if cid:
                    self.results[cid] = outcome.strip()


store = Store()


SECRET_FILE = Path(__file__).resolve().parent.parent / "trading" / "data" / "bridge_secret"


def bridge_secret() -> str:
    """The shared secret: MT4_BRIDGE_SECRET from .env if set, else the one the panel generated."""
    env = os.getenv("MT4_BRIDGE_SECRET", "").strip()
    if env:
        return env
    try:
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_secret(rotate: bool = False) -> str:
    """Create the secret on first pairing (or on request). Stored with owner-only permissions."""
    if not rotate:
        current = bridge_secret()
        if current:
            return current
    secret = secrets.token_urlsafe(24)
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.write_text(secret + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):   # not every filesystem supports chmod (e.g. Windows)
        os.chmod(SECRET_FILE, 0o600)
    return secret


def _secret_ok(header: str | None) -> None:
    expected = bridge_secret()
    if not expected:
        raise HTTPException(403, "MT4 bridge is not paired yet. Open the trading page and press 'Hubungkan EA'.")
    if not header or not hmac.compare_digest(header, expected):
        raise HTTPException(401, "Bad bridge secret.")


@router.post("/ingest")
async def ingest(request: Request, x_bridge_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _secret_ok(x_bridge_secret)
    body = await request.json()
    items = body if isinstance(body, list) else [body]
    n = store.ingest([i for i in items if isinstance(i, dict)])
    return {"ok": True, "received": n}


@router.get("/commands", response_class=PlainTextResponse)
def commands(ea: str = "", x_bridge_secret: str | None = Header(default=None)) -> str:
    _secret_ok(x_bridge_secret)
    lines = []
    for c in store.pop_commands():
        lines.append("|".join(str(c.get(k, "")) for k in
                              ("id", "action", "symbol", "side", "volume", "sl", "tp", "comment", "ticket")))
    return "\n".join(lines)


@router.post("/result", response_class=PlainTextResponse)
async def result(request: Request, x_bridge_secret: str | None = Header(default=None)) -> str:
    _secret_ok(x_bridge_secret)
    text = (await request.body()).decode("utf-8", "replace")
    store.set_results([ln for ln in text.splitlines() if ln.strip()])
    return "ok"


__all__ = ["router", "store", "Store", "EA_TIMEOUT", "RESULT_WAIT"]
