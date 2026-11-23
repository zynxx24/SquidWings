"""Risk engine for funded and personal accounts. It only says yes or no, and it sizes the lot.

Defaults follow the plan for a funded account, and every limit is configurable:

* Risk per trade: 0.25% of equity. The engine refuses anything above ``MAX_RISK_PER_TRADE``
  (1%). Requests for more are refused on purpose, not clamped quietly.
* At most one open position (``max_open``, capped at 3).
* Halt after ``max_consecutive_losses`` (default 3) until the next UTC day or a manual reset.
* Internal daily loss stop = 50% of the firm's daily limit, as the plan says. The firm's
  limit differs by program, so you must pass it in (``firm_daily_loss_pct``). If you do
  not, the engine falls back to 4% and says so in every decision.
* Internal total-loss stop = 50% of the firm's maximum loss (``firm_max_loss_pct``).
* No martingale: the size never grows after a loss.

Lot size: risk money / money lost per lot at the stop. The money per lot comes from the
broker (``order_calc_profit`` on MT5), so the account currency, such as USDC, is handled
by the broker and not by a guessed tick value.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from dataclasses import dataclass, field
from typing import Any

DEFAULT_RISK = 0.0025
MAX_RISK_PER_TRADE = 0.01
MAX_OPEN = 3
DEFAULT_FIRM_DAILY_PCT = 4.0   # used only when the firm's number is not given


@dataclass
class RiskConfig:
    risk_per_trade: float = DEFAULT_RISK
    max_open: int = 1
    max_consecutive_losses: int = 3
    firm_daily_loss_pct: float | None = None
    firm_max_loss_pct: float | None = None

    def validate(self) -> list[str]:
        problems = []
        if not (0 < self.risk_per_trade <= MAX_RISK_PER_TRADE):
            problems.append(f"risk per trade must be above 0 and at most {MAX_RISK_PER_TRADE * 100:.1f}% (got {self.risk_per_trade * 100:.2f}%)")
        if not (1 <= self.max_open <= MAX_OPEN):
            problems.append(f"max open positions must be 1 to {MAX_OPEN}")
        if self.max_consecutive_losses < 1:
            problems.append("max consecutive losses must be at least 1")
        return problems


@dataclass
class State:
    equity: float
    day: dt.date
    day_start_equity: float
    start_equity: float
    open_positions: int = 0
    consecutive_losses: int = 0
    halted: str | None = None
    notes: list[str] = field(default_factory=list)

    def roll_day(self, now: dt.datetime) -> None:
        """A new UTC day resets the day's start equity and the losing streak ("until tomorrow")."""
        if now.date() != self.day:
            self.day = now.date()
            self.day_start_equity = self.equity
            self.consecutive_losses = 0


def lots_for(risk_money: float, loss_per_lot: float, spec: dict[str, Any]) -> float:
    """Largest lot size whose stop loss costs at most `risk_money`, floored to the broker's step."""
    if loss_per_lot <= 0 or not math.isfinite(loss_per_lot):
        raise ValueError("loss per lot must be positive and finite")
    raw = risk_money / loss_per_lot
    step = float(spec["volume_step"]) or 0.01
    lots = math.floor(raw / step + 1e-9) * step
    lots = min(lots, float(spec["volume_max"]))
    return round(lots, 8)


def check(cfg: RiskConfig, state: State, *, lots: float | None, spec: dict[str, Any] | None,
          risk_money: float | None, now: dt.datetime) -> tuple[bool, list[str]]:
    """Every rule that must hold before a trade may be previewed. Returns (ok, reasons)."""
    reasons: list[str] = []
    cfg_problems = cfg.validate()
    reasons += cfg_problems
    state.roll_day(now)
    daily_pct = cfg.firm_daily_loss_pct or DEFAULT_FIRM_DAILY_PCT
    internal_daily = 0.5 * daily_pct / 100.0
    daily_loss = (state.day_start_equity - state.equity) / state.day_start_equity if state.day_start_equity else 0.0
    if state.halted:
        reasons.append(f"halted: {state.halted}")
    if state.open_positions >= cfg.max_open:
        reasons.append(f"{state.open_positions} position(s) already open; limit is {cfg.max_open}")
    if state.consecutive_losses >= cfg.max_consecutive_losses:
        reasons.append(f"{state.consecutive_losses} losses in a row; trading stops until tomorrow (UTC) or a reset")
    if daily_loss >= internal_daily:
        reasons.append(f"internal daily loss stop: lost {daily_loss * 100:.2f}% today, limit {internal_daily * 100:.2f}%")
    if cfg.firm_max_loss_pct is not None:
        total = (state.start_equity - state.equity) / state.start_equity if state.start_equity else 0.0
        if total >= 0.5 * cfg.firm_max_loss_pct / 100.0:
            reasons.append(f"internal total loss stop: down {total * 100:.2f}% from the start")
    if lots is not None and spec is not None and lots < float(spec["volume_min"]):
        reasons.append(f"size {lots} is below the broker minimum {spec['volume_min']}; the risk per trade is too small for this stop")
    if risk_money is not None and state.equity > 0 and risk_money / state.equity > cfg.risk_per_trade + 1e-9:
        reasons.append("risk money is above the configured risk per trade")
    if not state.notes and cfg.firm_daily_loss_pct is None:
        state.notes.append(f"firm daily loss limit not set: using {DEFAULT_FIRM_DAILY_PCT:.0f}% and the 50% internal rule")
    return (not reasons), reasons


def record_result(state: State, profit: float, equity_after: float, now: dt.datetime) -> None:
    """Update the streak and equity after a closed trade."""
    state.roll_day(now)
    state.equity = equity_after
    if profit < 0:
        state.consecutive_losses += 1
    elif profit > 0:
        state.consecutive_losses = 0


def monte_carlo(r_multiples: list[float], sims: int = 2000, seed: int = 7, dd_limit_r: float = 10.0,
                horizon: int | None = None) -> dict[str, Any]:
    """Shuffle the trade order and look at the worst drawdown in R. Deterministic for a given seed."""
    if not r_multiples:
        return {"paths": 0, "note": "no trades to resample"}
    rng = random.Random(seed)
    horizon = horizon or len(r_multiples)
    worst: list[float] = []
    breaches = 0
    for _ in range(sims):
        seq = r_multiples[:]
        rng.shuffle(seq)
        eq = peak = 0.0
        dd = 0.0
        for r in seq[:horizon]:
            eq += r
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        worst.append(dd)
        if dd >= dd_limit_r:
            breaches += 1
    worst.sort()
    pct = lambda p: round(worst[min(len(worst) - 1, int(p * len(worst)))], 2)  # noqa: E731
    return {
        "paths": sims, "horizon": horizon,
        "max_drawdown_r_p50": pct(0.50), "max_drawdown_r_p95": pct(0.95), "max_drawdown_r_p99": pct(0.99),
        f"p_drawdown_at_least_{int(dd_limit_r)}r": round(breaches / sims, 4),
        "note": "resampling the same trades; it does not add new information about the edge",
    }


__all__ = ["RiskConfig", "State", "lots_for", "check", "record_result", "monte_carlo", "DEFAULT_RISK", "MAX_RISK_PER_TRADE"]
