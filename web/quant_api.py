"""HTTP API for the quantitative proposal and the risk state. Nothing here sends an order.

* GET  /api/quant/propose?symbol=EURUSD  the rule's decision on the last closed M5 bar, with lot size and risk checks
* GET  /api/quant/state                  streak, daily and total loss state
* POST /api/quant/reset                  clear the state (after you have looked at why it halted)
* GET  /api/quant/config                 the limits in force

Every call needs the trading token, as the MT5 panel does. A proposal is a suggestion: the
panel fills the order ticket, and the normal preview and confirm still apply.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from trading import strategy
from trading.quant import engine
from trading.quant import risk as R
from web.mt5_api import _backend, guard

router = APIRouter(prefix="/api/quant", tags=["quant"])
SCALP_MIN_BARS = 400


def _news() -> list[strategy.NewsEvent]:
    """High-impact events from TRADE_NEWS_FILE (a JSON list). No file means no news filter, and the response says so."""
    path = os.getenv("TRADE_NEWS_FILE", "").strip()
    if not path:
        return []
    try:
        items = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"TRADE_NEWS_FILE could not be read: {exc}") from exc
    return strategy.load_news(items)


@router.get("/config", dependencies=[Depends(guard)])
def config() -> dict[str, Any]:
    cfg = engine.config_from_env()
    problems = cfg.validate()
    return {
        "risk_per_trade_pct": cfg.risk_per_trade * 100,
        "max_risk_per_trade_pct": R.MAX_RISK_PER_TRADE * 100,
        "max_open": cfg.max_open,
        "max_consecutive_losses": cfg.max_consecutive_losses,
        "firm_daily_loss_pct": cfg.firm_daily_loss_pct,
        "firm_max_loss_pct": cfg.firm_max_loss_pct,
        "problems": problems,
    }


@router.get("/propose", dependencies=[Depends(guard)])
def propose(symbol: str = Query(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")) -> dict[str, Any]:
    cfg = engine.config_from_env()
    if cfg.validate():
        raise HTTPException(422, {"problems": cfg.validate()})
    try:
        return engine.propose(_backend(), symbol, news=_news(), cfg=cfg)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - broker and data errors are shown, not hidden
        raise HTTPException(502, f"Could not build a proposal: {exc}") from exc


@router.get("/state", dependencies=[Depends(guard)])
def state() -> dict[str, Any]:
    st = engine._state["risk"]
    if st is None:
        return {"started": False}
    return {"started": True, "equity": st.equity, "day": st.day.isoformat(), "day_start_equity": st.day_start_equity,
            "start_equity": st.start_equity, "consecutive_losses": st.consecutive_losses,
            "open_positions": st.open_positions, "halted": st.halted, "notes": st.notes}


@router.post("/reset", dependencies=[Depends(guard)])
def reset() -> dict[str, Any]:
    engine.reset_state()
    return {"reset": True}


# ---------------------------------------------------------------- EA pairing (one click, no typing)


def _bridge_url(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/api/mt4"


def _set_file(secret: str, url: str) -> str:
    """An MT4 preset (.set) with the EA inputs filled in. Load it once in the EA's Inputs tab."""
    return "\n".join([
        f"BridgeUrl={url}",
        f"BridgeSecret={secret}",
        "AllowTrading=false",
        "TickThrottleMs=200",
        "PositionsEverySec=2",
        "CommandsEverySec=1",
        "SymbolsCsv=EURUSD,GBPUSD,XAUUSD",
        "MaxSlippage=20",
        "HISTORY_BARS=5000",
        "",
    ])


@router.get("/bridge", dependencies=[Depends(guard)])
def bridge_status() -> dict[str, Any]:
    from web.mt4_bridge import bridge_secret, store
    with store.lock:
        symbols = sorted(store.ticks)
        # The EA skips unchanged quotes, so a quiet market has old tick times. The account heartbeat
        # (sent every few seconds) is the real "last message" time.
        seen = max([store.account_seen, *(t.get("seen", 0) for t in store.ticks.values())])
    return {
        "paired": bool(bridge_secret()),
        "ea_online": store.alive(),
        "account": store.account.get("login") if store.account else None,
        "live": bool(store.account.get("live")) if store.account else False,
        "symbols": symbols,
        "bars": {s: len(store.bars.get(s, [])) for s in symbols},
        "last_message_s_ago": round(time.time() - seen, 1) if seen else None,
    }


