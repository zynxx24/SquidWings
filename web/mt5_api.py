"""HTTP API for the MT5 trading panel (``/trading``).

Safety, in order of what a request meets:

1. Trading is off unless ``MT5_TRADE_TOKEN`` is set in the environment. Every call except
   ``/status`` must send that token in the ``X-MT5-Token`` header. The web server has no
   login, and in tunnel mode anyone with the URL can reach it, so the token is what
   keeps strangers from reading the account or placing orders.
2. Real-money accounts are refused unless ``MT5_ALLOW_LIVE=1``.
3. Every order and every close goes in two steps: ``preview`` runs the rules and the
   broker's own check, and returns an id. ``confirm`` sends it. An id works once and
   expires after 60 seconds, so a click cannot be replayed and an old preview cannot
   be sent at a much later price.
4. The rules in ``trading.mt5_rules`` (volume cap, required stop loss, price sanity) run
   again at confirm time, with fresh prices.
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from trading import journal, mt5_rules
from trading.mt5_adapter import Backend, TradingError, make_backend
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/mt5", tags=["mt5"])

PREVIEW_TTL = 60
_state: dict[str, Any] = {"backend": None, "live": False}
_previews: dict[str, dict[str, Any]] = {}


def _local_only(request: Request | None) -> bool:
    """This machine only: the server is not opened to the public, and the request came from loopback."""
    from security.web_access import is_loopback_host, is_public_mode
    if is_public_mode() or request is None or request.client is None:
        return False
    return is_loopback_host(request.client.host)


def token_required() -> bool:
    return bool(os.getenv("MT5_TRADE_TOKEN", "").strip())


def guard(request: Request, x_mt5_token: str | None = Header(default=None)) -> None:
    """Who may trade.

    * Local use (this computer, not tunnelled): no token needed.
    * Public use (tunnel or non-local bind): needs MT5_TRADE_TOKEN, and the server refuses to trade
      without one, because anyone with the URL could otherwise place orders.
    * If MT5_TRADE_TOKEN is set, it is required even locally.
    """
    expected = os.getenv("MT5_TRADE_TOKEN", "").strip()
    if expected:
        if not x_mt5_token or not hmac.compare_digest(x_mt5_token, expected):
            raise HTTPException(401, "Wrong or missing trading token.")
        return
    if _local_only(request):
        return
    raise HTTPException(403, "Trading is off for public access. Set MT5_TRADE_TOKEN in .env and restart the server.")


def _backend() -> Backend:
    backend: Backend | None = _state["backend"]
    if backend is None:
        raise HTTPException(409, "Not connected. Log in first.")
    return backend


def _run(fn: Callable[..., Any], *args: Any) -> Any:
    """Turn a broker or connection failure into a readable error, not a 500."""
    try:
        return fn(*args)
    except TradingError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - the terminal raises many library types
        logger.warning(f"[mt5] {type(exc).__name__}: {exc}")
        raise HTTPException(502, f"Broker error: {exc}") from exc


def _prune() -> None:
    now = time.time()
    for key in [k for k, v in _previews.items() if v["expires"] < now]:
        del _previews[key]


class LoginIn(BaseModel):
    backend: Literal["sim", "mt5", "mt4"] = "sim"
    login: int = Field(0, ge=0)
    password: str = Field("", max_length=200)
    server: str = Field("", max_length=120)
    path: str | None = Field(None, max_length=400)


class OrderIn(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    side: Literal["buy", "sell"]
    volume: float = Field(..., gt=0)
    sl: float | None = None
    tp: float | None = None
    comment: str = Field("", max_length=64)
    reason: str = Field("", max_length=300)   # why the trade was taken; kept in the journal


class CloseIn(BaseModel):
    ticket: int = Field(..., ge=1)
    volume: float | None = Field(None, gt=0)


class ModifyIn(BaseModel):
    ticket: int = Field(..., ge=1)
    sl: float | None = Field(None, gt=0)
    tp: float | None = Field(None, gt=0)


class CancelIn(BaseModel):
    ticket: int = Field(..., ge=1)


class ConfirmIn(BaseModel):
    preview_id: str = Field(..., min_length=10, max_length=64)


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    backend = _state["backend"]
    return {
        "connected": backend is not None,
        "backend": backend.name if backend else None,
        "live": bool(_state["live"]),
        "trading_enabled": token_required() or _local_only(request),
        "token_required": token_required(),
        "limits": mt5_rules.limits(),
    }


@router.post("/connect", dependencies=[Depends(guard)])
def connect(body: LoginIn) -> dict[str, Any]:
    if _state["backend"] is not None:
        _state["backend"].disconnect()
        _state.update(backend=None, live=False)
    backend = _run(make_backend, body.backend)
    account = _run(backend.connect, body.login, body.password, body.server, body.path)
    problems = mt5_rules.check_account(account)
    if problems:
        backend.disconnect()
        raise HTTPException(403, problems[0])
    _state.update(backend=backend, live=bool(account.get("live")))
    logger.info(f"[mt5] connected backend={backend.name} login={account.get('login')} live={account.get('live')}")
    return {"account": account, "backend": backend.name, "live": bool(account.get("live"))}


@router.post("/disconnect", dependencies=[Depends(guard)])
def disconnect() -> dict[str, Any]:
    backend = _state["backend"]
    if backend is not None:
        _run(backend.disconnect)
    _state.update(backend=None, live=False)
    _previews.clear()
    return {"connected": False}


@router.get("/account", dependencies=[Depends(guard)])
def account() -> dict[str, Any]:
    result: dict[str, Any] = _run(_backend().account)
    return result


@router.get("/symbols", dependencies=[Depends(guard)])
def symbols(q: str = Query("", max_length=32)) -> dict[str, Any]:
    return {"symbols": _run(_backend().symbols, q)}


@router.get("/symbol", dependencies=[Depends(guard)])
def symbol(symbol: str = Query(..., max_length=32)) -> dict[str, Any]:
    b = _backend()
    return {"spec": _run(b.symbol_spec, symbol), "tick": _run(b.tick, symbol)}


@router.get("/rates", dependencies=[Depends(guard)])
def rates(symbol: str = Query(..., max_length=32), tf: str = Query("H1", max_length=4),
          count: int = Query(200, ge=10, le=6000)) -> dict[str, Any]:
    if tf not in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
        raise HTTPException(400, f"Unknown timeframe {tf}")
    return {"bars": _run(_backend().rates, symbol, tf, count)}


@router.get("/positions", dependencies=[Depends(guard)])
def positions() -> dict[str, Any]:
    return {"positions": _run(_backend().positions)}


@router.get("/orders", dependencies=[Depends(guard)])
def orders() -> dict[str, Any]:
    return {"orders": _run(_backend().orders)}


@router.get("/history", dependencies=[Depends(guard)])
def history(days: int = Query(7, ge=1, le=365)) -> dict[str, Any]:
    return {"deals": _run(_backend().history, days)}


def _entry_price(tick: dict[str, Any], side: str) -> float:
    return float(tick["ask"] if side == "buy" else tick["bid"])


def _risk(b: Backend, symbol: str, side: str, volume: float, sl: float | None, equity: float) -> dict[str, Any] | None:
    """Money lost if the stop is hit, from the broker's own profit calculation. None when there is no stop."""
    if not sl:
        return None
    entry = _entry_price(_run(b.tick, symbol), side)
    per_lot = _run(b.risk_per_lot, symbol, side, entry, sl)
    money = mt5_rules.risk_at_stop(volume, per_lot)
    return {"money": money, "per_lot": round(per_lot, 2),
            "pct_of_equity": round(money / equity * 100, 2) if equity > 0 else None}


