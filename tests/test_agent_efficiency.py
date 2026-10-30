"""Routing, context, provider, and tool behaviour found broken in the agent audit."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agent.harness.guards import normalize_arguments, tool_trace
from agent.harness.tiers import scaffold_for
from agent.harness.tool_select import select_tools
from agent.orchestrator import Orchestrator
from agent.pipeline_executor import PipelineExecutor
from brain.classifier.intent_classifier import IntentClassifier
from brain.context.context_assembler import ContextAssembler
from brain.memory.agent_working_memory import AgentWorkingMemory
from brain.memory.conversation_store import ConversationStore
from config.constants import TaskType
from model.connection.api_client import APIClient
from model.connection.providers import Provider, load_providers
from model.type.activity import ActivityHandler
from tool.register_tools import register_all_tools
from tool.registry.tool_registry import READ_ONLY_TOOLS, WORKSPACE_TOOLS, ToolRegistry
from tool.registry.tool_schema import ParamType, ToolParameter, ToolSchema

classifier = IntentClassifier()


@pytest.mark.parametrize(
    "text",
    ["saya suka sapi", "terapi untuk sakit kepala", "bagaimana kabarmu", "resep rendang padang"],
)
def test_everyday_words_are_not_code(text: str) -> None:
    assert classifier.classify(text).task_type != TaskType.CODE


@pytest.mark.parametrize(
    "text",
    ["buatkan file python hello.py", "perbaiki bug login di auth.py", "ubah README.md", "jalankan test", "git status dong"],
)
def test_file_and_dev_requests_are_code(text: str) -> None:
    assert classifier.classify(text).task_type == TaskType.CODE


@pytest.mark.parametrize("text", ["menghitung luas lingkaran", "hitung 25*4", "integral x dari 0 sampai 1"])
def test_math_survives_indonesian_affixes(text: str) -> None:
    assert classifier.classify(text).task_type == TaskType.MATH


def test_dates_are_not_arithmetic() -> None:
    assert classifier.classify("rapat tanggal 2026-10-30").task_type != TaskType.MATH


@pytest.mark.parametrize("text", ["saya terlalu lelah", "apa itu deepseek", "authentication flow nya gimana"])
def test_substrings_do_not_trigger_the_expensive_route(text: str) -> None:
    assert IntentClassifier.estimate_difficulty(text) == "easy"


def test_real_complexity_is_still_hard() -> None:
    assert IntentClassifier.estimate_difficulty("rancang arsitektur layanan ini") == "hard"
    assert IntentClassifier.estimate_difficulty("baca file lalu ringkas") == "medium"


async def test_short_chitchat_skips_the_llm_classifier() -> None:
    class Llm:
        calls = 0

        async def classify(self, _text: str) -> None:
            Llm.calls += 1

    await IntentClassifier(llm_classifier=Llm()).classify_async("ok makasih")  # type: ignore[arg-type]
    assert Llm.calls == 0


@pytest.mark.parametrize(
    ("text", "task", "difficulty", "expected"),
    [
        ("bagaimana kabarmu", TaskType.ACTIVITY, "easy", False),
        ("tolong carikan resep", TaskType.ACTIVITY, "easy", False),
        ("jelaskan closure", TaskType.CODE, "easy", False),
        ("berita terbaru hari ini", TaskType.ACTIVITY, "easy", True),
        ("analisis pasar", TaskType.REASON, "easy", False),
        ("analisis pasar", TaskType.REASON, "hard", True),
    ],
)
def test_only_research_and_real_reasoning_use_the_pipeline(text: str, task: TaskType, difficulty: str, expected: bool) -> None:
    assert Orchestrator._should_use_pipeline(None, text, task, difficulty) is expected  # type: ignore[arg-type]


async def test_pipeline_search_really_calls_web_search(tmp_path: Any) -> None:
    """Regression: the tool got one positional dict, so every search failed silently."""
    seen: list[Any] = []
    registry = ToolRegistry()

    async def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
        seen.append((query, max_results))
        return {"ok": True, "results": [{"title": "T", "url": "http://u", "snippet": "fact"}]}

    original = registry._tools.get("web_search")
    research = registry._tools.pop("web_research", None)
    registry.register(
        ToolSchema(
            name="web_search",
            description="s",
            parameters=[ToolParameter(name="query", param_type=ParamType.STRING, description="q", required=True)],
        ),
        web_search,
    )
    try:
        memory = AgentWorkingMemory(workspace_path=str(tmp_path))
        pipeline = PipelineExecutor(api_client=None, tool_registry=registry, working_memory=memory)  # type: ignore[arg-type]
        state = memory.get_or_create("s", "t", goal="g")
        state.search_queries = ["harga kopi"]
        await pipeline._run_search(state)
    finally:
        if original:
            registry._tools["web_search"] = original
        if research:
            registry._tools["web_research"] = research
    assert seen == [("harga kopi", 4)]
    assert state.web_results[0].snippet == "fact"


def assembled(store: ConversationStore, text: str = "pertanyaan baru") -> Any:
    assembler = ContextAssembler(conversation_store=store)
    return assembler.assemble(
        handler=ActivityHandler(), user_input=text, session_id="s",
        model_id="ag/x", context_window=1_000_000, model_max_output=8192,
    )


def test_old_messages_become_a_digest_instead_of_vanishing(tmp_path: Any) -> None:
    store = ConversationStore(storage_dir=tmp_path)
    store.add_message("s", "user", "KATA_KUNCI_LAMA tolong ingat ini")
    store.add_message("s", "assistant", "baik")
    for i in range(30):
        store.add_message("s", "user", f"pesan {i}")
        store.add_message("s", "assistant", f"balasan {i}")
    text = str(assembled(store).messages)
    assert "KATA_KUNCI_LAMA" in text
    assert "pesan 29" in text


def test_history_is_capped_on_huge_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("HISTORY_MAX_TOKENS", "2000")
    store = ConversationStore(storage_dir=tmp_path)
    for i in range(10):
        store.add_message("s", "user", f"pesan {i} " + "kata " * 1000)
        store.add_message("s", "assistant", "balasan " + "kata " * 1000)
    assert assembled(store).estimated_tokens < 5000


def test_next_turn_knows_what_tools_did(tmp_path: Any) -> None:
    store = ConversationStore(storage_dir=tmp_path)
    store.add_message("s", "assistant", "Selesai.", tool_trace="file_write app.py")
    assert "file_write app.py" in store.get_history_as_api_messages("s")[0]["content"]
    assert store.get_history("s")[0].content == "Selesai."  # the UI keeps the clean text
    assert ConversationStore(storage_dir=tmp_path).get_history("s")[0].tool_trace == "file_write app.py"


def test_tool_trace_summarises_and_marks_failures() -> None:
    history = [
        {"tool_call": {"name": "file_write", "arguments": {"path": "a.py"}}, "result": {"success": True, "result": {"ok": True}}},
        {"tool_call": {"name": "read_file", "arguments": {"file_path": "b.py"}}, "result": {"success": False, "error": "x"}},
    ]
    assert tool_trace(history) == "file_write a.py; read_file b.py (failed)"


# --- tools ---------------------------------------------------------------


def all_tools() -> ToolRegistry:
    return register_all_tools()


def test_workspace_argument_exists_on_every_workspace_tool() -> None:
    """Regression: graphify takes `workspace`, so injecting workspace_path crashed it."""
    registry = all_tools()
    for name, argument in WORKSPACE_TOOLS.items():
        if name not in registry._tools:  # graphify_* need a configured endpoint
            continue
        assert argument in inspect.signature(registry._tools[name].executor).parameters, name


def test_tool_schemas_match_their_functions() -> None:
    for name, entry in all_tools()._tools.items():
        parameters = inspect.signature(entry.executor).parameters
        schema = entry.schema.to_openai_schema()["function"]["parameters"]
        assert set(schema.get("properties", {})) <= set(parameters), name
        required = {k for k, p in parameters.items() if p.default is p.empty}
        assert required <= set(schema.get("required", [])), name


def test_read_only_list_names_real_tools() -> None:
    assert set(all_tools()._tools) >= READ_ONLY_TOOLS - {"graphify_query"}


def test_offered_schemas_hide_the_workspace_and_keep_a_stable_order() -> None:
    schemas = all_tools().get_all_schemas()
    scaffold = scaffold_for("x-flash")
    first = select_tools(schemas, TaskType.CODE, "ubah file", scaffold)
    second = select_tools(schemas, TaskType.CODE, "ubah file lagi", scaffold)
    assert [s["function"]["name"] for s in first] == [s["function"]["name"] for s in second]
    names = [s["function"]["name"] for s in first]
    assert names == sorted(names)
    for schema in first:
        assert "workspace_path" not in schema["function"]["parameters"]["properties"]


@pytest.mark.parametrize(
    ("tool", "sent", "expected"),
    [("file_search", "query", "pattern"), ("json_query", "query", "dotted_path"), ("vault_write", "content", "body")],
)
def test_common_wrong_argument_names_are_repaired(tool: str, sent: str, expected: str) -> None:
    schema = all_tools()._tools[tool].schema.to_openai_schema()["function"]["parameters"]
    arguments, _, repaired = normalize_arguments({sent: "x"}, schema)
    assert repaired and arguments.get(expected) == "x"


async def test_sync_tools_do_not_block_the_event_loop() -> None:
    registry = ToolRegistry()
    import threading

    main_thread = threading.get_ident()
    used: list[int] = []

    def sync_tool() -> str:
        used.append(threading.get_ident())
        return "ok"

    registry.register(ToolSchema(name="zz_sync_probe", description="d", parameters=[]), sync_tool)
    try:
        await registry.execute("zz_sync_probe", {})
    finally:
        del registry._tools["zz_sync_probe"]
    assert used and used[0] != main_thread


# --- providers -----------------------------------------------------------


def write_config(tmp_path: Any) -> Any:
    path = tmp_path / "providers.yaml"
    path.write_text(
        "providers:\n"
        "  - {id: fast, prefix: 'fast/', strip_prefix: true, base_url: 'http://fast.invalid/v1', api_key_env: FAST_KEY, max_concurrency: 1}\n"
        "  - {id: keep, prefix: 'keep/', base_url: 'http://keep.invalid/v1', api_key_env: KEEP_KEY}\n"
    )
    return path


def test_provider_config_loads_and_broken_config_is_harmless(tmp_path: Any) -> None:
    assert [p.id for p in load_providers(write_config(tmp_path))] == ["fast", "keep"]
    bad = tmp_path / "bad.yaml"
    bad.write_text(": : :")
    assert load_providers(bad) == []
    assert load_providers(tmp_path / "missing.yaml") == []


def client_with_providers(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> APIClient:
    monkeypatch.setenv("FAST_KEY", "k1")
    monkeypatch.setenv("KEEP_KEY", "k2")
    monkeypatch.setenv("PROVIDERS_CONFIG", str(write_config(tmp_path)))
    return APIClient("http://router.invalid/v1", "main-key")


def test_models_route_to_their_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    client = client_with_providers(monkeypatch, tmp_path)
    assert client.provider_of("ag/x") == "router"
    assert client.provider_of("fast/x") == "fast"
    assert client._route("fast/llama")[1] == "llama"       # prefix stripped
    assert client._route("keep/llama")[1] == "keep/llama"  # prefix kept
    assert client.provider_ids == ["router", "fast", "keep"]


def test_provider_without_a_key_is_inactive(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    client = client_with_providers(monkeypatch, tmp_path)
    monkeypatch.setenv("KEEP_KEY", "")
    assert client.provider_ids == ["router", "fast"]


async def test_requests_use_the_providers_own_url_and_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    client = client_with_providers(monkeypatch, tmp_path)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(url=str(request.url), body=request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = next(p for p in client._providers if p.id == "fast")
    mock = httpx.AsyncClient(base_url=provider.url, transport=httpx.MockTransport(handler))
    client._provider_clients["fast"] = (mock, provider.url, provider.key)
    await client.chat_completion(model="fast/llama", messages=[{"role": "user", "content": "x"}])
    assert seen["url"] == "http://fast.invalid/v1/chat/completions"
    assert client._client_for(Provider(id="other", base_url="http://o.invalid", api_key="k9")).headers["authorization"] == "Bearer k9"
    assert b'"model":"llama"' in seen["body"].replace(b" ", b"")


async def test_concurrency_limit_is_enforced(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    client = client_with_providers(monkeypatch, tmp_path)
    active = 0
    peak = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = next(p for p in client._providers if p.id == "fast")
    mock = httpx.AsyncClient(base_url=provider.url, transport=httpx.MockTransport(handler))
    client._provider_clients["fast"] = (mock, provider.url, provider.key)
    await asyncio.gather(*(client.chat_completion(model="fast/m", messages=[]) for _ in range(4)))
    assert peak == 1


async def test_sync_only_removes_models_of_the_provider_that_answered(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    client = client_with_providers(monkeypatch, tmp_path)

    class Registry:
        def __init__(self) -> None:
            self.ids = {"ag/old", "ag/new", "fast/kept", "keep/kept"}

        @property
        def model_ids(self) -> list[str]:
            return list(self.ids)

        def unregister(self, model_id: str) -> None:
            self.ids.discard(model_id)

        def register_from_api_models(self, items: list[dict[str, Any]]) -> None:
            self.ids.update(m["id"] for m in items)

    async def router_models() -> list[dict[str, Any]]:
        return [{"id": "ag/new"}]

    async def down(_provider: Provider) -> list[dict[str, Any]]:
        raise RuntimeError("down")

    client.list_models = router_models  # type: ignore[method-assign]
    client.list_provider_models = down  # type: ignore[method-assign]
    registry = Registry()
    counts = await client.sync_all_models(registry)
    assert registry.ids == {"ag/new", "fast/kept", "keep/kept"}
    assert counts == {"router": 1, "fast": 0, "keep": 0}


def test_billing_failure_cools_down_only_that_provider() -> None:
    marked: list[str] = []
    orch = SimpleNamespace(
        _api_client=SimpleNamespace(provider_ids=["a", "b"], provider_of=lambda m: m.split("/")[0]),
        _registry=SimpleNamespace(get_all=lambda: [SimpleNamespace(id=i) for i in ("a/1", "a/2", "b/1")]),
        _health_checker=SimpleNamespace(mark_unavailable=lambda m, _s: marked.append(m)),
    )
    assert Orchestrator._cool_down_provider(orch, SimpleNamespace(id="a/1")) is True  # type: ignore[arg-type]
    assert marked == ["a/1", "a/2"]


def test_billing_failure_with_one_provider_has_no_alternative() -> None:
    orch = SimpleNamespace(_api_client=SimpleNamespace(provider_ids=["a"]))
    assert Orchestrator._cool_down_provider(orch, SimpleNamespace(id="a/1")) is False  # type: ignore[arg-type]


def _response(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message}})


def test_router_lockout_becomes_a_retryable_error() -> None:
    from utils.exceptions import RateLimitError, ReasoningBudgetError

    client = APIClient("http://router.invalid/v1", "key")
    with pytest.raises(ReasoningBudgetError) as reasoning:
        client._handle_error(_response(502, "spent its entire max_tokens budget on reasoning (reset after 29s)"), "m")
    assert reasoning.value.retry_after == 29
    with pytest.raises(RateLimitError) as locked:
        client._handle_error(_response(503, "model unavailable (reset after 12s)"), "m")
    assert locked.value.retry_after == 12
