"""Paper simulation: virtual positions on the live price until a rule wins often enough. Never an order."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from addons.trading.quant import papersim
from addons.trading.quant.papersim import COIN, PaperSim, wilson_low


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class Feed:
    """A price that climbs one point a second, with a 0.2 spread. Records every call to prove no order."""

    def __init__(self, clock: Clock, slope: float = 1.0) -> None:
        self.clock, self.slope, self.calls = clock, slope, []

    def tick(self, symbol: str) -> dict[str, Any]:
        self.calls.append("tick")
        mid = 100 + (self.clock.t - 1_800_000_000) * self.slope
        return {"bid": mid - 0.1, "ask": mid + 0.1, "time": self.clock.t}

    def symbol_spec(self, symbol: str) -> dict[str, Any]:
        return {"digits": 2}

    def __getattr__(self, name: str) -> Any:   # send, close, modify ... must never be reached
        raise AssertionError(f"the simulation called backend.{name}")


def rising_bars(symbol: str) -> list[dict[str, float]]:
    return [{"t": i * 60.0, "o": 100 + i, "h": 101 + i, "l": 99.5 + i, "c": 100.8 + i, "v": 10.0} for i in range(200)]


def make(slope: float = 1.0, **kw: Any) -> tuple[PaperSim, Clock, Feed]:
    clock = Clock()
    feed = Feed(clock, slope)
    sim = PaperSim(feed, "XAUUSD", bars=rising_bars, clock=clock, sleep=clock.sleep, seed=1, **kw)
    return sim, clock, feed


def test_positions_open_at_ask_bid_and_close_after_the_hold():
    sim, clock, _ = make(hold_s=30)
    sim.step()
    assert sim.open and all(p.closes == clock.t + 30 for p in sim.open)
    buy = next(p for p in sim.open if p.side > 0)
    assert buy.entry == pytest.approx(100.1)                     # bought at the ask
    clock.t += 29
    sim.step()
    assert not sim.trades, "nothing closes before its time"
    clock.t += 1
    sim.step()
    closed = next(t for t in sim.trades if t["rule"] == buy.rule)
    assert closed["exit"] == pytest.approx(129.9)                 # sold at the bid, the spread paid
    assert closed["points"] == pytest.approx(29.8) and closed["win"]


def test_the_search_stops_when_a_rule_reaches_the_target():
    sim, _, feed = make(hold_s=10, target=0.6, min_trades=5, max_minutes=30)
    sim.run()
    assert sim.state == "found" and sim.ended == "target tercapai"
    report = sim.report()
    assert report["found"]["win_rate"] >= 0.6 and report["found"]["trades"] >= 5
    assert report["found"]["name"] != COIN, "the coin flip is a yardstick, never the answer"
    assert not report["open"], "open positions are closed before the run ends"
    assert set(feed.calls) == {"tick"}


def test_a_flat_market_ends_at_the_time_limit_without_a_find():
    sim, _, _ = make(slope=0.0, hold_s=10, max_minutes=2)
    sim.run()
    assert sim.state == "stopped" and sim.ended == "waktu habis" and sim.found is None
    assert all(r["win_rate"] in (None, 0.0) for r in sim.report()["ranking"]), "the spread makes a flat hold a loss"


def test_a_lucky_find_is_called_lucky():
    sim, _, _ = make(hold_s=10, target=0.6, min_trades=5)
    sim.run()
    report = sim.report()
    # Five out of five is 100%, but the 95% lower bound of five trades is far under a coin flip.
    assert report["found"]["wilson_low"] < 0.6
    assert "belum terbukti mencapai 60%" in report["warning"]


def test_wilson_lower_bound():
    assert wilson_low(0, 0) == 0
    assert wilson_low(6, 10) == pytest.approx(0.313, abs=0.01)
    assert wilson_low(600, 1000) == pytest.approx(0.569, abs=0.01)


def test_feed_errors_end_the_run_after_a_streak():
    sim, clock, feed = make(hold_s=10, max_minutes=5)
    feed.tick = lambda symbol: (_ for _ in ()).throw(RuntimeError("feed down"))  # type: ignore[method-assign]
    sim.run()
    assert sim.state == "stopped" and "error berturut-turut" in sim.ended


@pytest.mark.parametrize("kw", [{"hold_s": 1}, {"target": 0.5}, {"target": 1.0}, {"max_minutes": 0}])
def test_bad_settings_are_refused(kw):
    with pytest.raises(ValueError):
        make(**kw)


def test_the_sim_backend_moves_inside_a_minute(monkeypatch):
    import time as time_module

    from addons.trading.mt5_adapter import SimBackend

    sim = SimBackend()
    now = [1_800_000_000.0]
    monkeypatch.setattr(time_module, "time", lambda: now[0])
    prices = set()
    for _ in range(12):
        prices.add(sim.tick("EURUSD")["bid"])
        now[0] += 5
    assert len(prices) > 3, "a 30-second hold on the simulator must see the price move"


def test_api_runs_a_simulation_without_orders(monkeypatch):
    from addons.trading.web import mt5_api, quant_api
    from web.server import app

    clock = Clock()
    feed = Feed(clock)
    monkeypatch.setattr(quant_api, "_view_backend", lambda: feed)
    started = []

    class Thread:
        def __init__(self, target: Any, **kw: Any) -> None:
            started.append(target)

        def start(self) -> None:
            pass

    monkeypatch.setattr(quant_api.threading, "Thread", Thread)
    monkeypatch.setattr(quant_api, "_sim", None)
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.get("/api/quant/sim/status").json() == {"state": "none"}
    r = client.post("/api/quant/sim/start", json={"symbol": "XAUUSD", "hold_s": 30, "target": 0.6})
    assert r.status_code == 200 and r.json()["hold_s"] == 30 and started
    assert client.post("/api/quant/sim/start", json={"symbol": "XAUUSD", "target": 0.4}).status_code == 422
    stopped = client.post("/api/quant/sim/stop").json()
    assert stopped["symbol"] == "XAUUSD"
    assert mt5_api is not None


def test_remote_visitors_cannot_start_a_simulation_without_the_token(monkeypatch):
    from web.server import app

    monkeypatch.setenv("MT5_TRADE_TOKEN", "rahasia")
    remote = TestClient(app, client=("203.0.113.5", 50000))
    r = remote.post("/api/quant/sim/start", json={"symbol": "XAUUSD"})
    assert r.status_code in (401, 403)
    assert papersim.HOLD_S == 30


def test_positions_still_open_at_the_end_are_closed_at_the_last_price():
    sim, clock, _ = make(slope=0.0, hold_s=600, max_minutes=1)
    sim.run()
    assert sim.ended == "waktu habis" and not sim.open
    assert sim.trades and all(t["closed"] == clock.t for t in sim.trades)
    assert sim.report()["open"] == []


# ── the intraday style: M15/H1/H4 bars, a stop and a target in ATR ─────────────
class Mt4Like:
    """Like the MT4 bridge: M5 bars only, a higher timeframe is refused. The price is set by the test."""

    def __init__(self, clock: Clock) -> None:
        self.clock, self.mid, self.asked = clock, 100.0, []

    def tick(self, symbol: str) -> dict[str, Any]:
        return {"bid": self.mid - 0.05, "ask": self.mid + 0.05}

    def symbol_spec(self, symbol: str) -> dict[str, Any]:
        return {"digits": 2}

    def rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        self.asked.append(timeframe)
        if timeframe != "M5":
            raise RuntimeError("The MT4 bridge streams M1 and M5 bars.")
        end = int(self.clock.t) // 300 * 300
        return [{"t": end - i * 300, "o": 100.0 + (i % 7) * 0.3, "h": 101.0 + (i % 7) * 0.3, "l": 99.0 + (i % 7) * 0.3,
                 "c": 100.0 + ((i + 3) % 7) * 0.3, "v": 5} for i in range(count, 0, -1)]


def test_higher_bars_are_built_from_m5_and_the_forming_one_is_left_out():
    m5 = [{"t": 3600 + i * 300, "o": 1.0 + i, "h": 2.0 + i, "l": 0.5 + i, "c": 1.5 + i, "v": 1} for i in range(15)]
    h1 = papersim.resample(m5, 3600, now=3600 + 15 * 300)
    assert len(h1) == 1, "the second hour has only 3 of its 12 M5 bars and is still forming"
    assert h1[0] == {"t": 3600, "o": 1.0, "h": 13.0, "l": 0.5, "c": 12.5, "v": 12.0}
    bars = [{"t": i, "o": 10, "h": 11, "l": 9, "c": 10} for i in range(20)]
    assert papersim.atr(bars) == pytest.approx(2.0)


def test_intraday_decides_once_per_bar_and_closes_at_the_stop():
    clock = Clock()
    clock.t += 1200                                # 20 minutes into the hour
    feed = Mt4Like(clock)
    sim = PaperSim(feed, "XAUUSD", timeframe="H1", hold_bars=4, stop_atr=1.0, target_atr=2.0, max_minutes=24 * 60,
                   clock=clock, sleep=clock.sleep, seed=1)
    assert sim.hold_s == 4 * 3600
    sim.step()
    assert "M5" in feed.asked and sim.cycles == 1 and sim.atr > 0
    coin = next(p for p in sim.open if p.rule == COIN)
    assert coin.stop == pytest.approx(coin.entry - coin.side * sim.atr, abs=0.01)
    assert coin.target == pytest.approx(coin.entry + coin.side * 2 * sim.atr, abs=0.01)
    clock.t += 600
    sim.step()
    assert sim.cycles == 1, "no new cycle inside the same hour"
    feed.mid -= coin.side * 1.2 * sim.atr          # against the coin by more than its stop
    sim.step()
    closed = next(t for t in sim.trades if t["rule"] == COIN)
    assert closed["exit_reason"] == "stop" and not closed["win"]
    held = {p.rule for p in sim.open}
    clock.t = (clock.t // 3600 + 1) * 3600 + papersim.BAR_GRACE_S
    sim.step()
    assert sim.cycles == 2
    assert all(sum(p.rule == rule for p in sim.open) == 1 for rule in held), "a rule still holding sits the bar out"
    report = sim.report()
    assert report["timeframe"] == "H1" and report["hold_bars"] == 4 and report["atr"] > 0


@pytest.mark.parametrize("kw", [{"timeframe": "D7"}, {"timeframe": "H1", "hold_bars": 0},
                                {"timeframe": "H1", "stop_atr": -1}, {"max_minutes": 600}])
def test_bad_intraday_settings_are_refused(kw):
    with pytest.raises(ValueError):
        PaperSim(Mt4Like(Clock()), "XAUUSD", **({"max_minutes": 60} | kw))


def test_a_scalping_run_cannot_take_a_week_but_an_intraday_one_can():
    PaperSim(Mt4Like(Clock()), "XAUUSD", timeframe="H4", max_minutes=papersim.MAX_MINUTES_DAY)
    with pytest.raises(ValueError):
        PaperSim(Mt4Like(Clock()), "XAUUSD", max_minutes=papersim.MAX_MINUTES + 1)


def test_the_api_starts_the_intraday_style_and_refuses_settings_it_does_not_know(monkeypatch):
    pytest.importorskip("torch", reason="the trading add-on's model needs torch")
    from addons.trading.web import quant_api
    from web.server import app

    clock = Clock()
    monkeypatch.setattr(quant_api, "_view_backend", lambda: Mt4Like(clock))

    class Thread:
        def __init__(self, target: Any, **kw: Any) -> None:
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr(quant_api.threading, "Thread", Thread)
    monkeypatch.setattr(quant_api, "_sim", None)
    client = TestClient(app, client=("127.0.0.1", 50000))
    r = client.post("/api/quant/sim/start", json={"symbol": "XAUUSD", "timeframe": "H1", "hold_bars": 3, "llm": False,
                                                  "max_minutes": 48 * 60})
    assert r.status_code == 200 and r.json()["timeframe"] == "H1" and r.json()["hold_s"] == 3 * 3600
    client.post("/api/quant/sim/stop")
    monkeypatch.setattr(quant_api, "_sim", None)
    assert client.post("/api/quant/sim/start", json={"symbol": "XAUUSD", "max_minutes": 600, "llm": False}).status_code == 422
    unknown = client.post("/api/quant/sim/start", json={"symbol": "XAUUSD", "style": "harian", "llm": False})
    assert unknown.status_code == 422, "a setting the server does not know must not be dropped silently"


def test_the_server_says_when_its_code_changed_on_disk(monkeypatch):
    from addons.trading.web import quant_api
    from web.server import app

    client = TestClient(app, client=("127.0.0.1", 50000))
    body = client.get("/api/quant/server").json()
    assert "sim_timeframe" in body["features"] and isinstance(body["stale"], bool)
    path = "addons/trading/quant/papersim.py"
    monkeypatch.setitem(quant_api._loaded, path, quant_api._loaded.get(path, 0.0) - 60)   # as if edited after loading
    body = client.get("/api/quant/server").json()
    assert body["stale"] and path in body["changed"]