@router.get("/risk", dependencies=[Depends(guard)])
def risk(symbol: str = Query(..., max_length=32), side: Literal["buy", "sell"] = "buy",
         volume: float = Query(0.01, gt=0), sl: float | None = Query(None, gt=0)) -> dict[str, Any]:
    """What a stop would cost at this volume. Places nothing; the panel calls it while the ticket is edited."""
    b = _backend()
    equity = float(_run(b.account)["equity"])
    spec = _run(b.symbol_spec, symbol)
    volume = round(volume, 8)
    if not sl:
        return {"risk": None, "equity": equity}
    tick = _run(b.tick, symbol)
    entry = _entry_price(tick, side)
    return {
        "risk": _risk(b, symbol, side, volume, sl, equity),
        "equity": equity,
        "stop_distance": round(abs(entry - sl), spec["digits"]),
        "stops_level": spec.get("stops_level", 0),
    }


@router.post("/preview", dependencies=[Depends(guard)])
def preview_order(body: OrderIn) -> dict[str, Any]:
    _prune()
    b = _backend()
    order = body.model_dump()
    spec = _run(b.symbol_spec, order["symbol"])
    tick = _run(b.tick, order["symbol"])
    problems = mt5_rules.check_order(order, spec, tick)
    if problems:
        raise HTTPException(422, {"problems": problems})
    check = _run(b.check, order)
    if not check.get("ok"):
        raise HTTPException(422, {"problems": [f"Broker refused the order: {check.get('comment')}"], "check": check})
    equity = float(_run(b.account)["equity"])
    pid = secrets.token_urlsafe(16)
    _previews[pid] = {"kind": "order", "order": order, "expires": time.time() + PREVIEW_TTL}
    return {"preview_id": pid, "expires_in": PREVIEW_TTL, "order": order,
            "price": _entry_price(tick, order["side"]), "check": check,
            "risk": _risk(b, order["symbol"], order["side"], float(order["volume"]), order.get("sl"), equity),
            "equity": equity}


