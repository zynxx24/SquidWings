"""Pin how requests are routed. Data: tests/eval_data/. Report: python -m tests.routing_eval."""

from __future__ import annotations

import pytest

from agent.harness.tiers import scaffold_for
from agent.harness.tool_select import select_tools
from brain.classifier.intent_classifier import IntentClassifier
from config.constants import TaskType
from tests.routing_eval import INTENT_FLOOR, check_intent, check_routing, intent_accuracy, load
from tool.execute import execute_toolbox
from tool.register_tools import register_all_tools


def _params(cases: list[dict]) -> list:
    return [
        pytest.param(
            c,
            id=c["text"][:50],
            marks=[pytest.mark.xfail(strict=True, reason=str(c["gap"]))] if c.get("gap") else [],
        )
        for c in cases
    ]


@pytest.mark.parametrize("case", _params(load("intent_cases")))
def test_intent_case(case: dict) -> None:
    verdict = check_intent(case)
    assert verdict.ok, verdict.problems


@pytest.mark.parametrize("case", _params(load("routing_cases")))
def test_routing_case(case: dict) -> None:
    verdict = check_routing(case)
    assert verdict.ok, verdict.problems


def test_intent_accuracy_stays_above_the_floor() -> None:
    """Known gaps count as misses here, so piling up flags cannot hide a decline."""
    verdicts = [check_intent(c) for c in load("intent_cases")]
    assert intent_accuracy(verdicts) >= INTENT_FLOOR


def test_every_case_names_a_real_task_type() -> None:
    valid = {t.value for t in TaskType}
    for case in [*load("intent_cases"), *load("routing_cases")]:
        assert {case.get("task", "code"), *case.get("not", [])} <= valid, case["text"]


def test_cases_are_not_duplicated() -> None:
    for name in ("intent_cases", "routing_cases"):
        texts = [c["text"].strip().lower() for c in load(name)]
        assert len(texts) == len(set(texts)), name


@pytest.mark.parametrize("text", ["halo", "makasih ya", "buat landing page untuk toko roti"])
def test_hidden_toolbox_never_rides_along(text: str) -> None:
    """A request that does not ask for a toolbox tool must not get one."""
    task = IntentClassifier().classify(text).task_type
    offered = {s["function"]["name"] for s in select_tools(register_all_tools().get_all_schemas(), task, text, scaffold_for("x-flash"))}
    assert not offered & (execute_toolbox.deferred_names() - {"style_pick"}), offered & execute_toolbox.deferred_names()


@pytest.mark.parametrize("text", ["halo", "perbaiki bug login di auth.py", "buat presentasi pptx tentang AI"])
def test_offered_tools_respect_the_tier_cap(text: str) -> None:
    scaffold = scaffold_for("x-flash")
    task = IntentClassifier().classify(text).task_type
    names = [s["function"]["name"] for s in select_tools(register_all_tools().get_all_schemas(), task, text, scaffold)]
    assert scaffold.max_tools is not None
    assert len(names) <= scaffold.max_tools
    assert names == sorted(names)
