"""HTTP API for the quantitative proposal and the risk state. Nothing here sends an order.

* GET  /api/quant/propose?symbol=EURUSD  the rule's decision on the last closed M5 bar, with lot size and risk checks
* GET  /api/quant/state                  streak, daily and total loss state
* POST /api/quant/reset                  clear the state (after you have looked at why it halted)
* GET  /api/quant/config                 the limits in force

Reading needs no token, as on the MT5 panel: config, proposals, state, the EA status, the rule
backtests and the forward test's report. Changing something (reset, pairing the EA, starting or
stopping the forward test, a backtest reviewed by the LLM, which costs API calls) goes through the
same guard as an order. A proposal is a suggestion: the panel fills the order ticket, and the
normal preview and confirm still apply.

* GET  /api/quant/news?symbol=XAUUSD     calendar, crypto market, headlines by topic, and the MT4 market condition
* GET  /api/quant/llm                    the trading LLM settings; POST changes them (guarded)
* GET  /api/quant/llm/models             models to choose from (Router-page providers and models already used)
* GET  /api/quant/sim/logs               the saved per-cycle logs; /sim/logs/{name} downloads one
* GET  /api/quant/considerations?symbol= the trader's checklist picture: price levels and confluence zones, the
                                         market's clocks, each hour's seasonality, the cycle, session times for the chart
* GET  /api/quant/riskmath               the risk arithmetic for one plan: size, break-even rate, Kelly, streaks, ruin
"""

from __future__ import annotations

import contextlib
import json
import multiprocessing
import os
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from addons.trading import news as market_news
from addons.trading import strategy
from addons.trading.quant import (
    analyst,
    autotrade,
    cycles,
    engine,
    forward,
    llm_trader,
    memory,
    papersim,
    playbook,
    riskmath,
)
from addons.trading.quant import brain as brain_mod
from addons.trading.quant import risk as R
from addons.trading.web.mt5_api import _backend, _view_backend, guard
from brain.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/quant", tags=["quant"])
WORKER_TIMEOUT_S = 300
_worker: ProcessPoolExecutor | None = None
_forward: forward.ForwardTest | None = None
_sim: papersim.PaperSim | None = None
_worker_lock = threading.Lock()
SCALP_MIN_BARS = 400


def _news() -> list[strategy.NewsEvent]:
    """High-impact events from TRADE_NEWS_FILE (a JSON list), else the free economic calendar.

    TRADE_NEWS_AUTO=0 turns the calendar off. A calendar that cannot be fetched means no news filter.
    """
    path = os.getenv("TRADE_NEWS_FILE", "").strip()
    if not path:
        if os.getenv("TRADE_NEWS_AUTO", "1").strip() == "0":
            return []
        try:
            return strategy.load_news(market_news.get_desk().calendar_events())
        except Exception as exc:  # noqa: BLE001 - the proposal still works without the calendar
            logger.warning(f"[quant] economic calendar unavailable: {exc}")
            return []
    try:
        items = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"TRADE_NEWS_FILE could not be read: {exc}") from exc
    return strategy.load_news(items)


@router.get("/config")
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


@router.get("/propose")
def propose(symbol: str = Query(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")) -> dict[str, Any]:
    cfg = engine.config_from_env()
    if cfg.validate():
        raise HTTPException(422, {"problems": cfg.validate()})
    try:
        return engine.propose(_view_backend(), symbol, news=_news(), cfg=cfg)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - broker and data errors are shown, not hidden
        raise HTTPException(502, f"Could not build a proposal: {exc}") from exc


@router.get("/manage")
def manage() -> dict[str, Any]:
    """A suggested stop for each open position: break-even, then an ATR trail. Sends nothing.

    The panel can put a suggestion in the SL/TP form, where the usual preview and confirm apply.
    """
    from addons.trading.quant import features as F
    from addons.trading.quant import model as M

    backend = _view_backend()
    out: list[dict[str, Any]] = []
    try:
        positions = backend.positions()
        by_symbol: dict[str, list[dict[str, float]]] = {}
        for pos in positions:
            symbol = pos["symbol"]
            if symbol not in by_symbol:
                bars = backend.rates(symbol, "M5", 300)
                if getattr(backend, "name", "") == "mt5" and bars:
                    bars = bars[:-1]
                by_symbol[symbol] = bars
            bars = by_symbol[symbol]
            atr = F.wilder_atr(bars)[-1] if bars else float("nan")
            since = [b for b in bars if b["t"] >= int(pos.get("time") or 0)]
            move = M.manage(pos, since, atr)
            out.append({"ticket": pos["ticket"], "symbol": symbol, "side": pos["side"], "sl": pos.get("sl"),
                        "suggestion": move})
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - broker and data errors are shown, not hidden
        raise HTTPException(502, f"Could not check the open positions: {exc}") from exc
    return {"positions": out, "rule": {"break_even_after_r": M.BE_TRIGGER_R, "lock_r": M.BE_LOCK_R, "trail_atr": M.TRAIL_ATR},
            "note": "Suggestions only. Nothing is changed until you preview and confirm the new stop."}


@router.get("/state")
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
        "PositionsEverySec=1",
        "CommandsEveryMs=250",
        "SymbolsCsv=EURUSD,GBPUSD,XAUUSD",
        "MaxSlippage=20",
        "HISTORY_BARS=5000",
        "",
    ])