@router.post("/close/preview", dependencies=[Depends(guard)])
def preview_close(body: CloseIn) -> dict[str, Any]:
    _prune()
    b = _backend()
    pos = next((p for p in _run(b.positions) if p["ticket"] == body.ticket), None)
    if pos is None:
        raise HTTPException(404, f"Position {body.ticket} is not open.")
    volume = body.volume or pos["volume"]
    if volume > pos["volume"]:
        raise HTTPException(422, {"problems": [f"Closing {volume} is more than the open {pos['volume']}."]})
    pid = secrets.token_urlsafe(16)
    _previews[pid] = {"kind": "close", "ticket": body.ticket, "volume": volume, "expires": time.time() + PREVIEW_TTL}
    return {"preview_id": pid, "expires_in": PREVIEW_TTL, "position": pos, "volume": volume}


def _take(preview_id: str, kind: str) -> dict[str, Any]:
    _prune()
    item = _previews.pop(preview_id, None)
    if item is None:
        raise HTTPException(410, "This preview expired or was already used. Preview again.")
    if item["kind"] != kind:
        raise HTTPException(400, "Wrong kind of preview.")
    return item


@router.post("/order/confirm", dependencies=[Depends(guard)])
def confirm_order(body: ConfirmIn) -> dict[str, Any]:
    item = _take(body.preview_id, "order")
    b = _backend()
    order = item["order"]
    spec = _run(b.symbol_spec, order["symbol"])
    tick = _run(b.tick, order["symbol"])
    problems = mt5_rules.check_order(order, spec, tick)
    if problems:
        raise HTTPException(422, {"problems": problems})
    result = _run(b.send, order)
    logger.info(f"[mt5] order {order['side']} {order['volume']} {order['symbol']} ok={result.get('ok')} retcode={result.get('retcode')} comment={result.get('comment')}")
    if result.get("ok"):
        journal.record({"event": "order", "ticket": result.get("order") or result.get("deal"), "symbol": order["symbol"],
                        "side": order["side"], "volume": order["volume"], "sl": order.get("sl"), "tp": order.get("tp"),
                        "reason": order.get("reason", ""), "source": "manual", "backend": b.name})
    return {"result": result, "order": order}


@router.post("/close/confirm", dependencies=[Depends(guard)])
def confirm_close(body: ConfirmIn) -> dict[str, Any]:
    item = _take(body.preview_id, "close")
    b = _backend()
    # The position's profit just before closing; the close result does not always carry it.
    pos: dict[str, Any] = next((p for p in _run(b.positions) if p["ticket"] == item["ticket"]), {})
    result = _run(b.close, item["ticket"], item["volume"])
    logger.info(f"[mt5] close ticket={item['ticket']} volume={item['volume']} ok={result.get('ok')} comment={result.get('comment')}")
    if result.get("ok"):
        profit = result.get("profit", pos.get("profit", 0.0))
        journal.record({"event": "close", "ticket": item["ticket"], "symbol": pos.get("symbol"),
                        "volume": item["volume"], "profit": profit, "backend": b.name})
        try:   # the streak and daily-loss state must see every closed trade
            from trading.quant import engine as quant_engine
            quant_engine.on_close(float(profit), float(_run(b.account)["equity"]))
        except Exception as exc:  # noqa: BLE001 - the close already happened; a state error must not hide it
            logger.warning(f"[mt5] risk state not updated after close: {exc}")
    return {"result": result}