@router.post("/bridge/pair", dependencies=[Depends(guard)])
def bridge_pair(request: Request, rotate: bool = False) -> dict[str, Any]:
    """Create the shared secret (once) and return the EA preset. Rotating disconnects the EA until it is reloaded."""
    from web.mt4_bridge import ensure_secret
    secret = ensure_secret(rotate=rotate)
    url = _bridge_url(request)
    return {
        "bridge_url": url,
        "secret": secret,
        "preset_name": "Mt4Bridge.set",
        "preset": _set_file(secret, url),
        "steps": [
            "Di MetaTrader 4: File > Open Data Folder > MQL4 > Experts, salin Mt4Bridge.mq4 lalu compile (F7).",
            "Tools > Options > Expert Advisors: centang 'Allow WebRequest for listed URL' dan tambahkan " + url + ".",
            "Pasang Mt4Bridge di chart EURUSD (satu chart saja). Di tab Common, centang 'Allow live trading' hanya jika memang mau kirim order.",
            "Di tab Inputs, tekan Load lalu pilih Mt4Bridge.set yang diunduh dari halaman ini.",
        ],
    }


def _scalp_reviewer(use_llm: bool) -> tuple[Any, dict[str, int]]:
    from trading.quant import scalp_review as review

    if not use_llm:
        return review.rule_reviewer, {"llm_calls": 0, "llm_failures": 0}
    from config.secrets import get_secrets
    from config.settings import get_settings
    from model.connection.api_client import APIClient

    settings = get_settings()
    client = APIClient(settings.api_base_url, get_secrets().api_key, int(settings.timeout_seconds))
    return review.llm_reviewer(client, os.getenv("SCALP_LLM_MODEL", "").strip() or settings.default_model)


def _scalp_data(symbol: str) -> tuple[list[dict[str, float]], float, str]:
    """Recorded ticks when there are enough, else M1 bars, else nothing usable."""
    from trading.quant import ticks

    bars, spread = ticks.load_bars(symbol)
    if len(bars) >= SCALP_MIN_BARS:
        return bars, spread, f"recorded ticks in {ticks.BAR_SECONDS}s bars"
    backend = _backend()
    minute = backend.rates(symbol, "M1", 6000)
    if getattr(backend, "name", "") == "mt5" and minute:
        minute = minute[:-1]
    tick = backend.tick(symbol)
    return minute, max(0.0, float(tick["ask"]) - float(tick["bid"])), "M1 bars"


@router.get("/scalp/backtest", dependencies=[Depends(guard)])
def scalp_backtest(
    symbol: str = Query(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$"),
    llm: bool = False,
) -> dict[str, Any]:
    """Five-position, 30-second basket backtest, next to the same run with random entries. Places no orders."""
    from trading.quant import scalp

    try:
        bars, spread, source = _scalp_data(symbol)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - shown to the user, as the other backtest does
        raise HTTPException(502, f"Could not read data for {symbol}: {exc}") from exc
    if len(bars) < SCALP_MIN_BARS:
        raise HTTPException(422, f"Only {len(bars)} short bars available; this backtest needs at least {SCALP_MIN_BARS}.")
    reviewer, usage = _scalp_reviewer(llm)
    result = scalp.run(bars, spread=spread, reviewer=reviewer)
    baseline = scalp.run(bars, spread=spread, random_entries=True)
    return {
        "symbol": symbol, "source": source, "bars": len(bars), "summary": result["summary"], "reviews": result["reviews"][-10:],
        "random_entries": baseline["summary"], "llm": {"used": llm, **usage},
        "warning": "A high win rate alone is not profit: compare the expectancy and the random-entry row. Past results do not show future ones.",
    }


@router.get("/backtest", dependencies=[Depends(guard)])
def backtest(
    request: Request,
    symbol: str = Query(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$"),
    count: int = Query(5000, ge=600, le=20000),
    commission_r: float = Query(0.05, ge=0, le=1),
) -> dict[str, Any]:
    """Walk-forward backtest on the terminal's own M5 history (the demo account's data). Places no orders."""
    from trading.quant import backtest as bt
    b = _backend()
    try:
        bars = b.rates(symbol, "M5", count)
        if getattr(b, "name", "") == "mt5" and bars:
            bars = bars[:-1]
        spec_tick = b.tick(symbol)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Could not read history for {symbol}: {exc}") from exc
    spread = max(0.0, float(spec_tick["ask"]) - float(spec_tick["bid"]))
    if len(bars) < 1200 + 400 + 10:
        raise HTTPException(422, f"Only {len(bars)} closed bars available; the backtest needs at least 1610.")
    res = bt.run(bars, symbol=symbol, train=min(2000, len(bars) // 2), refit=500, spread=spread, commission_r=commission_r)
    return {
        "symbol": symbol, "bars": len(bars), "from": bars[0]["t"], "to": bars[-1]["t"],
        "spread_used": spread, "commission_r": commission_r,
        "summary": res["summary"], "last_trades": res["trades"][-50:],
        "warning": "Historical backtest on the terminal's data. Costs are the current spread and a fixed commission; "
                   "it is not a forward test and does not show future results.",
    }
