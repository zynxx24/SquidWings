"""Forward test on a demo account: the scalp ensemble with real positions on a real price feed.

Every 30 seconds it opens one basket of five positions, then closes what is left when the time is
up. A backtest cannot show fills, slippage or broker refusals. This does.

It sends orders without a confirm dialog, so these limits are not settings a caller can lift:

* A live account is refused, at the start and again before every basket.
* Every position has a real stop and target, at the smallest size.
* The run ends on its own at its time limit, at MAX_LOSS_PCT of the starting equity, or after
  MAX_ERRORS baskets in a row fail. Positions it did not open are never touched.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from trading import journal
from trading.quant import features as F
from trading.quant import scalp, ticks

HOLD_S = 30.0
LOT = 0.01
MAX_MINUTES = 120
MAX_LOSS_PCT = 2.0
MAX_ERRORS = 3
WARMUP_BARS = 150
BAR_WINDOW = 720
POLL_S = 1.0
SETTLE_S = 4.0
FOLDER = Path(__file__).resolve().parents[1] / "data" / "forward"


class ForwardTest:
    def __init__(self, backend: Any, symbol: str, minutes: float, *, policy: scalp.Policy | None = None,
                 bars: Callable[[str], list[dict[str, float]]] | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time, folder: Path | None = None) -> None:
        if not 0 < minutes <= MAX_MINUTES:
            raise ValueError(f"minutes must be above 0 and at most {MAX_MINUTES}")
        self.backend, self.symbol, self.minutes = backend, symbol, minutes
        self.policy = policy or scalp.Policy()
        self.bars = bars or (lambda name: ticks.load_bars(name)[0][-BAR_WINDOW:])
        self.sleep, self.clock = sleep, clock
        self.stop_event = threading.Event()
        self.baskets: list[dict[str, Any]] = []
        self.state = "ready"
        self.ended = ""
        self.errors = 0
        self.skipped = 0
        self.start_equity = 0.0
        self.end_equity = 0.0
        self.start_balance = 0.0
        self.end_balance = 0.0
        self.started_at = 0.0
        self.path = (folder or FOLDER) / f"{dt.datetime.now(dt.UTC):%Y%m%d-%H%M%S}-{symbol}.jsonl"

    def _account(self) -> dict[str, Any]:
        account: dict[str, Any] = self.backend.account()
        if account.get("live"):
            raise PermissionError("Forward test hanya untuk akun demo.")
        return account

    def _plan(self) -> dict[str, Any] | None:
        """This moment's decision, from bars that have closed. None when there is no side to take."""
        bars = self.bars(self.symbol)
        if len(bars) < WARMUP_BARS:
            return None
        score = scalp.weighted_scores(bars, scalp.votes(bars), max(1, round(HOLD_S / scalp._bar_seconds(bars))))[-1]
        atr = F.wilder_atr(bars)[-1]
        if not score or atr != atr or atr <= 0:
            return None
        side = 1 if score > 0 else -1
        tick = self.backend.tick(self.symbol)
        spec = self.backend.symbol_spec(self.symbol)
        entry = float(tick["ask"] if side > 0 else tick["bid"])
        spread = float(tick["ask"]) - float(tick["bid"])
        digits = int(spec["digits"])
        stop_distance = max(self.policy.sl_atr * atr, spread * 4)      # a stop inside the spread is refused
        target_unit = max(self.policy.tp_atr * atr, spread * 2)
        return {"side": "buy" if side > 0 else "sell", "score": round(score, 3), "atr": round(atr, digits + 1), "spread": spread,
                "entry": entry, "sl": round(entry - side * stop_distance, digits),
                "targets": [round(entry + side * share * target_unit, digits) for share in scalp.LADDER]}

    def _send(self, plan: dict[str, Any], target: float) -> dict[str, Any]:
        order = {"symbol": self.symbol, "side": plan["side"], "volume": LOT, "sl": plan["sl"], "tp": target, "comment": "forward"}
        result: dict[str, Any] = self.backend.send(order)
        ticket = result.get("order") or result.get("deal") if result.get("ok") else None
        if ticket:
            journal.record({"event": "order", "ticket": ticket, "symbol": self.symbol, "side": plan["side"], "volume": LOT,
                            "sl": plan["sl"], "tp": target, "reason": f"forward score {plan['score']}", "source": "forward",
                            "backend": getattr(self.backend, "name", "")})
        return {"ticket": ticket, "comment": str(result.get("comment", ""))[:80]}

    def _open_positions(self, tickets: set[int]) -> dict[int, dict[str, Any]]:
        return {int(p["ticket"]): p for p in self.backend.positions() if int(p["ticket"]) in tickets}

    def _basket(self, plan: dict[str, Any]) -> dict[str, Any]:
        begun = self.clock()
        with ThreadPoolExecutor(max_workers=scalp.POSITIONS) as pool:      # the EA fills them in one pass
            sent = list(pool.map(lambda target: self._send(plan, target), plan["targets"]))
        tickets = {int(item["ticket"]) for item in sent if item["ticket"]}
        last: dict[int, dict[str, Any]] = {}
        while tickets and self.clock() - begun < HOLD_S and not self.stop_event.is_set():
            live = self._open_positions(tickets)
            last.update(live)
            if not live and len(last) == len(tickets):
                break
            self.sleep(POLL_S)
        live = self._open_positions(tickets)
        last.update(live)
        if live:
            with ThreadPoolExecutor(max_workers=scalp.POSITIONS) as pool:
                list(pool.map(lambda ticket: self.backend.close(ticket, LOT), live))
        profits = {ticket: float(position.get("profit", 0.0)) + float(position.get("swap", 0.0)) for ticket, position in last.items()}
        for ticket in tickets:
            journal.record({"event": "close", "ticket": ticket, "symbol": self.symbol, "volume": LOT, "profit": round(profits.get(ticket, 0.0), 2),
                            "source": "forward", "backend": getattr(self.backend, "name", "")})
        return {"time": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), **{k: plan[k] for k in ("side", "score", "atr", "spread", "entry", "sl")},
                "opened": len(tickets), "refused": [item["comment"] for item in sent if not item["ticket"]][:2],
                "closed_by_time": len(live), "closed_by_broker": len(tickets) - len(live),
                "profit": round(sum(profits.values()), 2), "seconds": round(self.clock() - begun, 1)}

    def _log(self, basket: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(basket, ensure_ascii=False) + "\n")

    def run(self) -> None:
        try:
            account = self._account()
            self.start_equity = self.end_equity = float(account["equity"])
            self.start_balance = self.end_balance = float(account.get("balance", 0))
            self.started_at = self.clock()
            self.state = "running"
            limit = self.started_at + self.minutes * 60
            while not self.stop_event.is_set():
                if self.clock() >= limit:
                    self.ended = "batas waktu"
                    break
                equity = self.end_equity = float(self._account()["equity"])
                if self.start_equity - equity >= self.start_equity * MAX_LOSS_PCT / 100:
                    self.ended = f"rugi menyentuh {MAX_LOSS_PCT}% ekuitas awal"
                    break
                self._cycle()
                if self.errors >= MAX_ERRORS:
                    self.ended = f"{MAX_ERRORS} kali gagal berturut-turut"
                    break
            self.ended = self.ended or "dihentikan"
        except Exception as exc:  # noqa: BLE001 - the reason must reach the report
            self.ended = f"error: {exc}"
        finally:
            self.sleep(SETTLE_S)      # the EA reports the balance a moment after the last close
            with contextlib.suppress(Exception):
                account = self._account()
                self.end_equity, self.end_balance = float(account["equity"]), float(account.get("balance", 0))
            self.state = "finished"

    def _cycle(self) -> None:
        plan = self._plan()
        if plan is None:
            self.skipped += 1
            self.sleep(HOLD_S / 6)
            return
        basket = self._basket(plan)
        self.baskets.append(basket)
        self._log(basket)
        self.errors = self.errors + 1 if not basket["opened"] else 0

    def report(self) -> dict[str, Any]:
        rows = [b for b in self.baskets if b["opened"]]
        profits = [b["profit"] for b in rows]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p <= 0]
        positions = sum(b["opened"] for b in rows)
        out: dict[str, Any] = {
            "state": self.state, "ended": self.ended, "symbol": self.symbol, "minutes_limit": self.minutes,
            "minutes_run": round((self.clock() - self.started_at) / 60, 1) if self.started_at else 0,
            "baskets": len(rows), "positions": positions, "skipped_checks": self.skipped,
            "refused_orders": sum(5 - b["opened"] for b in self.baskets), "file": str(self.path),
            "refusal_examples": sorted({text for b in self.baskets for text in b["refused"]})[:3],
            "start_equity": self.start_equity, "end_equity": self.end_equity,
            # The money that really moved. "profit" below is the last price seen before each close.
            "balance_change": round(self.end_balance - self.start_balance, 2),
        }
        if rows:
            out.update(win_rate=round(len(wins) / len(rows), 3), profit=round(sum(profits), 2),
                       avg_win=round(statistics.fmean(wins), 2) if wins else 0.0,
                       avg_loss=round(statistics.fmean(losses), 2) if losses else 0.0,
                       by_broker=sum(b["closed_by_broker"] for b in rows), by_time=sum(b["closed_by_time"] for b in rows),
                       avg_basket_seconds=round(statistics.fmean(b["seconds"] for b in rows), 1))
        return out


__all__ = ["ForwardTest", "HOLD_S", "LOT", "MAX_LOSS_PCT", "MAX_MINUTES"]
