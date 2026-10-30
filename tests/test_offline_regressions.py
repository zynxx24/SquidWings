"""Regression tests for fixes verified without an AI provider."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.agent_loop import AgentLoop
from agent.team_orchestrator import Team, TeamMember, TeamOrchestrator, TeamRun, TeamStore
from model.connection.response_handler import ParsedResponse
from tool.registry.tool_registry import ToolRegistry
from tool.registry.tool_schema import ToolSchema


def test_team_store_skips_only_the_damaged_run(tmp_path: Path) -> None:
    (tmp_path / "runs" / "broken.json").parent.mkdir(parents=True)
    (tmp_path / "runs" / "broken.json").write_text("{not json", encoding="utf-8")
    valid = TeamRun(id="valid", team_id="team", task="offline")
    (tmp_path / "runs" / "valid.json").write_text(json.dumps(valid.__dict__), encoding="utf-8")
    store = TeamStore(tmp_path)
    assert store.get_run("valid").task == "offline"
    assert store.get_run("broken") is None


@pytest.mark.asyncio
async def test_team_update_preserves_identity_and_does_not_save_temporary_id(tmp_path: Path) -> None:
    member = TeamMember(id="m1", name="Worker", role="tester", model_id="mock")
    original = Team(id="stable", name="Before", description="", members=[member, TeamMember(id="m2", name="Other", role="reviewer", model_id="mock")], lead_id="m1", created_at=123.0)
    store = TeamStore(tmp_path)
    store.put_team(original)
    registry = SimpleNamespace(get=lambda model_id: object() if model_id == "mock" else None)
    orchestrator = TeamOrchestrator(SimpleNamespace(registry=registry), store)
    updated = await orchestrator.update_team("stable", "After", "updated", [member.__dict__, original.members[1].__dict__], "m2")
    assert updated.id == "stable"
    assert updated.created_at == 123.0
    assert updated.lead_id == "m2"
    assert [team.id for team in TeamStore(tmp_path).list_teams()] == ["stable"]


class FakeClient:
    def __init__(self) -> None:
        self.responses = [
            ParsedResponse(tool_calls=[{"id": "call-1", "name": "mock_tool", "arguments": {"value": 2}}], finish_reason="tool_calls"),
            ParsedResponse(content="done", finish_reason="stop"),
        ]
        self.requests = []

    async def chat_completion(self, **kwargs):
        self.requests.append(kwargs)
        return {"choices": [{"message": {"content": self.responses.pop(0).content}, "finish_reason": "stop"}]}


@pytest.mark.asyncio
async def test_agent_loop_sends_openai_wire_tool_calls() -> None:
    client = FakeClient()
    tools = ToolRegistry()
    tools.reset()
    tools.register(ToolSchema(name="mock_tool", description="mock"), lambda value: value * 2)
    loop = AgentLoop(client, tools, max_iterations=3)
    # Controlled parser result: no model or HTTP transport involved.
    captured_messages = []

    async def call_model(**kwargs):
        captured_messages.append(kwargs["messages"])
        return client.responses.pop(0)
    loop._call_model = call_model
    result = await loop.run("mock", [{"role": "user", "content": "calculate"}], stream=False)
    assert result.content == "done"
    assert result.tool_calls_history[0]["result"]["success"]
    assert result.tool_calls_history[0]["result"]["result"] == 4
    follow_up = captured_messages[1]
    assert follow_up[-2]["tool_calls"] == [{
        "id": "call-1",
        "type": "function",
        "function": {"name": "mock_tool", "arguments": '{"value": 2}'},
    }]
    assert follow_up[-1] == {
        "role": "tool", "tool_call_id": "call-1", "content": "4"
    }


@pytest.mark.asyncio
async def test_thinking_only_turn_nudges_instead_of_finishing() -> None:
    """A thinking-only reply must not be accepted as the final answer.

    Regression for the reasoning-model failure mode where the model emits a
    long thinking block, no content and no tool call, and the loop used to
    return that empty answer as final — so the user saw the agent "answer"
    without having read a single file.
    """
    loop = AgentLoop(FakeClient(), ToolRegistry(), max_iterations=3)
    seen: list[list[dict]] = []

    async def call_model(**kwargs):
        seen.append([dict(message) for message in kwargs["messages"]])
        if len(seen) == 1:
            return ParsedResponse(thinking="belum membaca apa pun", content="")
        return ParsedResponse(content="selesai", finish_reason="stop")

    loop._call_model = call_model
    result = await loop.run(
        "mock", [{"role": "user", "content": "baca berkas"}], stream=False
    )

    assert result.content == "selesai"
    assert result.iterations == 2
    # The first thinking-only turn must be answered with a nudge, not accepted.
    assert seen[1][-1]["role"] == "user"
    assert "Jangan menyimpulkan sebelum membaca" in seen[1][-1]["content"]
    # The thinking text must be echoed back so the model keeps its own context.
    assert seen[1][-2]["role"] == "assistant"
    assert "<thinking>" in seen[1][-2]["content"]


@pytest.mark.asyncio
async def test_thinking_only_turns_do_not_consume_tool_budget() -> None:
    """Thinking-only turns must not eat the tool-call iteration budget.

    The loop is allowed max_iterations + MAX_THINKING_ONLY_ITERATIONS turns,
    so a reasoning model that "thinks twice" before acting still gets its
    full complement of tool rounds.
    """
    from config.constants import MAX_THINKING_ONLY_ITERATIONS

    loop = AgentLoop(FakeClient(), ToolRegistry(), max_iterations=2)
    calls = 0

    async def call_model(**kwargs):
        nonlocal calls
        calls += 1
        return ParsedResponse(thinking=f"putaran {calls}", content="")

    loop._call_model = call_model
    result = await loop.run("mock", [{"role": "user", "content": "x"}], stream=False)

    assert calls == 2 + MAX_THINKING_ONLY_ITERATIONS
    assert result.iterations == 2 + MAX_THINKING_ONLY_ITERATIONS
    # Never converged: content stays empty rather than being invented.
    assert result.content == ""


@pytest.mark.asyncio
async def test_tool_call_resets_thinking_only_streak() -> None:
    """A real tool call is progress and must reset the streak counter.

    Without the reset, a long task that interleaves thinking and tool calls
    would hit the escalation nudge even though the model is working normally.
    """
    tools = ToolRegistry()
    tools.reset()
    tools.register(ToolSchema(name="mock_tool", description="mock"), lambda value: value)
    loop = AgentLoop(FakeClient(), tools, max_iterations=6)
    script = [
        ParsedResponse(thinking="rencana", content=""),
        ParsedResponse(
            tool_calls=[{"id": "c1", "name": "mock_tool", "arguments": {"value": 1}}],
            finish_reason="tool_calls",
        ),
        ParsedResponse(thinking="rencana lagi", content=""),
        ParsedResponse(content="selesai", finish_reason="stop"),
    ]
    nudges: list[str] = []

    async def call_model(**kwargs):
        nudges.append(kwargs["messages"][-1].get("content") or "")
        return script.pop(0)

    loop._call_model = call_model
    result = await loop.run("mock", [{"role": "user", "content": "kerja"}], stream=False)

    assert result.content == "selesai"
    # The second thinking-only turn is streak 1 again, so it gets the gentle
    # nudge — not the escalated one that a streak of 2 would produce.
    escalated = [text for text in nudges if "Jangan mengarang isi berkas" in text]
    assert escalated == []


def test_append_system_handles_str_and_cached_blocks() -> None:
    """Appending must not disturb a cached block, or the provider cache is lost."""
    from agent.orchestrator import Orchestrator

    plain = [{"role": "system", "content": "Initial"}]
    Orchestrator._append_system(plain, " more")
    assert plain[0]["content"] == "Initial more"

    cached_block = {"type": "text", "text": "Base", "cache_control": {"type": "ephemeral"}}
    cached = [{"role": "system", "content": [cached_block]}]
    Orchestrator._append_system(cached, "Volatile")
    assert cached[0]["content"][0] == cached_block
    assert cached[0]["content"][1] == {"type": "text", "text": "Volatile"}


async def test_fallback_moves_past_failing_models_to_one_that_answers(tmp_path) -> None:
    """A failing primary and a failing first fallback must not end the request."""
    from agent.orchestrator import Orchestrator
    from brain.memory.conversation_store import ConversationStore
    from config.constants import ResponseStatus
    from config.settings import Settings
    from model.registry.model_info import ModelInfo

    good = {"choices": [{"message": {"content": "Berhasil"}, "finish_reason": "stop"}], "usage": {}}
    calls: list[str] = []

    class Client:
        provider_ids = ["router"]

        async def chat_completion(self, **kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] != "ag/third":
                raise RuntimeError(f"{kwargs['model']} is down")
            return good

    orch = Orchestrator.from_settings(Settings(), api_client=Client())
    store = ConversationStore(storage_dir=tmp_path)
    orch._conversation_store = store
    orch._context_assembler._store = store
    for model_id in ("ag/first", "ag/second", "ag/third"):
        orch.registry.register(ModelInfo(id=model_id, display_name=model_id, provider="ag"))

    response = await orch.process(user_input="halo", session_id="s", override_model="ag/first", stream=False)

    assert response.content == "Berhasil"
    assert response.status == ResponseStatus.FALLBACK_USED
    assert calls[0] == "ag/first" and calls[-1] == "ag/third"


# --- audit fixes (2026-10-30) ---


def _retries(error, attempt):
    from model.connection.api_client import APIClient
    from model.connection.providers import Provider

    return APIClient._should_retry_transient(Provider(id="x", transient_retries=3), error, attempt)


def test_bansos_never_retries_a_request_that_cannot_succeed():
    from utils.exceptions import ModelError

    assert not _retries(ModelError("bad", status_code=401), 1)
    assert not _retries(ModelError("bad", status_code=400), 1)


def test_bansos_retries_transient_errors_up_to_the_cap():
    from utils.exceptions import ModelError, RateLimitError

    assert _retries(ModelError("boom", status_code=503), 1)
    assert _retries(RateLimitError("slow"), 2)
    assert _retries(RuntimeError("network"), 2)
    assert not _retries(RuntimeError("network"), 3)


def test_empty_model_list_does_not_wipe_the_registry():
    import asyncio

    from model.connection.api_client import APIClient

    class Registry:
        model_ids = ["ag/a", "bansos/b"]
        removed: list = []

        def unregister(self, model_id):
            self.removed.append(model_id)

        def register_from_api_models(self, items):
            pass

    async def empty():
        return []

    client = APIClient("http://router.invalid/v1", "key")
    client._providers = []
    client.list_models = empty
    registry = Registry()
    asyncio.run(APIClient.sync_all_models(client, registry))
    assert registry.removed == []


def test_mark_unavailable_expires_and_blocks_routing(monkeypatch):
    from model.connection import health_check
    from model.connection.health_check import HealthChecker

    now = [100.0]
    monkeypatch.setattr(health_check.time, "monotonic", lambda: now[0])
    checker = HealthChecker()
    checker.mark_unavailable("m", 60)
    assert not checker.is_available("m")
    assert checker.filter_healthy(["m", "n"]) == ["n"]
    now[0] = 161.0
    assert checker.is_available("m")