@router.get("/bridge")
def bridge_status() -> dict[str, Any]:
    from addons.trading.web.mt4_bridge import EA_VERSION, bridge_secret, store
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
        "trading_allowed": bool(store.account.get("trade_allowed")) if store.account else False,
        "ea_outdated": store.account is not None and int(store.account.get("ver") or 1) < EA_VERSION,
        "bars": {s: len(store.bars.get(s, [])) for s in symbols},
        "last_message_s_ago": round(time.time() - seen, 1) if seen else None,
    }


@router.post("/bridge/pair", dependencies=[Depends(guard)])
def bridge_pair(request: Request, rotate: bool = False) -> dict[str, Any]:
    """Create the shared secret (once) and return the EA preset. Rotating disconnects the EA until it is reloaded."""
    from addons.trading.web.mt4_bridge import ensure_secret
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


def _in_worker(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a heavy pure-Python job in its own process.

    A thread would hold the GIL for the whole job, and every price update, order and page refresh
    would wait behind it.
    """
    global _worker
    if os.getenv("QUANT_WORKER", "1").strip() == "0":
        return fn(*args, **kwargs)
    with _worker_lock:
        if _worker is None:
            _worker = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
        worker = _worker
    try:
        return worker.submit(fn, *args, **kwargs).result(timeout=WORKER_TIMEOUT_S)
    except BrokenProcessPool:
        with _worker_lock:
            _worker = None
        return fn(*args, **kwargs)


def _short_history(found: int, needed: int) -> str:
    return (f"Baru ada {found} bar, butuh minimal {needed}. Lepas Mt4Bridge dari chart lalu pasang lagi "
            "supaya EA mengirim history-nya. Bar berikutnya tersimpan di disk, jadi cukup sekali.")


def _scalp_reviewer(use_llm: bool) -> tuple[Any, dict[str, int]]:
    from addons.trading.quant import scalp_review as review

    if not use_llm:
        return review.rule_reviewer, {"llm_calls": 0, "llm_failures": 0}
    from brain.config.secrets import get_secrets
    from brain.config.settings import get_settings
    from brain.model.connection.api_client import APIClient

    settings = get_settings()
    client = APIClient(settings.api_base_url, get_secrets().api_key, int(settings.timeout_seconds))
    return review.llm_reviewer(client, os.getenv("SCALP_LLM_MODEL", "").strip() or settings.default_model)


def _scalp_data(symbol: str) -> tuple[list[dict[str, float]], float, str]:
    """Recorded ticks when there are enough, else M1 bars, else nothing usable."""
    from addons.trading.quant import ticks

    bars, spread = ticks.load_bars(symbol)
    if len(bars) >= SCALP_MIN_BARS:
        return bars, spread, f"recorded ticks in {ticks.BAR_SECONDS}s bars"
    backend = _view_backend()
    minute = backend.rates(symbol, "M1", 6000)
    if getattr(backend, "name", "") == "mt5" and minute:
        minute = minute[:-1]
    tick = backend.tick(symbol)
    return minute, max(0.0, float(tick["ask"]) - float(tick["bid"])), "M1 bars"


@router.get("/scalp/backtest")
def scalp_backtest(
    request: Request,
    symbol: str = Query(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$"),
    llm: bool = False,
    x_mt5_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Five-position, 30-second basket backtest, next to the same run with random entries. Places no orders."""
    from addons.trading.quant import scalp

    if llm:   # the LLM reviewer spends API credit, so it is guarded like an order
        guard(request, x_mt5_token)

    try:
        bars, spread, source = _scalp_data(symbol)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - shown to the user, as the other backtest does
        raise HTTPException(502, f"Could not read data for {symbol}: {exc}") from exc
    if len(bars) < SCALP_MIN_BARS:
        raise HTTPException(422, _short_history(len(bars), SCALP_MIN_BARS))
    try:
        reviewer, usage = _scalp_reviewer(llm)
        # An LLM reviewer holds a live connection, so it stays here. The rule can move to the worker.
        result = scalp.run(bars, spread=spread, reviewer=reviewer) if llm else _in_worker(scalp.run, bars, spread=spread, reviewer=reviewer)
        baseline = _in_worker(scalp.run, bars, spread=spread, random_entries=True)
    except Exception as exc:  # noqa: BLE001 - shown to the user instead of a bare 500
        logger.exception("[quant] scalp backtest failed")
        raise HTTPException(502, f"Backtest gagal: {exc}") from exc
    return {
        "symbol": symbol, "source": source, "bars": len(bars), "summary": result["summary"], "reviews": result["reviews"][-10:],
        "random_entries": baseline["summary"], "llm": {"used": llm, **usage},
        "warning": "A high win rate alone is not profit: compare the expectancy and the random-entry row. Past results do not show future ones.",
    }


class ForwardIn(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    minutes: float = Field(..., gt=0, le=forward.MAX_MINUTES)
    llm_mode: str | None = Field(None, pattern=r"^(off|confirm|decide)$")   # None: the saved setting


def _trader(model: str | None = None, max_calls: int | None = None, session_tag: str = "trading-llm") -> llm_trader.LLMTrader:
    """The trading LLM, only with a model a Router provider really serves: a wrong id fails here, not mid-run."""
    cfg = llm_trader.load_config()
    if (model or "").strip() == "claude-code":      # a Claude Code session answers from a folder queue, no Router model
        from addons.trading.quant.claude_bridge import ClaudeCodeTrader
        return ClaudeCodeTrader(max_calls=max_calls or int(cfg["max_calls"]), timeout_s=300.0)
    try:
        served = llm_trader.served_models_sync()["models"]
    except Exception as exc:  # noqa: BLE001 - providers unreachable: the run says so if the model fails
        logger.warning(f"[quant] could not list the Router's models: {exc}")
        served = []
    chosen, problem = llm_trader.resolve_model(model or cfg["model"], served)
    if problem:
        raise HTTPException(422, problem)
    return llm_trader.LLMTrader(chosen, max_calls=max_calls or int(cfg["max_calls"]),
                                timeout_s=float(cfg["timeout_s"]), session_tag=session_tag)


@router.post("/forward/start", dependencies=[Depends(guard)])
def forward_start(body: ForwardIn) -> dict[str, Any]:
    """Start the demo-only forward test. It places real orders on the demo account by itself."""
    from addons.trading.web.mt4_bridge import store as mt4_store

    global _forward
    if _forward and _forward.state == "running":
        raise HTTPException(409, "Forward test masih berjalan.")
    backend = _backend()
    if backend.account().get("live"):
        raise HTTPException(403, "Forward test hanya untuk akun demo.")
    if backend.name == "mt4" and not (mt4_store.account or {}).get("trade_allowed"):
        raise HTTPException(409, "EA menolak order: AllowTrading di chart masih mati. Tekan F7 di chart, tab Inputs.")
    mode = body.llm_mode or llm_trader.load_config()["forward_mode"]
    trader = _trader(session_tag="trading-forward") if mode != "off" else None
    desk = market_news.get_desk()
    _forward = forward.ForwardTest(backend, body.symbol, body.minutes, llm=trader, llm_mode=mode,
                                   news=(lambda: market_news.brief(desk.snapshot(body.symbol, fetch=False))) if trader else None)
    threading.Thread(target=_forward.run, name="forward-test", daemon=True).start()
    return {"started": True, "file": str(_forward.path), "llm_mode": mode, "llm_model": trader.model if trader else ""}


@router.post("/forward/stop", dependencies=[Depends(guard)])
def forward_stop() -> dict[str, Any]:
    if _forward is None:
        raise HTTPException(404, "Belum ada forward test.")
    _forward.stop_event.set()
    return _forward.report()


@router.get("/forward/status")
def forward_status() -> dict[str, Any]:
    if _forward is None:
        raise HTTPException(404, "Belum ada forward test.")
    return _forward.report()


class SimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")   # a setting this server does not know is refused, not silently dropped
    symbol: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    hold_s: float = Field(papersim.HOLD_S, ge=5, le=600)
    target: float = Field(papersim.TARGET, gt=0.5, lt=1)
    min_trades: int = Field(papersim.MIN_TRADES, ge=5, le=1000)
    max_minutes: float = Field(60, gt=0, le=papersim.MAX_MINUTES_DAY)   # scalping stops at MAX_MINUTES
    stop_on_find: bool = True
    timeframe: str = Field("", pattern=r"^(|M15|M30|H1|H4)$")         # "" scalping, else the intraday style
    hold_bars: int = Field(papersim.HOLD_BARS, ge=1, le=48)
    stop_atr: float = Field(papersim.STOP_ATR, ge=0, le=10)
    target_atr: float = Field(papersim.TARGET_ATR, ge=0, le=10)
    brain: bool = True                                                  # intraday: the neural brain trades, learns and briefs the LLM
    trail_atr: float = Field(0.0, ge=0, le=5)                           # intraday, brain and LLM positions: trailing exit (0 = fixed target)
    lock_atr: float = Field(0.3, ge=0, le=2)
    llm: bool | None = None                   # None: the saved setting
    llm_model: str | None = Field(None, max_length=200)
    llm_max_calls: int | None = Field(None, ge=1, le=2000)
    news: bool = True
    candle: bool = True                       # intraday: the candle model's scenarios trade as their own rule, when a model exists


@router.post("/sim/start", dependencies=[Depends(guard)])
def sim_start(body: SimIn) -> dict[str, Any]:
    """Start the paper simulation: virtual positions on the live price, no order is ever sent.

    With the LLM on, every cycle is one model call (up to ``llm_max_calls``), which costs tokens.
    """
    global _sim
    if _sim and _sim.state == "running":
        raise HTTPException(409, "Simulasi masih berjalan. Hentikan dulu.")
    backend = _view_backend()
    try:
        backend.tick(body.symbol)
    except Exception as exc:  # noqa: BLE001 - an unknown symbol or a dead feed, said plainly
        raise HTTPException(409, f"Harga {body.symbol} tidak bisa dibaca: {exc}") from exc
    use_llm = llm_trader.load_config()["sim_enabled"] if body.llm is None else body.llm
    trader = _trader(body.llm_model, body.llm_max_calls, "trading-sim") if use_llm else None
    desk = market_news.get_desk()
    try:
        _sim = papersim.PaperSim(backend, body.symbol, hold_s=body.hold_s, target=body.target,
                                 min_trades=body.min_trades, max_minutes=body.max_minutes, stop_on_find=body.stop_on_find,
                                 llm=trader, news=(lambda: desk.snapshot(body.symbol, fetch=False)) if body.news else None,
                                 folder=_sim_folder(), timeframe=body.timeframe, hold_bars=body.hold_bars,
                                 stop_atr=body.stop_atr, target_atr=body.target_atr, trail_atr=body.trail_atr, lock_atr=body.lock_atr,
                                 brain=brain_mod.get_brain(body.symbol, body.timeframe) if body.timeframe and body.brain else None,
                                 candle=bool(body.timeframe and body.candle and _candle_model_exists(body.symbol)))
    except ValueError as exc:
        raise HTTPException(422, f"Setelan simulasi tidak valid: {exc}") from exc
    threading.Thread(target=_sim.run, name="paper-sim", daemon=True).start()
    return _sim.report()


# ── auto trade: real orders from the brain and the LLM, a trailing stop as the exit ──────────
_auto: Any = None


class AutoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    timeframe: str = Field("H1", pattern=r"^(M15|M30|H1|H4)$")
    mode: str = Field("brain", pattern=r"^(brain|llm|agree)$")
    lots: float = Field(0.01, gt=0, le=autotrade.MAX_LOTS)
    trail_atr: float = Field(1.0, ge=0.2, le=5)
    lock_atr: float = Field(0.3, ge=0, le=2)
    max_daily_loss_pct: float = Field(2.0, gt=0, le=20)
    max_hours: float = Field(24, gt=0, le=autotrade.MAX_HOURS)
    allow_live: bool = False
    llm_model: str | None = Field(None, max_length=200)
    llm_max_calls: int | None = Field(None, ge=1, le=2000)
    news: bool = True


@router.post("/auto/start", dependencies=[Depends(guard)])
def auto_start(body: AutoIn) -> dict[str, Any]:
    """Start the auto trade. It sends real orders by itself: a demo account unless allow_live is set."""
    from addons.trading.web.mt4_bridge import store as mt4_store

    global _auto
    if _auto and _auto.state == "running":
        raise HTTPException(409, "Auto trade masih berjalan. Hentikan dulu.")
    backend = _backend()
    if backend.account().get("live") and not body.allow_live:
        raise HTTPException(403, "Akun ini live. Centang 'izinkan akun live' kalau memang mau auto trade di sana.")
    if backend.name == "mt4" and not (mt4_store.account or {}).get("trade_allowed"):
        raise HTTPException(409, "EA menolak order: AllowTrading di chart masih mati. Tekan F7 di chart, tab Inputs.")
    brain = brain_mod.get_brain(body.symbol, body.timeframe)
    if not brain.ready:
        if brain.needs_training():
            brain.train_in_background(backend)
        raise HTTPException(409, f"Otak {body.symbol} {body.timeframe} belum dilatih. Pelatihan dimulai di latar; coba lagi sebentar lagi.")
    trader = _trader(body.llm_model, body.llm_max_calls, "trading-auto") if body.mode != "brain" else None
    desk = market_news.get_desk()
    try:
        _auto = autotrade.AutoTrader(backend, body.symbol, body.timeframe, brain=brain, llm=trader, mode=body.mode, lots=body.lots,
                                     trail_atr=body.trail_atr, lock_atr=body.lock_atr, max_daily_loss_pct=body.max_daily_loss_pct,
                                     max_hours=body.max_hours, allow_live=body.allow_live,
                                     news=(lambda: desk.snapshot(body.symbol, fetch=False)) if body.news and trader else None)
    except ValueError as exc:
        raise HTTPException(422, f"Setelan auto trade tidak valid: {exc}") from exc
    threading.Thread(target=_auto.run, name="auto-trade", daemon=True).start()
    return _auto.report()


@router.post("/auto/stop", dependencies=[Depends(guard)])
def auto_stop() -> dict[str, Any]:
    if _auto is None:
        raise HTTPException(404, "Belum ada auto trade.")
    _auto.stop_event.set()
    return _auto.report()


@router.get("/auto/status")
def auto_status() -> dict[str, Any]:
    if _auto is None:
        return {"state": "idle"}
    return _auto.report()


def _sim_folder() -> Path:
    custom = os.getenv("SIM_LOG_DIR", "").strip()
    return Path(custom).expanduser() if custom else papersim.FOLDER


@router.get("/sim/logs")
def sim_logs() -> dict[str, Any]:
    """The saved runs, newest first: one JSON line per cycle, per LLM answer and per closed trade."""
    folder = _sim_folder()
    files = sorted(folder.glob("*.jsonl"), reverse=True)[:100] if folder.is_dir() else []
    return {"folder": str(folder), "files": [{"name": f.name, "bytes": f.stat().st_size, "modified": f.stat().st_mtime} for f in files]}


@router.get("/sim/logs/{name}")
def sim_log(name: str) -> FileResponse:
    if not name.endswith(".jsonl") or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "Nama file tidak valid.")
    path = _sim_folder() / name
    if not path.is_file():
        raise HTTPException(404, "Log tidak ditemukan.")
    return FileResponse(path, media_type="application/x-ndjson", filename=name)


# ---------------------------------------------------------------- news and the trading LLM


@router.get("/news")
def news(
    request: Request,
    symbol: str = Query("", max_length=32, pattern=r"^[A-Za-z0-9._-]*$"),
    refresh: bool = False,
    x_mt5_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Calendar, crypto market, headlines by topic, and what the MT4 price says. Cached per source."""
    if refresh:     # forcing every source again is the visitor's request to spend others' rate limits
        guard(request, x_mt5_token)
    snap = market_news.get_desk().snapshot(symbol, force=refresh)
    if symbol:
        try:
            backend = _view_backend()
            bars = backend.rates(symbol, "M1", 120)
            snap["condition"] = market_news.market_condition(bars, backend.tick(symbol))
        except Exception as exc:  # noqa: BLE001 - news without a connected terminal is still news
            snap["condition"] = {"state": "tidak terhubung", "error": str(exc)[:120]}
    return snap


@router.get("/llm")
def llm_settings() -> dict[str, Any]:
    cfg = llm_trader.load_config()
    served = llm_trader._served["models"]          # the cached list only: this page load must not wait on providers
    default, problem = llm_trader.resolve_model("", served)
    _, model_problem = llm_trader.resolve_model(cfg["model"], served) if cfg["model"] else ("", "")
    return {**cfg, "default_model": default, "default_problem": problem, "model_problem": model_problem,
            "forward_modes": list(llm_trader.FORWARD_MODES)}


@router.post("/llm", dependencies=[Depends(guard)])
def llm_save(body: dict[str, Any]) -> dict[str, Any]:
    try:
        llm_trader.save_config(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return llm_settings()


@router.get("/llm/models")
async def llm_models(refresh: bool = False) -> dict[str, Any]:
    """The models each Router-page provider serves, by provider. Spends no tokens."""
    try:
        return await llm_trader.list_models(force=refresh)
    except Exception as exc:  # noqa: BLE001 - an empty list with the reason beats a 500
        return {"groups": [], "models": [], "recent": [], "default": "", "default_problem": "", "errors": [str(exc)[:200]]}


@router.post("/sim/stop", dependencies=[Depends(guard)])
def sim_stop() -> dict[str, Any]:
    if _sim is None:
        raise HTTPException(404, "Belum ada simulasi.")
    _sim.stop_event.set()
    return _sim.report()


# ── the trading brain and the LLM analyst ───────────────────────────────────
SYMBOL_PATTERN = r"^[A-Za-z0-9._-]+$"
TF_PATTERN = r"^(M15|M30|H1|H4)$"


class BrainIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(..., min_length=1, max_length=32, pattern=SYMBOL_PATTERN)
    timeframe: str = Field("H1", pattern=TF_PATTERN)


class AnalystIn(BrainIn):
    llm_model: str | None = Field(None, max_length=200)


def _backend_or_none() -> Any:
    try:
        return _view_backend()
    except Exception:  # noqa: BLE001 - not connected: the saved history still trains the brain
        return None


def _plan_lots(backend: Any, plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """The lot size for an open plan under the panel's risk settings, as for an order proposal."""
    if not plan or plan.get("entry") is None or backend is None:
        return None
    try:
        cfg = engine.config_from_env()
        equity = float(backend.account()["equity"])
        risk_money = equity * cfg.risk_per_trade
        loss = backend.risk_per_lot(plan["symbol"], plan["side"], plan["entry"], plan["stop"])
        lots = R.lots_for(risk_money, loss, backend.symbol_spec(plan["symbol"]))
        return {"lots": lots, "risk_money": round(risk_money, 2), "risk_pct": round(cfg.risk_per_trade * 100, 2)}
    except Exception as exc:  # noqa: BLE001 - the plan stays useful without a size
        return {"error": str(exc)[:160]}


@router.get("/brain")
def brain_status(symbol: str = Query(..., min_length=1, max_length=32, pattern=SYMBOL_PATTERN),
                 timeframe: str = Query("H1", pattern=TF_PATTERN), analyse: bool = True) -> dict[str, Any]:
    """The brain's state (memory, exam, learning history, conditions) and, when trained, its view of the last bar."""
    brain = brain_mod.get_brain(symbol, timeframe)
    out: dict[str, Any] = {"status": brain.status()}
    if analyse and brain.ready:
        backend = _backend_or_none()
        spread = None
        try:
            tick = backend.tick(symbol) if backend is not None else None
            spread = float(tick["ask"]) - float(tick["bid"]) if tick else None
            out["view"] = brain.analyse(backend, spread=spread)
        except Exception as exc:  # noqa: BLE001 - shown on the panel
            out["view"] = {"ok": False, "reason": str(exc)[:200]}
    return out


@router.post("/brain/train", dependencies=[Depends(guard)])
def brain_train(body: BrainIn) -> dict[str, Any]:
    """Train the brain again now, in the background, from every M5 bar recorded for the symbol."""
    brain = brain_mod.get_brain(body.symbol, body.timeframe)
    started = brain.train_in_background(_backend_or_none())
    return {"started": started, "status": brain.status()}


@router.get("/analyst")
def analyst_plans(symbol: str = Query(..., min_length=1, max_length=32, pattern=SYMBOL_PATTERN),
                  timeframe: str | None = Query(None, pattern=TF_PATTERN), limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    """The analyst's plans for a symbol, each followed to its target, stop or end of hold, and its record."""
    backend = _backend_or_none()
    plans = analyst.update(symbol, memory.history_m5(symbol, backend))
    if timeframe:
        plans = [p for p in plans if p.get("timeframe") == timeframe]
    latest = plans[-1] if plans else None
    return {"latest": latest, "lots": _plan_lots(backend, latest) if latest and latest.get("status") == "open" else None,
            "plans": list(reversed(plans[-limit:])), "summary": analyst.summary(plans)}


@router.post("/analyst/run", dependencies=[Depends(guard)])
def analyst_run(body: AnalystIn) -> dict[str, Any]:
    """Ask the LLM analyst for a plan now: one model call. Nothing is sent to the broker."""
    backend = _view_backend()
    trader = _trader(body.llm_model, 1, "trading-analyst")
    snap: dict[str, Any] = {}
    try:
        snap = market_news.get_desk().snapshot(body.symbol, fetch=False)
        snap["brief"] = market_news.brief(snap)
    except Exception as exc:  # noqa: BLE001 - news is extra context
        logger.debug(f"[analyst] news: {exc}")
    try:
        result = analyst.run(backend, body.symbol, body.timeframe, trader, news=snap)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - a feed or model failure, said plainly
        raise HTTPException(502, f"Analyst gagal: {exc}") from exc
    finally:
        trader.close()
    plan = result["plan"]
    return {"plan": plan, "lots": _plan_lots(backend, plan), "view": result["view"], "llm": trader.report()}


# ── the trader's checklist: levels, time, cycles, risk arithmetic ───────────
@router.get("/considerations")
def considerations(symbol: str = Query(..., min_length=1, max_length=32, pattern=SYMBOL_PATTERN),
                   timeframe: str = Query("H1", pattern=r"^(M5|M15|M30|H1|H4)$"),
                   days: int = Query(6, ge=1, le=30)) -> dict[str, Any]:
    """What the LLM is shown before a trade, in full, for the panel to draw: pivot and Fibonacci levels and where they
    meet, the market's clocks and next events, what each New York hour and weekday did before (with its test),
    the dominant cycle (with its test), and when each session was open over the last ``days`` days."""
    backend = _backend_or_none()
    now = time.time()
    seconds = memory.BASE_SECONDS if timeframe == "M5" else memory.TIMEFRAMES[timeframe]
    bars = memory.resample(memory.history_m5(symbol, backend), seconds, now)
    price, digits = None, 5
    try:
        if backend is not None:
            tick = backend.tick(symbol)
            price = (float(tick["bid"]) + float(tick["ask"])) / 2
            digits = int(backend.symbol_spec(symbol).get("digits", 5))
    except Exception:  # noqa: BLE001 - not connected: the last close stands in for the price
        pass
    try:
        view = playbook.considerations(bars, seconds, now, price=price, digits=digits, key=(symbol, timeframe))
    except Exception as exc:  # noqa: BLE001 - said plainly on the panel
        raise HTTPException(502, f"Checklist gagal dihitung: {exc}") from exc
    view.update(symbol=symbol, timeframe=timeframe, bars=len(bars), digits=digits,
                session_bands=cycles.session_bands(now - days * 86400, now + 86400))
    return view


@router.get("/riskmath")
def risk_math(
    win_rate: float = Query(..., gt=0, lt=1), rr: float = Query(..., gt=0, le=50),
    risk_pct: float = Query(1.0, gt=0, le=100), trades: int = Query(100, ge=1, le=10000),
    ruin_drawdown_pct: float = Query(30.0, gt=0, lt=100), cost_r: float = Query(0.0, ge=0, le=5),
) -> dict[str, Any]:
    """The arithmetic behind a win rate and a reward to risk: what breaks even, what it earns per trade, how much
    Kelly would risk, how long the losing streaks get, and the chance of ever being ``ruin_drawdown_pct`` down."""
    needed = riskmath.breakeven_win_rate(rr, cost_r)
    risk = risk_pct / 100.0
    streaks = {str(k): round(riskmath.losing_streak_probability(win_rate, trades, k), 4) for k in (3, 5, 8, 10)}
    typical = riskmath.expected_longest_losing_streak(win_rate, trades)
    return {
        "win_rate": win_rate, "rr": rr, "breakeven_win_rate": round(needed, 4), "edge": round(win_rate - needed, 4),
        "expectancy_r": round(riskmath.expectancy(win_rate, rr - cost_r, 1 + cost_r), 4),
        "kelly": round(riskmath.kelly(win_rate, rr), 4), "half_kelly": round(riskmath.kelly(win_rate, rr) / 2, 4),
        "losing_streak_probability": streaks, "typical_longest_losing_streak": round(typical, 1),
        "drawdown_of_typical_streak_pct": round((1 - (1 - risk) ** typical) * 100, 2),
        "risk_of_ruin": round(riskmath.risk_of_ruin(win_rate, rr, risk, ruin_drawdown_pct / 100.0), 4),
        "recovery_needed_pct": round(riskmath.recovery(ruin_drawdown_pct / 100.0) * 100, 1),
        "note": "Angka ini benar hanya kalau win rate dan R:R-nya benar. Dari sedikit trade keduanya masih tebakan.",
    }


# ── is this server running the code on disk? ───────────────────────────────
# The page is read from disk on every load, but the Python stays what it was when the server
# started, so a new panel can talk to an old server and its new settings are silently dropped.
_ROOT = Path(__file__).resolve().parents[3]
_loaded: dict[str, float] = {}


def _own_modules() -> dict[str, float]:
    """The project's own loaded modules and their file's modification time now."""
    out: dict[str, float] = {}
    for module in list(sys.modules.values()):
        name = getattr(module, "__file__", None)
        if not name:
            continue
        path = Path(name)
        if not path.is_absolute() or ".venv" in path.parts or _ROOT not in path.parents:
            continue
        try:
            out[str(path.relative_to(_ROOT))] = path.stat().st_mtime
        except (OSError, ValueError):
            continue
    return out


def changed_since_start() -> list[str]:
    """Files whose code changed after this server loaded them: the server needs a restart to run it."""
    now = _own_modules()
    for path, mtime in now.items():
        _loaded.setdefault(path, mtime)        # a module loaded later was loaded as it is now
    return sorted(path for path, mtime in now.items() if mtime > _loaded[path] + 1)


_own_modules_at_start = _own_modules()
_loaded.update(_own_modules_at_start)
_STARTED = time.time()


@router.get("/server")
def server_code() -> dict[str, Any]:
    changed = changed_since_start()
    return {"started": _STARTED, "pid": os.getpid(), "stale": bool(changed), "changed": changed[:20],
            "features": ["sim_timeframe", "brain", "analyst", "considerations"]}


@router.get("/sim/status")
def sim_status() -> dict[str, Any]:
    if _sim is None:
        return {"state": "none"}
    return _sim.report()


# ── the candle model: its training process, its forecast and scenarios on the live bars ─────────
def _candle_model_exists(symbol: str) -> bool:
    from addons.trading.quant import candle_view, m5store

    return candle_view.pick_size(m5store.instrument(symbol)) is not None


class CandleTrainIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(..., pattern=r"^(start|stop)$")
    size: str = Field("base", pattern=r"^(fast|base|large|xl)$")
    fresh: bool = False
    symbol: str = Field("XAUUSD", min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")


@router.get("/candle/status")
def candle_status(symbol: str = Query("XAUUSD", min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")) -> dict[str, Any]:
    """The candle model's training: running or not, its epoch, and the exam per candle of its best version."""
    from addons.trading.quant import candle_view

    return candle_view.training_status(symbol)


@router.get("/candle/forecast")
def candle_forecast(symbol: str = Query(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")) -> dict[str, Any]:
    """The next 12 M5 candles from the newest bars, with the 10-90% band, the scenarios (levels A and B) and the exam."""
    from addons.trading.quant import candle_view, memory

    price = None
    backend = None
    with contextlib.suppress(Exception):
        backend = _view_backend()
        tick = backend.tick(symbol)
        price = (float(tick["bid"]) + float(tick["ask"])) / 2
    return candle_view.view(symbol, memory.history_m5(symbol, backend), price=price)


@router.post("/candle/train", dependencies=[Depends(guard)])
def candle_train(body: CandleTrainIn) -> dict[str, Any]:
    """Start (in the background, carrying on from the checkpoint unless fresh) or stop the candle model's training."""
    from addons.trading.quant import candle_view

    if body.action == "stop":
        return candle_view.stop_training()
    try:
        return candle_view.start_training(body.size, body.fresh, body.symbol)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# ── the model lab page: monitor, settings and control of one candle model run ─────────────────
_SIZE = r"^(fast|base|large|xl)$"
_SYM = r"^[A-Za-z0-9._-]+$"


class CandleSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field("XAUUSD", min_length=1, max_length=32, pattern=_SYM)
    size: str = Field("base", pattern=_SIZE)
    values: dict[str, float | int | bool | None] = Field(default_factory=dict, max_length=40)


class CandleControlIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field("XAUUSD", min_length=1, max_length=32, pattern=_SYM)
    size: str = Field("base", pattern=_SIZE)
    cmd: str = Field(..., pattern=r"^(reset_lr|save)$")


@router.get("/candle/lab")
def candle_lab(symbol: str = Query("XAUUSD", min_length=1, max_length=32, pattern=_SYM),
               size: str = Query("", pattern=r"^(|fast|base|large|xl)$")) -> dict[str, Any]:
    """Everything the lab shows about one run: process, epoch, exam, settings (saved, in use, default) and the sizes."""
    from addons.trading.quant import candle_view

    return candle_view.lab_status(symbol, size or None)


@router.get("/candle/history")
def candle_history(symbol: str = Query("XAUUSD", min_length=1, max_length=32, pattern=_SYM),
                   size: str = Query("base", pattern=_SIZE), points: int = Query(600, ge=50, le=5000)) -> dict[str, Any]:
    """The run's epochs for the charts, thinned to about ``points`` rows (best epochs always kept)."""
    from addons.trading.quant import candle_view

    return candle_view.lab_history(symbol, size, points)


@router.get("/candle/log")
def candle_log(lines: int = Query(200, ge=10, le=2000)) -> dict[str, Any]:
    """The tail of candle_lm.log."""
    from addons.trading.quant import candle_view

    return candle_view.log_tail(lines)


@router.post("/candle/settings", dependencies=[Depends(guard)])
def candle_settings(body: CandleSettingsIn) -> dict[str, Any]:
    """Save the run's settings. A running trainer takes the live ones after its current epoch."""
    from addons.trading.quant import candle_view

    try:
        return candle_view.save_settings(body.symbol, body.size, body.values)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/candle/control", dependencies=[Depends(guard)])
def candle_control(body: CandleControlIn) -> dict[str, Any]:
    """reset_lr: full learning rate again (wakes a run waiting for data); save: write best.pt after this epoch."""
    from addons.trading.quant import candle_view

    try:
        return candle_view.control(body.symbol, body.size, body.cmd)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/backtest")
def backtest(
    request: Request,
    symbol: str = Query(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$"),
    count: int = Query(5000, ge=600, le=20000),
    commission_r: float = Query(0.05, ge=0, le=1),
    manage: bool = False,
) -> dict[str, Any]:
    """Walk-forward backtest on the terminal's own M5 history (the demo account's data). Places no orders."""
    from addons.trading.quant import backtest as bt
    b = _view_backend()
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
        raise HTTPException(422, _short_history(len(bars), 1610))
    res = _in_worker(bt.run, bars, symbol=symbol, train=min(2000, len(bars) // 2), refit=500, spread=spread, commission_r=commission_r, manage=manage)
    return {
        "symbol": symbol, "bars": len(bars), "from": bars[0]["t"], "to": bars[-1]["t"],
        "spread_used": spread, "commission_r": commission_r, "managed": manage,
        "summary": res["summary"], "last_trades": res["trades"][-50:],
        "warning": "Historical backtest on the terminal's data. Costs are the current spread and a fixed commission; "
                   "it is not a forward test and does not show future results.",
    }
