"""Trailing exit: the stop is the only way out, and it follows the price once the target is reached.

A plan has a stop and a target in ATR. With a trailing exit the target does not close the
position. When the price touches it, the stop is moved to ``lock_atr`` ATR below the target
(above, for a sell): most of the target is locked in. From then on the stop follows the best
price at ``trail_atr`` ATR behind it, and never moves back. The position closes when the price
comes back to the stop, or when the hold runs out. So a move that keeps going past the target
keeps paying, and one that turns around at the target still pays nearly the target.

``TrailState`` is updated tick by tick (``update``), both in the paper simulation and in the
live auto trade. ``follow`` plays the same rule over closed M5 bars, so the analyst's journal
and the memory can say how a trailing plan would have ended; inside one bar the stop is checked
first, so the replay never flatters a plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TRAIL_ATR = 1.0          # the stop follows this many ATR behind the best price, once locked
LOCK_ATR = 0.3           # at the target the stop is set this many ATR back from it
TRAIL_MIN, TRAIL_MAX = 0.2, 5.0
LOCK_MIN, LOCK_MAX = 0.0, 2.0


@dataclass
class TrailState:
    side: int                     # 1 buy, -1 sell
    stop: float | None            # the current stop price
    target: float | None          # the level that arms the trail; None trails from the start
    atr: float
    trail_atr: float = TRAIL_ATR
    lock_atr: float = LOCK_ATR
    best: float = 0.0             # the best price seen in the position's favour
    locked: bool = False          # the target was reached and the stop moved up behind it
    moves: int = 0                # how many times the stop was moved

    def _tighter(self, candidate: float) -> bool:
        return self.stop is None or self.side * (candidate - self.stop) > 0

    def update(self, price: float) -> str:
        """Feed one price. Returns "exit" when the stop is hit, "locked" or "trailed" when the stop moved, else ""."""
        if self.stop is not None and self.side * (price - self.stop) <= 0:
            return "exit"
        if not self.best or self.side * (price - self.best) > 0:
            self.best = price
        moved = ""
        if not self.locked and self.target is not None and self.side * (price - self.target) >= 0 and self.atr > 0:
            self.locked = True
            lock = self.target - self.side * self.lock_atr * self.atr
            if self._tighter(lock):
                self.stop, moved = lock, "locked"
        armed = self.locked or self.target is None
        if armed and self.trail_atr > 0 and self.atr > 0:
            trail = self.best - self.side * self.trail_atr * self.atr
            if self._tighter(trail):
                self.stop, moved = trail, moved or "trailed"
        if moved:
            self.moves += 1
        return moved

    @property
    def exit_label(self) -> str:
        """How a close at the stop should be named: a trailed stop after the target, or the original stop."""
        return "trail" if self.locked else "stop"

    def to_dict(self) -> dict[str, Any]:
        return {"stop": self.stop, "target_level": self.target, "best": self.best, "locked": self.locked,
                "trail_atr": self.trail_atr, "lock_atr": self.lock_atr, "moves": self.moves}


def clamp(trail_atr: float | None, lock_atr: float | None) -> tuple[float, float]:
    t = TRAIL_ATR if trail_atr is None else max(TRAIL_MIN, min(TRAIL_MAX, float(trail_atr)))
    lk = LOCK_ATR if lock_atr is None else max(LOCK_MIN, min(LOCK_MAX, float(lock_atr)))
    return round(t, 2), round(lk, 2)


def follow(side: int, entry: float, stop: float | None, target: float | None, atr: float, bars: list[dict[str, float]],
           *, start: float, until: float, bar_seconds: float, spread: float = 0.0, trail_atr: float = TRAIL_ATR,
           lock_atr: float = LOCK_ATR, digits: int = 5) -> dict[str, Any] | None:
    """Play the trailing exit over closed bars after ``start``. None while the position is still running.

    A buy is marked at the bid (the bar's prices); a sell at the ask, which is ``spread`` above them.
    Inside each bar the stop is checked on the adverse extreme first, then the favourable extreme
    moves the stop, so a bar that crosses both counts as the stop.
    """
    state = TrailState(side, stop, target, atr, trail_atr, lock_atr)
    last: dict[str, float] | None = None
    for b in bars:
        if b["t"] < start:
            continue
        if b["t"] >= until:
            break
        last = b
        worst = b["l"] if side > 0 else b["h"] + spread
        best = b["h"] if side > 0 else b["l"] + spread
        if state.update(worst) == "exit":
            exit_price = float(state.stop if state.stop is not None else worst)
            return {"status": state.exit_label, "exit": round(exit_price, digits), "closed_at": b["t"] + bar_seconds,
                    "result_atr": round((exit_price - entry) * side / atr, 3) if atr else None, "trail": state.to_dict()}
        state.update(best)
    if bars and bars[-1]["t"] + bar_seconds >= until and last is not None:
        exit_price = last["c"] if side > 0 else last["c"] + spread
        return {"status": "timeout", "exit": round(exit_price, digits), "closed_at": until,
                "result_atr": round((exit_price - entry) * side / atr, 3) if atr else None, "trail": state.to_dict()}
    return None


__all__ = ["TrailState", "follow", "clamp", "TRAIL_ATR", "LOCK_ATR"]
