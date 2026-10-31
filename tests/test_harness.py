"""The execution harness, driven by a scripted fake model. No network."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from agent.agent_loop import AgentLoop
from agent.harness import Harness, Tier, playbooks, scaffold_for, tier_for
from agent.harness.guards import (
    LoopGuard,
    clip_text,
    compact_messages,
    normalize_arguments,
    repair_json_arguments,
    tool_failed,
)
from agent.harness.tool_select import select_tools
from agent.harness.verifier import changed_files, verify_turn
from config.constants import TaskType
from tool.registry.tool_registry import ToolRegistry
from tool.registry.tool_schema import ParamType, ToolParameter, ToolSchema


def reply(content: str = "", calls: list[tuple[str, Any]] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {
                "id": f"c{i}",
                "type": "function",
                "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)},
            }
            for i, (name, args) in enumerate(calls)
        ]
    return {
        "choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class FakeClient:
    """Plays back scripted responses and records every request."""

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


@pytest.fixture
def registry(tmp_path) -> ToolRegistry:
    ToolRegistry.reset_singleton() if hasattr(ToolRegistry, "reset_singleton") else None
    reg = ToolRegistry()
    reg._tools.clear()

    def write(path: str, content: str = "") -> dict[str, Any]:
        target = tmp_path / path
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path}

    def fail(path: str = "") -> dict[str, Any]:
        return {"ok": False, "error": "no such file"}

    reg.register(
        ToolSchema(
            name="file_write",
            description="write",
            parameters=[
                ToolParameter(name="path", param_type=ParamType.STRING, description="p", required=True),
                ToolParameter(name="content", param_type=ParamType.STRING, description="c", required=False),
            ],
        ),
        write,
    )
    reg.register(
        ToolSchema(
            name="read_file",
            description="read",
            parameters=[ToolParameter(name="file_path", param_type=ParamType.STRING, description="p", required=True)],
        ),
        fail,
    )
    return reg


def make_harness(registry: ToolRegistry, tmp_path, model: str = "ag/gemini-flash", **options: Any) -> Harness:
    schemas = registry.get_all_schemas()
    return Harness(
        scaffold=scaffold_for(model),
        tools=schemas,
        parameters={s["function"]["name"]: s["function"]["parameters"] for s in schemas},
        user_input="buat file",
        workspace=str(tmp_path),
        **options,
    )


async def run(client: FakeClient, registry: ToolRegistry, harness: Harness | None) -> Any:
    loop = AgentLoop(client, registry, harness=harness)  # type: ignore[arg-type]
    return await loop.run("ag/gemini-flash", [{"role": "user", "content": "x"}], stream=False)


# --- tiers ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "tier"),
    [
        ("ag/gemini-3.8-flash-high", Tier.LITE),
        ("kg/claude-haiku-5-5", Tier.LITE),
        ("bansos/anything", Tier.LITE),
        ("ag/claude-sonnet-4-6", Tier.STANDARD),
        ("kg/deepseek-v4-pro", Tier.STANDARD),
        ("ag/claude-opus-4-6-thinking", Tier.FRONTIER),
        ("vk/openai/gpt-6-sol", Tier.FRONTIER),
    ],
)
def test_tiers(model: str, tier: Tier) -> None:
    assert tier_for(model) == tier


def test_cheaper_tiers_get_more_supervision() -> None:
    lite, top = scaffold_for("x-flash"), scaffold_for("x-opus")
    assert lite.stuck_after < top.stuck_after
    assert lite.advisor_calls > top.advisor_calls
    assert lite.max_tools is not None and top.max_tools is None


# --- tool selection ------------------------------------------------------


def test_tool_selection_is_small_and_relevant() -> None:
    from tool.register_tools import register_all_tools

    schemas = register_all_tools().get_all_schemas()
    lite = scaffold_for("x-flash")
    names = [s["function"]["name"] for s in select_tools(schemas, TaskType.CODE, "perbaiki bug", lite)]
    assert len(names) <= 16
    assert {"read_file", "file_patch", "execute_command"} <= set(names)
    assert "obsidian_write" not in names

    wants_vault = [s["function"]["name"] for s in select_tools(schemas, TaskType.CODE, "simpan ke vault", lite)]
    assert "vault_write" in wants_vault

    assert len(select_tools(schemas, TaskType.CODE, "x", scaffold_for("x-opus"))) == len(schemas)


def test_disabled_tools_are_never_offered() -> None:
    schema = [{"function": {"name": "execute_command"}}, {"function": {"name": "read_file"}}]
    chosen = select_tools(schema, TaskType.CODE, "x", scaffold_for("x-flash"), frozenset({"execute_command"}))
    assert [s["function"]["name"] for s in chosen] == ["read_file"]


# --- guards --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('{"a": 1,}', {"a": 1}),
        ("{'a': 1}", {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Here you go: {"a": 1} done', {"a": 1}),
        ({"_raw": '{"a": 1,}'}, {"a": 1}),
        ("not json", None),
        ("[1, 2]", None),
    ],
)
def test_repair_json_arguments(raw: Any, expected: Any) -> None:
    assert repair_json_arguments(raw) == expected


SCHEMA = {
    "properties": {"file_path": {"type": "string"}, "limit": {"type": "integer"}, "deep": {"type": "boolean"}},
    "required": ["file_path"],
}


def test_normalize_fixes_aliases_types_and_unknown_args() -> None:
    args, error, repaired = normalize_arguments({"path": "a.py", "limit": "5", "deep": "true", "junk": 1}, SCHEMA)
    assert error is None and repaired
    assert args == {"file_path": "a.py", "limit": 5, "deep": True}


def test_normalize_explains_missing_arguments() -> None:
    _, error, _ = normalize_arguments({}, SCHEMA)
    assert error and "file_path" in error and "Expected" in error


def test_clip_keeps_head_and_tail() -> None:
    clipped = clip_text("A" * 500 + "B" * 500 + "ERROR", 100)
    assert clipped.startswith("A") and clipped.endswith("ERROR") and "omitted" in clipped


def test_compact_drops_oldest_tool_results_only() -> None:
    messages: list[dict[str, Any]] = [{"role": "user", "content": "task"}]
    messages += [{"role": "tool", "content": "x" * 1000} for _ in range(10)]
    dropped = compact_messages(messages, max_chars=5000, keep_recent=2)
    assert dropped > 0
    assert messages[0]["content"] == "task"
    assert messages[-1]["content"] == "x" * 1000
    assert "removed" in messages[1]["content"]


def test_tool_failed_sees_soft_failures() -> None:
    assert tool_failed({"success": True, "result": {"ok": False, "error": "nope"}}) == "nope"
    assert tool_failed({"success": False, "error": "boom"}) == "boom"
    assert tool_failed({"success": True, "result": {"ok": True}}) is None


def test_loop_guard_spots_repeats_and_failures() -> None:
    guard = LoopGuard(stuck_after=2)
    assert guard.observe("a", {"x": 1}, None) is None
    assert guard.observe("a", {"x": 1}, None) is None
    assert "sama" in (guard.observe("a", {"x": 1}, None) or "")
    assert guard.needs_help

    failing = LoopGuard(stuck_after=2)
    assert failing.observe("a", {"x": 1}, "e1") is None
    assert "gagal" in (failing.observe("b", {"x": 2}, "e2") or "")


# --- verifier ------------------------------------------------------------


def write_entry(path: str, ok: bool = True) -> dict[str, Any]:
    return {
        "tool_call": {"name": "file_write", "arguments": {"path": path}},
        "result": {"success": True, "result": {"ok": ok, "error": "disk full"}},
    }


async def test_verifier_catches_broken_python(tmp_path) -> None:
    (tmp_path / "bad.py").write_text("def broken(:\n")
    issues, _ = await verify_turn("done", [write_entry("bad.py")], str(tmp_path), True)
    assert any("bad.py" in issue for issue in issues)


async def test_verifier_passes_good_files(tmp_path) -> None:
    (tmp_path / "ok.py").write_text("x = 1\n")
    (tmp_path / "ok.json").write_text("{}")
    history = [write_entry("ok.py"), write_entry("ok.json")]
    issues, passed = await verify_turn("done", history, str(tmp_path), True)
    assert issues == [] and passed


@pytest.mark.parametrize("claim", ["Sudah saya buat file itu.", "File berhasil dibuat.", "I have created the file."])
async def test_verifier_catches_claims_without_work(tmp_path, claim: str) -> None:
    issues, _ = await verify_turn(claim, [], str(tmp_path), True)
    assert any("no tool call" in issue for issue in issues)


async def test_verifier_ignores_plain_answers(tmp_path) -> None:
    assert (await verify_turn("Ibu kota Prancis adalah Paris.", [], str(tmp_path), True))[0] == []


async def test_verifier_skips_claim_check_when_no_tools_were_offered(tmp_path) -> None:
    assert (await verify_turn("Sudah saya buat.", [], str(tmp_path), False))[0] == []


async def test_verifier_catches_success_claim_after_failed_write(tmp_path) -> None:
    issues, _ = await verify_turn("File berhasil dibuat.", [write_entry("a.py", ok=False)], str(tmp_path), True)
    assert any("failed" in issue for issue in issues)


def test_changed_files_ignores_failed_writes(tmp_path) -> None:
    assert changed_files([write_entry("a.py", ok=False)], str(tmp_path)) == []
    assert changed_files([write_entry("a.py")], str(tmp_path)) == [tmp_path / "a.py"]


# --- loop integration ----------------------------------------------------


async def test_without_harness_no_tools_are_sent(registry) -> None:
    client = FakeClient(reply("hello"))
    await run(client, registry, None)
    assert "tools" not in client.requests[0]


async def test_with_harness_tools_are_sent_and_run(registry, tmp_path) -> None:
    client = FakeClient(reply("", [("file_write", {"path": "a.py", "content": "x = 1\n"})]), reply("Selesai."))
    result = await run(client, registry, make_harness(registry, tmp_path))
    assert client.requests[0]["tools"]
    assert (tmp_path / "a.py").read_text() == "x = 1\n"
    assert result.content == "Selesai."


async def test_malformed_arguments_are_repaired(registry, tmp_path) -> None:
    harness = make_harness(registry, tmp_path)
    client = FakeClient(reply("", [("file_write", '{"file_path": "a.py", "text": "x = 1\\n",}')]), reply("ok"))
    await run(client, registry, harness)
    assert (tmp_path / "a.py").exists()
    assert harness.stats.repairs >= 1


async def test_broken_output_goes_back_to_the_model(registry, tmp_path) -> None:
    resets: list[int] = []
    harness = make_harness(registry, tmp_path, on_reset=lambda: resets.append(1))
    client = FakeClient(
        reply("", [("file_write", {"path": "a.py", "content": "def broken(:\n"})]),
        reply("Selesai."),
        reply("", [("file_write", {"path": "a.py", "content": "def fine():\n    pass\n"})]),
        reply("Sekarang benar."),
    )
    result = await run(client, registry, harness)
    assert result.content == "Sekarang benar."
    assert harness.stats.verify_rounds == 1
    assert harness.stats.unresolved == []
    assert resets
    feedback = str(client.requests[2]["messages"][-1]["content"])
    assert "a.py" in feedback


async def test_unfixed_problems_are_reported_honestly(registry, tmp_path) -> None:
    harness = make_harness(registry, tmp_path)
    harness.scaffold = scaffold_for("x-opus")  # one verify round
    client = FakeClient(
        reply("", [("file_write", {"path": "a.py", "content": "def broken(:\n"})]),
        reply("Selesai."),
        reply("Masih selesai."),
    )
    result = await run(client, registry, harness)
    assert harness.stats.unresolved
    assert "Belum terverifikasi" in result.notes


async def test_stuck_run_consults_the_advisor(registry, tmp_path) -> None:
    harness = make_harness(registry, tmp_path, advisor_model="ag/opus")
    failing = reply("", [("read_file", {"file_path": "missing.py"})])
    advice = reply("Periksa path-nya, file ada di src/.")
    client = FakeClient(failing, failing, advice, reply("Saya berhenti: berkas tidak ada."))
    result = await run(client, registry, harness)
    assert harness.stats.advisor_calls == 1
    assert client.requests[2]["model"] == "ag/opus"
    assert "tools" not in client.requests[2]
    assert "Periksa path-nya" in str(client.requests[3]["messages"][-1]["content"])
    assert result.content.startswith("Saya berhenti")


async def test_run_escalates_once_advice_is_used_up(registry, tmp_path) -> None:
    harness = make_harness(registry, tmp_path, advisor_model="ag/opus")
    harness.scaffold = scaffold_for("x-sonnet")  # one advisor call, stuck after 3
    failing = [reply("", [("read_file", {"file_path": f"m{i}.py"})]) for i in range(6)]
    client = FakeClient(*failing[:3], reply("hint"), *failing[3:], reply("Sudah dicoba model kuat."))
    result = await run(client, registry, harness)
    assert harness.stats.escalated_to == "ag/opus"
    assert client.requests[-1]["model"] == "ag/opus"
    assert result.model == "ag/opus"


async def test_endless_repeats_are_cut_off(registry, tmp_path) -> None:
    harness = make_harness(registry, tmp_path)
    same = reply("", [("read_file", {"file_path": "x.py"})])
    client = FakeClient(*[same] * 6, reply("Berhenti."))
    result = await run(client, registry, harness)
    assert result.content == "Berhenti."
    assert "tools" not in client.requests[-1]


async def test_budget_runs_out_with_an_answer_not_silence(registry, tmp_path) -> None:
    harness = make_harness(registry, tmp_path)
    calls = [reply("", [("file_write", {"path": f"f{i}.txt", "content": "x"})]) for i in range(40)]
    limit = harness.scaffold.max_iterations + 3
    client = FakeClient(*calls[: limit - 1], reply("Ringkasan pekerjaan."))
    result = await run(client, registry, harness)
    assert result.content == "Ringkasan pekerjaan."
    assert "tools" not in client.requests[-1]


async def test_disabled_tool_is_refused_even_if_named_directly(registry, tmp_path) -> None:
    harness = make_harness(registry, tmp_path, disabled_tools=frozenset({"file_write"}))
    client = FakeClient(reply("", [("file_write", {"path": "a.py", "content": "x"})]), reply("Tidak bisa."))
    await run(client, registry, harness)
    assert not (tmp_path / "a.py").exists()


async def test_text_tool_calls_become_real_calls(registry, tmp_path) -> None:
    text = 'Saya tulis.\n```tool:file_write\n{"path": "t.txt", "content": "hi"}\n```'
    harness = make_harness(registry, tmp_path)
    client = FakeClient(reply(text), reply("ok"))
    await run(client, registry, harness)
    assert (tmp_path / "t.txt").exists()


async def test_tool_events_are_emitted(registry, tmp_path) -> None:
    events: list[dict[str, Any]] = []
    harness = make_harness(registry, tmp_path, on_event=events.append)
    client = FakeClient(reply("", [("file_write", {"path": "a.txt", "content": "x"})]), reply("ok"))
    await run(client, registry, harness)
    phases = [(e.get("name"), e.get("phase")) for e in events if e.get("type") == "tool"]
    assert phases == [("file_write", "start"), ("file_write", "end")]


# --- playbooks -----------------------------------------------------------


def history(*names: str, error: str | None = None) -> list[dict[str, Any]]:
    entries = [
        {"tool_call": {"name": n, "arguments": {"path": f"{n}.py"}}, "result": {"success": True, "result": {"ok": True}}}
        for n in names
    ]
    if error:
        entries.insert(0, {"tool_call": {"name": "read_file", "arguments": {"file_path": "x.py"}}, "result": {"success": False, "error": error}})
    return entries


@pytest.fixture
def vault(tmp_path) -> Any:
    root = tmp_path / "vault"
    root.mkdir()
    return root


async def test_short_runs_are_not_recorded(vault) -> None:
    assert not await playbooks.record_run("deploy aplikasi flask", "code", "m", history("a"), vault=vault)


async def test_playbook_round_trip_and_merge(vault, monkeypatch) -> None:
    monkeypatch.setattr(playbooks.vault_context, "global_vault_path", lambda *a, **k: vault)
    monkeypatch.setattr(
        playbooks.vault_tool, "_vault", lambda workspace_path=None, scope="session": (vault, {}), raising=False
    )
    task = "deploy aplikasi flask ke docker container"
    assert await playbooks.record_run(task, "code", "m", history("a", "b", "c", error="port dipakai"), vault=vault)

    found = playbooks.find_relevant("cara deploy aplikasi flask ke docker", "code", vault=vault)
    assert len(found) == 1
    assert found[0]["steps"]
    assert any("port dipakai" in p for p in found[0]["pitfalls"])

    assert await playbooks.record_run(task, "code", "m2", history("a", "b", "c"), vault=vault)
    merged = playbooks.find_relevant(task, "code", vault=vault)
    assert len(list((vault / "50-playbooks").glob("*.md"))) == 1
    assert merged[0]["runs"] == 2
    assert any("port dipakai" in p for p in merged[0]["pitfalls"])


def test_unrelated_requests_match_nothing(vault) -> None:
    assert playbooks.find_relevant("resep rendang padang", "activity", vault=vault) == []


def test_prompt_rendering_is_bounded_and_marked_as_data() -> None:
    note = {"title": "t", "runs": 2, "steps": ["a"] * 50, "pitfalls": ["p" * 200] * 8}
    text = playbooks.render_for_prompt([note], 300)
    assert len(text) <= 300
    assert "not orders" in text


# --- provider failures in the middle of a run ------------------------------


async def test_reasoning_lockout_is_waited_out_with_a_bigger_budget(registry, tmp_path, monkeypatch) -> None:
    from agent import agent_loop
    from utils.exceptions import ReasoningBudgetError

    waits: list[float] = []

    async def no_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(agent_loop.asyncio, "sleep", no_sleep)
    budgets: list[int] = []
    outcomes: list[Any] = [ReasoningBudgetError("empty", retry_after=29), reply("Jawaban.")]

    class Client:
        async def chat_completion(self, **kwargs: Any) -> dict[str, Any]:
            budgets.append(kwargs["max_tokens"])
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    harness = make_harness(registry, tmp_path)
    result = await AgentLoop(Client(), registry, harness=harness).run(  # type: ignore[arg-type]
        "m", [{"role": "user", "content": "x"}], max_tokens=100, stream=False
    )
    assert result.content == "Jawaban."
    assert budgets == [100, 200]
    assert waits == [29]


async def test_failure_after_tool_work_keeps_the_result(registry, tmp_path) -> None:
    """Regression: the work was done, yet the user only saw an error."""
    outcomes: list[Any] = [reply("", [("file_write", {"path": "a.txt", "content": "x"})]), RuntimeError("502")]

    class Client:
        async def chat_completion(self, **_kwargs: Any) -> dict[str, Any]:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    harness = make_harness(registry, tmp_path)
    result = await AgentLoop(Client(), registry, harness=harness).run(  # type: ignore[arg-type]
        "m", [{"role": "user", "content": "x"}], stream=False
    )
    assert (tmp_path / "a.txt").exists()
    assert result.finish_reason == "error"
    assert "Belum selesai" in result.notes
    assert harness.stats.unresolved


async def test_failure_after_tool_work_hands_over_to_the_stronger_model(registry, tmp_path) -> None:
    models: list[str] = []
    outcomes: list[Any] = [
        reply("", [("file_write", {"path": "a.txt", "content": "x"})]), RuntimeError("502"), reply("Selesai."),
    ]

    class Client:
        async def chat_completion(self, **kwargs: Any) -> dict[str, Any]:
            models.append(kwargs["model"])
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    harness = make_harness(registry, tmp_path, advisor_model="ag/opus")
    result = await AgentLoop(Client(), registry, harness=harness).run(  # type: ignore[arg-type]
        "m", [{"role": "user", "content": "x"}], stream=False
    )
    assert result.content == "Selesai."
    assert models == ["m", "m", "ag/opus"]


def test_topic_tools_do_not_push_out_the_ability_to_run_things() -> None:
    """Regression: mentioning memory dropped execute_command and run_checks."""
    from tool.register_tools import register_all_tools

    schemas = register_all_tools().get_all_schemas()
    names = {
        s["function"]["name"]
        for s in select_tools(schemas, TaskType.CODE, "simpan ke memori pakai memory_remember", scaffold_for("x-flash"))
    }
    assert {"execute_command", "run_checks", "read_file", "file_patch", "memory_remember"} <= names


# --- shared budgets ------------------------------------------------------


async def test_out_of_budget_run_writes_its_result_instead_of_continuing(registry, tmp_path) -> None:
    from agent.harness import RunLimits

    client = FakeClient(
        reply(calls=[("file_write", {"path": "a.txt", "content": "x"})]),
        reply("hasil sejauh ini"),
    )
    limits = RunLimits(max_rounds=10, tokens=1, deadline=time.time() + 60)
    harness = make_harness(registry, tmp_path, limits=limits)
    result = await run(client, registry, harness)
    assert result.content == "hasil sejauh ini"
    assert harness.stats.out_of_budget
    assert "tools" not in client.requests[-1] or not client.requests[-1]["tools"]
    assert "Jangan panggil tool" in str(client.requests[-1]["messages"][-1]["content"])


async def test_past_the_deadline_the_first_answer_is_final(registry, tmp_path) -> None:
    from agent.harness import RunLimits

    client = FakeClient(reply("cepat"))
    harness = make_harness(registry, tmp_path, limits=RunLimits(10, 100_000, deadline=0.0))
    assert (await run(client, registry, harness)).content == "cepat"
    assert len(client.requests) == 1


def test_limits_shrink_every_tier_and_turn_off_the_advisor() -> None:
    from agent.harness import RunLimits

    limits = RunLimits(max_rounds=10, tokens=1, deadline=0.0)
    for model in ("x-flash", "ag/claude-sonnet-4-6", "vk/openai/gpt-6-sol"):
        scaffold = limits.apply(scaffold_for(model))
        assert scaffold.max_iterations == 10
        assert scaffold.context_chars <= 24_000
        assert scaffold.max_tools == 10
        assert scaffold.advisor_calls == 0
