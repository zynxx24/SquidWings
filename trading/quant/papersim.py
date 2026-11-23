"""Paper simulation on the live price: open, wait 30 seconds, close, again, until a rule wins enough.

Nothing here sends an order. Every cycle each candidate rule (the eight scalp strategies, their
weighted ensemble, and a coin flip as a yardstick) that has a side opens one virtual position at the
real ask or bid. After ``hold_s`` seconds it closes at the real bid or ask, so the spread is paid
as it would be. The search stops when a rule reaches ``target`` win rate over at least
``min_trades`` trades, or at its time limit.

Two more candidates join the eight rules and their ensemble:

* ``matematika``: fourteen measurements combined into P(up) by a logistic regression that was
  graded on bars it had not seen (``confluence``). It takes a side only when it passed that test.
* ``llm``: the trading LLM (``llm_trader``), asked once per cycle with the math, the rules' live
  records, its own last trades, the session, the MT4 market condition and the news. Its answer
  arrives a few seconds into the cycle and its position opens at that moment's price.

Every cycle's numbers (votes, the math and its contributions, the session, the news mood, the
LLM's answer) and every closed trade go to ``trading/data/sim/<start>-<symbol>.jsonl``, and the
report splits each rule's record by session (Tokyo, London, New York, their overlaps).

Two styles. Scalping (the default, ``timeframe=""``) holds seconds on 5-second bars, where the
spread is often half a bar's range and "none" is usually the honest answer. The intraday style
(``timeframe`` M15, M30, H1 or H4, built from the EA's M5 bars when the backend has no such
bars) decides once per closed bar, holds ``hold_bars`` bars, and closes earlier at a stop and a
target set in ATR (``stop_atr``, ``target_atr``; the LLM may choose its own). Each rule keeps at
most one position, so a rule whose trade is still open sits out the next bar.

A win rate is not an edge. Ten rules tried at once means one will look good by luck, a coin flip
runs alongside to show how much, and the report gives the 95% lower bound and the break-even win
rate (wins can be smaller than losses) next to every win rate. Each rule's average result also
gets a t-test, and its p-value is adjusted for the number of rules tried (Sidak), so "the best of
twelve" is judged as the best of twelve.

Once per closed bar (per minute in scalping) the run takes the trader's checklist picture
(``playbook.considerations``): price levels and their confluence zones, the market's clocks, the
hour's seasonality and the cycle. The LLM reads it, every position is tagged with the conditions
it was opened in (which part of the day, into a zone or away from one, with or against the pivot,
the swing and the cycle), and the report splits each rule's record by those conditions, so the
panel shows *where* a rule makes and loses, not only how often.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import math
import random
import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trading import news as market_news
from trading.quant import (
    analyst,
    confluence,
    memory,
    playbook,
    riskmath,
    scalp,
    sessions,
    ticks,
    trail,
)
from trading.quant.memory import resample

HOLD_S = 30.0
TARGET = 0.6
MIN_TRADES = 20
MAX_MINUTES = 240
POLL_S = 1.0
MAX_ERRORS = 10
BARS_NEEDED = 60
KEEP_TRADES = 400
ENSEMBLE = "ensemble"
COIN = "acak"
MATH = "matematika"
LLM = "llm"
NEURAL = "neural"
CANDLE = "candle_lm"
CANDIDATES = (*scalp.STRATEGIES, ENSEMBLE, MATH, COIN)
LABELS = {
    "ema_trend": "Tren EMA 9/21", "momentum": "Momentum 5 bar", "breakout": "Breakout 20 bar",
    "rsi_reversal": "RSI berbalik", "band_reversal": "Bollinger berbalik", "vwap_side": "Sisi VWAP",
    "kalman_slope": "Kemiringan Kalman", "candle_pressure": "Tekanan candle", ENSEMBLE: "Ensemble berbobot",
    MATH: "Matematika gabungan (14 ukuran)", LLM: "LLM", COIN: "Lempar koin (pembanding)",
    NEURAL: "Jaringan saraf (otak)", CANDLE: "Model candle (skenario A/B)",
}
FOLDER = Path(__file__).resolve().parents[1] / "data" / "sim"
KEEP_CYCLES = 120
LLM_MAX_LATE_S = 0.5          # an answer later than this share of the hold is dropped: the moment has passed
TIMEFRAMES = {"M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}
HOLD_BARS = 4
STOP_ATR = 1.5
TARGET_ATR = 2.0
MAX_MINUTES_DAY = 7 * 24 * 60   # the intraday style may run a week
BAR_GRACE_S = 15                # wait this long after a bar closes, so the EA's last M5 bar is in
ATR_BARS = 14
BASE_BARS = 6000                # M5 bars asked for when building the higher timeframe (the bridge keeps 6000)
PICTURE_EVERY_S = 60.0          # scalping: the checklist picture (levels, clocks, cycle) is refreshed this often
MIN_TAG_TRADES = 3              # a condition is shown to the LLM once it has this many trades


def atr(bars: list[dict[str, float]], n: int = ATR_BARS) -> float:
    """The average true range of the last ``n`` bars."""
    ranges = [max(b["h"], prev["c"]) - min(b["l"], prev["c"]) for prev, b in zip(bars[-n - 1:-1], bars[-n:], strict=False)]
    return statistics.fmean(ranges) if ranges else 0.0


def wilson_low(wins: int, n: int, z: float = 1.96) -> float:
    """The 95% lower bound of a win rate: how low the true rate could plausibly be."""
    if n == 0:
        return 0.0
    p = wins / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / (1 + z * z / n))


@dataclass
class Stats:
    trades: int = 0
    wins: int = 0
    win_points: float = 0.0
    loss_points: float = 0.0
    sum_sq: float = 0.0

    def add(self, points: float) -> None:
        self.trades += 1
        self.sum_sq += points * points
        if points > 0:
            self.wins += 1
            self.win_points += points
        else:
            self.loss_points += -points

    def report(self, name: str) -> dict[str, Any]:
        losses = self.trades - self.wins
        avg_win = self.win_points / self.wins if self.wins else 0.0
        avg_loss = self.loss_points / losses if losses else 0.0
        return {
            "name": name, "label": LABELS.get(name, name), "trades": self.trades, "wins": self.wins,
            "win_rate": round(self.wins / self.trades, 4) if self.trades else None,
            "wilson_low": round(wilson_low(self.wins, self.trades), 4),
            "avg_win": avg_win, "avg_loss": avg_loss,
            # Wins smaller than losses need a higher win rate just to break even.
            "breakeven_win_rate": round(avg_loss / (avg_win + avg_loss), 4) if avg_win + avg_loss else None,
            "net_points": self.win_points - self.loss_points,
            "expectancy": (self.win_points - self.loss_points) / self.trades if self.trades else None,
            "payoff": round(avg_win / avg_loss, 3) if avg_loss else None,
            "profit_factor": round(self.win_points / self.loss_points, 3) if self.loss_points else None,
            # is the average result above zero by more than luck (one-sided t-test on the points)
            **{k: v for k, v in riskmath.mean_test_sums(self.trades, self.win_points - self.loss_points, self.sum_sq).items()
               if k != "n"},
        }


@dataclass
class Position:
    rule: str
    side: int            # 1 buy, -1 sell
    entry: float
    opened: float
    closes: float
    cycle: int
    session: str = ""
    why: str = ""
    stop: float | None = None
    target: float | None = None
    trail: trail.TrailState | None = None   # a trailing exit: the target arms it instead of closing
    risk: float | None = None               # the distance to the first stop: results in R are points / risk
    tags: dict[str, str] = field(default_factory=dict)   # the conditions it was opened in (playbook.tags)


@dataclass
class Pending:
    """A virtual limit order: a buy fills when the ask comes down to ``entry``, a sell when the bid comes up to it."""
    rule: str
    side: int
    entry: float
    stop: float
    target: float
    expires: float
    hold_s: float
    cycle: int
    why: str = ""
    kind: str = ""


@dataclass
class PaperSim:
    backend: Any
    symbol: str
    hold_s: float = HOLD_S
    target: float = TARGET
    min_trades: int = MIN_TRADES
    max_minutes: float = 60.0
    stop_on_find: bool = True
    bars: Callable[[str], list[dict[str, float]]] | None = None
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    seed: int | None = None
    llm: Any = None                                     # an llm_trader.LLMTrader, or None for no LLM rule
    news: Callable[[], dict[str, Any]] | None = None    # the news snapshot (cached), or None
    folder: Path | None = None                          # where the per-cycle log goes; None keeps it in memory
    timeframe: str = ""                                 # "" scalping; M15, M30, H1 or H4 the intraday style
    hold_bars: int = HOLD_BARS                          # intraday: how many bars a position is held at most
    stop_atr: float = STOP_ATR                          # intraday: stop and target in ATR, 0 for none
    target_atr: float = TARGET_ATR
    brain: Any = None                                   # intraday: a brain.Brain that trades, learns from every bar, and briefs the LLM
    candle: bool = False                                # intraday: the candle model's scenarios trade as their own rule (candle_view)
    trail_atr: float = 0.0                              # intraday, brain and LLM positions: trailing exit (0 = fixed target)
    lock_atr: float = trail.LOCK_ATR                    # at the target the stop locks this many ATR behind it
    stop_event: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        if self.timeframe:
            if self.timeframe not in TIMEFRAMES:
                raise ValueError(f"timeframe: {', '.join(TIMEFRAMES)}, or empty for scalping")
            if not 1 <= int(self.hold_bars) <= 48:
                raise ValueError("hold_bars must be 1-48")
            if not (0 <= self.stop_atr <= 10 and 0 <= self.target_atr <= 10):
                raise ValueError("stop_atr and target_atr must be 0-10")
            self.hold_bars = int(self.hold_bars)
            self.hold_s = float(self.hold_bars * TIMEFRAMES[self.timeframe])
            limit = MAX_MINUTES_DAY
        else:
            if not 5 <= self.hold_s <= 600:
                raise ValueError("hold_s must be 5-600 seconds")
            limit = MAX_MINUTES
        if not 0.5 < self.target < 1:
            raise ValueError("target must be between 0.5 and 1")
        if not 0 < self.max_minutes <= limit:
            raise ValueError(f"max_minutes must be above 0 and at most {limit}")
        self.atr = 0.0
        self._bucket: int | None = None
        self.min_trades = max(5, int(self.min_trades))
        if self.brain is not None and not self.timeframe:
            raise ValueError("the brain needs the intraday style (a timeframe)")
        if self.candle and not self.timeframe:
            raise ValueError("the candle model needs the intraday style (a timeframe)")
        extra = (((NEURAL,) if self.brain is not None else ()) + ((CANDLE,) if self.candle else ())
                 + ((LLM,) if self.llm is not None else ()))
        self.candidates = (*CANDIDATES[:-1], *extra, COIN)
        self.brain_view: dict[str, Any] = {}
        self._llm_system: str | None = None
        self._llm_view: dict[str, Any] = {}
        self.analyst_last: dict[str, Any] | None = None
        self.pending: list[Pending] = []
        self.candle_last: dict[str, Any] = {}
        self.stats = {name: Stats() for name in self.candidates}
        self.by_session: dict[tuple[str, str], Stats] = {}
        self.by_tag: dict[tuple[str, str, str], Stats] = {}
        self.picture: dict[str, Any] | None = None
        self._picture_at = 0.0
        self.cycle_log: list[dict[str, Any]] = []
        self.math: dict[str, Any] | None = None
        self._math_key: tuple[float, int] | None = None
        self._llm_pending = False
        self._llm_answer: dict[str, Any] | None = None
        self.llm_skips = 0
        self.news_view: dict[str, Any] = {}
        self.condition: dict[str, Any] = {}
        self.path = (self.folder / f"{dt.datetime.now(dt.UTC):%Y%m%d-%H%M%S}-{self.symbol}.jsonl") if self.folder else None
        self.open: list[Position] = []
        self.trades: list[dict[str, Any]] = []
        self.state = "ready"
        self.ended = ""
        self.found: dict[str, Any] | None = None
        self.cycles = 0
        self.errors = 0
        self.started_at = 0.0
        self.last_error = ""
        self.digits = 5
        self._coin = random.Random(self.seed)
        self._lock = threading.Lock()

    # ── market ──────────────────────────────────────────────────────────────
    def _bars(self) -> list[dict[str, float]]:
        if self.bars:
            return self.bars(self.symbol)
        if self.timeframe:
            return self._timeframe_bars()
        recorded, _ = ticks.load_bars(self.symbol)          # 5-second bars from the EA's quotes, when there are enough
        if len(recorded) >= scalp.WARMUP_BARS:
            return recorded[-720:]
        keys = ("t", "o", "h", "l", "c", "v")
        return [{k: float(b[k]) for k in keys if k in b} for b in self.backend.rates(self.symbol, "M1", 300)]

    def _timeframe_bars(self) -> list[dict[str, float]]:
        """Closed bars of the chosen timeframe: from the backend when it has them, else built from M5."""
        seconds, now = TIMEFRAMES[self.timeframe], self.clock()
        keys = ("t", "o", "h", "l", "c", "v")
        try:
            direct = [{k: float(b[k]) for k in keys if k in b} for b in self.backend.rates(self.symbol, self.timeframe, 600)]
            direct = [b for b in direct if b["t"] + seconds <= now]
            if len(direct) >= BARS_NEEDED:
                return direct
        except Exception:  # noqa: BLE001 - the MT4 bridge only streams M1 and M5, so build it
            pass
        base = [{k: float(b[k]) for k in keys if k in b} for b in self.backend.rates(self.symbol, "M5", BASE_BARS)]
        return resample(base, seconds, now)

    def _levels(self, side: int, entry: float, stop_atr: float | None = None, target_atr: float | None = None
                ) -> tuple[float | None, float | None]:
        """The stop and target prices of an intraday position; none in scalping."""
        if not self.timeframe or self.atr <= 0:
            return None, None
        k_stop = self.stop_atr if stop_atr is None else stop_atr
        k_target = self.target_atr if target_atr is None else target_atr
        stop = round(entry - side * k_stop * self.atr, self.digits) if k_stop > 0 else None
        target = round(entry + side * k_target * self.atr, self.digits) if k_target > 0 else None
        return stop, target

    def _votes(self, bars: list[dict[str, float]]) -> dict[str, int]:
        """Each rule's side on the last closed bar: 1 buy, -1 sell, 0 nothing."""
        rows = scalp.votes(bars)
        horizon = max(1, round(self.hold_s / scalp._bar_seconds(bars)))
        score = scalp.weighted_scores(bars, rows, horizon)[-1]
        self.ensemble_score = round(score, 4)
        sides = {name: rows[i][-1] for i, name in enumerate(scalp.STRATEGIES)}
        sides[ENSEMBLE] = (score > 0.15) - (score < -0.15)
        sides[MATH] = int(self._math(bars, horizon).get("side", 0))
        sides[COIN] = self._coin.choice((1, -1))
        return sides

    def _math(self, bars: list[dict[str, float]], horizon: int) -> dict[str, Any]:
        """The confluence answer, worked out again only when a new bar has closed."""
        key = (bars[-1]["t"], len(bars))
        if key != self._math_key:
            self._math_key = key
            try:
                self.math = confluence.analyse(bars[-900:], horizon)
            except Exception as exc:  # noqa: BLE001 - bad data costs this rule its vote, not the run
                self.math = {"ok": False, "side": 0, "reason": f"error: {exc}"}
        return self.math or {}

    # ── the checklist picture ───────────────────────────────────────────────
    def _read_picture(self, bars: list[dict[str, float]], bid: float, ask: float, now: float) -> None:
        """Levels, clocks, seasonality and the cycle for this cycle. Intraday: from the run's own bars.
        Scalping: from the M5 history, since a day's pivots and swings cannot be read from 5-second bars."""
        try:
            if self.timeframe:
                source, seconds = bars, TIMEFRAMES[self.timeframe]
            elif now - self._picture_at < PICTURE_EVERY_S and self.picture is not None:
                return
            else:
                source, seconds = memory.history_m5(self.symbol, self.backend), memory.BASE_SECONDS
            self._picture_at = now
            self.picture = playbook.considerations(source, seconds, now, price=(bid + ask) / 2, digits=self.digits,
                                                   key=(self.symbol, self.timeframe or "M5"))
        except Exception as exc:  # noqa: BLE001 - the picture is extra evidence, never a reason to stop
            self.picture = {"ok": False, "reason": f"error: {exc}"[:160]}

    def _picture_brief(self) -> dict[str, Any]:
        """A few numbers of the picture for the cycle log."""
        view = self.picture or {}
        if not view.get("ok"):
            return {}
        lv, cycle = view.get("levels") or {}, view.get("cycle") or {}
        return {"phase": (view.get("clock") or {}).get("phase"), "room_up_atr": lv.get("room_up_atr"),
                "room_down_atr": lv.get("room_down_atr"), "at_zone": (lv.get("at_zone") or {}).get("strength"),
                "fib_retraced": (lv.get("fib") or {}).get("retraced"),
                "cycle": cycle.get("direction") if cycle.get("significant") else None}

    def _tagged(self, p: Position) -> Position:
        """Note the conditions a position is opened in, and its risk when it has a stop."""
        p.tags = playbook.tags(self.picture, "buy" if p.side > 0 else "sell")
        if p.stop is not None and p.stop != p.entry:
            p.risk = abs(p.entry - p.stop)
        return p

    def _like_now(self, rule: str) -> dict[str, list[dict[str, Any]]]:
        """For a buy and for a sell now: how this rule's earlier trades under the same conditions ended."""
        out: dict[str, list[dict[str, Any]]] = {}
        for side in ("buy", "sell"):
            rows = []
            for name, value in playbook.tags(self.picture, side).items():
                st = self.by_tag.get((rule, name, value))
                if st and st.trades >= MIN_TAG_TRADES:
                    rows.append({"condition": f"{name}: {value}", "trades": st.trades, "wins": st.wins,
                                 "win_rate_cautious": round(riskmath.beta_lower(st.wins, st.trades), 3),
                                 "net_points": round(st.win_points - st.loss_points, self.digits + 1)})
            if rows:
                out[side] = rows
        return out

    # ── the cycle log ───────────────────────────────────────────────────────
    def _log(self, row: dict[str, Any]) -> None:
        if row.get("type") == "cycle":
            self.cycle_log.append(row)
            del self.cycle_log[:-KEEP_CYCLES]
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:      # a full disk must not stop the run; the report says so
            self.last_error = f"log: {exc}"

    def _math_view(self) -> dict[str, Any]:
        m = self.math or {}
        if not m.get("ok"):
            return {"ok": False, "reason": m.get("reason", "")}
        return {k: m[k] for k in ("p_up", "clarity", "side", "graded", "test_accuracy", "base_rate", "test_samples",
                                  "test_accuracy_when_called", "regime", "reason")} | {"top": m["contributions"][:6]}

    def _read_news(self) -> None:
        if self.news is None:
            return
        try:
            snap = self.news()
        except Exception as exc:  # noqa: BLE001 - news is extra context, never a reason to stop
            self.news_view = {"error": str(exc)[:120]}
            return
        cal = snap.get("calendar", {})
        self.news_view = {"mood": snap.get("mood"), "near_high": cal.get("near_high"), "next_high": cal.get("next_high"),
                          "fear_greed": (snap.get("crypto") or {}).get("fear_greed"),
                          "topics": {k: {"count": v["count"], "tone": v["tone"]} for k, v in snap.get("topics", {}).items()
                                     if k in snap.get("relevant", [])},
                          "brief": market_news.brief(snap)}

    # ── the LLM rule ────────────────────────────────────────────────────────
    def _llm_context(self, bars: list[dict[str, float]], sides: dict[str, int], bid: float, ask: float, now: float) -> dict[str, Any]:
        closes = [b["c"] for b in bars[-21:]]
        moves = [round((closes[i] - closes[i - 1]) / closes[i - 1] * 100, 4) for i in range(1, len(closes)) if closes[i - 1]]
        mine = [t for t in self.trades if t["rule"] == LLM][-8:]
        record = self.stats[LLM].report(LLM)
        word = {1: "buy", -1: "sell", 0: "none"}
        in_r = [t["r"] for t in self.trades if t["rule"] == LLM and t.get("r") is not None]
        like_now = self._like_now(LLM)
        return {
            "symbol": self.symbol, "time_utc": dt.datetime.fromtimestamp(now, dt.UTC).isoformat(timespec="seconds"),
            "session": sessions.label(now), "style": "harian" if self.timeframe else "scalping",
            "timeframe": self.timeframe or f"{scalp._bar_seconds(bars):g}s", "hold_seconds": self.hold_s, "bid": bid, "ask": ask,
            "spread": round(ask - bid, self.digits + 1), "bar_seconds": scalp._bar_seconds(bars),
            **({"atr": round(self.atr, self.digits + 1), "spread_vs_atr": round((ask - bid) / self.atr, 3) if self.atr else None,
                "default_stop_atr": self.stop_atr, "default_target_atr": self.target_atr,
                "move_last_50_bars_pct": round((closes[-1] / bars[-51]["c"] - 1) * 100, 3) if len(bars) > 51 else None}
               if self.timeframe else {}),
            "recent_bar_moves_pct": moves, "market_condition": self.condition,
            "math": self._math_view(),
            "rules_now": {LABELS[name]: word[side] for name, side in sides.items() if name not in (COIN, LLM)},
            "ensemble_score": getattr(self, "ensemble_score", 0.0),
            "rules_record": [{"rule": LABELS[n], "trades": st.trades, "win_rate": st.report(n)["win_rate"],
                              "net_points": round(st.win_points - st.loss_points, self.digits + 1)}
                             for n, st in self.stats.items() if st.trades and n not in (LLM,)],
            "my_record": {"trades": record["trades"], "win_rate": record["win_rate"], "net_points": round(record["net_points"], self.digits + 1)},
            "my_last_trades": [{"side": t["side"], "points": round(t["points"], self.digits + 1), "win": t["win"], "why": t.get("why", ""),
                                **({"r": t["r"]} if t.get("r") is not None else {}),
                                **({"conditions": t["tags"]} if t.get("tags") else {})}
                               for t in mine],
            **({"my_math": {k: v for k, v in riskmath.record(in_r).items() if k != "kelly"}} if in_r else {}),
            **({"my_record_like_now": like_now} if like_now else {}),
            **playbook.compact(self.picture),
            "news_brief": self.news_view.get("brief", ""),
        }

    def _ask_llm(self, context: dict[str, Any], cycle: int, asked_at: float) -> None:
        answer = self.llm.decide(context, system=self._llm_system) if self._llm_system else self.llm.decide(context)
        with self._lock:
            self._llm_pending = False
            if answer is not None and self.state == "running":
                self._llm_answer = {**answer, "cycle": cycle, "asked_at": asked_at}
            elif answer is None:
                self._log({"type": "llm", "cycle": cycle, "t": self.clock(), "error": self.llm.last_error or self.llm.why_unavailable})

    def _start_llm(self, bars: list[dict[str, float]], sides: dict[str, int], bid: float, ask: float, now: float) -> None:
        if self.llm is None or self._llm_pending or any(p.rule == LLM for p in self.open) or not self.llm.available:
            return
        self._llm_pending = True
        if self.brain is not None and self.brain_view.get("ok"):
            # The analyst's job: the brain, look-alikes, conditions, and its own journal across runs.
            plans = analyst.load(self.symbol, getattr(self.brain, "folder", None))
            context = analyst.build_context(self.brain_view, symbol=self.symbol, timeframe=self.timeframe, bid=bid, ask=ask,
                                            now=now, bars=bars, news={"brief": self.news_view.get("brief", ""),
                                                                      "mood": self.news_view.get("mood"),
                                                                      "calendar": {"near_high": self.news_view.get("near_high"),
                                                                                   "next_high": self.news_view.get("next_high")}},
                                            plans=plans, trail_atr=self.trail_atr or None, considerations=self.picture)
            self._llm_system, self._llm_view = analyst.SYSTEM, dict(self.brain_view)
        else:
            context = self._llm_context(bars, sides, bid, ask, now)
            self._llm_system, self._llm_view = None, {}
        threading.Thread(target=self._ask_llm, args=(context, self.cycles, now), name="sim-llm", daemon=True).start()

    def _take_llm_answer(self, bid: float, ask: float, now: float) -> None:
        answer, self._llm_answer = self._llm_answer, None
        if answer is None:
            return
        late = now - answer["asked_at"] > self.hold_s * LLM_MAX_LATE_S
        side = {"buy": 1, "sell": -1}.get(answer["side"], 0)
        opened = bool(side) and not late
        plan = None
        if self._llm_view.get("ok") and not late:
            plan = analyst.make_plan(answer, self._llm_view, symbol=self.symbol, timeframe=self.timeframe, bid=bid, ask=ask,
                                     digits=self.digits, now=now, source="sim", model=getattr(self.llm, "model", ""),
                                     cost_usd=getattr(self.llm, "cost_usd", 0.0), trail_atr=self.trail_atr or None,
                                     lock_atr=self.lock_atr, considerations=self.picture)
            analyst.record(plan, getattr(self.brain, "folder", None))
            self.analyst_last = plan
        if opened:
            entry = ask if side > 0 else bid
            if plan is not None and plan.get("entry") is not None:
                stop, target = plan["stop"], plan["target"]
                closes = now + self._brain_hold_s()
            else:
                stop, target = self._levels(side, entry, answer.get("stop_atr"), answer.get("target_atr"))
                closes = now + self.hold_s
            self.open.append(self._trailing(self._tagged(Position(LLM, side, entry, now, closes, answer["cycle"], sessions.key(now),
                                                                  answer.get("why", ""), stop, target)), answer.get("trail_atr")))
        elif side and late:
            self.llm_skips += 1
        self._log({"type": "llm", "cycle": answer["cycle"], "t": now, "side": answer["side"], "confidence": answer.get("confidence"),
                   "why": answer.get("why", ""), "latency_ms": answer.get("latency_ms"), "opened": opened, "late": late,
                   **{k: answer[k] for k in ("stop_atr", "target_atr") if k in answer},
                   "cost_usd": round(self.llm.cost_usd, 6)})

    # ── one step ────────────────────────────────────────────────────────────
    def step(self) -> None:
        """Close what is due, then open a new cycle when nothing is open."""
        now = self.clock()
        tick = self.backend.tick(self.symbol)
        bid, ask = float(tick["bid"]), float(tick["ask"])
        self.step_close(bid, ask, now)
        if self.found and self.stop_on_find:
            return
        self._take_llm_answer(bid, ask, now)
        self._fill_pending(bid, ask, now)
        if self.timeframe:
            # Intraday: one cycle per closed bar; a rule still holding its trade sits this bar out.
            bucket = int((now - BAR_GRACE_S) // TIMEFRAMES[self.timeframe])
            if bucket == self._bucket:
                return
        elif any(p.rule != LLM for p in self.open):
            # Scalping: the LLM's position runs on its own clock, so it does not hold the next cycle back.
            return
        bars = self._bars()
        if len(bars) < BARS_NEEDED:
            raise RuntimeError(f"butuh {BARS_NEEDED} bar{' ' + self.timeframe if self.timeframe else ''} untuk menilai, "
                               f"baru ada {len(bars)}")
        if self.timeframe:
            self._bucket = bucket
            self.atr = atr(bars)
        self.cycles += 1
        session = sessions.key(now)
        sides = self._votes(bars)
        if self.news is not None and (self.cycles == 1 or self.cycles % 10 == 0):
            self._read_news()
        if self.brain is not None:
            sides[NEURAL] = self._brain_cycle(bid, ask, now)
        self._read_picture(bars, bid, ask, now)
        busy = {p.rule for p in self.open}
        for rule, side in sides.items():
            if side and rule not in busy:
                entry = ask if side > 0 else bid
                if rule == NEURAL:
                    best = self.brain_view["best"]
                    stop, target = self._levels(side, entry, best["stop_atr"], best["target_atr"])
                    closes = now + self._brain_hold_s()
                    self.open.append(self._trailing(self._tagged(Position(rule, side, entry, now, closes, self.cycles, session, "",
                                                                          stop, target))))
                    continue
                stop, target = self._levels(side, entry)
                closes = now + self.hold_s
                self.open.append(self._tagged(Position(rule, side, entry, now, closes, self.cycles, session, "", stop, target)))
        if CANDLE in self.stats:
            sides[CANDLE] = self._candle_cycle(bid, ask, now, session)
        self.condition = market_news.market_condition(bars, tick)
        self._log({"type": "cycle", "cycle": self.cycles, "t": now, "session": session, "session_label": sessions.label(now),
                   "bid": bid, "ask": ask, "spread": round(ask - bid, self.digits + 2), "votes": sides,
                   "ensemble_score": getattr(self, "ensemble_score", 0.0), "math": self._math_view(),
                   "condition": self.condition, "mood": (self.news_view.get("mood") or {}).get("label"),
                   "picture": self._picture_brief(),
                   **({"timeframe": self.timeframe, "atr": round(self.atr, self.digits + 1)} if self.timeframe else {})})
        self._start_llm(bars, sides, bid, ask, now)

    def _trailing(self, p: Position, trail_atr: float | None = None) -> Position:
        """Give a brain or LLM position the trailing exit when the run uses one: its target arms the trail."""
        k = self.trail_atr if trail_atr is None else float(trail_atr)
        if not self.timeframe or k <= 0 or self.atr <= 0:
            return p
        t, lk = trail.clamp(k, self.lock_atr)
        p.trail = trail.TrailState(p.side, p.stop, p.target, self.atr, t, lk)
        p.target = None
        return p

    # ── the brain ───────────────────────────────────────────────────────────
    def _brain_hold_s(self) -> float:
        return float(memory.HOLD_BARS[self.timeframe] * TIMEFRAMES[self.timeframe])

    def _brain_cycle(self, bid: float, ask: float, now: float) -> int:
        """Ask the brain about the bar that closed, give it this bar's live context, retrain when due. Its side."""
        try:
            self.brain_view = self.brain.analyse(self.backend, now=now, spread=ask - bid)
        except Exception as exc:  # noqa: BLE001 - a brain failure costs its vote, not the run
            self.brain_view = {"ok": False, "reason": f"error: {exc}"[:200]}
        fear_greed = (self.news_view.get("fear_greed") or {}).get("value") if isinstance(self.news_view.get("fear_greed"), dict) else None
        mood = (self.news_view.get("mood") or {}).get("score", 0.0) if isinstance(self.news_view.get("mood"), dict) else 0.0
        with contextlib.suppress(Exception):
            memory.record_context(self.symbol, self.timeframe, now, spread=ask - bid, mood=float(mood or 0.0),
                                  fear_greed=float(fear_greed) if fear_greed is not None else None,
                                  folder=getattr(self.brain, "folder", None))
        with contextlib.suppress(Exception):
            self.brain.maybe_train(self.backend)
        view = self.brain_view
        if not view.get("ok") or not view.get("take"):
            return 0
        return 1 if view["best"]["side"] == "buy" else -1

    # ── the candle model's scenarios ────────────────────────────────────────
    def _candle_cycle(self, bid: float, ask: float, now: float, session: str) -> int:
        """Ask the candle model about the newest M5 bar. A trend scenario opens at market; otherwise its levels A and B
        wait as limit orders (one cancels the other). Busy while its position or orders are alive. Its market side."""
        from trading.quant import candle_view

        v = candle_view.view(self.symbol, memory.history_m5(self.symbol, self.backend), price=(bid + ask) / 2)
        self.candle_last = ({"ok": True, "exam": v.get("exam"), "close": v.get("close"), "atr": v.get("atr_price"),
                             "last_bar_time": v.get("last_bar_time"), "scenarios": v.get("scenarios", []),
                             "path": [{k: c.get(k) for k in ("candle", "close", "close_p10_p90", "p_up")} for c in v.get("candles", [])]}
                            if v.get("ok") else {"ok": False, "reason": v.get("reason", "")})
        if not v.get("ok") or self.pending or any(p.rule == CANDLE for p in self.open):
            return 0
        scen = v.get("scenarios", [])
        trend = next((s for s in scen if s["order"] == "market"), None)
        if trend is not None:
            side = 1 if trend["side"] == "buy" else -1
            entry = ask if side > 0 else bid
            # the scenario's distances, moved from its reference price to the real fill
            stop = round(entry + trend["stop"] - trend["entry"], self.digits)
            target = round(entry + trend["target"] - trend["entry"], self.digits)
            self.open.append(self._tagged(Position(CANDLE, side, entry, now, now + trend["hold_s"], self.cycles, session,
                                                   trend["why"], stop, target)))
            self._log({"type": "candle", "cycle": self.cycles, "t": now, "kind": "trend", "side": trend["side"], "entry": entry})
            return side
        for s in scen:
            if s["order"] == "limit" and s["valid_until"] > now:
                self.pending.append(Pending(CANDLE, 1 if s["side"] == "buy" else -1, s["entry"], s["stop"], s["target"],
                                            s["valid_until"], s["hold_s"], self.cycles, s["why"], s["kind"]))
        if self.pending:
            self._log({"type": "candle", "cycle": self.cycles, "t": now, "kind": "limits",
                       "orders": [{"side": "buy" if p.side > 0 else "sell", "entry": p.entry} for p in self.pending]})
        return 0

    def _fill_pending(self, bid: float, ask: float, now: float) -> None:
        """Fill the limit orders the price reached, drop the expired ones; a fill cancels its rule's other orders."""
        if not self.pending:
            return
        keep, filled = [], set()
        for p in self.pending:
            if p.rule in filled:
                continue
            if now > p.expires:
                self._log({"type": "candle", "t": now, "kind": "expired", "side": "buy" if p.side > 0 else "sell", "entry": p.entry})
                continue
            if (ask <= p.entry if p.side > 0 else bid >= p.entry) and not any(o.rule == p.rule for o in self.open):
                entry = ask if p.side > 0 else bid
                self.open.append(self._tagged(Position(p.rule, p.side, entry, now, now + p.hold_s, p.cycle, sessions.key(now),
                                                       p.why, p.stop, p.target)))
                filled.add(p.rule)
                self._log({"type": "candle", "t": now, "kind": "filled", "scenario": p.kind,
                           "side": "buy" if p.side > 0 else "sell", "level": p.entry, "entry": entry})
                continue
            keep.append(p)
        self.pending = [p for p in keep if p.rule not in filled]

    def step_close(self, bid: float, ask: float, now: float) -> None:
        """Close the positions whose time is up or whose stop or target was hit, at the bid (a buy) or the ask (a sell)."""
        due = [(p, why) for p in self.open if (why := self._exit_reason(p, bid, ask, now))]
        for position, why in due:
            exit_price = bid if position.side > 0 else ask
            points = (exit_price - position.entry) * position.side
            self.stats[position.rule].add(points)
            self.by_session.setdefault((position.rule, position.session), Stats()).add(points)
            for name, value in position.tags.items():
                self.by_tag.setdefault((position.rule, name, value), Stats()).add(points)
            trade: dict[str, Any] = {
                "rule": position.rule, "side": "buy" if position.side > 0 else "sell", "cycle": position.cycle,
                "entry": position.entry, "exit": exit_price, "opened": position.opened, "closed": now,
                "points": points, "win": points > 0, "session": position.session, "exit_reason": why,
            }
            if position.why:
                trade["why"] = position.why
            if position.risk:
                trade["r"] = round(points / position.risk, 3)
            if position.tags:
                trade["tags"] = position.tags
            if position.trail is not None:
                trade["trail"] = position.trail.to_dict()
            self.trades.append(trade)
            self._log({"type": "close", **trade})
        if due:
            gone = {id(p) for p, _ in due}
            self.open = [p for p in self.open if id(p) not in gone]
            del self.trades[:-KEEP_TRADES]
            self._check_found()

    @staticmethod
    def _exit_reason(p: Position, bid: float, ask: float, now: float) -> str:
        price = bid if p.side > 0 else ask
        if p.trail is not None:
            if p.trail.update(price) == "exit":
                return p.trail.exit_label
            p.stop = p.trail.stop
            return "waktu" if p.closes <= now else ""
        if p.stop is not None and (price - p.stop) * p.side <= 0:
            return "stop"
        if p.target is not None and (price - p.target) * p.side >= 0:
            return "target"
        return "waktu" if p.closes <= now else ""

    def _check_found(self) -> None:
        if self.found:
            return
        best = None
        for name, stats in self.stats.items():
            if name == COIN or stats.trades < self.min_trades:
                continue
            rate = stats.wins / stats.trades
            if rate >= self.target and (best is None or rate > best[1]):
                best = (name, rate)
        if best:
            report = self.stats[best[0]].report(best[0])
            self.found = {**report, "at": self.clock(), "cycle": self.cycles}

    # ── the run ─────────────────────────────────────────────────────────────
    def run(self) -> None:
        self.state = "running"
        self.started_at = self.clock()
        try:
            spec = self.backend.symbol_spec(self.symbol) if hasattr(self.backend, "symbol_spec") else {}
            self.digits = int(spec.get("digits", 5))
        except Exception:  # noqa: BLE001 - digits only format the numbers
            pass
        end = self.started_at + self.max_minutes * 60
        while not self.stop_event.is_set():
            if self.clock() >= end:
                self.ended = "waktu habis"
                break
            try:
                with self._lock:
                    self.step()
                self.errors = 0
            except Exception as exc:  # noqa: BLE001 - a feed hiccup must not end the run at once
                self.errors += 1
                self.last_error = str(exc)
                if self.errors >= MAX_ERRORS:
                    self.ended = f"berhenti setelah {MAX_ERRORS} error berturut-turut: {exc}"
                    break
            if self.found and self.stop_on_find and not self.open:
                self.ended = "target tercapai"
                break
            self.sleep(POLL_S)
        else:
            self.ended = self.ended or "dihentikan"
        self._settle()
        self.state = "found" if self.found else "stopped"
        self._llm_answer = None
        if self.llm is not None:
            self.llm.close()
        self._log({"type": "end", "t": self.clock(), "ended": self.ended, "found": self.found,
                   "llm": self.llm.report() if self.llm is not None else None})

    def _brain_report(self) -> dict[str, Any]:
        view = self.brain_view
        status = self.brain.status()
        out = {"state": status.get("state"), "progress": status.get("progress"), "error": status.get("error"),
               "ready": status.get("ready"), "rows": status.get("rows"), "verdict": (status.get("exam") or {}).get("verdict")}
        if view.get("ok"):
            out.update(best=view["best"], take=view["take"], similar=view.get("similar", {}).get("plans"))
        else:
            out["reason"] = view.get("reason", "")
        return out

    def _settle(self) -> None:
        """Close what is still open at the last price, so no position is left hanging on the chart."""
        if not self.open:
            return
        try:
            tick = self.backend.tick(self.symbol)
        except Exception:  # noqa: BLE001 - without a price they are dropped, not counted
            self.open = []
            return
        with self._lock:
            for position in self.open:
                position.closes = self.clock()
            self.step_close(float(tick["bid"]), float(tick["ask"]), self.clock())

    # ── report ──────────────────────────────────────────────────────────────
    def report(self) -> dict[str, Any]:
        with self._lock:
            ranking = sorted((self.stats[n].report(n) for n in self.candidates),
                             key=lambda r: (r["name"] != COIN, r["win_rate"] or 0, r["trades"]), reverse=True)
            tried = sum(1 for r in ranking if r["name"] != COIN and r["trades"])
            for row in ranking:          # the best of several rules is judged as the best of several
                row["p_adjusted"] = riskmath.sidak(row.get("p_value"), tried) if row["name"] != COIN else row.get("p_value")
            leader = next((r for r in ranking if r["name"] != COIN and r["trades"]), None)
            by_profit = max((r for r in ranking if r["name"] != COIN and r["trades"] >= 2 and r.get("t_stat") is not None),
                            key=lambda r: r["t_stat"], default=None)
            significance = ""
            if by_profit is not None:
                p_adj = by_profit["p_adjusted"]
                significance = (f"Paling untung per trade: {by_profit['label']} (t = {by_profit['t_stat']:+.2f} dari "
                                f"{by_profit['trades']} trade). Peluang hasil sebagus ini muncul karena kebetulan pada salah satu "
                                f"dari {tried} aturan: {p_adj:.0%}. "
                                + ("Lolos uji (di bawah 5%)." if by_profit["t_stat"] > 0 and p_adj < 0.05 else
                                   "Belum bisa dibedakan dari keberuntungan." if by_profit["t_stat"] > 0 else
                                   "Tidak ada aturan yang untung rata-rata."))
            coin = self.stats[COIN].report(COIN)
            now = self.clock()
            warning = ""
            if self.found and self.found["wilson_low"] < 0.5:
                warning = (f"{self.found['label']} menang {self.found['win_rate']:.0%} dari {self.found['trades']} trade, "
                           f"tapi batas bawah 95%-nya {self.found['wilson_low']:.0%}: belum terbukti lebih baik dari "
                           "lempar koin. Dengan 10 aturan dicoba sekaligus, satu memang bisa tembus target karena untung.")
            elif self.found and self.found["wilson_low"] < self.target:
                warning = (f"Batas bawah 95% {self.found['label']} baru {self.found['wilson_low']:.0%}: belum terbukti "
                           f"mencapai {self.target:.0%}. Jalankan lagi dengan minimal trade lebih besar untuk memastikan.")
            elif self.found and self.found["breakeven_win_rate"] and self.found["win_rate"] < self.found["breakeven_win_rate"]:
                warning = "Menang sering, tetapi rata-rata kalahnya lebih besar dari menangnya: tetap rugi."
            return {
                "state": self.state, "ended": self.ended, "symbol": self.symbol, "digits": self.digits,
                "hold_s": self.hold_s, "target": self.target, "min_trades": self.min_trades,
                "timeframe": self.timeframe, "hold_bars": self.hold_bars if self.timeframe else None,
                "stop_atr": self.stop_atr if self.timeframe else None, "target_atr": self.target_atr if self.timeframe else None,
                "trail_atr": self.trail_atr if self.timeframe else None, "lock_atr": self.lock_atr if self.timeframe else None,
                "atr": round(self.atr, self.digits + 1) if self.timeframe else None,
                "max_minutes": self.max_minutes, "started_at": self.started_at,
                "elapsed_s": round((now - self.started_at) if self.started_at else 0.0, 1),
                "cycles": self.cycles, "errors": self.errors, "last_error": self.last_error,
                "ranking": ranking, "leader": leader, "coin": coin, "found": self.found, "warning": warning,
                "significance": significance, "rules_tried": tried,
                "open": [{"rule": p.rule, "side": "buy" if p.side > 0 else "sell", "entry": p.entry, "opened": p.opened,
                          "closes": p.closes, "left_s": max(0.0, round(p.closes - now, 1)), "stop": p.stop,
                          "target_level": p.target, "tags": p.tags, **({"trail": p.trail.to_dict()} if p.trail else {})}
                         for p in self.open],
                "trades": self.trades[-200:],
                "total_trades": sum(s.trades for s in self.stats.values()),
                "median_points": statistics.median([t["points"] for t in self.trades]) if self.trades else 0.0,
                "session": sessions.describe(now),
                "by_session": [{"rule": rule, "session": key, "label": sessions.label_of(key), **{
                    k: v for k, v in st.report(rule).items() if k in ("trades", "wins", "win_rate", "net_points")}}
                    for (rule, key), st in sorted(self.by_session.items())],
                "by_condition": [{"rule": rule, "name": name, "value": value, **{
                    k: v for k, v in st.report(rule).items() if k in ("trades", "wins", "win_rate", "wilson_low", "net_points")}}
                    for (rule, name, value), st in sorted(self.by_tag.items())],
                "picture": self._picture_brief(),
                "math": self._math_view() if self.math else None,
                "llm": ({**self.llm.report(), "pending": self._llm_pending, "late_skips": self.llm_skips}
                        if self.llm is not None else None),
                "brain": self._brain_report() if self.brain is not None else None,
                "analyst_last": self.analyst_last,
                "pending": [{"rule": p.rule, "kind": p.kind, "side": "buy" if p.side > 0 else "sell", "entry": p.entry,
                             "stop": p.stop, "target_level": p.target, "expires": p.expires, "why": p.why} for p in self.pending],
                "candle": self.candle_last or None,
                "news": {k: v for k, v in self.news_view.items() if k != "brief"},
                "condition": self.condition,
                "cycle_log": self.cycle_log[-30:],
                "file": str(self.path) if self.path else "",
            }