# ---------------------------------------------------------------- change SL/TP and cancel pending orders


@router.post("/modify/preview", dependencies=[Depends(guard)])
def preview_modify(body: ModifyIn) -> dict[str, Any]:
    _prune()
    b = _backend()
    pos = next((p for p in _run(b.positions) if p["ticket"] == body.ticket), None)
    if pos is None:
        raise HTTPException(404, f"Position {body.ticket} is not open.")
    spec = _run(b.symbol_spec, pos["symbol"])
    tick = _run(b.tick, pos["symbol"])
    problems = mt5_rules.check_modify(pos, body.sl, body.tp, spec, tick)
    if problems:
        raise HTTPException(422, {"problems": problems})
    change = {"ticket": body.ticket, "sl": body.sl, "tp": body.tp}
    pid = secrets.token_urlsafe(16)
    _previews[pid] = {"kind": "modify", "change": change, "expires": time.time() + PREVIEW_TTL}
    equity = float(_run(b.account)["equity"])
    return {"preview_id": pid, "expires_in": PREVIEW_TTL, "position": pos, "change": change,
            "price": _entry_price(tick, pos["side"]),
            "risk": _risk(b, pos["symbol"], pos["side"], pos["volume"], body.sl, equity)}


@router.post("/modify/confirm", dependencies=[Depends(guard)])
def confirm_modify(body: ConfirmIn) -> dict[str, Any]:
    item = _take(body.preview_id, "modify")
    b = _backend()
    change = item["change"]
    pos = next((p for p in _run(b.positions) if p["ticket"] == change["ticket"]), None)
    if pos is None:
        raise HTTPException(409, f"Position {change['ticket']} closed before the change was sent.")
    spec = _run(b.symbol_spec, pos["symbol"])
    tick = _run(b.tick, pos["symbol"])   # fresh prices: the preview may be up to a minute old
    problems = mt5_rules.check_modify(pos, change["sl"], change["tp"], spec, tick)
    if problems:
        raise HTTPException(422, {"problems": problems})
    result = _run(b.modify, change["ticket"], change["sl"], change["tp"])
    logger.info(f"[mt5] modify ticket={change['ticket']} ok={result.get('ok')} retcode={result.get('retcode')} comment={result.get('comment')}")
    if result.get("ok"):
        journal.record({"event": "modify", "ticket": change["ticket"], "symbol": pos["symbol"], "side": pos["side"],
                        "sl": change["sl"], "tp": change["tp"], "backend": b.name})
    return {"result": result}


@router.post("/cancel/preview", dependencies=[Depends(guard)])
def preview_cancel(body: CancelIn) -> dict[str, Any]:
    _prune()
    b = _backend()
    order = next((o for o in _run(b.orders) if o["ticket"] == body.ticket), None)
    if order is None:
        raise HTTPException(404, f"Order {body.ticket} is not pending.")
    pid = secrets.token_urlsafe(16)
    _previews[pid] = {"kind": "cancel", "ticket": body.ticket, "expires": time.time() + PREVIEW_TTL}
    return {"preview_id": pid, "expires_in": PREVIEW_TTL, "order": order}


@router.post("/cancel/confirm", dependencies=[Depends(guard)])
def confirm_cancel(body: ConfirmIn) -> dict[str, Any]:
    item = _take(body.preview_id, "cancel")
    b = _backend()
    result = _run(b.cancel, item["ticket"])
    logger.info(f"[mt5] cancel order={item['ticket']} ok={result.get('ok')}")
    if result.get("ok"):
        journal.record({"event": "cancel", "ticket": item["ticket"], "backend": b.name})
    return {"result": result}
