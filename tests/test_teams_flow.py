"""Team flows: dependency order, review loop, and templates."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.orchestrator import AgentResponse
from agent.team_orchestrator import (
    TeamMember,
    TeamOrchestrator,
    TeamStore,
    execution_layers,
    team_templates,
)
from config.constants import ResponseStatus


class FakeOrchestrator:
    def __init__(self, replies: dict[str, list[str]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.replies = replies or {}
        self.registry = SimpleNamespace(get=lambda _id: object())

    async def process(self, **kwargs: Any) -> AgentResponse:
        self.calls.append(kwargs)
        for marker, queue in self.replies.items():
            if marker in kwargs["session_id"] and queue:
                return AgentResponse(content=queue.pop(0), status=ResponseStatus.SUCCESS)
        return AgentResponse(content=f"report from {kwargs['session_id']}", status=ResponseStatus.SUCCESS)


def member(id_: str, *after: str) -> dict[str, Any]:
    return {"id": id_, "name": id_.title(), "role": "r", "model_id": "m", "depends_on": list(after)}


def make(tmp_path: Path, replies: dict[str, list[str]] | None = None) -> tuple[TeamOrchestrator, FakeOrchestrator]:
    fake = FakeOrchestrator(replies)
    return TeamOrchestrator(fake, TeamStore(tmp_path / "teams")), fake  # type: ignore[arg-type]


async def finish(manager: TeamOrchestrator, team_id: str, task: str = "do it") -> Any:
    run = await manager.start_run(team_id, task)
    await asyncio.wait_for(manager.run_tasks[run.id], timeout=5)
    return manager.store.get_run(run.id)


def test_layers_follow_dependencies() -> None:
    members = [TeamMember(i, i, "r", "m", depends_on=d) for i, d in [("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"])]]
    assert [[m.id for m in layer] for layer in execution_layers(members)] == [["a"], ["b", "c"], ["d"]]


def test_cycles_are_rejected(tmp_path: Path) -> None:
    manager, _ = make(tmp_path)
    with pytest.raises(ValueError, match="cycle"):
        manager.create_team("t", "", [member("a", "b"), member("b", "a")], "a")


@pytest.mark.parametrize(
    ("members", "lead", "reviewer", "message"),
    [
        ([member("a", "a"), member("b")], "b", None, "itself"),
        ([member("a", "ghost"), member("b")], "b", None, "unknown"),
        ([member("a"), member("b")], "a", "a", "own work"),
        ([member("a"), member("b")], "a", "ghost", "reviewer"),
        ([member("../x"), member("b")], "b", None, "letters"),
    ],
)
def test_invalid_teams_are_rejected(tmp_path: Path, members: list[dict[str, Any]], lead: str, reviewer: str | None, message: str) -> None:
    manager, _ = make(tmp_path)
    with pytest.raises(ValueError, match=message):
        manager.create_team("t", "", members, lead, reviewer)


async def test_downstream_sees_upstream_report(tmp_path: Path) -> None:
    manager, fake = make(tmp_path, {"-planner-": ["THE PLAN"]})
    team = manager.create_team("t", "", [member("planner"), member("builder", "planner"), member("lead")], "lead")
    run = await finish(manager, team.id)
    assert run.status == "completed"
    order = [c["session_id"].split("-", 2)[2] for c in fake.calls]
    assert order.index("planner-worker") < order.index("builder-worker") < order.index("lead-lead")
    builder_call = next(c for c in fake.calls if "builder" in c["session_id"])
    assert "THE PLAN" in builder_call["workspace_context"]


async def test_lead_only_runs_as_lead(tmp_path: Path) -> None:
    manager, fake = make(tmp_path)
    team = manager.create_team("t", "", [member("a"), member("lead")], "lead")
    await finish(manager, team.id)
    assert [c["session_id"].endswith("lead-worker") for c in fake.calls].count(True) == 0


async def test_reviewer_can_send_the_draft_back(tmp_path: Path) -> None:
    manager, fake = make(tmp_path, {
        "-lead-lead": ["draft one"],
        "-lead-revision": ["draft two"],
        "-rev-reviewer": ["VERDICT: REVISE\nThe date is wrong.", "VERDICT: PASS"],
    })
    team = manager.create_team("t", "", [member("a"), member("lead"), member("rev")], "lead", "rev")
    run = await finish(manager, team.id)
    assert run.status == "completed"
    assert run.result == "draft two"
    assert [r["verdict"] for r in run.reviews] == ["REVISE", "PASS"]
    revision = next(c for c in fake.calls if "revision" in c["session_id"])
    assert "The date is wrong" in revision["workspace_context"]
    assert "draft one" in revision["workspace_context"]


async def test_review_rounds_are_capped(tmp_path: Path) -> None:
    manager, fake = make(tmp_path, {"-rev-reviewer": ["VERDICT: REVISE\nno"] * 10})
    team = manager.create_team("t", "", [member("lead"), member("rev")], "lead", "rev")
    run = await finish(manager, team.id)
    assert run.status == "completed"
    assert len(run.reviews) == 2
    assert sum("revision" in c["session_id"] for c in fake.calls) == 1


async def test_broken_reviewer_does_not_lose_the_answer(tmp_path: Path) -> None:
    manager, fake = make(tmp_path, {"-lead-lead": ["the answer"]})
    original = fake.process

    async def process(**kwargs: Any) -> AgentResponse:
        if "reviewer" in kwargs["session_id"]:
            raise RuntimeError("provider down")
        return await original(**kwargs)

    fake.process = process  # type: ignore[method-assign]
    team = manager.create_team("t", "", [member("lead"), member("rev")], "lead", "rev")
    run = await finish(manager, team.id)
    assert run.status == "completed"
    assert run.result == "the answer"
    assert run.reviews[0]["verdict"] == "skipped"


def test_old_saved_teams_still_load(tmp_path: Path) -> None:
    directory = tmp_path / "teams"
    directory.mkdir()
    (directory / "teams.json").write_text(
        '[{"id":"t","name":"n","description":"","lead_id":"a","members":'
        '[{"id":"a","name":"A","role":"r","model_id":"m"}]}]'
    )
    team = TeamStore(directory).get_team("t")
    assert team is not None and team.reviewer_id is None and team.members[0].depends_on == []


def test_templates_are_valid_and_spend_the_strong_model_on_review(tmp_path: Path) -> None:
    manager, _ = make(tmp_path)
    models = ["ag/gemini-flash", "ag/claude-sonnet", "ag/claude-opus"]
    templates = team_templates(models)
    assert templates
    for template in templates:
        if not template["reviewer_id"]:
            continue
        manager.create_team(template["name"], "", template["members"], template["lead_id"], template["reviewer_id"])
        reviewer = next(m for m in template["members"] if m["id"] == template["reviewer_id"])
        assert reviewer["model_id"] == "ag/claude-opus"


def test_templates_work_with_a_single_model() -> None:
    used = [m["model_id"] for t in team_templates(["only"]) for m in t["members"] if m.get("kind", "agent") == "agent"]
    assert used and all(model == "only" for model in used)
    assert team_templates([]) == []


async def test_agents_keep_the_task_as_intent_and_leave_no_chat_history(tmp_path: Path) -> None:
    manager, fake = make(tmp_path)
    team = manager.create_team("t", "", [member("a"), member("lead")], "lead")
    await finish(manager, team.id, task="audit keamanan")
    assert all(c["task_hint"] == "audit keamanan" and c["persist"] is False for c in fake.calls)


def set_limits(monkeypatch: pytest.MonkeyPatch, **values: int) -> None:
    from config.settings import get_settings

    for name, value in values.items():
        monkeypatch.setattr(get_settings(), name, value)


async def test_members_get_a_budget(tmp_path: Path) -> None:
    manager, fake = make(tmp_path)
    team = manager.create_team("t", "", [member("lead"), member("a")], "lead")
    await finish(manager, team.id)
    by_mode = {c["session_id"].rsplit("-", 1)[1]: c["limits"] for c in fake.calls}
    assert by_mode["worker"].max_rounds == 10
    assert by_mode["lead"].max_rounds < by_mode["worker"].max_rounds
    assert by_mode["lead"].tokens < by_mode["worker"].tokens


async def test_workers_are_skipped_when_time_runs_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_limits(monkeypatch, team_run_seconds=0)
    manager, fake = make(tmp_path)
    team = manager.create_team("t", "", [member("lead"), member("a"), member("b", "a"), member("rev")], "lead", "rev")
    manager.store.get_team(team.id).members[1].depends_on = []
    run = await finish(manager, team.id)
    assert run.agents["a"]["status"] == "skipped"
    # With no reports at all there is nothing to write from.
    assert run.status == "failed"
    assert not fake.calls


async def test_reports_survive_a_failing_lead(tmp_path: Path) -> None:
    manager, fake = make(tmp_path, {"-a-worker": ["temuan penting"]})
    original = fake.process

    async def process(**kwargs: Any) -> AgentResponse:
        if "lead" in kwargs["session_id"]:
            raise RuntimeError("provider down")
        return await original(**kwargs)

    fake.process = process  # type: ignore[method-assign]
    team = manager.create_team("t", "", [member("lead"), member("a")], "lead")
    run = await finish(manager, team.id)
    assert run.status == "completed"
    assert "temuan penting" in run.result and "provider down" in run.result


async def test_a_hanging_model_cannot_block_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent import team_orchestrator

    monkeypatch.setattr(team_orchestrator, "HARD_TIMEOUT_GRACE_SECONDS", 0)
    set_limits(monkeypatch, team_member_seconds=0)
    manager, fake = make(tmp_path)
    original = fake.process

    async def process(**kwargs: Any) -> AgentResponse:
        if "-a-" in kwargs["session_id"]:
            await asyncio.sleep(60)
        return await original(**kwargs)

    fake.process = process  # type: ignore[method-assign]
    team = manager.create_team("t", "", [member("lead"), member("a"), member("b")], "lead")
    run = await finish(manager, team.id)
    assert run.agents["a"]["status"] == "failed"
    assert "batas waktu" in run.agents["a"]["error"]
    assert run.status == "completed"
