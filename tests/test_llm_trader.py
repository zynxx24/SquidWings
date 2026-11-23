"""The trading LLM: parsing, counting and pricing its calls, the paper simulation's LLM rule and log, and the forward test's gate.

The model is a fake: no network, no tokens spent.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

from model.router import pricing
from trading.quant import llm_trader, papersim
from trading.quant.llm_trader import LLMTrader, parse_decision


class FakeClient:
    def __init__(self, replies: list[Any]) -> None:
        self.replies, self.prompts = list(replies), []

    async def chat_completion(self, model: str, messages: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
        self.prompts.append(messages[-1]["content"])
        reply = self.replies.pop(0) if self.replies else '{"side": "none"}'
        if isinstance(reply, Exception):
            raise reply
        return {"choices": [{"message": {"content": reply}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050}}


@pytest.fixture(autouse=True)
def private_prices(tmp_path, monkeypatch):
    monkeypatch.setattr(pricing, "_book", pricing.PriceBook(tmp_path / "prices.json"))
    monkeypatch.setattr(pricing, "store_dir", lambda: tmp_path)


@pytest.mark.parametrize(("text", "side"), [
    ('{"side": "buy", "confidence": 0.7, "why": "trend"}', "buy"),
    ('Sure.\n```json\n{"side": "SELL", "confidence": 2}\n```', "sell"),
    ('thinking... {"side": "long"}', "buy"),
    ('{"side": "wait", "confidence": "x"}', "none"),
])
def test_replies_are_read_and_checked(text, side):
    answer = parse_decision(text)
    assert answer["side"] == side and 0 <= answer["confidence"] <= 1


@pytest.mark.parametrize("text", ["no json here", '{"side": "moon"}', "[1, 2]"])
def test_bad_replies_are_refused(text):
    with pytest.raises(ValueError):
        parse_decision(text)


def test_calls_are_counted_priced_and_capped():
    client = FakeClient(['{"side": "buy", "confidence": 0.6, "why": "a"}'] * 3)
    trader = LLMTrader("claude-haiku-5-5", max_calls=2, client=client)
    assert trader.decide({"symbol": "XAUUSD", "news_brief": "Gold up"})["side"] == "buy"
    assert "News and market mood:\nGold up" in client.prompts[0] and '"symbol":"XAUUSD"' in client.prompts[0]
    trader.decide({})
    assert trader.decide({}) is None and "batas 2 panggilan" in trader.why_unavailable
    report = trader.report()
    assert report["calls"] == 2 and report["input_tokens"] == 2000 and report["output_tokens"] == 100
    assert report["cost_usd"] == pytest.approx(2 * (1000 * 0.10 + 50 * 0.50) / 1e6) and not report["unpriced"]


def test_a_model_that_keeps_failing_is_left_alone():
    trader = LLMTrader("unknown/model", client=FakeClient([RuntimeError("502"), "not json", '{"side": "buy"}']))
    assert trader.decide({}) is None and trader.decide({}) is None
    assert not trader.available and "gagal 2 kali" in trader.why_unavailable
    assert trader.decide({}) is None and trader.calls == 2


def test_config_is_saved_and_checked(tmp_path):
    path = tmp_path / "llm.json"
    saved = llm_trader.save_config({"model": "vk/x", "sim_enabled": True, "max_calls": 5000, "forward_mode": "confirm"}, path)
    assert saved["max_calls"] == 2000 and llm_trader.load_config(path)["forward_mode"] == "confirm"
    with pytest.raises(ValueError):
        llm_trader.save_config({"forward_mode": "yolo"}, path)


# ── the LLM in the paper simulation ─────────────────────────────────────────
class Clock:
    def __init__(self) -> None:
        self.t = 1_794_312_000.0      # a Tuesday, 12:00 UTC: London is open

    def __call__(self) -> float:
        return self.t


class Feed:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def tick(self, symbol: str) -> dict[str, Any]:
        mid = 100 + (self.clock.t - 1_794_312_000.0)
        return {"bid": mid - 0.1, "ask": mid + 0.1}

    def symbol_spec(self, symbol: str) -> dict[str, Any]:
        return {"digits": 2}


def rising(symbol: str) -> list[dict[str, float]]:
    return [{"t": i * 60.0, "o": 100 + i, "h": 101 + i, "l": 99.5 + i, "c": 100.8 + i, "v": 10.0} for i in range(200)]


class SyncThread:
    """Runs the LLM call at once, so the test sees its answer on the next step."""

    def __init__(self, target: Any, args: tuple[Any, ...] = (), **kw: Any) -> None:
        self.target, self.args = target, args

    def start(self) -> None:
        self.target(*self.args)


def test_the_llm_takes_its_own_virtual_position_and_every_cycle_is_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(papersim.threading, "Thread", SyncThread)
    clock = Clock()
    client = FakeClient(['{"side": "buy", "confidence": 0.8, "why": "rising"}', '{"side": "none", "why": "spread"}'])
    trader = LLMTrader("claude-haiku-5-5", client=client)
    snapshot = {"mood": {"label": "netral", "score": 0}, "calendar": {}, "topics": {}, "relevant": [], "headlines": []}
    sim = papersim.PaperSim(Feed(clock), "XAUUSD", hold_s=30, bars=rising, clock=clock, sleep=lambda s: None, seed=1,
                            llm=trader, news=lambda: snapshot, folder=tmp_path)
    sim.state = "running"
    sim.step()                                    # cycle 1: rules open, the LLM is asked
    assert all(p.rule != papersim.LLM for p in sim.open)
    context = json.loads(client.prompts[0].split("Facts:\n", 1)[1].split("\n\n", 1)[0])
    assert context["session"] == "London" and "math" in context and context["rules_now"]
    clock.t += 2
    sim.step()                                    # the answer arrives: the LLM buys at this ask
    llm_pos = next(p for p in sim.open if p.rule == papersim.LLM)
    assert llm_pos.side == 1 and llm_pos.entry == pytest.approx(102.1) and llm_pos.why == "rising"
    clock.t += 29
    sim.step()                                    # the rules close and cycle 2 opens; the LLM is still open
    assert sim.cycles == 2 and any(p.rule == papersim.LLM for p in sim.open)
    clock.t += 2
    sim.step()
    trade = next(t for t in sim.trades if t["rule"] == papersim.LLM)
    assert trade["win"] and trade["why"] == "rising" and trade["session"] == "london"
    report = sim.report()
    assert report["llm"]["calls"] >= 1 and report["llm"]["cost_usd"] > 0
    assert any(r["rule"] == papersim.LLM and r["label"] == "London" for r in report["by_session"])
    assert report["math"] is not None and report["cycle_log"][0]["session_label"] == "London"
    rows = [json.loads(line) for line in sim.path.read_text().splitlines()]
    assert {"cycle", "llm", "close"} <= {r["type"] for r in rows}
    assert any(r["type"] == "llm" and r["opened"] for r in rows)


def test_a_late_answer_is_not_traded(monkeypatch):
    monkeypatch.setattr(papersim.threading, "Thread", SyncThread)
    clock = Clock()
    trader = LLMTrader("claude-haiku-5-5", client=FakeClient(['{"side": "sell"}']))
    sim = papersim.PaperSim(Feed(clock), "XAUUSD", hold_s=30, bars=rising, clock=clock, sleep=lambda s: None, llm=trader)
    sim.state = "running"
    sim.step()
    clock.t += 20                                 # more than half the hold has passed
    sim.step()
    assert all(p.rule != papersim.LLM for p in sim.open) and sim.llm_skips == 1


def test_without_an_llm_there_is_no_llm_rule():
    clock = Clock()
    sim = papersim.PaperSim(Feed(clock), "XAUUSD", bars=rising, clock=clock, sleep=lambda s: None)
    assert papersim.LLM not in sim.candidates and papersim.MATH in sim.candidates
    assert sim.report()["llm"] is None


# ── the LLM as the forward test's gate ─────────────────────────────────────
def test_forward_confirm_mode_skips_when_the_llm_disagrees(tmp_path, monkeypatch):
    from tests.test_forward import Backend, _bars
    from tests.test_forward import Clock as FClock
    from trading.quant.forward import ForwardTest

    monkeypatch.setenv("TRADE_JOURNAL", str(tmp_path / "journal.jsonl"))
    clock = FClock()
    trader = LLMTrader("claude-haiku-5-5", client=FakeClient(['{"side": "none", "why": "unclear"}'] * 50))
    run = ForwardTest(Backend(), "XAUUSD", 1.0, bars=lambda s: _bars(), sleep=clock.sleep, clock=clock, folder=tmp_path,
                      llm=trader, llm_mode="confirm")
    run.run()
    assert not run.baskets and run.llm_vetoes > 0 and run.report()["llm"]["calls"] > 0


def test_forward_decide_mode_trades_the_llm_side(tmp_path, monkeypatch):
    from tests.test_forward import Backend, _bars
    from tests.test_forward import Clock as FClock
    from trading.quant.forward import ForwardTest

    monkeypatch.setenv("TRADE_JOURNAL", str(tmp_path / "journal.jsonl"))
    clock = FClock()
    backend = Backend()
    trader = LLMTrader("claude-haiku-5-5", client=FakeClient(['{"side": "sell", "why": "fade"}'] * 50))
    run = ForwardTest(backend, "XAUUSD", 0.5, bars=lambda s: _bars(), sleep=clock.sleep, clock=clock, folder=tmp_path,
                      llm=trader, llm_mode="decide")
    run.run()
    assert run.baskets and all(o["side"] == "sell" for o in backend.sent)
    assert run.baskets[0]["llm"] == "fade"
    with pytest.raises(ValueError):
        ForwardTest(backend, "XAUUSD", 1.0, folder=tmp_path, llm_mode="decide")


def test_llm_api(monkeypatch, tmp_path):
    from web import quant_api
    from web.server import app

    monkeypatch.setattr(llm_trader, "CONFIG_PATH", tmp_path / "llm.json")
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.post("/api/quant/llm", json={"model": "vk/fast", "forward_mode": "decide"}).json()["model"] == "vk/fast"
    assert client.get("/api/quant/llm").json()["forward_mode"] == "decide"
    assert client.post("/api/quant/llm", json={"forward_mode": "x"}).status_code == 422
    clock = Clock()
    monkeypatch.setattr(quant_api, "_view_backend", lambda: Feed(clock))
    started: list[Any] = []

    class Thread:
        def __init__(self, target: Any, **kw: Any) -> None:
            started.append(target)

        def start(self) -> None:
            pass

    monkeypatch.setattr(quant_api.threading, "Thread", Thread)
    monkeypatch.setattr(quant_api, "_sim", None)
    monkeypatch.setenv("SIM_LOG_DIR", str(tmp_path / "sim"))
    r = client.post("/api/quant/sim/start", json={"symbol": "XAUUSD", "llm": True, "llm_max_calls": 7}).json()
    assert r["llm"]["model"] == "vk/fast" and r["llm"]["max_calls"] == 7 and started
    client.post("/api/quant/sim/stop")
    assert client.get("/api/quant/sim/logs").json()["files"] == []
    assert client.get("/api/quant/sim/logs/..%2Fllm.json").status_code in (400, 404)
    (tmp_path / "sim").mkdir()
    (tmp_path / "sim" / "run.jsonl").write_text('{"type": "cycle"}\n')
    assert client.get("/api/quant/sim/logs/run.jsonl").text.startswith('{"type"')
    assert threading is not None


# ── only models the Router serves ──────────────────────────────────────────
def test_the_model_must_be_one_the_router_serves(monkeypatch):
    from config import settings as settings_module

    served = ["vky/gemini/gemini-3.8-flash", "9R/kg/claude-haiku-5-5"]
    assert llm_trader.resolve_model("vky/gemini/gemini-3.8-flash", served) == ("vky/gemini/gemini-3.8-flash", "")
    model, problem = llm_trader.resolve_model("ag/gemini-3.8-flash-high", served)
    assert "tidak dilayani" in problem
    assert llm_trader.resolve_model("anything", []) == ("anything", ""), "providers unreachable: nothing to check against"

    class Settings:
        default_model = "ag/gemini-3.8-flash-high"     # an old DEFAULT_MODEL in .env

    monkeypatch.setattr(settings_module, "get_settings", lambda: Settings())
    model, problem = llm_trader.resolve_model("", served)
    assert model == "" and "ag/gemini-3.8-flash-high tidak dilayani Router lagi" in problem
    Settings.default_model = "9R/kg/claude-haiku-5-5"
    assert llm_trader.resolve_model("", served) == ("9R/kg/claude-haiku-5-5", "")


def test_a_simulation_with_a_model_the_router_does_not_serve_is_refused(monkeypatch, tmp_path):
    from web import quant_api
    from web.server import app

    monkeypatch.setattr(llm_trader, "CONFIG_PATH", tmp_path / "llm.json")
    monkeypatch.setattr(llm_trader, "served_models_sync", lambda force=False: {"models": ["vky/a", "9R/b"], "groups": []})
    clock = Clock()
    monkeypatch.setattr(quant_api, "_view_backend", lambda: Feed(clock))
    monkeypatch.setattr(quant_api, "_sim", None)
    client = TestClient(app, client=("127.0.0.1", 50000))
    r = client.post("/api/quant/sim/start", json={"symbol": "XAUUSD", "llm": True, "llm_model": "ag/gemini-3.8-flash-high"})
    assert r.status_code == 422 and "tidak dilayani" in r.json()["detail"]
    assert quant_api._sim is None, "nothing started"


def test_the_picker_lists_models_by_provider(monkeypatch):
    import asyncio

    from model.router import dispatch
    from model.router.store import get_store

    store = get_store()
    provider = store.add_provider({"name": "Picker Test", "base_url": "http://127.0.0.1:9/v1", "prefix": "pk/", "kind": "openai"})
    store.add_account(provider.id, {"api_key": "sk-test-1234567890"})

    async def fake_list(p: Any, account: Any = None) -> list[dict[str, Any]]:
        return [{"id": "pk/fast-chat"}, {"id": "pk/text-embedding-3"}] if p.id == provider.id else []

    monkeypatch.setattr(dispatch, "list_models", fake_list)
    try:
        r = asyncio.run(llm_trader.list_models(force=True))
        group = next(g for g in r["groups"] if g["provider"] == provider.id)
        assert group["name"] == "Picker Test" and group["models"] == ["pk/fast-chat"], "embedding models are left out"
    finally:
        store.delete_provider(provider.id)
        llm_trader._served.update({"at": 0.0, "models": [], "groups": [], "errors": []})


def test_router_clients_are_not_shared_between_event_loops():
    import asyncio

    from model.router import dispatch
    from model.router.store import Account, ProviderConfig

    provider = ProviderConfig(id="loop-test", name="Loop", kind="openai", base_url="http://127.0.0.1:9/v1", prefix="lt/")
    account = Account(id="a1", label="a", api_key="sk-x")

    async def grab() -> tuple[Any, list[Any]]:
        client = dispatch._client(provider, account)
        return client, [loop for loop in dispatch._client_loops.values() if loop.is_closed()]

    first, _ = asyncio.run(grab())
    second, stale = asyncio.run(grab())
    assert first is not second, "the second loop gets its own client, not one bound to a closed loop"
    assert stale == [], "clients of closed loops are dropped"


def test_the_llm_sets_its_own_stop_and_target_in_the_intraday_style(monkeypatch):
    monkeypatch.setattr(papersim.threading, "Thread", SyncThread)
    clock = Clock()
    client = FakeClient(['{"side": "buy", "confidence": 0.7, "why": "trend", "stop_atr": 9, "target_atr": 3}'])
    trader = LLMTrader("claude-haiku-5-5", client=client)

    def h1(symbol: str) -> list[dict[str, float]]:
        return [{"t": 1_794_312_000.0 - (200 - i) * 3600, "o": 100 + i, "h": 102 + i, "l": 99 + i, "c": 101 + i, "v": 1.0}
                for i in range(200)]

    sim = papersim.PaperSim(Feed(clock), "XAUUSD", timeframe="H1", bars=h1, clock=clock, sleep=lambda s: None,
                            llm=trader, max_minutes=600)
    sim.state = "running"
    sim.step()
    context = json.loads(client.prompts[0].split("Facts:\n", 1)[1].split("\n\n", 1)[0])
    assert context["style"] == "harian" and context["timeframe"] == "H1" and context["atr"] > 0
    assert "harian" in llm_trader.SYSTEM and "stop_atr" in llm_trader.SYSTEM
    clock.t += 2
    sim.step()
    mine = next(p for p in sim.open if p.rule == papersim.LLM)
    assert mine.stop == pytest.approx(mine.entry - 5 * sim.atr, abs=0.01), "a stop of 9 ATR is cut to the 5 ATR limit"
    assert mine.target == pytest.approx(mine.entry + 3 * sim.atr, abs=0.01)
