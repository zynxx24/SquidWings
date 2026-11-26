"""Flows that run exactly along the drawn lines (n8n style), the Data table node, relations, and old teams as flows."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from brain.agent import team_flow
from brain.agent.orchestrator import AgentResponse
from brain.agent.team_orchestrator import TeamOrchestrator, TeamStore
from brain.config.constants import ResponseStatus
from brain.utils.datastore import registry, table_ops


class FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.registry = SimpleNamespace(get=lambda _id: object())

    async def process(self, **kwargs: Any) -> AgentResponse:
        self.calls.append(kwargs)
        return AgentResponse(content=f"hasil {kwargs['session_id'].split('-')[-2]}", status=ResponseStatus.SUCCESS)


def agent(id_: str, *after: str) -> dict[str, Any]:
    return {"id": id_, "name": id_, "role": "r", "model_id": "m", "depends_on": list(after)}


def node(id_: str, kind: str, config: dict[str, Any], *after: str) -> dict[str, Any]:
    return {"id": id_, "name": id_, "kind": kind, "config": config, "depends_on": list(after)}


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(registry, "DATA_DIR", tmp_path / "datastore")
    return tmp_path / "datastore"


def make(tmp_path: Path) -> tuple[TeamOrchestrator, FakeOrchestrator]:
    fake = FakeOrchestrator()
    return TeamOrchestrator(fake, TeamStore(tmp_path / "teams")), fake  # type: ignore[arg-type]


async def finish(manager: TeamOrchestrator, team_id: str, task: str = "tugas") -> Any:
    run = await manager.start_run(team_id, task)
    await asyncio.wait_for(manager.run_tasks[run.id], timeout=5)
    return manager.store.get_run(run.id)


def shop(data_dir: Path) -> None:
    """Products and orders in the built-in Tabel data, an order pointing at a product by code."""
    source = registry.ensure_builtin()
    backend = registry.backend_for(source)
    backend.create_table("produk", ["kode", "nama", "harga"])
    backend.create_table("pesanan", ["kode_produk", "jumlah"])
    backend.insert("produk", {"kode": "KS-1", "nama": "Kaos", "harga": "75000"})
    backend.insert("produk", {"kode": "TB-2", "nama": "Tote", "harga": "55000"})
    backend.insert("pesanan", {"kode_produk": "KS-1", "jumlah": "2"})
    backend.insert("pesanan", {"kode_produk": "KS-1", "jumlah": "1"})


# --- running along the lines ------------------------------------------------


@pytest.mark.asyncio
async def test_flow_runs_only_what_the_trigger_reaches(tmp_path: Path) -> None:
    manager, fake = make(tmp_path)
    team = manager.create_team("f", "", [agent("a"), agent("b", "a"), agent("lepas")], None,
                               style="flow", starts=["a"])
    assert team.lead_id == ""
    run = await finish(manager, team.id)
    assert run.status == "completed"
    assert run.agents["lepas"]["status"] == "skipped"
    assert run.result == "hasil b"
    # The first step reads the task, the next one reads what the first made.
    assert "[TEAM TASK]" in fake.calls[0]["workspace_context"]
    assert "hasil a" in fake.calls[1]["workspace_context"]
    assert "last step" in fake.calls[1]["user_input"]
    assert "next step" in fake.calls[0]["user_input"]


@pytest.mark.asyncio
async def test_flow_without_a_start_says_how_to_fix_it(tmp_path: Path) -> None:
    manager, _ = make(tmp_path)
    team = manager.create_team("f", "", [agent("a")], None, style="flow")
    with pytest.raises(ValueError, match="Pemicu"):
        await manager.start_run(team.id, "x")


@pytest.mark.asyncio
async def test_flow_result_is_the_last_step_and_needs_no_agent(tmp_path: Path) -> None:
    manager, _ = make(tmp_path)
    team = manager.create_team("f", "", [
        node("t", "set", {"text": "Halo {{input}}"}),
        node("u", "set", {"text": "{{input}}!"}, "t"),
    ], None, style="flow", starts=["t"], layout={"t": [10, 20], "__trigger": [0, 0], "ghost": [1, 1]})
    assert team.layout == {"t": [10.0, 20.0], "__trigger": [0.0, 0.0]}
    run = await finish(manager, team.id, task="dunia")
    assert run.result == "Halo dunia!"


def test_bad_node_is_named_in_the_error(tmp_path: Path) -> None:
    manager, _ = make(tmp_path)
    with pytest.raises(ValueError, match='Node "Ambil URL"'):
        manager.create_team("f", "", [{**node("h", "http", {"url": "ftp://x"}), "name": "Ambil URL"}], None, style="flow")


# --- old teams as flows ----------------------------------------------------------


def test_lead_and_reviewer_become_drawn_connections() -> None:
    members = [agent("riset"), agent("tulis", "riset"), agent("lead"), agent("cek"),
               node("kirim", "telegram", {}), node("log", "set", {"text": "x"}, "lead")]
    flow, starts = team_flow.to_flow(members, "lead", "cek")
    by = {m["id"]: m for m in flow}
    assert starts == ["riset"]
    assert by["lead"]["depends_on"] == ["tulis"]
    assert by["cek"]["depends_on"] == ["lead"]
    assert "jawaban final" in by["cek"]["instructions"]
    assert by["kirim"]["depends_on"] == ["cek"]
    # What followed the lead now follows the reviewer, which writes the final answer.
    assert by["log"]["depends_on"] == ["cek"]


# --- Data table node ---------------------------------------------------------------


def test_datatable_config_is_checked() -> None:
    check = team_flow.normalize_node_config
    with pytest.raises(ValueError, match="sumber"):
        check("datatable", {"operation": "get"}, ())
    with pytest.raises(ValueError, match="syarat"):
        check("datatable", {"source": "tabel", "table": "t", "operation": "delete"}, ())
    with pytest.raises(ValueError, match="pencocok"):
        check("datatable", {"source": "tabel", "table": "t", "operation": "upsert",
                            "values": [{"column": "a", "value": "1"}]}, ())
    kept = check("datatable", {"source": "tabel", "table": "t", "operation": "get", "limit": "5", "sort": "harga",
                               "where": [{"column": "a", "op": ">", "value": "1"}, {"column": "", "value": "x"}]}, ())
    assert kept == {"source": "tabel", "table": "t", "operation": "get", "where": [{"column": "a", "op": ">", "value": "1"}],
                    "combine": "and", "limit": 5, "sort": "harga", "desc": False}


def test_datatable_operations_on_builtin_table(data_dir: Path) -> None:
    shop(data_dir)

    def run(config: dict[str, Any], values: dict[str, str] | None = None) -> tuple[Any, str]:
        def fill(text: str) -> str:
            return team_flow.render_template(text, values or {})
        content, note = table_ops.run(team_flow.normalize_node_config("datatable", config, ()), fill,
                                      registry.open_backend, registry.get_relations())
        return json.loads(content), note

    rows, note = run({"source": "tabel", "table": "produk", "operation": "get", "sort": "harga", "desc": True})
    assert [r["kode"] for r in rows] == ["KS-1", "TB-2"]
    assert note.startswith("2 baris")
    rows, _ = run({"source": "tabel", "table": "produk", "operation": "get", "combine": "or",
                   "where": [{"column": "kode", "op": "=", "value": "TB-2"}, {"column": "nama", "op": "contains", "value": "kao"}]})
    assert len(rows) == 2
    row, _ = run({"source": "tabel", "table": "produk", "operation": "insert",
                  "values": [{"column": "kode", "value": "{{json.kode}}"}, {"column": "nama", "value": "Topi"}]},
                 team_flow.json_values('{"kode": "TP-3"}'))
    assert row["kode"] == "TP-3" and row["_key"]
    changed, note = run({"source": "tabel", "table": "produk", "operation": "upsert", "match": ["kode"],
                         "values": [{"column": "kode", "value": "TP-3"}, {"column": "harga", "value": "40000"}]})
    assert note.startswith("1 baris diubah") and changed[0]["harga"] == "40000"
    _, note = run({"source": "tabel", "table": "produk", "operation": "upsert", "match": ["kode"],
                   "values": [{"column": "kode", "value": "BARU"}]})
    assert "ditambah" in note
    _, note = run({"source": "tabel", "table": "produk", "operation": "delete",
                   "where": [{"column": "kode", "op": "=", "value": "BARU"}]})
    assert note.startswith("1 baris dihapus")


def test_relations_bring_related_rows_both_ways(data_dir: Path) -> None:
    shop(data_dir)
    relations = registry.get_relations()
    relation = relations.add("tabel", "pesanan", "kode_produk", "tabel", "produk", "kode")
    assert (relation.name, relation.reverse) == ("produk", "pesanan")
    with pytest.raises(Exception, match="sudah ada"):
        relations.add("tabel", "pesanan", "kode_produk", "tabel", "produk", "kode")

    def get(table: str) -> list[dict[str, Any]]:
        config = team_flow.normalize_node_config(
            "datatable", {"source": "tabel", "table": table, "operation": "get", "include": [relation.id]}, ())
        return json.loads(table_ops.run(config, lambda t: t, registry.open_backend, relations)[0])

    orders = get("pesanan")
    assert orders[0]["produk"]["nama"] == "Kaos"
    products = {p["kode"]: p for p in get("produk")}
    assert len(products["KS-1"]["pesanan"]) == 2 and products["TB-2"]["pesanan"] == []
    registry.get_registry().remove("tabel")
    assert relations.all() == []


@pytest.mark.asyncio
async def test_datatable_node_in_a_flow_and_by_itself(tmp_path: Path, data_dir: Path) -> None:
    shop(data_dir)
    manager, _ = make(tmp_path)
    get = node("ambil", "datatable", {"source": "tabel", "table": "produk", "operation": "get",
                                      "where": [{"column": "kode", "op": "=", "value": "{{input}}"}]})
    team = manager.create_team("f", "", [get, node("jawab", "set", {"text": "{{json.nama}} Rp{{json.harga}}"}, "ambil")],
                               None, style="flow", starts=["ambil"])
    run = await finish(manager, team.id, task="KS-1")
    assert run.result == "Kaos Rp75000"
    tested = await manager.test_node(get, "TB-2")
    assert json.loads(tested["content"])[0]["nama"] == "Tote"
    with pytest.raises(ValueError, match="Jalankan alur"):
        await manager.test_node(agent("a"), "x")


def test_json_values_reach_into_objects_and_lists() -> None:
    values = team_flow.json_values('[{"nama": "A", "isi": {"x": 1}}, {"nama": "B"}]')
    assert values["json.nama"] == "A"
    assert values["json.1.nama"] == "B"
    assert values["json.0.isi.x"] == "1"
    assert values["json.count"] == "2"
    assert team_flow.json_values("bukan json") == {}


@pytest.mark.asyncio
async def test_datatable_refuses_a_field_the_input_lacks(tmp_path: Path, data_dir: Path) -> None:
    shop(data_dir)
    manager, _ = make(tmp_path)
    insert = node("tulis", "datatable", {"source": "tabel", "table": "produk", "operation": "insert",
                                         "values": [{"column": "kode", "value": "{{json.kode}}"}]})
    with pytest.raises(ValueError, match=r"\{\{json.kode\}\} tidak ada di input.*teks biasa"):
        await manager.test_node(insert, "bukan json")
    with pytest.raises(ValueError, match="kolom: sku"):
        await manager.test_node(insert, '{"sku": "X"}')
    done = await manager.test_node(insert, '{"kode": "BARU"}')
    assert json.loads(done["content"])["kode"] == "BARU"


@pytest.mark.asyncio
async def test_switching_a_flow_off_rests_its_schedule(tmp_path: Path) -> None:
    manager, _ = make(tmp_path)
    team = manager.create_team("f", "", [agent("a")], None, style="flow", starts=["a"],
                               schedule={"mode": "interval", "minutes": 5, "task": "cek"})
    manager.set_active(team.id, False)
    assert await manager.run_due_schedules(now=team.updated_at + 3600) == []
    manager.set_active(team.id, True)
    stored = manager.store.get_team(team.id)
    started = await manager.run_due_schedules(now=(stored.last_scheduled_at or 0) + 600)
    assert len(started) == 1
    await asyncio.wait_for(manager.run_tasks[started[0]], timeout=5)


def test_json_values_read_a_related_row_through_the_first_row() -> None:
    values = team_flow.json_values('[{"kode": "KS-1", "produk": {"nama": "Kaos"}}]')
    assert values["json.produk.nama"] == "Kaos"
    assert values["json.0.produk.nama"] == "Kaos"
