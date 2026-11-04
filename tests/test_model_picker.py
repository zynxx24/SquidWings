"""The learned model picker: rotation, learning from results, cheap-first easy tasks, and discovery of new models.

Offline. The picker uses a seeded random generator, so every assertion is deterministic.
"""

from __future__ import annotations

import random
import time
from collections import Counter

import pytest
import yaml

from config.constants import TaskType
from config.settings import get_settings
from model.registry.model_info import ModelInfo
from model.registry.model_registry import ModelRegistry
from model.settings.model_picker import ModelPicker, discover, is_chat_model, is_cheap
from model.settings.routing_type import Difficulty, RoutingType


def _picker(seed: int = 3) -> ModelPicker:
    return ModelPicker(path=None, rng=random.Random(seed))


def test_name_helpers_separate_cheap_and_non_chat_models():
    assert is_cheap("prov/glm-5-3-flash") and is_cheap("prov/gemini-3.8-flash-high")
    assert not is_cheap("prov/claude-opus-4-6-thinking")
    assert is_chat_model("prov/glm-5-3-flash")
    assert not is_chat_model("prov/openai/text-embedding-3")


def test_a_model_that_keeps_failing_loses_to_one_that_works():
    p = _picker()
    for _ in range(30):
        p.record("prov/bad", ok=False)
        p.record("prov/good", ok=True, latency_ms=800)
    wins = Counter(p.choose(["prov/bad", "prov/good"], now=time.time()) for _ in range(300))
    assert wins["prov/good"] >= 285


def test_an_untried_model_still_gets_turns():
    p = _picker()
    for _ in range(40):
        p.record("prov/known", ok=True, latency_ms=900)
    wins = Counter(p.choose(["prov/known", "prov/new"], now=time.time()) for _ in range(400))
    assert wins["prov/new"] > 0, "a model with no record must still be tried"


def test_idle_models_rotate_in_when_records_are_equal():
    p = _picker()
    now = time.time()
    for _ in range(10):
        p.record("prov/busy", ok=True, latency_ms=900)
        p.record("prov/idle", ok=True, latency_ms=900)
    p._stats["prov/busy"]["last"] = now           # used just now
    p._stats["prov/idle"]["last"] = now - 86_400  # not used for a day
    wins = Counter(p.choose(["prov/busy", "prov/idle"], now=now) for _ in range(400))
    assert wins["prov/idle"] > wins["prov/busy"]


def test_easy_tasks_prefer_cheap_models_when_records_are_equal():
    p = _picker()
    for _ in range(10):
        p.record("prov/claude-opus-4-6-thinking", ok=True, latency_ms=900)
        p.record("prov/glm-5-3-flash", ok=True, latency_ms=900)
    cheap = Counter(p.choose(["prov/claude-opus-4-6-thinking", "prov/glm-5-3-flash"], prefer_cheap=True, now=time.time())
                    for _ in range(300))
    assert cheap["prov/glm-5-3-flash"] > cheap["prov/claude-opus-4-6-thinking"]


def test_stats_survive_a_restart(tmp_path):
    path = tmp_path / "stats.json"
    ModelPicker(path).record("prov/glm-5-3-flash", ok=True, latency_ms=700)
    reloaded = ModelPicker(path)
    assert reloaded.stats("prov/glm-5-3-flash")["ok"] == 1


def test_choose_returns_none_without_candidates():
    assert _picker().choose([]) is None


def test_discover_takes_cheap_models_for_easy_and_skips_frontier_for_medium():
    models = [ModelInfo(id="prov/glm-5-3-flash"), ModelInfo(id="prov/claude-opus-4-6-thinking"),
              ModelInfo(id="prov/openai/text-embedding-3")]

    def frontier(mid: str) -> bool:
        return "opus" in mid

    easy = discover(models, prefer_cheap=True, needs_tools=False, is_frontier=frontier)
    medium = discover(models, prefer_cheap=False, needs_tools=False, is_frontier=frontier)
    assert easy == ["prov/glm-5-3-flash"]
    assert medium == ["prov/glm-5-3-flash"]


@pytest.fixture
def registry():
    reg = ModelRegistry()
    saved = dict(reg._models)
    reg._models.clear()
    try:
        yield reg
    finally:
        reg._models.clear()
        reg._models.update(saved)


def test_a_model_the_api_adds_later_is_routed_without_a_config_edit(registry):
    """The bug this fixes: a model not listed in the YAML was never tried."""
    registry.register(ModelInfo(id="prov/glm-5-3-flash", provider="kg"))
    registry.register(ModelInfo(id="prov/deepseek-v9-flash", provider="kg"))
    router = RoutingType(config_path=get_settings().models_config_path, registry=registry, picker=_picker(11))
    picked = Counter(router.route(TaskType.ACTIVITY, Difficulty.EASY).id for _ in range(200))
    assert picked["prov/glm-5-3-flash"] > 0 and picked["prov/deepseek-v9-flash"] > 0


def _router(registry, tmp_path, routing):
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump({"routing": routing}), encoding="utf-8")
    return RoutingType(config_path=path, registry=registry, picker=_picker(5))


def test_a_pinned_model_is_kept_for_hard_tasks(registry, tmp_path):
    registry.register(ModelInfo(id="pinned-model"))
    registry.register(ModelInfo(id="vendor/opus-big"))
    router = _router(registry, tmp_path, {"reason": {"hard": "pinned-model"}})
    assert {router.route(TaskType.REASON, Difficulty.HARD).id for _ in range(20)} == {"pinned-model"}


def test_with_nothing_pinned_hard_tasks_take_the_strongest_listed(registry, tmp_path):
    registry.register(ModelInfo(id="vendor/opus-big"))
    registry.register(ModelInfo(id="vendor/tiny-flash"))
    router = _router(registry, tmp_path, {"reason": {"default_difficulty": "hard"}})
    assert router.route(TaskType.REASON, Difficulty.HARD).id == "vendor/opus-big"


@pytest.mark.parametrize("difficulty", list(Difficulty))
def test_names_that_say_nothing_about_price_still_get_routed(registry, tmp_path, difficulty):
    registry.register(ModelInfo(id="llama-3.3-70b"))
    registry.register(ModelInfo(id="text-embedding-3"))
    router = _router(registry, tmp_path, {"activity": {"default_difficulty": "easy"}})
    assert router.route(TaskType.ACTIVITY, difficulty).id == "llama-3.3-70b"


def test_an_empty_endpoint_gives_no_model_and_no_crash(registry, tmp_path):
    router = _router(registry, tmp_path, {"activity": {}})
    assert router.route(TaskType.ACTIVITY, Difficulty.HARD) is None
    assert router.route(TaskType.ACTIVITY, Difficulty.EASY) is None


def test_a_plain_openai_model_list_is_usable_for_the_agent():
    model = ModelInfo.from_api_response({"id": "llama-3.3-70b", "object": "model", "owned_by": "meta"})
    assert model.supports_tools and model.context_window > 0 and model.max_output > 0


def test_a_model_that_reports_no_tools_is_believed():
    model = ModelInfo.from_api_response({"id": "x", "capabilities": {"tools": False, "vision": True}})
    assert not model.supports_tools


def test_outcomes_reach_the_picker_through_the_router(registry, tmp_path):
    picker = ModelPicker(tmp_path / "s.json", rng=random.Random(2))
    router = RoutingType(config_path=get_settings().models_config_path, registry=registry, picker=picker)
    router.record_outcome("prov/glm-5-3-flash", True, 650.0)
    assert picker.stats("prov/glm-5-3-flash")["ok"] == 1
