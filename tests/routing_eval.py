"""Offline eval of how requests are routed: task type, skills woken, tools offered.

The cases live in tests/eval_data/*.json so a prompt can be added without touching code.
No model is called. Run `python -m tests.routing_eval` for a report with accuracy per task
and the wrong answers; tests/test_routing_eval.py pins every case.

A case with "gap": true is a known miss. It is expected to fail (strict xfail), so fixing the
router makes the test say "remove the gap flag" and a new miss cannot hide behind an old one.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA = Path(__file__).parent / "eval_data"
# Below this share of correct intent cases the suite fails, whatever the individual flags say.
INTENT_FLOOR = 0.9


def load(name: str) -> list[dict[str, Any]]:
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


@dataclass
class Verdict:
    text: str
    ok: bool
    problems: list[str] = field(default_factory=list)
    gap: bool = False


def _quiet() -> None:
    # Every classify() logs a line. A report of 100 prompts should not scroll past them.
    logging.disable(logging.CRITICAL)


def check_intent(case: dict[str, Any]) -> Verdict:
    """One intent case. "task" must match exactly; "not" lists tasks the request must never get."""
    from brain.classifier.intent_classifier import IntentClassifier

    _quiet()
    got = IntentClassifier().classify(case["text"]).task_type.value
    problems = []
    if "task" in case and got != case["task"]:
        problems.append(f"expected {case['task']}, got {got}")
    if got in case.get("not", []):
        problems.append(f"got forbidden {got}")
    return Verdict(case["text"], not problems, problems, bool(case.get("gap")))


def check_routing(case: dict[str, Any]) -> Verdict:
    """One routing case: the tools offered to a small model and the skills this request wakes."""
    from agent.harness.tiers import scaffold_for
    from agent.harness.tool_select import select_tools
    from brain import skills
    from brain.classifier.intent_classifier import IntentClassifier
    from tool.register_tools import register_all_tools

    _quiet()
    text = case["text"]
    task = IntentClassifier().classify(text).task_type
    scaffold = scaffold_for("x-flash")
    offered = {s["function"]["name"] for s in select_tools(register_all_tools().get_all_schemas(), task, text, scaffold)}
    picked = {s.name for s in skills.select(text, task=task.value)}

    problems = []
    if "task" in case and task.value != case["task"]:
        problems.append(f"task: expected {case['task']}, got {task.value}")
    if missing := [t for t in case.get("tools", []) if t not in offered]:
        problems.append(f"tools missing: {missing}")
    if extra := [t for t in case.get("no_tools", []) if t in offered]:
        problems.append(f"tools that should not be offered: {extra}")
    if missing_skills := [s for s in case.get("skills", []) if s not in picked]:
        problems.append(f"skills missing: {missing_skills}")
    if extra_skills := [s for s in case.get("no_skills", []) if s in picked]:
        problems.append(f"skills that should not be picked: {extra_skills}")
    return Verdict(text, not problems, problems, bool(case.get("gap")))


def intent_accuracy(verdicts: list[Verdict]) -> float:
    return sum(v.ok for v in verdicts) / len(verdicts) if verdicts else 1.0


def report() -> str:
    """Human-readable summary: accuracy per expected task, then every miss."""
    cases = load("intent_cases")
    verdicts = [check_intent(c) for c in cases]
    per_task: dict[str, list[bool]] = defaultdict(list)
    for case, verdict in zip(cases, verdicts, strict=True):
        per_task[case.get("task", "(guard)")].append(verdict.ok)

    lines = [f"Intent: {sum(v.ok for v in verdicts)}/{len(verdicts)} = {intent_accuracy(verdicts):.0%} (floor {INTENT_FLOOR:.0%})"]
    for task, oks in sorted(per_task.items()):
        lines.append(f"  {task:<13} {sum(oks)}/{len(oks)}")
    wrong = Counter()
    for verdict in verdicts:
        if not verdict.ok:
            wrong["known gap" if verdict.gap else "NEW MISS"] += 1
            lines.append(f"  {'gap ' if verdict.gap else 'MISS'} {verdict.text!r}: {'; '.join(verdict.problems)}")

    routing = [check_routing(c) for c in load("routing_cases")]
    lines.append(f"\nRouting (tools + skills): {sum(v.ok for v in routing)}/{len(routing)}")
    for verdict in routing:
        if not verdict.ok:
            wrong["known gap" if verdict.gap else "NEW MISS"] += 1
            lines.append(f"  {'gap ' if verdict.gap else 'MISS'} {verdict.text!r}: {'; '.join(verdict.problems)}")
    lines.append(f"\n{wrong['NEW MISS']} new misses, {wrong['known gap']} known gaps")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
