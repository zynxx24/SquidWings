"""The trading brain: memory rows and outcomes, the neural ensemble, look-alikes, the analyst's plans, the API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from addons.trading import news as market_news
from addons.trading.quant import analyst, brain, memory, neural, papersim
from addons.trading.quant.memory import PLANS, SL, TIMEOUT, TP

os.environ.setdefault("TRADING_BRAIN_SIZE", "kecil")   # tests train the small networks; the machine's size is for real use

T0 = 1_780_000_000 // 86400 * 86400      # mid 2026: before the real clock, as recorded history is


def m5(path: list[tuple[float, float, float, float]], start: float = T0) -> list[dict[str, float]]:
    return [{"t": start + i * 300, "o": o, "h": h, "l": lo, "c": c, "v": 10.0} for i, (o, h, lo, c) in enumerate(path)]


def london_drift(days: int = 40, seed: int = 3, drift: float = 0.0005) -> list[dict[str, float]]:
    """A random walk that climbs every morning from 07:00 to 11:00 UTC: something real to find."""
    rng = np.random.default_rng(seed)
    price, out = 2000.0, []
    for i in range(days * 288):
        t = T0 + i * 300
        hour = (t % 86400) // 3600
        step = (drift * price if 7 <= hour < 11 else 0.0) + rng.normal(0, 0.0008 * price)
        o, c = price, price + step
        out.append({"t": float(t), "o": o, "h": max(o, c) + abs(rng.normal(0, 0.0003 * price)),
                    "l": min(o, c) - abs(rng.normal(0, 0.0003 * price)), "c": c, "v": 100.0})
        price = c
    return out


@pytest.fixture(scope="module")
def trained(tmp_path_factory) -> tuple[brain.Brain, list[dict[str, float]]]:
    base = london_drift()
    b = brain.Brain("SYNTH", "M15", folder=tmp_path_factory.mktemp("brain"))
    b.train(base=base, now=base[-1]["t"] + 300, spread=0.2, members=3)
    return b, base


# ── outcomes ────────────────────────────────────────────────────────────────
def test_the_target_or_the_stop_whichever_the_m5_bars_touch_first():
    seconds, hold = 900, 2
    flat = [(100, 100.5, 99.5, 100)] * 30
    tf_bars = memory.resample(m5(flat), seconds, now=T0 + 30 * 300)
    i = len(tf_bars) - 4                                       # a bar with two full bars after it
    start = int((tf_bars[i]["t"] + seconds - T0) // 300)
    path = list(flat)
    path[start + 1] = (100, 103.5, 99.9, 103)                 # up 3.5: a 1-ATR target of a buy is touched
    base = m5(path)
    cls, res, done = memory.outcomes(tf_bars, base, seconds, hold, spread=0.0)
    atr = memory.wilder_atr(*(np.array([b[k] for b in tf_bars]) for k in ("h", "l", "c")))[i]
    buy_1_1 = PLANS.index((1, 1.0, 1.0))
    assert done[i] and atr < 3.4
    assert cls[i, buy_1_1] == TP and res[i, buy_1_1] == pytest.approx(1.0)
    sell_1_1 = PLANS.index((-1, 1.0, 1.0))
    assert cls[i, sell_1_1] == SL and res[i, sell_1_1] == pytest.approx(-1.0)
    assert not done[-1], "the newest bar's future is not complete yet"


def test_a_bar_touching_both_counts_the_stop_and_the_spread_is_paid():
    seconds = 900
    flat = [(100, 100.5, 99.5, 100)] * 30
    tf_bars = memory.resample(m5(flat), seconds, now=T0 + 30 * 300)
    i = len(tf_bars) - 4
    start = int((tf_bars[i]["t"] + seconds - T0) // 300)
    path = list(flat)
    path[start] = (100, 110, 90, 100)                          # one wild M5 bar through both levels
    cls, _, _ = memory.outcomes(tf_bars, m5(path), seconds, 2, spread=0.0)
    assert all(cls[i, p] == SL for p in range(len(PLANS))), "never assume the target came first"
    calm = memory.outcomes(tf_bars, m5(flat), seconds, 2, spread=0.4)
    buy_timeout = calm[1][i, PLANS.index((1, 2.0, 3.0))]
    assert calm[0][i, PLANS.index((1, 2.0, 3.0))] == TIMEOUT and buy_timeout < 0, "a flat market costs the spread"


def test_the_memory_has_every_measurement_and_knows_when_the_news_archive_reaches(tmp_path):
    base = london_drift(days=10)
    events = [{"time": base[-1]["t"] - 3600 * 5, "currency": "USD", "impact": "high", "title": "CPI"}]
    archive = {"events": events, "coverage": [[base[-1]["t"] - 86400 * 2, base[-1]["t"] + 3600]]}
    ds = memory.build("SYNTH", "H1", base, now=base[-1]["t"] + 300, spread=0.2, archive=archive, currencies=["USD"])
    assert ds.x.shape == (ds.rows, len(ds.names)) and len(ds.names) >= 80
    assert not np.isnan(ds.x).any()
    known = ds.x[:, ds.names.index("news_known")]
    assert known[-1] == 1 and known[0] == 0
    assert {"s_london", "hour_sin", "c0_body", "ret_55", "m_hurst_x_move", "prev_hi_dist"} <= set(ds.names)


# ── the brain ───────────────────────────────────────────────────────────────
def test_the_brain_finds_a_real_pattern_and_passes_its_exam(trained):
    b, base = trained
    status = b.status()
    exam = status["exam"]
    assert status["ready"] and exam["passed"], exam["verdict"]
    assert exam["avg_r_atr"] > exam["static_avg_r_atr"] > 0
    assert exam["buys"] > exam["sells"], "the planted move is up"
    ds = b.dataset(base=base, now=base[-1]["t"] + 300, spread=0.2)
    choice, edge = neural.edges(b._wave, ds.x)
    hours = (ds.times % 86400 // 3600).astype(int)
    take = edge > b._wave.threshold
    morning, evening = take[(hours >= 6) & (hours < 10)].mean(), take[(hours >= 14) & (hours < 22)].mean()
    assert morning > 0.5 > 0.2 > evening, (morning, evening)
    assert all(PLANS[c][0] > 0 for c in choice[take & (hours >= 6) & (hours < 10)])


def test_the_brain_is_saved_reloaded_and_answers_with_look_alikes(trained):
    b, base = trained
    again = brain.Brain("SYNTH", "M15", folder=b.folder)
    assert again.ready and again.status()["rows"] == b.status()["rows"]
    view = again.analyse(base=base, now=base[-1]["t"] + 300, spread=0.2)
    assert view["ok"] and len(view["plans"]) == 6 and view["best"]["edge_atr"] <= view["best"]["ev_atr"]
    assert view["similar"]["count"] == brain.NEIGHBOURS and len(view["similar"]["plans"]) == 2
    assert view["conditions_now"]["session"]["rows"] > 0
    small = brain.compact(view)
    assert set(small) >= {"exam", "plans_ranked", "similar_past", "conditions_now", "brain_would_trade"}
    assert len(json.dumps(small)) < 6000, "the LLM gets the gist, not the memory"


def test_news_that_covers_too_little_of_the_memory_is_left_out_of_the_networks(trained):
    b, _ = trained
    assert "news_next_h" in b.status()["masked"], "no archive: news would make 'now' look unlike every row learned"
    names = ["news_known", "live_context", *brain.NEWS_COLUMNS[1:], *brain.LIVE_COLUMNS[1:]]
    x = np.zeros((10, len(names)))
    x[:, 0] = 1
    mask, share = brain.coverage_mask(names, x)
    assert share["news"] == 1 and not mask[0] and mask[1]


def test_too_little_memory_is_said_plainly(tmp_path):
    b = brain.Brain("TINY", "H1", folder=tmp_path)
    status = b.train(base=london_drift(days=3), spread=0.2)
    assert status["state"] == "error" and "butuh" in status["error"] and not status["ready"]
    assert not b.analyse(base=london_drift(days=3), spread=0.2)["ok"]


def test_the_calendar_archive_keeps_every_week(tmp_path):
    week1 = [{"time": 1000.0 * 86400, "currency": "USD", "impact": "high", "title": "NFP"}]
    week2 = [{"time": 1007.0 * 86400, "currency": "USD", "impact": "high", "title": "CPI"}]
    market_news.archive_calendar(week1, tmp_path)
    assert market_news.archive_calendar(week2, tmp_path) == 2
    archive = market_news.load_calendar_archive(tmp_path)
    assert [e["title"] for e in archive["events"]] == ["NFP", "CPI"] and len(archive["coverage"]) == 2


# ── the analyst ─────────────────────────────────────────────────────────────
def view_for(side: str = "buy") -> dict[str, Any]:
    best = {"plan": PLANS.index((1 if side == "buy" else -1, 1.5, 3.0)), "name": f"{side} SL1.5/TP3", "side": side,
            "stop_atr": 1.5, "target_atr": 3.0, "p_target": 0.5, "p_stop": 0.4, "ev_atr": 0.4, "edge_atr": 0.3}
    other = {**best, "side": "sell" if side == "buy" else "buy", "plan": PLANS.index((-1 if side == "buy" else 1, 1.0, 1.0)),
             "name": "other", "stop_atr": 1.0, "target_atr": 1.0}
    return {"ok": True, "atr": 2.0, "hold_bars": 4, "best": best, "plans": [best, other],
            "best_buy": best if side == "buy" else other, "best_sell": other if side == "buy" else best, "exam": {"passed": True}}


def test_a_plan_has_its_stop_and_target_on_the_right_sides():
    now = T0 + 10 * 3600
    buy = analyst.make_plan({"side": "buy", "why": "x"}, view_for("buy"), symbol="XAU", timeframe="H1", bid=100.0, ask=100.2,
                            digits=2, now=now, source="analyst")
    assert buy["entry"] == 100.2 and buy["stop"] == pytest.approx(97.2) and buy["target"] == pytest.approx(106.2)
    assert buy["rr"] == 2.0 and buy["status"] == "open" and buy["hold_until"] == now + 4 * 3600
    sell = analyst.make_plan({"side": "sell", "stop_atr": 1, "target_atr": 2}, view_for("buy"), symbol="XAU", timeframe="H1",
                             bid=100.0, ask=100.2, digits=2, now=now, source="sim")
    assert sell["entry"] == 100.0 and sell["stop"] == pytest.approx(102.0) and sell["target"] == pytest.approx(96.0)
    wait = analyst.make_plan({"side": "none", "why": "news soon"}, view_for("buy"), symbol="XAU", timeframe="H1",
                             bid=100.0, ask=100.2, digits=2, now=now, source="analyst")
    assert wait["status"] == "wait" and wait["shadow"]["side"] == "buy" and "entry" not in wait


def test_plans_and_waits_are_followed_to_their_end_and_become_lessons(tmp_path):
    now = T0 + 10 * 3600
    win = analyst.make_plan({"side": "buy", "why": "trend"}, view_for("buy"), symbol="XAU", timeframe="H1", bid=100.0,
                            ask=100.0, digits=2, now=now, source="analyst")
    wait = analyst.make_plan({"side": "none", "why": "unsure"}, view_for("buy"), symbol="XAU", timeframe="H1", bid=100.0,
                             ask=100.0, digits=2, now=now, source="analyst")
    analyst.record(win, tmp_path)
    analyst.record(wait, tmp_path)
    path = [(100, 100.5, 99.5, 100)] * 4 + [(100, 106.5, 99.8, 106)] + [(106, 106.2, 105.8, 106)] * 3
    plans = analyst.update("XAU", m5(path, start=now - now % 300), tmp_path)
    assert plans[0]["status"] == "target" and plans[0]["result_r"] == pytest.approx(2.0)
    assert plans[1]["status"] == "wait" and plans[1]["shadow"]["status"] == "target"
    total = analyst.summary(plans)
    assert total["plans"] == 1 and total["target_rate"] == 1 and total["waits_missed_winner"] == 1
    lessons = analyst.lessons(plans)
    assert lessons[0]["side"] == "none" and "target" in lessons[0]["brain_would_have"]
    assert lessons[1]["ended"] == "target"


class Backend:
    def __init__(self, base: list[dict[str, float]]) -> None:
        self.base = base

    def tick(self, symbol: str) -> dict[str, Any]:
        c = self.base[-1]["c"]
        return {"bid": c, "ask": c + 0.2}

    def symbol_spec(self, symbol: str) -> dict[str, Any]:
        return {"digits": 2, "contract_size": 100, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 50}

    def rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        if timeframe != "M5":
            raise RuntimeError("M5 only")
        return self.base[-count:]


class Trader:
    model = "fake/analyst"
    cost_usd = 0.001
    last_error = ""
    why_unavailable = ""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer, self.asked = answer, []

    def decide(self, context: dict[str, Any], system: str | None = None) -> dict[str, Any]:
        self.asked.append((context, system))
        return dict(self.answer)


def test_the_analyst_reads_the_brain_and_its_own_record(trained, tmp_path, monkeypatch):
    b, base = trained
    monkeypatch.setattr(memory, "history_m5", lambda symbol, backend=None, folder=None: base)
    trader = Trader({"side": "buy", "plan": None, "valid_bars": 2, "confidence": 0.7, "why": "brain and look-alikes agree"})
    out = analyst.run(Backend(base), "SYNTH", "M15", trader, folder=tmp_path, brain=b, now=base[-1]["t"] + 300)
    context, system = trader.asked[0]
    assert system == analyst.SYSTEM and "brain" in context and context["brain"]["ok"]
    assert {"last_candles", "my_record", "my_last_plans", "session"} <= set(context)
    plan = out["plan"]
    assert plan["side"] == "buy" and plan["stop"] < plan["entry"] < plan["target"] and plan["source"] == "analyst"
    assert analyst.load("SYNTH", tmp_path)[-1]["id"] == plan["id"]


# ── the brain in the live simulation ────────────────────────────────────────
class FakeBrain:
    def __init__(self, folder: Path) -> None:
        self.folder, self.trained, self.view = folder, 0, view_for("buy") | {"take": True, "timeframe": "H1"}

    def analyse(self, backend: Any, **kw: Any) -> dict[str, Any]:
        return self.view

    def maybe_train(self, backend: Any) -> bool:
        self.trained += 1
        return False

    def status(self) -> dict[str, Any]:
        return {"state": "ready", "ready": True, "rows": 1000, "exam": {"verdict": "lulus"}}


def test_the_brain_trades_in_the_sim_learns_each_bar_and_the_llm_writes_the_plan(tmp_path, monkeypatch):
    class Sync:
        def __init__(self, target: Any, args: tuple[Any, ...] = (), **kw: Any) -> None:
            self.target, self.args = target, args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(papersim.threading, "Thread", Sync)
    base = london_drift(days=8)
    clock = [base[-1]["t"] + 600]
    fake = FakeBrain(tmp_path)
    trader = Trader({"side": "sell", "stop_atr": 1, "target_atr": 2, "why": "fade"})
    trader.available = True        # type: ignore[attr-defined]
    trader.report = lambda: {"calls": 1}  # type: ignore[attr-defined]
    sim = papersim.PaperSim(Backend(base), "SYNTH", timeframe="H1", max_minutes=600, clock=lambda: clock[0], sleep=lambda s: None,
                            brain=fake, llm=trader, seed=1)
    sim.state = "running"
    sim.step()
    neural_pos = next(p for p in sim.open if p.rule == papersim.NEURAL)
    assert neural_pos.side == 1 and neural_pos.stop == pytest.approx(neural_pos.entry - 1.5 * sim.atr, abs=0.01)
    assert neural_pos.closes == pytest.approx(clock[0] + memory.HOLD_BARS["H1"] * 3600)
    assert fake.trained == 1 and memory.load_context("SYNTH", "H1", tmp_path), "each bar's spread is kept for training"
    context, system = trader.asked[0]
    assert system == analyst.SYSTEM and context["brain"]["ok"]
    clock[0] += 2
    sim.step()
    llm_pos = next(p for p in sim.open if p.rule == papersim.LLM)
    plan = analyst.load("SYNTH", tmp_path)[-1]
    assert plan["source"] == "sim" and plan["side"] == "sell" and llm_pos.stop == plan["stop"] and llm_pos.target == plan["target"]
    report = sim.report()
    assert report["brain"]["best"]["side"] == "buy" and report["analyst_last"]["id"] == plan["id"]
    assert any(r["name"] == papersim.NEURAL for r in report["ranking"])


def test_the_brain_needs_the_intraday_style(tmp_path):
    with pytest.raises(ValueError):
        papersim.PaperSim(Backend(london_drift(days=1)), "SYNTH", brain=FakeBrain(tmp_path))


# ── the API ─────────────────────────────────────────────────────────────────
def test_brain_and_analyst_api(trained, tmp_path, monkeypatch):
    from addons.trading.web import quant_api
    from web.server import app

    b, base = trained
    monkeypatch.setattr(quant_api, "_view_backend", lambda: Backend(base))
    monkeypatch.setattr(memory, "history_m5", lambda symbol, backend=None, folder=None: base)
    monkeypatch.setattr(brain, "get_brain", lambda symbol, timeframe, folder=None: b)
    monkeypatch.setattr(analyst, "_path", lambda symbol, folder=None: tmp_path / f"analyst-{symbol}.json")
    client = TestClient(app, client=("127.0.0.1", 50000))
    r = client.get("/api/quant/brain", params={"symbol": "SYNTH", "timeframe": "M15"}).json()
    assert r["status"]["ready"] and r["view"]["ok"] and r["status"]["exam"]["passed"]
    assert client.get("/api/quant/analyst", params={"symbol": "SYNTH"}).json()["latest"] is None
    trader = Trader({"side": "buy", "why": "agree"})
    trader.close = lambda: None   # type: ignore[attr-defined]
    trader.report = lambda: {"calls": 1}  # type: ignore[attr-defined]
    monkeypatch.setattr(quant_api, "_trader", lambda *a, **k: trader)
    ran = client.post("/api/quant/analyst/run", json={"symbol": "SYNTH", "timeframe": "M15"})
    assert ran.status_code == 200 and ran.json()["plan"]["side"] == "buy"
    listed = client.get("/api/quant/analyst", params={"symbol": "SYNTH"}).json()
    assert listed["latest"]["id"] == ran.json()["plan"]["id"] and listed["summary"]["open"] == 1
    assert client.post("/api/quant/brain/train", json={"symbol": "SYNTH", "timeframe": "D1"}).status_code == 422
    assert client.post("/api/quant/analyst/run", json={"symbol": "SYNTH", "timeframe": "M15", "order": True}).status_code == 422
