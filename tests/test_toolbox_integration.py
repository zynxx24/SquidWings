"""The toolbox inside the agent: which tools each tier is offered, find_tools mid-run, argument repair, skills."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent.agent_loop import AgentLoop
from agent.harness import Harness, scaffold_for
from agent.harness.guards import normalize_arguments
from agent.harness.tool_select import select_tools, toolbox_hint
from config.constants import TaskType
from tool.execute.execute_toolbox import CATALOG, deferred_names
from tool.register_tools import register_all_tools
from tool.registry.tool_registry import WORKSPACE_TOOLS, ToolRegistry, is_read_only


@pytest.fixture(scope="module")
def schemas() -> list[dict[str, Any]]:
    return register_all_tools().get_all_schemas()


def names(tools: list[dict[str, Any]]) -> set[str]:
    return {t["function"]["name"] for t in tools}


@pytest.fixture()
def session(tmp_path, monkeypatch):
    """An active session workspace, as the web server sets one per chat."""
    from utils import workspace_context

    token = workspace_context._active_workspace.set(str(tmp_path))
    yield tmp_path
    workspace_context._active_workspace.reset(token)


# ── What each tier is offered ─────────────────────────────────────────────────
@pytest.mark.parametrize("model", ["x-flash", "x-sonnet", "x-opus"])
def test_no_toolbox_tool_rides_along_on_an_ordinary_request(schemas, model):
    offered = names(select_tools(schemas, TaskType.CODE, "perbaiki bug login", scaffold_for(model)))
    assert not offered & deferred_names()


@pytest.mark.parametrize("model", ["x-flash", "x-sonnet"])
def test_small_models_always_get_find_tools_within_their_limit(schemas, model):
    scaffold = scaffold_for(model)
    for task in (TaskType.CODE, TaskType.ACTIVITY, TaskType.REASON, TaskType.SCHOOL_TASK):
        offered = select_tools(schemas, task, "tolong bantu", scaffold)
        assert "find_tools" in names(offered) and len(offered) <= scaffold.max_tools


@pytest.mark.parametrize(("model", "request_text", "expected"), [
    ("x-flash", "berapa cicilan kpr 500 juta bunga 7% 20 tahun", "loan_payment"),
    ("x-flash", "apotek terdekat dari monas", "osm_nearby"),
    ("x-sonnet", "cuaca bandung besok", "weather"),
    ("x-opus", "kurs dollar ke rupiah hari ini", "currency_convert"),
    ("x-opus", "ubah 1250000 jadi terbilang", "terbilang"),
])
def test_the_request_brings_its_tool_on_every_tier(schemas, model, request_text, expected):
    assert expected in names(select_tools(schemas, TaskType.ACTIVITY, request_text, scaffold_for(model)))


def test_matched_tools_never_push_out_the_core_kit(schemas):
    offered = names(select_tools(schemas, TaskType.CODE, "hitung statistik rata-rata median csv json warna kontras",
                                 scaffold_for("x-flash")))
    assert {"read_file", "file_patch", "file_write", "execute_command"} <= offered


def test_math_requests_get_the_exact_calculator(schemas):
    offered = names(select_tools(schemas, TaskType.MATH, "berapa 17 x 23", scaffold_for("x-flash")))
    assert {"calc", "solve_equation"} <= offered


def test_a_named_hidden_tool_needs_its_whole_name(schemas):
    flash = scaffold_for("x-flash")
    assert "calc" in names(select_tools(schemas, TaskType.ACTIVITY, "pakai calc ya", flash))
    assert "calc" not in names(select_tools(schemas, TaskType.CODE, "fix calculateTotal()", scaffold_for("x-opus")))


def test_unlocked_tools_come_back_in_later_turns_of_the_same_chat(schemas, session):
    from utils import session_settings

    session_settings.unlock_tools(session, ["zakat_calc"])
    for model in ("x-flash", "x-opus"):
        assert "zakat_calc" in names(select_tools(schemas, TaskType.ACTIVITY, "lanjutkan", scaffold_for(model)))


def test_disabled_toolbox_tools_are_never_offered(schemas):
    offered = select_tools(schemas, TaskType.ACTIVITY, "cuaca bandung", scaffold_for("x-opus"),
                           disabled=frozenset({"weather", "find_tools"}))
    assert not {"weather", "find_tools"} & names(offered)


def test_the_hint_is_added_only_with_find_tools(schemas):
    offered = select_tools(schemas, TaskType.ACTIVITY, "x", scaffold_for("x-flash"))
    assert "find_tools" in toolbox_hint(offered) and "hidden" in toolbox_hint(offered)
    assert toolbox_hint([]) == ""


def test_workspace_path_stays_hidden_from_the_model(schemas):
    offered = select_tools(schemas, TaskType.ACTIVITY, "rekap csv penjualan per kota", scaffold_for("x-flash"))
    table = next(t for t in offered if t["function"]["name"] == "table_query")
    assert "workspace_path" not in table["function"]["parameters"]["properties"]


def test_registry_metadata_covers_the_toolbox():
    register_all_tools()
    assert WORKSPACE_TOOLS["gmaps_import"] == "workspace_path" and WORKSPACE_TOOLS["table_query"] == "workspace_path"
    assert is_read_only("calc") and is_read_only("find_tools") and not is_read_only("table_query")
    assert not is_read_only("weather")


@pytest.mark.parametrize(("alias", "target"), [
    ("calculator", "calc"), ("hitung", "calc"), ("currency", "currency_convert"), ("google_maps", "places_search"),
    ("functions.unit_conversion", "unit_convert"), ("tool_search", "find_tools"), ("Word-Count", "text_stats"),
])
def test_invented_names_resolve(alias, target):
    assert register_all_tools().resolve_name(alias) == target


def test_an_unknown_tool_points_at_find_tools():
    from utils.exceptions import ToolError

    with pytest.raises(ToolError, match="find_tools"):
        asyncio.run(register_all_tools().execute("compute_mortgage_thing", {}))


def test_registry_runs_a_toolbox_tool_through_the_guard():
    out = asyncio.run(register_all_tools().execute("calc", {"expression": "2^8"}))
    assert out["ok"] and out["result"] == 256


# ── find_tools during a run ───────────────────────────────────────────────────
def _reply(content: str = "", calls: list[tuple[str, Any]] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [{"id": f"c{i}", "type": "function",
                                  "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


class _Client:
    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        # Copy the tool list: the harness grows the same list object during the run.
        self.requests.append({**kwargs, "tools": list(kwargs.get("tools") or [])})
        return self.responses.pop(0)


def test_find_tools_offers_what_it_unlocked_from_the_next_request_on(schemas, session):
    registry = register_all_tools()
    scaffold = scaffold_for("x-flash")
    tools = select_tools(schemas, TaskType.ACTIVITY, "tolong bantu", scaffold)
    assert "loan_payment" not in names(tools)
    harness = Harness(scaffold=scaffold, tools=tools,
                      parameters={s["function"]["name"]: s["function"]["parameters"] for s in schemas},
                      user_input="tolong bantu", workspace=str(session))
    client = _Client(
        _reply(calls=[("find_tools", {"query": "cicilan pinjaman bunga"})]),
        _reply(calls=[("loan_payment", {"principal": "100000000", "annual_rate": "12", "months": "12"})]),
        _reply("Cicilan per bulan sekitar Rp8,88 juta."),
    )
    result = asyncio.run(AgentLoop(client, registry, harness=harness).run(  # type: ignore[arg-type]
        "prov/x-flash", [{"role": "user", "content": "x"}], stream=False))
    second_offer = names(client.requests[1]["tools"])
    assert "loan_payment" in second_offer and "loan_payment" not in names(client.requests[0]["tools"])
    loan = result.tool_calls_history[1]["result"]
    assert loan["success"] and loan["result"]["first_payment"] == pytest.approx(8_884_878.82, abs=0.05)
    assert len(harness.tools) <= scaffold.max_tools + 8


def test_a_toolbox_tool_called_without_being_offered_still_runs(schemas, session):
    registry = register_all_tools()
    scaffold = scaffold_for("x-flash")
    harness = Harness(scaffold=scaffold, tools=select_tools(schemas, TaskType.ACTIVITY, "x", scaffold),
                      parameters={s["function"]["name"]: s["function"]["parameters"] for s in schemas},
                      user_input="x", workspace=str(session))
    client = _Client(_reply(calls=[("terbilang", {"value": "2026"})]), _reply("Dua ribu dua puluh enam."))
    result = asyncio.run(AgentLoop(client, registry, harness=harness).run(  # type: ignore[arg-type]
        "prov/x-flash", [{"role": "user", "content": "x"}], stream=False))
    call = result.tool_calls_history[0]["result"]
    # "value" is not a parameter of terbilang; it is renamed to the one missing required argument.
    assert call["success"] and call["result"]["terbilang"] == "Dua ribu dua puluh enam"


def test_workspace_tools_get_the_session_folder_even_by_alias(session):
    (session / "jual.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    registry = register_all_tools()
    loop = AgentLoop(_Client(), registry)  # type: ignore[arg-type]
    out = asyncio.run(loop._execute_tool_call({"name": "read_csv", "arguments": {"path": "jual.csv"}}))
    assert out["success"] and out["result"]["rows"] == 1


# ── Argument repair ───────────────────────────────────────────────────────────
def _params(name: str) -> dict[str, Any]:
    return CATALOG[name].schema().to_openai_schema()["function"]["parameters"]


@pytest.mark.parametrize(("name", "given", "expected"), [
    ("stats_describe", {"values": "[1, 2, 3]"}, {"values": [1, 2, 3]}),
    ("stats_describe", {"values": "1, 2, 3"}, {"values": ["1", "2", "3"]}),
    ("phone_id", {"numbers": "0812345678"}, {"numbers": ["0812345678"]}),
    ("geometry", {"shape": "circle", "dims": '{"radius": 7}'}, {"shape": "circle", "dims": {"radius": 7}}),
    ("loan_payment", {"principal": "1.250.000", "annual_rate": "7,5", "months": "12.0"},
     {"principal": 1250000.0, "annual_rate": 7.5, "months": 12}),
    ("tax_vat", {"amount": 100, "included": "ya"}, {"amount": 100, "included": True}),
    ("terbilang", {"number": 2026}, {"number": "2026"}),
    ("calc", {"expr": "1+1"}, {"expression": "1+1"}),
])
def test_normalize_arguments_fixes_small_model_mistakes(name, given, expected):
    fixed, problem, _ = normalize_arguments(given, _params(name))
    assert problem is None and fixed == expected


def test_two_unknown_arguments_are_not_guessed():
    fixed, problem, _ = normalize_arguments({"a": 1, "b": 2}, _params("calc"))
    assert problem and "expression" in problem


# ── Skills: main ones first, the rest when the case calls for them ────────────
def test_code_tasks_only_list_code_and_general_skills():
    from brain import skills

    index = skills.index_text([], "code")
    assert "fix-bug" in index and "teman-curhat" not in index and "jawab-tugas" not in index
    assert "google-maps" not in index and "animate" not in index


def test_google_maps_skill_is_dormant_until_its_context_appears():
    from brain import skills

    assert "google-maps" not in skills.index_text([], "activity")
    assert skills.woken("tolong rekap hasil scrape google maps klinik gigi") == ["google-maps"]
    for text in ("fix source maps webpack error", "jarak antar tombol css", "lokasi file config dimana", "in place sort"):
        assert "google-maps" not in skills.woken(text)


def test_an_awake_google_maps_skill_brings_its_tools(schemas, session):
    from agent.harness.briefing import skill_guidance

    text = "tolong rekap hasil scrape google maps klinik gigi di bandung"
    guide, used = skill_guidance(text, TaskType.ACTIVITY, scaffold_for("x-flash").skill_chars)
    assert "google-maps" in used and "gmaps_import" in guide
    offered = names(select_tools(schemas, TaskType.ACTIVITY, "lanjutkan", scaffold_for("x-flash")))
    assert {"gmaps_import", "places_search"} <= offered


@pytest.mark.enterprise
def test_trading_skill_brings_the_risk_calculators(schemas):
    offered = names(select_tools(schemas, TaskType.ACTIVITY, "analisa posisi xauusd di akun mt4 saya", scaffold_for("x-flash")))
    assert {"trading_snapshot", "position_size", "risk_reward"} <= offered


def test_load_skill_does_not_reveal_dormant_skills(session):
    from tool.execute.execute_skills.skill_tool import load_skill

    out = asyncio.run(load_skill("nope"))
    assert out["ok"] is False and "google-maps" not in out["error"] and "fix-bug" in out["error"]
    assert asyncio.run(load_skill("Google_Maps"))["ok"] is True


def test_every_tool_a_skill_names_exists():
    from brain import skills

    registry = register_all_tools()
    for skill in skills.load_all().values():
        for tool in skill.tools:
            assert registry.has_tool(tool), f"{skill.name} names missing tool {tool}"


def test_registry_singleton_survives_repeat_registration():
    a = register_all_tools()
    count = a.tool_count
    b = register_all_tools(ToolRegistry())
    assert b.tool_count == count
