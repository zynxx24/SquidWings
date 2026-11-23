"""Live proposal: model + news filter + risk engine + sizing, on the last CLOSED M5 bar.

Nothing here sends an order. ``propose`` returns the rule's decision, the lot size, and every
risk check, so the panel can fill the order ticket. The user still previews and confirms.
"""

from __future__ import annotations

import datetime as dt
import os
from collections import OrderedDict
from typing import Any

from trading import strategy as legacy_news
from trading.quant import features as F
from trading.quant import model as M
from trading.quant import risk as R
from trading.quant.hmm import GaussianHMM, label_states
from utils.workspace_context import get_active_session

TRAIN_BARS = 2000   # live: one fit on the most recent closed bars per request


def config_from_env() -> R.RiskConfig:
    def num(name: str) -> float | None:
        raw = os.getenv(name, "").strip()
        return float(raw) if raw else None

    risk = num("QUANT_RISK_PER_TRADE")
    cfg = R.RiskConfig(
        risk_per_trade=(risk / 100.0) if risk is not None else R.DEFAULT_RISK,
        max_open=int(num("QUANT_MAX_OPEN") or 1),
        max_consecutive_losses=int(num("QUANT_MAX_CONSECUTIVE_LOSSES") or 3),
        firm_daily_loss_pct=num("QUANT_FIRM_DAILY_LOSS_PCT"),
        firm_max_loss_pct=num("QUANT_FIRM_MAX_LOSS_PCT"),
    )
    return cfg


_state: dict[str, Any] = {"risk": None}

# Fitted regime model per (session, backend, symbol, last bar time, last close, train size). The
# backend is in the key because two feeds can share a symbol and a bar time. The close is in the
# key because the MT4 store replaces a bar when the EA re-sends the same time with new prices.
# Keyed by session as the per-session isolation rule requires, and bounded so a long run cannot
# grow it without limit.
_FIT_CACHE_MAX = 32
_FitKey = tuple[str, str, str, int, float, int]
_fit_cache: OrderedDict[_FitKey, tuple[dict[str, int], list[float] | None]] = OrderedDict()


def _cached_fit(key: _FitKey) -> tuple[dict[str, int], list[float] | None] | None:
    hit = _fit_cache.get(key)
    if hit is not None:
        _fit_cache.move_to_end(key)
    return hit


def _store_fit(key: _FitKey, value: tuple[dict[str, int], list[float] | None]) -> None:
    _fit_cache[key] = value
    _fit_cache.move_to_end(key)
    while len(_fit_cache) > _FIT_CACHE_MAX:
        _fit_cache.popitem(last=False)


def _spread_of(backend: Any, symbol: str) -> float | None:
    """Ask minus bid right now, or None when the backend cannot quote (the gate is then skipped and the response says so)."""
    tick_fn = getattr(backend, "tick", None)
    if tick_fn is None:
        return None
    try:
        tick = tick_fn(symbol)
        spread = float(tick["ask"]) - float(tick["bid"])
    except Exception:  # noqa: BLE001 - a missing quote must not block the rest of the proposal
        return None
    return spread if spread >= 0 else None


def _risk_state(equity: float, now: dt.datetime) -> R.State:
    state: R.State | None = _state["risk"]
    if state is None:
        state = R.State(equity=equity, day=now.date(), day_start_equity=equity, start_equity=equity)
        _state["risk"] = state
    return state


def reset_state() -> None:
    _state["risk"] = None


def on_close(profit: float, equity_after: float) -> None:
    """Called after a closed trade so the streak and daily stop see it."""
    st = _state["risk"]
    if st is not None:
        R.record_result(st, profit, equity_after, dt.datetime.now(dt.UTC))


def propose(backend: Any, symbol: str, *, news: list[Any] | None = None, count: int = 2400,
            cfg: R.RiskConfig | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
    cfg = cfg or config_from_env()
    now = now or dt.datetime.now(dt.UTC)
    problems = cfg.validate()
    account = backend.account()
    equity = float(account["equity"])
    state = _risk_state(equity, now)
    state.open_positions = len(backend.positions())   # the broker is the source of truth for open trades

    bars = backend.rates(symbol, "M5", count)
    if getattr(backend, "name", "") == "mt5" and bars:
        bars = bars[:-1]        # copy_rates_from_pos index 0 is the bar still forming
    if len(bars) < TRAIN_BARS // 2:
        return {"symbol": symbol, "side": None, "blocked": f"not enough closed M5 bars ({len(bars)})", "ok": False}

    f = F.compute(bars)
    last = len(bars) - 1
    key = (get_active_session(), str(getattr(backend, "name", "")), symbol, int(bars[last]["t"]), float(bars[last]["c"]), TRAIN_BARS)
    cached = _cached_fit(key)
    if cached is None:
        rows = []
        for t in range(max(0, last - TRAIN_BARS), last):
            u = M.feature_vector(f, bars, t)
            if u is not None:
                rows.append(u)
        if len(rows) < 60:
            return {"symbol": symbol, "side": None, "blocked": "not enough valid feature rows to fit the regime model", "ok": False}
        hmm = GaussianHMM(3).fit(rows, iters=25)
        states = label_states(hmm)
        last_u = M.feature_vector(f, bars, last)
        # Only the posterior at the last bar is needed, so the filter runs once over the window.
        post_last = hmm.filter(rows + [last_u])[-1] if last_u is not None else None
        cached = (states, post_last)
        _store_fit(key, cached)
    states, post_last = cached

    spread = _spread_of(backend, symbol)
    prop = M.decide(symbol, bars, f, post_last, states, last, spread=spread)
    when = dt.datetime.fromtimestamp(bars[last]["t"], dt.UTC)
    if prop.side and news:
        reason = legacy_news.news_block(symbol, when, news)
        if reason:
            prop.blocked = reason
            prop.side = None
    out: dict[str, Any] = {"symbol": symbol, "ok": prop.side is not None, "proposal": prop.as_dict(),
                           "bar_time": when.isoformat(timespec="minutes"), "equity": equity, "spread": spread,
                           "config": {"risk_per_trade_pct": cfg.risk_per_trade * 100, "max_open": cfg.max_open,
                                      "max_consecutive_losses": cfg.max_consecutive_losses,
                                      "firm_daily_loss_pct": cfg.firm_daily_loss_pct, "firm_max_loss_pct": cfg.firm_max_loss_pct},
                           "notes": list(state.notes), "problems": problems}
    if prop.side is None:
        out["blocked"] = prop.blocked
        return out

    spec = backend.symbol_spec(symbol)
    risk_money = equity * cfg.risk_per_trade
    loss_1lot = backend.risk_per_lot(symbol, prop.side, prop.entry, prop.sl)
    try:
        lots = R.lots_for(risk_money, loss_1lot, spec)
    except ValueError as exc:
        out.update(ok=False, blocked=str(exc))
        return out
    ok, reasons = R.check(cfg, state, lots=lots, spec=spec, risk_money=risk_money, now=now)
    out.update(lots=lots, risk_money=round(risk_money, 2), loss_per_lot=round(loss_1lot, 2),
               ok=ok and not problems, risk_reasons=reasons)
    if problems or not ok:
        out["blocked"] = (problems + reasons)[0]
    return out


def clear_fit_cache() -> None:
    _fit_cache.clear()


__all__ = ["propose", "config_from_env", "on_close", "reset_state", "clear_fit_cache"]
