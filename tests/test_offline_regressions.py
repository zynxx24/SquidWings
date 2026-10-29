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
from tool.sandbox.permission_guard import PermissionGuard


def test_permission_guard_uses_path_boundaries(tmp_path: Path) -> None:
    root = tmp_path / "project"
    sibling = tmp_path / "project-private"
    root.mkdir()
    sibling.mkdir()
    guard = PermissionGuard(root)
    assert guard.check_permission("write", str(root / "ok.txt"))
    assert not guard.check_permission("write", str(sibling / "stolen.txt"))
    assert not guard.check_permission("delete", str(sibling / "stolen.txt"))
    assert not guard.check_permission("delete", str(root))
    assert not guard.check_permission("write", "/etc/squidwings")


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


def test_append_to_system_message_handles_str_and_cached_blocks() -> None:
    """_append_to_system_message must handle both string content and cached block lists."""
    from main import _append_to_system_message

    # 1. Plain string content
    msg_str = {"role": "system", "content": "Initial prompt"}
    _append_to_system_message(msg_str, "Skill text", separator="\n")
    assert msg_str["content"] == "Initial prompt\nSkill text"

    # 2. Anthropic prompt caching list of blocks
    msg_cached = {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "Base system prompt",
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }
    _append_to_system_message(msg_cached, "Skill prompt", separator="\n")
    assert isinstance(msg_cached["content"], list)
    assert len(msg_cached["content"]) == 1
    assert msg_cached["content"][0]["text"] == "Base system prompt\nSkill prompt"
    # cache_control must be preserved on the block
    assert msg_cached["content"][0]["cache_control"] == {"type": "ephemeral"}

    # 3. Vault memory appending to cached block
    _append_to_system_message(msg_cached, "\nVault memory context", separator="")
    assert msg_cached["content"][0]["text"] == "Base system prompt\nSkill prompt\nVault memory context"


@pytest.mark.asyncio
async def test_fallback_advances_chain_and_recovers_to_previous_model() -> None:
    """When a primary model fails and first fallback raises (e.g. 404),

    the fallback loop must continue to the next model or previous working model without crashing.
    """
    from unittest.mock import AsyncMock, MagicMock
    from main import process_input
    from model.registry.model_info import ModelInfo
    from config.constants import TaskType

    api_client = MagicMock()
    # First model (primary) fails in stream
    # Fallback #1 (e.g. 404) raises exception
    # Fallback #2 (previous model) returns valid response
    api_client.chat_completion_stream = MagicMock(side_effect=RuntimeError("Primary model failed"))
    api_client.chat_completion = AsyncMock(
        side_effect=[
            RuntimeError("Model 'ag/claude-opus-4-6-thinking' [404]: Requested entity was not found"),
            {
                "choices": [{"message": {"content": "Berhasil dijawab oleh model sebelumnya"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        ]
    )

    classifier = MagicMock()
    classifier.classify_with_difficulty.return_value = (
        MagicMock(task_type=TaskType.REASON, confidence=0.9), "medium"
    )

    router = MagicMock()
    router.route.return_value = ModelInfo(id="kg/claude-sonnet-4-6", display_name="Sonnet", provider="kg")
    router._default_model = "ag/gemini-3.8-flash-high"

    fb_model_1 = ModelInfo(id="ag/claude-opus-4-6-thinking", display_name="Opus", provider="ag")
    fallback = MagicMock()
    fallback._tried = {}
    fallback.get_next.side_effect = [fb_model_1, None]

    registry = MagicMock()
    prev_model = ModelInfo(id="kg/claude-haiku-5-5", display_name="Haiku", provider="kg")
    registry.get.side_effect = lambda mid: prev_model if mid == "kg/claude-haiku-5-5" else fb_model_1

    context_assembler = MagicMock()
    context_assembler.assemble.return_value = MagicMock(messages=[{"role": "user", "content": "hi"}])
    conversation_store = MagicMock()
    health_checker = MagicMock()

    res = await process_input(
        user_input="test query",
        session_id="test-fallback-sess",
        api_client=api_client,
        classifier=classifier,
        router=router,
        context_assembler=context_assembler,
        conversation_store=conversation_store,
        health_checker=health_checker,
        fallback=fallback,
        stream=True,
        registry=registry,
        previous_model="kg/claude-haiku-5-5",
    )

    assert "Berhasil dijawab oleh model sebelumnya" in res
    assert api_client.chat_completion.call_count == 2

