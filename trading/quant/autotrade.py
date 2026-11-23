"""Auto trade: the brain (and the LLM) open real positions, and a trailing stop is the only way out.

One run watches one symbol on one intraday timeframe. On every closed bar it asks the brain
(``brain.analyse``) and, in the ``llm`` and ``agree`` modes, the analyst LLM with the same
evidence the panel's "Analisa sekarang" gives it. When there is a side to take and no position
of its own is open, it sends one order at a fixed lot with a real stop and **no take profit**:
the target only arms the trailing stop (``trail``). From then on, every poll, the stop is moved
in the terminal (``backend.modify``) as the trail tightens, and never widened. The hold ends the
position at the latest.

Modes: ``brain`` trades the brain's best plan when the brain would take it; ``llm`` trades what
the analyst LLM answers (its stop, target and trail); ``agree`` needs both on the same side.

It sends orders without a confirm dialog, so these limits are built in, not settings:

* A live account is refused unless ``allow_live`` was set explicitly when the run was started **and**
  ``MT5_ALLOW_LIVE=1`` is in the environment: the page cannot loosen what the owner set there.
* One position at a time, at the lot given, never above ``MAX_LOTS`` or ``MT5_MAX_VOLUME``.
* Every order passes the same checks as a manual one (``mt5_rules``: volume step and limits, stop
  levels) and its stop may not risk more than ``MT5_MAX_RISK_PCT`` of equity.
* The run ends on its own at its time limit, when the equity drops ``max_daily_loss_pct`` below
  the day's starting equity, or after ``MAX_ERRORS`` failed orders in a row. Positions it did
  not open are never touched.

Every decision and every stop move goes to a JSONL file in ``trading/data/autotrade/`` and to
the trade journal, and the analyst's journal gets each plan, so the LLM sees how its trailing
plans ended on the next call.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trading import journal, mt5_rules
from trading.quant import analyst, memory, sessions, trail
from utils.logger import get_logger

logger = get_logger(__name__)

MODES = ("brain", "llm", "agree")
POLL_S = 2.0
BAR_GRACE_S = 15
FIRST_LATE_S = 60               # a run started later than this after a bar close waits for the next close
MODIFY_RETRY_MAX_S = 60         # a refused stop move is tried again, waiting longer each time up to this
MAX_LOTS = 1.0
MAX_HOURS = 24 * 7
MAX_ERRORS = 3
MIN_STOP_SPREADS = 4            # a stop closer than this many spreads is widened: the broker would refuse it
CONFIRM_GRACE_S = 15            # a new position may take a few reports to appear in the terminal's list
UNCONFIRMED_WAIT_S = 120        # how long an order with no answer is looked for before it counts as not filled
FOLDER = Path(__file__).resolve().parents[1] / "data" / "autotrade"
KEEP_LOG = 200


@dataclass
class Live:
    ticket: int
    side: int
    entry: float
    stop: float
    target_level: float | None
    opened: float
    closes: float
    lots: float
    state: trail.TrailState
    source: str
    why: str = ""
    plan_id: str = ""
    last_seen: dict[str, Any] = field(default_factory=dict)
    modify_failed: int = 0
    modify_retry_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"ticket": self.ticket, "side": "buy" if self.side > 0 else "sell", "entry": self.entry, "stop": self.state.stop,
                "target_level": self.target_level, "opened": self.opened, "closes": self.closes, "lots": self.lots,
                "source": self.source, "why": self.why, "best": self.state.best, "locked": self.state.locked,
                "moves": self.state.moves, "profit": float(self.last_seen.get("profit") or 0.0) + float(self.last_seen.get("swap") or 0.0),
                "price": self.last_seen.get("price")}


class AutoTrader:
    def __init__(self, backend: Any, symbol: str, timeframe: str, *, brain: Any, llm: Any = None, mode: str = "brain",
                 lots: float = 0.01, trail_atr: float = trail.TRAIL_ATR, lock_atr: float = trail.LOCK_ATR,
                 max_daily_loss_pct: float = 2.0, max_hours: float = 24.0, allow_live: bool = False,
                 news: Callable[[], dict[str, Any]] | None = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, folder: Path | None = None) -> None:
        if timeframe not in memory.TIMEFRAMES:
            raise ValueError(f"timeframe: {', '.join(memory.TIMEFRAMES)}")
        if mode not in MODES:
            raise ValueError(f"mode: {', '.join(MODES)}")
        if mode != "brain" and llm is None:
            raise ValueError("mode llm/agree needs an LLM")
        if not 0 < lots <= MAX_LOTS:
            raise ValueError(f"lots must be above 0 and at most {MAX_LOTS}")
        env_max = float(mt5_rules.limits()["max_volume"])
        if lots > env_max:
            raise ValueError(f"lots {lots:g} di atas batas MT5_MAX_VOLUME={env_max:g} di .env")
        if not 0 < max_hours <= MAX_HOURS:
            raise ValueError(f"max_hours must be above 0 and at most {MAX_HOURS}")
        if not 0 < max_daily_loss_pct <= 20:
            raise ValueError("max_daily_loss_pct must be above 0 and at most 20")
        self.backend, self.symbol, self.timeframe = backend, symbol, timeframe
        self.brain, self.llm, self.mode = brain, llm, mode
        self.lots, self.allow_live = float(lots), bool(allow_live)
        self.trail_atr, self.lock_atr = trail.clamp(trail_atr, lock_atr)
        self.max_daily_loss_pct, self.max_hours = float(max_daily_loss_pct), float(max_hours)
        self.news, self.clock, self.sleep = news, clock, sleep
        self.folder = folder or FOLDER
        self.stop_event = threading.Event()
        self.state = "idle"
        self.ended = ""
        self.started_at = 0.0
        self.day_start_equity = 0.0
        self.day = -1
        self.equity = 0.0
        self.live: Live | None = None
        # An order the terminal took but never answered: it may be open. No new order until it is found or given up.
        self.unconfirmed: dict[str, Any] | None = None
        self.closed: list[dict[str, Any]] = []
        self.log: list[dict[str, Any]] = []
        self.errors = 0
        self.orders = self.modifies = self.decisions = 0
        self.last_decision: dict[str, Any] = {}
        self.brain_view: dict[str, Any] = {}
        self._bucket = -1
        self._lock = threading.Lock()
        self.digits = 5
        with contextlib.suppress(Exception):
            self.digits = int(backend.symbol_spec(symbol).get("digits", 5))
        self.path = self.folder / f"{dt.datetime.now(dt.UTC):%Y%m%d-%H%M%S}-{symbol}-{timeframe}.jsonl"

    # ── guards ──────────────────────────────────────────────────────────────
    def _account(self) -> dict[str, Any]:
        account: dict[str, Any] = self.backend.account()
        if account.get("live") and not self.allow_live:
            raise PermissionError("Auto trade menolak akun live: centang 'izinkan akun live' bila memang itu maksudnya.")
        if account.get("live") and not mt5_rules.limits()["allow_live"]:
            raise PermissionError("Akun live ditolak: MT5_ALLOW_LIVE=0 di .env berlaku untuk semua order, termasuk auto trade.")
        return account

    def _rule_problems(self, order: dict[str, Any], bid: float, ask: float, entry: float) -> list[str]:
        """The checks a manual order passes, and the money its stop puts at risk."""
        spec: dict[str, Any] = {"digits": self.digits, "volume_min": 0.0, "volume_max": float("inf"), "volume_step": 0.0}
        with contextlib.suppress(Exception):
            spec.update(self.backend.symbol_spec(self.symbol))
        problems = mt5_rules.check_order(order, spec, {"bid": bid, "ask": ask})
        risk_per_lot = getattr(self.backend, "risk_per_lot", None)
        if callable(risk_per_lot) and self.equity > 0:
            with contextlib.suppress(Exception):
                money = mt5_rules.risk_at_stop(self.lots, risk_per_lot(self.symbol, order["side"], entry, order["sl"]))
                problems += mt5_rules.check_risk({"pct_of_equity": round(money / self.equity * 100, 2)})
        return problems

    def _check_day(self, equity: float, now: float) -> None:
        day = int(now // 86400)
        if day != self.day:
            self.day, self.day_start_equity = day, equity

    # ── logging ─────────────────────────────────────────────────────────────
    def _log(self, row: dict[str, Any]) -> None:
        row = {"t": round(self.clock(), 1), **row}
        with self._lock:
            self.log.append(row)
            del self.log[:-KEEP_LOG]
        with contextlib.suppress(OSError):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(row, ensure_ascii=False, default=float) + "\n")

    # ── deciding ────────────────────────────────────────────────────────────
    def _decide(self, bid: float, ask: float, now: float) -> dict[str, Any] | None:
        """The side to take on this closed bar, with its stop and target in ATR, or None."""
        self.decisions += 1
        try:
            self.brain_view = view = self.brain.analyse(self.backend, now=now, spread=ask - bid)
        except Exception as exc:  # noqa: BLE001 - a brain failure skips this bar, the run goes on
            self.brain_view = view = {"ok": False, "reason": f"error: {exc}"[:200]}
        with contextlib.suppress(Exception):
            memory.record_context(self.symbol, self.timeframe, now, spread=ask - bid, folder=getattr(self.brain, "folder", None))
        with contextlib.suppress(Exception):
            self.brain.maybe_train(self.backend)
        if not view.get("ok"):
            return {"side": 0, "why": f"otak: {view.get('reason', '')}", "source": "brain"}
        best = view["best"]
        brain_side = (1 if best["side"] == "buy" else -1) if view.get("take") else 0
        decision: dict[str, Any] = {"side": brain_side, "stop_atr": best["stop_atr"], "target_atr": best["target_atr"],
                                    "trail_atr": self.trail_atr, "why": f"otak {best['name']} edge {best['edge_atr']:+.2f} ATR",
                                    "source": "brain", "brain_side": brain_side, "plan": best["name"]}
        if self.mode == "brain":
            return decision
        answer = self._ask_llm(view, bid, ask, now)
        if answer is None:
            return {**decision, "side": 0, "why": "LLM tidak menjawab", "source": "llm"}
        llm_side = {"buy": 1, "sell": -1}.get(answer["side"], 0)
        plan = answer["_plan"]
        decision.update(source="llm", llm_side=llm_side, confidence=answer.get("confidence"), why=answer.get("why", ""),
                        plan_id=plan["id"])
        if plan.get("entry") is not None:
            decision.update(stop_atr=plan["stop_atr"], target_atr=plan["target_atr"], trail_atr=plan.get("trail_atr", self.trail_atr))
        if self.mode == "llm":
            decision["side"] = llm_side
        else:
            decision["side"] = llm_side if llm_side and llm_side == brain_side else 0
            if not decision["side"]:
                decision["why"] = f"otak dan LLM tidak sepakat ({best['side']} vs {answer['side']}): {answer.get('why', '')}"
        return decision

    def _ask_llm(self, view: dict[str, Any], bid: float, ask: float, now: float) -> dict[str, Any] | None:
        folder = getattr(self.brain, "folder", None)
        base = memory.history_m5(self.symbol, self.backend)
        plans = analyst.update(self.symbol, base, folder)
        bars = memory.resample(base, memory.TIMEFRAMES[self.timeframe], now)
        news = None
        if self.news is not None:
            with contextlib.suppress(Exception):
                news = self.news()
        picture = analyst.consider(self.symbol, self.timeframe, bars, now, price=(bid + ask) / 2, digits=self.digits)
        context = analyst.build_context(view, symbol=self.symbol, timeframe=self.timeframe, bid=bid, ask=ask, now=now, bars=bars,
                                        news=news, plans=plans, trail_atr=self.trail_atr, considerations=picture)
        context["note"] = f"Real orders: {self.lots} lot, stop in the terminal, trailing exit. 'none' sends nothing."
        answer = self.llm.decide(context, system=analyst.SYSTEM)
        if answer is None:
            return None
        plan = analyst.make_plan(answer, view, symbol=self.symbol, timeframe=self.timeframe, bid=bid, ask=ask, digits=self.digits,
                                 now=now, source="auto", model=getattr(self.llm, "model", ""), cost_usd=getattr(self.llm, "cost_usd", 0.0),
                                 trail_atr=self.trail_atr, lock_atr=self.lock_atr, considerations=picture)
        analyst.record(plan, folder)
        return {**answer, "_plan": plan}

    # ── orders ──────────────────────────────────────────────────────────────
    def _open(self, decision: dict[str, Any], bid: float, ask: float, now: float) -> None:
        side = int(decision["side"])
        atr = float(self.brain_view.get("atr") or 0.0)
        if atr <= 0:
            self._log({"type": "skip", "why": "ATR tidak diketahui"})
            return
        spread = max(0.0, ask - bid)
        entry = ask if side > 0 else bid
        stop_distance = max(float(decision["stop_atr"]) * atr, spread * MIN_STOP_SPREADS)
        stop = round(entry - side * stop_distance, self.digits)
        target = round(entry + side * float(decision["target_atr"]) * atr, self.digits)
        order = {"symbol": self.symbol, "side": "buy" if side > 0 else "sell", "volume": self.lots, "sl": stop, "tp": None,
                 "comment": f"auto {self.timeframe} trail"}
        problems = self._rule_problems(order, bid, ask, entry)
        if problems:
            self.errors += 1         # the same settings fail every bar: three in a row end the run with the reason
            self._log({"type": "refused", "side": order["side"], "comment": "; ".join(problems)[:200]})
            return
        before: set[int] = set()
        with contextlib.suppress(Exception):
            before = {int(p["ticket"]) for p in self.backend.positions()}
        try:
            result: dict[str, Any] = self.backend.send(order)
        except Exception as exc:  # noqa: BLE001 - a broker error is one failed order
            result = {"ok": False, "comment": str(exc)[:120]}
        ticket = (result.get("order") or result.get("deal")) if result.get("ok") else None
        self.orders += 1
        if not ticket:
            self.errors += 1
            if result.get("unknown"):
                self.unconfirmed = {"at": now, "before": before, "side": side, "stop": stop, "target": target, "atr": atr,
                                    "decision": decision}
                self._log({"type": "unconfirmed", "side": order["side"], "comment": str(result.get("comment", ""))[:120]})
                return
            self._log({"type": "refused", "side": order["side"], "comment": str(result.get("comment", ""))[:120]})
            return
        self.errors = 0
        self._track(int(ticket), side, entry, stop, target, atr, decision, now)

    def _track(self, ticket: int, side: int, entry: float, stop: float, target: float, atr: float, decision: dict[str, Any],
               now: float, how: str = "open") -> None:
        """Start managing a filled position: its trail, its hold, and the journal."""
        t, lk = trail.clamp(decision.get("trail_atr", self.trail_atr), self.lock_atr)
        state = trail.TrailState(side, stop, target, atr, t, lk)
        hold = memory.HOLD_BARS[self.timeframe] * memory.TIMEFRAMES[self.timeframe]
        self.live = Live(ticket, side, entry, stop, target, now, now + hold, self.lots, state, decision.get("source", "brain"),
                         decision.get("why", ""), decision.get("plan_id", ""))
        side_name = "buy" if side > 0 else "sell"
        journal.record({"event": "order", "ticket": ticket, "symbol": self.symbol, "side": side_name, "volume": self.lots,
                        "sl": stop, "tp": None, "reason": f"auto {self.mode}: {decision.get('why', '')}"[:200], "source": "auto",
                        "backend": getattr(self.backend, "name", "")})
        self._log({"type": how, "ticket": ticket, "side": side_name, "entry": entry, "stop": stop, "target_level": target,
                   "atr": round(atr, self.digits + 1), "trail_atr": t, "lock_atr": lk, "source": self.live.source, "why": self.live.why})

    def _find_unconfirmed(self, now: float) -> None:
        """Look for the position of an order that got no answer, and manage it; or give up after a while."""
        pending = self.unconfirmed
        if pending is None:
            return
        side_name = "buy" if pending["side"] > 0 else "sell"
        for p in self.backend.positions():
            if (int(p["ticket"]) not in pending["before"] and p.get("symbol") == self.symbol and p.get("side") == side_name
                    and str(p.get("comment") or "").startswith("auto")):
                self.unconfirmed = None
                entry = float(p.get("open_price") or 0.0)
                stop = float(p.get("sl") or pending["stop"])
                self._track(int(p["ticket"]), pending["side"], entry, stop, pending["target"], pending["atr"],
                            pending["decision"], now, how="adopted")
                self.errors = 0
                return
        if now - pending["at"] >= UNCONFIRMED_WAIT_S:
            self.unconfirmed = None
            self._log({"type": "not_filled", "side": side_name, "why": f"tidak ada posisi baru setelah {UNCONFIRMED_WAIT_S} detik"})

    def _manage(self, bid: float, ask: float, now: float) -> None:
        """Move the stop behind the price, close at the hold, and notice a close made by the broker."""
        live = self.live
        if live is None:
            self._find_unconfirmed(now)
            return
        positions = {int(p["ticket"]): p for p in self.backend.positions()}
        pos = positions.get(live.ticket)
        if pos is None:
            if now - live.opened < CONFIRM_GRACE_S:
                return              # filled, but the terminal's list has not caught up yet
            self._closed(live, "broker", now)
            return
        live.last_seen = pos
        price = bid if live.side > 0 else ask
        moved = live.state.update(price)
        if moved == "exit" or now >= live.closes:
            why = live.state.exit_label if moved == "exit" else "waktu"
            try:
                result = self.backend.close(live.ticket, live.lots)
                ok = bool(result.get("ok", True))
            except Exception as exc:  # noqa: BLE001 - the broker may close it at the stop anyway
                ok, result = False, {"comment": str(exc)[:120]}
            self._log({"type": "close_sent", "ticket": live.ticket, "why": why, "ok": ok, "comment": str(result.get("comment", ""))[:120]})
            if ok:
                self._closed(live, why, now)
            return
        # The terminal's stop follows the trail. A refused move is not dropped until the trail moves
        # again: it is tried again on its own, a little later each time, so the stop in the terminal
        # (the one that holds if this app stops) does not stay behind.
        if live.state.stop is not None and now >= live.modify_retry_at:
            new_stop = round(float(live.state.stop), self.digits)
            if live.side * (new_stop - live.stop) > 0:
                try:
                    result = self.backend.modify(live.ticket, new_stop, None)
                    ok = bool(result.get("ok", True))
                except Exception as exc:  # noqa: BLE001 - tried again after a pause
                    ok, result = False, {"comment": str(exc)[:120]}
                self.modifies += 1
                if ok:
                    live.stop, live.modify_failed, live.modify_retry_at = new_stop, 0, 0.0
                else:
                    live.modify_failed += 1
                    live.modify_retry_at = now + min(MODIFY_RETRY_MAX_S, POLL_S * 2 ** live.modify_failed)
                self._log({"type": "trail", "ticket": live.ticket, "stop": new_stop, "best": live.state.best, "locked": live.state.locked,
                           "how": moved or "retry", "ok": ok, "comment": str(result.get("comment", ""))[:120],
                           **({} if ok else {"failed": live.modify_failed, "retry_in_s": round(live.modify_retry_at - now)})})

    def _closed(self, live: Live, why: str, now: float) -> None:
        profit = float(live.last_seen.get("profit") or 0.0) + float(live.last_seen.get("swap") or 0.0)
        row = {**live.to_dict(), "closed": now, "why": why, "profit": round(profit, 2)}
        self.closed.append(row)
        del self.closed[:-KEEP_LOG]
        self.live = None
        journal.record({"event": "close", "ticket": live.ticket, "symbol": self.symbol, "volume": live.lots, "profit": round(profit, 2),
                        "source": "auto", "backend": getattr(self.backend, "name", "")})
        self._log({"type": "closed", **row})

    # ── the run ─────────────────────────────────────────────────────────────
    def _cycle(self) -> None:
        now = self.clock()
        tick = self.backend.tick(self.symbol)
        bid, ask = float(tick["bid"]), float(tick["ask"])
        self._manage(bid, ask, now)
        seconds = memory.TIMEFRAMES[self.timeframe]
        bucket = int((now - BAR_GRACE_S) // seconds)
        if bucket == self._bucket:
            return
        first, self._bucket = self._bucket < 0, bucket
        if first and now - bucket * seconds > BAR_GRACE_S + FIRST_LATE_S:
            # Started in the middle of a bar: the bar the brain would read closed a while ago and the
            # price has moved on since, so the first decision waits for the next close.
            self._log({"type": "wait", "why": "dimulai di tengah bar: keputusan pertama menunggu bar berikutnya tutup",
                       "next_decision": (bucket + 1) * seconds + BAR_GRACE_S})
            return
        decision = self._decide(bid, ask, now)
        if decision is None:
            return
        self.last_decision = {**{k: v for k, v in decision.items() if k != "_plan"}, "at": now, "bid": bid, "ask": ask,
                              "side": {1: "buy", -1: "sell"}.get(int(decision.get("side") or 0), "none")}
        self._log({"type": "decision", **self.last_decision})
        if decision.get("side") and self.live is None and self.unconfirmed is None:
            self._open(decision, bid, ask, now)

    def run(self) -> None:
        try:
            account = self._account()
            self.equity = float(account["equity"])
            self.started_at = self.clock()
            self._check_day(self.equity, self.started_at)
            self.state = "running"
            limit = self.started_at + self.max_hours * 3600
            self._log({"type": "start", "mode": self.mode, "lots": self.lots, "trail_atr": self.trail_atr, "lock_atr": self.lock_atr,
                       "equity": self.equity, "live_account": bool(account.get("live"))})
            while not self.stop_event.is_set():
                now = self.clock()
                if now >= limit:
                    self.ended = "batas waktu"
                    break
                account = self._account()
                self.equity = float(account["equity"])
                self._check_day(self.equity, now)
                if self.day_start_equity and self.day_start_equity - self.equity >= self.day_start_equity * self.max_daily_loss_pct / 100:
                    self.ended = f"rugi hari ini menyentuh {self.max_daily_loss_pct:g}% ekuitas awal hari"
                    break
                try:
                    self._cycle()
                except Exception as exc:  # noqa: BLE001 - a feed hiccup is logged, the run goes on
                    self._log({"type": "error", "error": str(exc)[:200]})
                if self.errors >= MAX_ERRORS:
                    self.ended = f"{MAX_ERRORS} order ditolak berturut-turut"
                    break
                self.sleep(POLL_S)
            self.ended = self.ended or "dihentikan"
        except Exception as exc:  # noqa: BLE001 - the reason must reach the report
            self.ended = f"error: {exc}"
        finally:
            self._settle()
            self.state = "finished"
            self._log({"type": "end", "ended": self.ended})

    def _settle(self) -> None:
        """Close the position this run opened, so nothing it manages is left without its trail."""
        live = self.live
        if live is None:
            if self.unconfirmed is not None:
                self._log({"type": "warning", "why": "sebuah order tanpa jawaban mungkin terbuka di terminal; periksa posisinya"})
            return
        try:
            result = self.backend.close(live.ticket, live.lots)
            ok = bool(result.get("ok"))
        except Exception as exc:  # noqa: BLE001 - reported below
            ok, result = False, {"comment": str(exc)[:120]}
        if ok:
            self._closed(live, "run ended", self.clock())
            return
        # Still open in the terminal, with its last stop. It stays in the report instead of being called closed.
        self._log({"type": "close_failed", "ticket": live.ticket, "stop": live.stop, "comment": str(result.get("comment", ""))[:120]})
        self.ended = f"{self.ended}; posisi {live.ticket} MASIH TERBUKA (stop {live.stop}), tutup manual di terminal"

    def report(self) -> dict[str, Any]:
        with self._lock:
            now = self.clock()
            profit = sum(float(c.get("profit") or 0.0) for c in self.closed)
            wins = sum(1 for c in self.closed if float(c.get("profit") or 0.0) > 0)
            return {
                "state": self.state, "ended": self.ended, "symbol": self.symbol, "timeframe": self.timeframe, "mode": self.mode,
                "lots": self.lots, "trail_atr": self.trail_atr, "lock_atr": self.lock_atr, "max_daily_loss_pct": self.max_daily_loss_pct,
                "max_hours": self.max_hours, "allow_live": self.allow_live, "started_at": self.started_at,
                "elapsed_s": round(now - self.started_at, 1) if self.started_at else 0.0,
                "equity": self.equity, "day_start_equity": self.day_start_equity,
                "day_pnl": round(self.equity - self.day_start_equity, 2) if self.day_start_equity else 0.0,
                "decisions": self.decisions, "orders": self.orders, "modifies": self.modifies, "errors": self.errors,
                "position": self.live.to_dict() if self.live else None,
                "closed": list(self.closed[-50:]), "closed_count": len(self.closed), "wins": wins,
                "profit": round(profit, 2), "last_decision": self.last_decision,
                "brain": {"ok": self.brain_view.get("ok"), "take": self.brain_view.get("take"), "best": self.brain_view.get("best"),
                          "reason": self.brain_view.get("reason")} if self.brain_view else None,
                "llm": self.llm.report() if self.llm is not None else None,
                "log": list(self.log[-40:]), "file": str(self.path), "session": sessions.describe(now),
            }


__all__ = ["AutoTrader", "MODES", "MAX_LOTS", "MAX_HOURS"]
