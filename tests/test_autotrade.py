"""The trailing exit, in the paper simulation, in the analyst's journal, and in the live auto trade. No network."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from trading.quant import analyst, autotrade, llm_trader, papersim, trail
from trading.quant.autotrade import AutoTrader
from trading.quant.memory import PLANS
from trading.quant.trail import TrailState


# ── the math ────────────────────────────────────────────────────────────────
def test_the_target_locks_the_stop_and_the_stop_then_follows_the_best_price():
    s = TrailState(1, stop=98.0, target=102.0, atr=1.0, trail_atr=1.0, lock_atr=0.3)
    assert s.update(100.0) == "" and s.stop == 98.0
    assert s.update(101.9) == "" and not s.locked, "below the target nothing moves"
    assert s.update(102.0) == "locked" and s.locked and s.stop == pytest.approx(101.7)
    assert s.update(102.5) == "" and s.stop == pytest.approx(101.7), "the trail is still behind the lock"
    assert s.update(104.0) == "trailed" and s.stop == pytest.approx(103.0)
    assert s.update(103.5) == "" and s.stop == pytest.approx(103.0), "never widened"
    assert s.update(103.0) == "exit" and s.exit_label == "trail" and s.moves == 2


def test_a_sell_trails_the_other_way_and_a_plain_stop_is_still_a_stop():
    s = TrailState(-1, stop=102.0, target=98.0, atr=1.0, trail_atr=0.5, lock_atr=0.0)
    assert s.update(98.0) == "locked" and s.stop == pytest.approx(98.0)
    assert s.update(96.0) == "trailed" and s.stop == pytest.approx(96.5)
    assert s.update(96.5) == "exit" and s.exit_label == "trail"
    plain = TrailState(1, stop=98.0, target=102.0, atr=1.0)
    assert plain.update(97.9) == "exit" and plain.exit_label == "stop"
    assert trail.clamp(99, None) == (5.0, 0.3) and trail.clamp(None, -1) == (1.0, 0.0)


def bars(path: list[tuple[float, float, float, float]], start: float = 0.0) -> list[dict[str, float]]:
    return [{"t": start + i * 300, "o": o, "h": h, "l": lo, "c": c, "v": 1.0} for i, (o, h, lo, c) in enumerate(path)]


def test_follow_replays_the_trail_over_bars_and_checks_the_stop_first():
    path = [(100, 100.5, 99.5, 100), (100, 102.2, 99.9, 102), (102, 104.5, 101.9, 104), (104, 104.2, 102.9, 103)]
    end = trail.follow(1, 100.0, 98.0, 102.0, 1.0, bars(path, 300), start=300, until=300 * 10, bar_seconds=300, digits=2)
    assert end and end["status"] == "trail" and end["exit"] == pytest.approx(103.5)
    assert end["result_atr"] == pytest.approx(3.5) and end["trail"]["locked"]
    wild = [(100, 100.5, 99.5, 100), (100, 103, 97, 101)]
    end = trail.follow(1, 100.0, 98.0, 102.0, 1.0, bars(wild, 300), start=300, until=3000, bar_seconds=300, digits=2)
    assert end and end["status"] == "stop" and end["result_atr"] == pytest.approx(-2.0), "a bar through both counts the stop"
    assert trail.follow(1, 100.0, 98.0, 102.0, 1.0, bars([(100, 100.5, 99.5, 100)], 300), start=300, until=3000, bar_seconds=300) is None


# ── the simulation ──────────────────────────────────────────────────────────
class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0 + 15         # an hour bar closed just now (the boundary plus BAR_GRACE_S)

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class Feed:
    def __init__(self, clock: Clock) -> None:
        self.clock, self.mid = clock, 2000.0

    def tick(self, symbol: str) -> dict[str, Any]:
        return {"bid": self.mid - 0.1, "ask": self.mid + 0.1, "time": self.clock.t}

    def symbol_spec(self, symbol: str) -> dict[str, Any]:
        return {"digits": 2}

    def rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        t0 = self.clock.t - 300 * 2000
        return [{"t": t0 + i * 300, "o": 2000.0, "h": 2001.0, "l": 1999.0, "c": 2000.0, "v": 5.0} for i in range(2000)]

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the simulation called backend.{name}")


class FakeBrain:
    folder = None
    ready = True

    def needs_training(self, rows: int | None = None) -> bool:
        return False

    def __init__(self, side: str = "buy") -> None:
        best = {"plan": PLANS.index((1 if side == "buy" else -1, 1.0, 2.0)), "name": f"{side} SL1/TP2", "side": side,
                "stop_atr": 1.0, "target_atr": 2.0, "p_target": 0.5, "p_stop": 0.3, "ev_atr": 0.5, "edge_atr": 0.4}
        self.view = {"ok": True, "take": True, "best": best, "best_buy": best, "best_sell": best, "plans": [best], "atr": 1.0,
                     "hold_bars": 4, "exam": {"passed": True}, "timeframe": "H1"}

    def analyse(self, backend: Any, **kw: Any) -> dict[str, Any]:
        return dict(self.view, atr=1.0)

    def maybe_train(self, backend: Any) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {"state": "ready", "ready": True, "rows": 1000, "exam": {"verdict": "lulus"}}


def test_in_the_sim_the_brains_target_arms_the_trail_and_the_trade_closes_above_the_target():
    clock = Clock()
    feed = Feed(clock)
    sim = papersim.PaperSim(feed, "XAUUSD", timeframe="H1", max_minutes=24 * 60, clock=clock, sleep=clock.sleep, seed=1,
                            brain=FakeBrain(), trail_atr=1.0, lock_atr=0.3)
    sim.state = "running"
    sim.step()
    pos = next(p for p in sim.open if p.rule == papersim.NEURAL)
    assert pos.trail is not None and pos.target is None and pos.trail.target == pytest.approx(pos.entry + 2 * sim.atr, abs=0.01)
    assert next(o for o in sim.report()["open"] if o["rule"] == papersim.NEURAL)["trail"]["locked"] is False
    feed.mid += 2.5 * sim.atr                        # past the target: no close, the stop locks
    sim.step()
    assert pos in sim.open and pos.trail.locked and pos.stop == pytest.approx(pos.trail.target - 0.3 * sim.atr, abs=0.01)
    feed.mid += 1.5 * sim.atr                        # further: the stop trails one ATR behind the best price
    sim.step()
    assert pos.stop == pytest.approx(feed.mid - 0.1 - 1.0 * sim.atr, abs=0.01)
    feed.mid -= 1.2 * sim.atr                        # back to the trail: closed in profit
    sim.step()
    closed = next(t for t in sim.trades if t["rule"] == papersim.NEURAL)
    assert closed["exit_reason"] == "trail" and closed["win"] and closed["trail"]["moves"] >= 2
    assert closed["points"] > 2.5 * sim.atr, "more than the fixed target would have paid"


def test_the_llm_may_set_its_own_trail_and_the_plan_carries_it():
    answer = llm_trader.parse_decision('{"side": "buy", "stop_atr": 1, "target_atr": 2, "trail_atr": 0.7, "confidence": 0.6}')
    assert answer["trail_atr"] == 0.7
    view = FakeBrain().view
    plan = analyst.make_plan(answer, view, symbol="XAU", timeframe="H1", bid=100.0, ask=100.2, digits=2, now=1_800_000_000.0,
                             source="auto", trail_atr=1.0, lock_atr=0.3)
    assert plan["exit_style"] == "trail" and plan["trail_atr"] == 0.7 and plan["lock_atr"] == 0.3
    fixed = analyst.make_plan({"side": "buy"}, view, symbol="XAU", timeframe="H1", bid=100.0, ask=100.2, digits=2, now=1.0, source="x")
    assert "exit_style" not in fixed


def test_a_trailing_plan_in_the_journal_ends_by_the_trail_and_counts_as_a_target(tmp_path):
    now = 1_800_000_000.0
    view = FakeBrain().view | {"atr": 2.0}
    plan = analyst.make_plan({"side": "buy", "why": "trend", "confidence": 0.7}, view, symbol="XAU", timeframe="H1", bid=100.0, ask=100.0,
                             digits=2, now=now, source="auto", trail_atr=1.0, lock_atr=0.5)
    analyst.record(plan, tmp_path)
    path = [(100, 100.5, 99.5, 100)] * 2 + [(100, 104.5, 99.8, 104)] + [(104, 108.0, 103.9, 107.5)] + [(107.5, 107.6, 105.0, 105.2)] * 2
    plans = analyst.update("XAU", bars(path, start=now + 300), tmp_path)
    assert plans[0]["status"] == "trail" and plans[0]["result_r"] > 2.0
    summary = analyst.summary(plans)
    assert summary["targets"] == 1 and summary["calibration"]["plans"] == 1
    assert analyst.lessons(plans)[0]["ended"] == "trail" and analyst.lessons(plans)[0]["trail_atr"] == 1.0


# ── the live auto trade ─────────────────────────────────────────────────────
class Broker:
    """A demo account that fills at once, keeps positions, and records every order, modify and close."""

    name = "fake"

    def __init__(self, clock: Clock, live: bool = False) -> None:
        self.clock, self.live, self.mid, self.equity = clock, live, 2000.0, 10_000.0
        self.sent: list[dict[str, Any]] = []
        self.modified: list[tuple[int, float | None, float | None]] = []
        self.closed: list[int] = []
        self.open: dict[int, dict[str, Any]] = {}
        self.next = 500

    def account(self) -> dict[str, Any]:
        return {"equity": self.equity, "balance": self.equity, "live": self.live}

    def tick(self, symbol: str) -> dict[str, Any]:
        return {"bid": self.mid - 0.1, "ask": self.mid + 0.1, "time": self.clock.t}

    def symbol_spec(self, symbol: str) -> dict[str, Any]:
        return {"digits": 2}

    def rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        t0 = self.clock.t - 300 * 2000
        return [{"t": t0 + i * 300, "o": 2000.0, "h": 2001.0, "l": 1999.0, "c": 2000.0, "v": 5.0} for i in range(2000)]

    def send(self, order: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(order)
        self.next += 1
        self.open[self.next] = {"ticket": self.next, "symbol": order["symbol"], "side": order["side"], "volume": order["volume"],
                                "open_price": self.mid, "price": self.mid, "sl": order["sl"], "tp": None, "profit": 0.0, "swap": 0.0,
                                "time": int(self.clock.t)}
        return {"ok": True, "order": self.next}

    def positions(self) -> list[dict[str, Any]]:
        for p in self.open.values():
            sign = 1 if p["side"] == "buy" else -1
            p["price"] = self.mid
            p["profit"] = round((self.mid - p["open_price"]) * sign * 100, 2)
        return [dict(p) for p in self.open.values()] + [{"ticket": 7, "symbol": "EURUSD", "side": "buy", "profit": -9.0}]

    def modify(self, ticket: int, sl: float | None, tp: float | None) -> dict[str, Any]:
        self.modified.append((ticket, sl, tp))
        self.open[ticket]["sl"] = sl
        return {"ok": True}

    def close(self, ticket: int, volume: float) -> dict[str, Any]:
        self.closed.append(ticket)
        self.open.pop(ticket, None)
        return {"ok": True}


def test_the_auto_trade_opens_on_the_bar_moves_the_stop_in_the_terminal_and_closes_at_the_trail(tmp_path, monkeypatch):
    from trading import journal

    monkeypatch.setattr(journal, "path", lambda: tmp_path / "journal.jsonl")
    clock = Clock()
    broker = Broker(clock)
    auto = AutoTrader(broker, "XAUUSD", "H1", brain=FakeBrain("buy"), mode="brain", lots=0.02, trail_atr=1.0, lock_atr=0.3,
                      clock=clock, sleep=clock.sleep, folder=tmp_path)
    auto.state = "running"
    auto.started_at = clock.t
    auto._check_day(10_000.0, clock.t)
    auto._cycle()
    assert len(broker.sent) == 1 and broker.sent[0]["tp"] is None and broker.sent[0]["volume"] == 0.02
    assert broker.sent[0]["sl"] == pytest.approx(2000.1 - 1.0, abs=0.01), "a real stop, no take profit"
    live = auto.live
    assert live is not None and live.target_level == pytest.approx(2002.1, abs=0.01)
    clock.t += 10
    auto._cycle()
    assert len(broker.sent) == 1, "one position at a time, one decision per bar"
    broker.mid = 2002.3                                 # the target level is reached: lock, not close
    auto._cycle()
    assert auto.live is live and broker.modified and broker.modified[-1][1] == pytest.approx(2002.1 - 0.3, abs=0.01)
    assert broker.modified[-1][2] is None and not broker.closed
    broker.mid = 2004.0                                 # trailing one ATR behind the best bid
    auto._cycle()
    assert broker.modified[-1][1] == pytest.approx(2003.9 - 1.0, abs=0.01)
    broker.mid = 2002.8                                 # back to the trail: closed by the run, in profit
    auto._cycle()
    assert broker.closed == [live.ticket] and auto.live is None
    report = auto.report()
    assert report["closed"][0]["why"] == "trail" and report["closed"][0]["profit"] > 0 and report["modifies"] == 2
    rows = [r for r in journal.read() if r.get("source") == "auto"]
    assert [r["event"] for r in rows] == ["order", "close"]


def test_a_close_made_by_the_broker_is_noticed_and_the_hold_ends_a_position(tmp_path, monkeypatch):
    from trading import journal

    monkeypatch.setattr(journal, "path", lambda: tmp_path / "journal.jsonl")
    clock = Clock()
    broker = Broker(clock)
    auto = AutoTrader(broker, "XAUUSD", "H1", brain=FakeBrain("sell"), clock=clock, sleep=clock.sleep, folder=tmp_path)
    auto.state, auto.started_at = "running", clock.t
    auto._cycle()
    ticket = auto.live.ticket
    broker.open.pop(ticket)                            # the terminal's stop closed it
    auto._cycle()
    assert auto.live is not None, "a fresh fill may still be missing from the terminal's list: not called closed yet"
    clock.t += autotrade.CONFIRM_GRACE_S
    auto._cycle()
    assert auto.live is None and auto.report()["closed"][0]["why"] == "broker"
    clock.t = (clock.t // 3600 + 1) * 3600 + autotrade.BAR_GRACE_S
    auto._cycle()
    assert auto.live is not None
    clock.t = auto.live.closes + 1
    auto._cycle()
    assert broker.closed == [ticket + 1] and auto.report()["closed"][-1]["why"] == "waktu"


def test_the_run_refuses_a_live_account_unless_allowed_and_stops_at_the_daily_loss(tmp_path, monkeypatch):
    from trading import journal

    monkeypatch.setattr(journal, "path", lambda: tmp_path / "journal.jsonl")
    clock = Clock()
    refused = AutoTrader(Broker(clock, live=True), "XAUUSD", "H1", brain=FakeBrain(), clock=clock, sleep=clock.sleep, folder=tmp_path)
    refused.run()
    assert refused.state == "finished" and "live" in refused.ended and not refused.decisions
    broker = Broker(clock)
    auto = AutoTrader(broker, "XAUUSD", "H1", brain=FakeBrain(), max_daily_loss_pct=1.0, max_hours=1, clock=clock, sleep=clock.sleep,
                      folder=tmp_path)
    original = auto._cycle

    def losing() -> None:
        original()
        broker.equity -= 60.0                           # every poll loses money

    auto._cycle = losing  # type: ignore[method-assign]
    auto.run()
    assert "rugi hari ini" in auto.ended and broker.closed, "the open position is closed when the run ends"
    assert auto.report()["state"] == "finished" and auto.report()["file"].endswith(".jsonl")


@pytest.mark.parametrize("kw", [{"mode": "llm"}, {"lots": 0}, {"lots": 5}, {"timeframe": "M5"}, {"max_hours": 0}, {"mode": "x"}])
def test_bad_settings_are_refused(kw, tmp_path):
    args = {"timeframe": "H1"} | kw
    with pytest.raises(ValueError):
        AutoTrader(Broker(Clock()), "XAUUSD", args.pop("timeframe"), brain=FakeBrain(), folder=tmp_path, **args)  # type: ignore[arg-type]


def test_the_agree_mode_needs_the_brain_and_the_llm_on_one_side(tmp_path, monkeypatch):
    from trading import journal
    from trading.quant import memory

    monkeypatch.setattr(journal, "path", lambda: tmp_path / "journal.jsonl")
    clock = Clock()
    broker = Broker(clock)
    monkeypatch.setattr(memory, "history_m5", lambda symbol, backend=None, folder=None: broker.rates(symbol, "M5", 2000))

    class Trader:
        model, cost_usd, calls = "fake/llm", 0.0, 0

        def __init__(self, side: str) -> None:
            self.side = side

        def decide(self, context: dict[str, Any], system: str | None = None) -> dict[str, Any]:
            self.calls += 1
            assert "exit" in context and "trailing" in context["exit"]
            return {"side": self.side, "confidence": 0.8, "why": "llm says", "stop_atr": 1.2, "target_atr": 2.5}

        def report(self) -> dict[str, Any]:
            return {"calls": self.calls}

    brain = FakeBrain("buy")
    brain.folder = tmp_path
    disagree = AutoTrader(broker, "XAUUSD", "H1", brain=brain, llm=Trader("sell"), mode="agree", clock=clock, sleep=clock.sleep, folder=tmp_path)
    disagree.state, disagree.started_at = "running", clock.t
    disagree._cycle()
    assert not broker.sent and "tidak sepakat" in disagree.last_decision["why"] and disagree.last_decision["side"] == "none"
    agree = AutoTrader(broker, "XAUUSD", "H1", brain=brain, llm=Trader("buy"), mode="agree", clock=clock, sleep=clock.sleep, folder=tmp_path)
    agree.state, agree.started_at = "running", clock.t
    agree._cycle()
    assert len(broker.sent) == 1 and broker.sent[0]["sl"] == pytest.approx(2000.1 - 1.2, abs=0.01), "the LLM's stop"
    assert analyst.load("XAUUSD", tmp_path)[-1]["source"] == "auto" and agree.report()["llm"]["calls"] == 1


def test_an_order_without_an_answer_is_found_and_managed_never_sent_twice(tmp_path, monkeypatch):
    """The EA took the order but its answer was lost: the position may be open, so the run looks for it."""
    from trading import journal

    monkeypatch.setattr(journal, "path", lambda: tmp_path / "journal.jsonl")
    clock = Clock()
    broker = Broker(clock)
    real_send = broker.send

    def lost_answer(order: dict[str, Any]) -> dict[str, Any]:
        real_send(order)                               # filled in the terminal...
        return {"ok": False, "unknown": True, "comment": "unknown: the EA took the command but did not report"}

    broker.send = lost_answer  # type: ignore[method-assign]
    auto = AutoTrader(broker, "XAUUSD", "H1", brain=FakeBrain("buy"), clock=clock, sleep=clock.sleep, folder=tmp_path)
    auto.state, auto.started_at = "running", clock.t
    auto._cycle()
    assert auto.live is None and auto.unconfirmed is not None
    for ticket in broker.open:
        broker.open[ticket]["comment"] = "auto H1 trail"
    clock.t = (clock.t // 3600 + 1) * 3600 + autotrade.BAR_GRACE_S   # a new bar wants to trade again
    auto._cycle()
    assert len(broker.sent) == 1, "never a second order while the first may be open"
    assert auto.live is not None and auto.live.ticket in broker.open and auto.unconfirmed is None
    assert any(r["type"] == "adopted" for r in auto.log)


def test_an_order_without_an_answer_and_no_position_is_given_up(tmp_path):
    clock = Clock()
    broker = Broker(clock)
    broker.send = lambda order: {"ok": False, "unknown": True, "comment": "unknown"}  # type: ignore[method-assign]
    auto = AutoTrader(broker, "XAUUSD", "H1", brain=FakeBrain("buy"), clock=clock, sleep=clock.sleep, folder=tmp_path)
    auto.state, auto.started_at = "running", clock.t
    auto._cycle()
    clock.t += autotrade.UNCONFIRMED_WAIT_S
    auto._cycle()
    assert auto.unconfirmed is None and any(r["type"] == "not_filled" for r in auto.log)


def test_a_close_that_fails_at_the_end_leaves_the_position_reported_open(tmp_path, monkeypatch):
    from trading import journal

    monkeypatch.setattr(journal, "path", lambda: tmp_path / "journal.jsonl")
    clock = Clock()
    broker = Broker(clock)
    auto = AutoTrader(broker, "XAUUSD", "H1", brain=FakeBrain("buy"), clock=clock, sleep=clock.sleep, folder=tmp_path)
    auto.state, auto.started_at = "running", clock.t
    auto._cycle()
    ticket = auto.live.ticket
    broker.close = lambda ticket, volume: {"ok": False, "comment": "The EA did not pick it up"}  # type: ignore[method-assign]
    auto.ended = "dihentikan"
    auto._settle()
    report = auto.report()
    assert report["position"] and report["position"]["ticket"] == ticket and "MASIH TERBUKA" in auto.ended
    assert not [r for r in journal.read() if r.get("event") == "close"], "the journal must not say it closed"


def test_a_run_started_in_the_middle_of_a_bar_waits_for_the_next_close(tmp_path, monkeypatch):
    from trading import journal

    monkeypatch.setattr(journal, "path", lambda: tmp_path / "journal.jsonl")
    clock = Clock()
    clock.t += 20 * 60                                 # 20 minutes into the hour
    broker = Broker(clock)
    auto = AutoTrader(broker, "XAUUSD", "H1", brain=FakeBrain("buy"), clock=clock, sleep=clock.sleep, folder=tmp_path)
    auto.state, auto.started_at = "running", clock.t
    auto._cycle()
    assert not broker.sent and auto.decisions == 0 and auto.log[-1]["type"] == "wait"
    clock.t += 30 * 60
    auto._cycle()
    assert not broker.sent, "still the same bar"
    clock.t = (clock.t // 3600 + 1) * 3600 + autotrade.BAR_GRACE_S
    auto._cycle()
    assert len(broker.sent) == 1 and auto.decisions == 1


def test_a_refused_stop_move_is_tried_again_later_without_waiting_for_the_trail(tmp_path, monkeypatch):
    from trading import journal

    monkeypatch.setattr(journal, "path", lambda: tmp_path / "journal.jsonl")
    clock = Clock()
    broker = Broker(clock)
    auto = AutoTrader(broker, "XAUUSD", "H1", brain=FakeBrain("buy"), trail_atr=1.0, lock_atr=0.3, clock=clock, sleep=clock.sleep,
                      folder=tmp_path)
    auto.state, auto.started_at = "running", clock.t
    auto._cycle()
    real_modify, refusals = broker.modify, []

    def refuse_twice(ticket: int, sl: float | None, tp: float | None) -> dict[str, Any]:
        if len(refusals) < 2:
            refusals.append(sl)
            return {"ok": False, "comment": "invalid stops"}
        return real_modify(ticket, sl, tp)

    broker.modify = refuse_twice  # type: ignore[method-assign]
    broker.mid = 2002.3                                # the target: the stop locks, the terminal refuses
    auto._cycle()
    assert len(refusals) == 1 and auto.live.modify_failed == 1
    auto._cycle()
    assert len(refusals) == 1, "no hammering: the next try waits"
    clock.t += autotrade.POLL_S * 2
    auto._cycle()                                      # tried again with no new trail move, refused again
    assert len(refusals) == 2 and auto.live.modify_failed == 2
    clock.t += autotrade.MODIFY_RETRY_MAX_S
    auto._cycle()
    assert broker.modified and broker.modified[-1][1] == pytest.approx(2002.1 - 0.3, abs=0.01)
    assert auto.live.stop == broker.modified[-1][1] and auto.live.modify_failed == 0


def test_the_env_limits_bind_the_auto_trade_too(tmp_path, monkeypatch):
    """MT5_MAX_VOLUME, MT5_ALLOW_LIVE and MT5_MAX_RISK_PCT hold for auto trade as for a manual order."""
    clock = Clock()
    monkeypatch.setenv("MT5_MAX_VOLUME", "0.10")
    with pytest.raises(ValueError, match="MT5_MAX_VOLUME"):
        AutoTrader(Broker(clock), "XAUUSD", "H1", brain=FakeBrain(), lots=0.5, folder=tmp_path)
    monkeypatch.setenv("MT5_ALLOW_LIVE", "0")
    live = AutoTrader(Broker(clock, live=True), "XAUUSD", "H1", brain=FakeBrain(), allow_live=True, clock=clock,
                      sleep=clock.sleep, folder=tmp_path)
    live.run()
    assert "MT5_ALLOW_LIVE" in live.ended, "the page's checkbox cannot loosen the owner's .env"

    monkeypatch.setenv("MT5_MAX_RISK_PCT", "1")
    broker = Broker(clock)
    broker.risk_per_lot = lambda symbol, side, entry, sl: abs(entry - sl) * 100_000  # type: ignore[attr-defined]
    auto = AutoTrader(broker, "XAUUSD", "H1", brain=FakeBrain("buy"), lots=0.05, clock=clock, sleep=clock.sleep, folder=tmp_path)
    auto.state, auto.started_at, auto.equity = "running", clock.t, 10_000.0
    auto._cycle()
    assert broker.sent == [] and auto.live is None
    assert "MT5_MAX_RISK_PCT" in next(r for r in auto.log if r["type"] == "refused")["comment"]


def test_the_api_starts_stops_and_reports_the_auto_trade(monkeypatch, tmp_path):
    from web import quant_api
    from web.server import app

    clock = Clock()
    broker = Broker(clock)
    monkeypatch.setattr(quant_api, "_backend", lambda: broker)
    monkeypatch.setattr(quant_api, "_view_backend", lambda: broker)
    monkeypatch.setattr(quant_api.brain_mod, "get_brain", lambda symbol, timeframe, folder=None: FakeBrain())

    class Thread:
        def __init__(self, target: Any, **kw: Any) -> None:
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr(quant_api.threading, "Thread", Thread)
    monkeypatch.setattr(quant_api, "_auto", None)
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.get("/api/quant/auto/status").json()["state"] == "idle"
    r = client.post("/api/quant/auto/start", json={"symbol": "XAUUSD", "timeframe": "H1", "mode": "brain", "lots": 0.05, "trail_atr": 1.5})
    assert r.status_code == 200 and r.json()["lots"] == 0.05 and r.json()["trail_atr"] == 1.5 and not broker.sent
    assert client.post("/api/quant/auto/stop").status_code == 200
    monkeypatch.setattr(quant_api, "_auto", None)
    assert client.post("/api/quant/auto/start", json={"symbol": "XAUUSD", "lots": 3}).status_code == 422
    assert client.post("/api/quant/auto/start", json={"symbol": "XAUUSD", "style": "x"}).status_code == 422
    broker.live = True
    assert client.post("/api/quant/auto/start", json={"symbol": "XAUUSD"}).status_code == 403
