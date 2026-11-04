"""Live proposal: model + news filter + risk engine + sizing, on the last CLOSED M5 bar.

Nothing here sends an order. ``propose`` returns the rule's decision, the lot size, and every
risk check, so the panel can fill the order ticket. The user still previews and confirms.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

from trading import strategy as legacy_news
from trading.quant import features as F
from trading.quant import model as M
from trading.quant import risk as R
from trading.quant.hmm import GaussianHMM, label_states

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


def _risk_state(equity: float, now: dt.datetime) -> R.State:
    if _state["risk"] is None:
        _state["risk"] = R.State(equity=equity, day=now.date(), day_start_equity=equity, start_equity=equity)
    return _state["risk"]


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
    rows = []
    for t in range(max(0, last - TRAIN_BARS), last):
        u = M.feature_vector(f, bars, t)
        if u is not None:
            rows.append(u)
    if len(rows) < 60:
        return {"symbol": symbol, "side": None, "blocked": "not enough valid feature rows to fit the regime model", "ok": False}
    hmm = GaussianHMM(3).fit(rows, iters=25)
    states = label_states(hmm)
    lo = max(0, last - TRAIN_BARS)
    seq_idx = [t for t in range(lo, last + 1) if M.feature_vector(f, bars, t) is not None]
    post = hmm.filter([M.feature_vector(f, bars, t) for t in seq_idx])
    post_at = dict(zip(seq_idx, post, strict=False))

    prop = M.decide(symbol, bars, f, post_at.get(last), states, last)
    when = dt.datetime.fromtimestamp(bars[last]["t"], dt.UTC)
    if prop.side and news:
        reason = legacy_news.news_block(symbol, when, news)
        if reason:
            prop.blocked = reason
            prop.side = None
    out: dict[str, Any] = {"symbol": symbol, "ok": prop.side is not None, "proposal": prop.as_dict(),
                           "bar_time": when.isoformat(timespec="minutes"), "equity": equity,
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


__all__ = ["propose", "config_from_env", "on_close", "reset_state"]
