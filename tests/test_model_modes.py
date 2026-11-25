"""The two model modes: a model the user picks (locked: errors explained, never swapped) and Auto
(the autopilot: cheapest reliable model for the tier the request needs). Offline."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from brain.harness import autopilot, model_errors
from brain.harness.tiers import Tier


class _Picker:
    def __init__(self, stats: dict[str, dict[str, float]] | None = None) -> None:
        self._stats = stats or {}

    def stats(self, model_id: str) -> dict[str, float]:
        return self._stats.get(model_id, {})


def _model(model_id: str, window: int = 128_000, tools: bool = True, vision: bool = True) -> SimpleNamespace:
    return SimpleNamespace(id=model_id, context_window=window, supports_tools=tools, supports_vision=vision)


MODELS = [_model("x/claude-haiku-9"), _model("x/claude-sonnet-9"), _model("x/claude-opus-9"),
          _model("x/gemini-flash-9")]


@pytest.fixture(autouse=True)
def _no_prices(monkeypatch):
    from brain.model.router import pricing

    monkeypatch.setattr(pricing, "cost_of", lambda model, **_: None)


def test_difficulty_picks_the_tier():
    pick = lambda d: autopilot.choose(MODELS, _Picker(), task="reason", difficulty=d, needs=autopilot.Needs())  # noqa: E731
    assert pick("easy").tier == Tier.LITE
    assert pick("medium").model.id == "x/claude-sonnet-9"
    assert pick("hard").model.id == "x/claude-opus-9"


def test_tool_work_never_lands_on_a_lite_model():
    choice = autopilot.choose(MODELS, _Picker(), task="code", difficulty="easy", needs=autopilot.Needs(tools=True))
    assert choice.tier == Tier.STANDARD


def test_a_model_that_keeps_failing_loses_to_a_reliable_one():
    picker = _Picker({"x/claude-haiku-9": {"ok": 1, "fail": 9}, "x/gemini-flash-9": {"ok": 20, "fail": 0}})
    choice = autopilot.choose(MODELS, picker, task="reason", difficulty="easy", needs=autopilot.Needs())
    assert choice.model.id == "x/gemini-flash-9" and "sukses" in choice.reason


def test_the_cheaper_of_two_reliable_models_wins(monkeypatch):
    from brain.model.router import pricing

    prices = {"x/claude-haiku-9": 0.004, "x/gemini-flash-9": 0.0005}
    monkeypatch.setattr(pricing, "cost_of", lambda model, **_: prices.get(model))
    choice = autopilot.choose(MODELS, _Picker(), task="reason", difficulty="easy", needs=autopilot.Needs())
    assert choice.model.id == "x/gemini-flash-9"


def test_a_long_conversation_needs_a_window_that_holds_it():
    models = [_model("x/claude-sonnet-small", window=8_000), _model("x/claude-sonnet-big", window=200_000)]
    choice = autopilot.choose(models, _Picker(), task="reason", difficulty="medium", needs=autopilot.Needs(tokens=50_000))
    assert choice.model.id == "x/claude-sonnet-big"


def test_images_need_vision():
    models = [_model("x/claude-sonnet-a", vision=False), _model("x/claude-sonnet-b", vision=True)]
    choice = autopilot.choose(models, _Picker(), task="multimodal", difficulty="medium", needs=autopilot.Needs(vision=True))
    assert choice.model.id == "x/claude-sonnet-b"


def test_a_bad_turn_escalates_the_next_and_a_good_one_steps_back():
    autopilot.record("chat-1", ok=False)
    assert autopilot.choose(MODELS, _Picker(), task="reason", difficulty="easy", needs=autopilot.Needs(),
                            session_id="chat-1").tier == Tier.STANDARD
    autopilot.record("chat-1", ok=True)
    assert autopilot.escalation("chat-1") == 0
    assert autopilot.escalation("chat-2") == 0, "one chat's trouble does not raise another's cost"


def test_a_missing_tier_moves_up_first():
    choice = autopilot.choose([_model("x/claude-opus-9")], _Picker(), task="reason", difficulty="easy",
                              needs=autopilot.Needs())
    assert choice.model.id == "x/claude-opus-9" and "tidak tersedia" in choice.reason


@pytest.mark.parametrize(("error", "kind"), [
    (SimpleNamespace(status_code=401), "auth"),
    (RuntimeError("HTTP 402 billing_error: top up at ..."), "billing"),
    (RuntimeError("429 Too Many Requests"), "rate_limit"),
    (RuntimeError("model not found: x/y"), "not_found"),
    (RuntimeError("This model's maximum context length is 8192 tokens"), "context"),
    (TimeoutError("stream idle for 300s"), "timeout"),
    (RuntimeError("502 Bad Gateway"), "server"),
])
def test_errors_are_named(error, kind):
    exc = error if isinstance(error, BaseException) else type("E", (Exception,), {"status_code": error.status_code})("no")
    assert model_errors.classify(exc) == kind
    assert "tidak diganti otomatis" in model_errors.explain(exc, "x/m")


async def test_a_locked_model_reports_its_error_and_no_other_model_answers(tmp_path):
    from brain.agent.orchestrator import Orchestrator
    from brain.config.constants import ResponseStatus
    from brain.config.settings import Settings
    from brain.memory.conversation_store import ConversationStore
    from brain.model.registry.model_info import ModelInfo

    calls: list[str] = []

    class Client:
        provider_ids = ["router"]

        async def chat_completion(self, **kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] == "prov/picked":
                raise RuntimeError("HTTP 402 billing_error: top up at the provider")
            return {"choices": [{"message": {"content": "dari model lain"}, "finish_reason": "stop"}], "usage": {}}

    orch = Orchestrator.from_settings(Settings(), api_client=Client())
    store = ConversationStore(storage_dir=tmp_path)
    orch._conversation_store = store
    orch._context_assembler._store = store
    saved = dict(orch.registry._models)
    orch.registry._models.clear()
    try:
        for model_id in ("prov/picked", "prov/other"):
            orch.registry.register(ModelInfo(id=model_id, display_name=model_id, provider="ag"))
        response = await orch.process(user_input="halo", session_id="s", override_model="prov/picked",
                                      model_locked=True, stream=False)
        unknown = await orch.process(user_input="halo", session_id="s2", override_model="prov/nope",
                                     model_locked=True, stream=False)
    finally:
        orch.registry._models.clear()
        orch.registry._models.update(saved)
    assert response.status == ResponseStatus.ERROR
    assert "prov/picked" in response.content and "402" in response.content
    assert set(calls) == {"prov/picked"}, "no other model may answer a locked chat"
    assert unknown.status == ResponseStatus.ERROR and "tidak ada di daftar" in unknown.content
