"""Studio: motion.json validation, the HTTP API the editor uses, and the agent's tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils import studio_doc


def test_the_template_is_valid_and_bad_documents_say_where_they_are_wrong() -> None:
    doc, errors = studio_doc.validate(studio_doc.template())
    assert errors == [] and doc["width"] == 1080 and doc["layers"][1]["keys"]["opacity"][0]["t"] == 0
    _, errors = studio_doc.validate({"width": 1081, "layers": [
        {"type": "blob"}, {"id": "t", "type": "text", "keys": {"colour": [{"t": 0, "v": 1}]}},
        {"id": "i", "type": "image", "src": "../../etc/passwd"}], "audio": [{"src": "/etc/hosts"}]})
    text = " | ".join(errors)
    assert "layers[0].type" in text and "colour" in text and "layers[2].src" in text and "audio[0].src" in text


def test_odd_sizes_are_made_even_and_keys_sorted() -> None:
    doc, errors = studio_doc.validate({"width": 1081, "height": 607, "layers": [
        {"id": "a", "type": "rect", "keys": {"x": [{"t": 2, "v": 10}, {"t": 0, "v": 0}]}}]})
    assert not errors and doc["width"] == 1082 and doc["height"] == 608
    assert [k["t"] for k in doc["layers"][0]["keys"]["x"]] == [0, 2]


def test_a_save_over_a_newer_change_is_refused(tmp_path: Path) -> None:
    _, stamp = studio_doc.save(tmp_path, "promo", studio_doc.template())
    doc, _ = studio_doc.load(tmp_path, "promo")
    studio_doc.save(tmp_path, "promo", doc, stamp)          # first writer wins
    with pytest.raises(studio_doc.ConflictError):
        studio_doc.save(tmp_path, "promo", doc, stamp)      # second one saw an older version
    with pytest.raises(studio_doc.StudioError):
        studio_doc.folder(tmp_path, "../escape")


def test_agent_tools_create_save_and_close_notes(tmp_path: Path) -> None:
    from tool.execute.execute_toolbox import studio_tools as st

    created = st.studio_create("Promo Kopi", workspace_path=str(tmp_path))
    name = created["name"]
    assert name == "promo-kopi" and created["open"] == "/studio?project=promo-kopi"
    got = st.studio_get(name, workspace_path=str(tmp_path))
    doc = got["doc"]
    doc["notes"] = [{"n": 1, "t": 1.0, "box": [10, 10, 100, 50], "layer": "title", "text": "Judul lebih besar", "status": "open"}]
    saved = st.studio_save(name, doc, got["stamp"], workspace_path=str(tmp_path))
    assert saved["ok"] and saved["summary"]["open_notes"] == 1
    stale = st.studio_save(name, doc, got["stamp"], workspace_path=str(tmp_path))
    assert stale["conflict"]
    bad = st.studio_save(name, {**doc, "layers": [{"type": "nope"}]}, saved["stamp"], workspace_path=str(tmp_path))
    assert not bad["ok"] and "layers[0].type" in bad["error"]
    notes = st.studio_notes(name, workspace_path=str(tmp_path))
    assert notes["count"] == 1 and notes["notes"][0]["layer_summary"]["type"] == "text"
    assert st.studio_note_done(name, 1, "Judul dibesarkan ke 150 px.", workspace_path=str(tmp_path))["open_notes_left"] == 0
    assert st.studio_notes(name, workspace_path=str(tmp_path))["count"] == 0


def test_the_editor_api_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from web.server import app

    monkeypatch.setenv("SQUIDWINGS_WORKSPACE", str(tmp_path))
    client = TestClient(app, client=("127.0.0.1", 50000))
    session = "sess-abcdef123456"
    created = client.post(f"/api/studio/{session}/projects", json={"name": "Intro", "width": 1920, "height": 1080}).json()
    assert created["name"] == "intro" and created["doc"]["width"] == 1920
    got = client.get(f"/api/studio/{session}/project/intro").json()
    got["doc"]["background"] = "#101010"
    put = client.put(f"/api/studio/{session}/project/intro", json={"doc": got["doc"], "base": got["stamp"]})
    assert put.status_code == 200
    conflict = client.put(f"/api/studio/{session}/project/intro", json={"doc": got["doc"], "base": got["stamp"]})
    assert conflict.status_code == 409 and conflict.json()["detail"]["stamp"] == put.json()["stamp"]
    up = client.post(f"/api/studio/{session}/project/intro/asset?file=logo (1).png", content=b"\x89PNG fake")
    assert up.json()["src"] == "assets/logo (1).png"
    assert client.get(f"/api/studio/{session}/project/intro/asset/logo (1).png").content == b"\x89PNG fake"
    assert client.post(f"/api/studio/{session}/project/intro/asset?file=x.svg", content=b"<svg/>").status_code == 415
    assert client.get(f"/api/studio/{session}/project/intro/asset/..%2Fmotion.json").status_code == 404
    page = client.get("/studio?embed=1")
    assert page.status_code == 200 and "render.js" in page.text


def test_check_sees_what_a_viewer_would_see(tmp_path: Path) -> None:
    doc = studio_doc.template()
    assert studio_doc.check(doc) == []
    doc["layers"].append({"id": "long", "type": "text", "text": "Teks panjang sekali yang pasti keluar dari layar", "size": 140,
                          "fill": "#F4EBDD", "props": {"x": 540, "y": 820}, "start": 0, "end": 1})
    doc["layers"].append({"id": "clip", "type": "video", "src": "assets/klip.mp4", "w": 800, "h": 450,
                          "props": {"x": 540, "y": 1400}, "start": 0, "end": 4})
    messages = " | ".join(i["message"] for i in studio_doc.check(doc, tmp_path, "x"))
    for expected in ("terpotong di tepi", "kontras teks", "hanya tampil", "bertumpuk", "assets/klip.mp4 belum ada"):
        assert expected in messages, expected


def test_a_studio_turn_brings_its_protocol_context_and_tools(tmp_path: Path) -> None:
    from agent.harness.tiers import scaffold_for
    from agent.harness.tool_select import select_tools
    from config.constants import TaskType
    from tool.register_tools import register_all_tools
    from utils import studio_context

    studio_doc.save(tmp_path, "promo", studio_doc.template(title="Kopi Senja"))
    assert studio_context.clean({"project": "../x"}) is None
    ctx = studio_context.clean({"project": "promo", "t": 1.2, "layer": "title", "box": [1, 2, 3, 4]})
    token = studio_context.set_current(ctx)
    try:
        block = studio_context.prompt_block(tmp_path)
        tools = {t["function"]["name"] for t in select_tools(register_all_tools().get_all_schemas(), TaskType.MATH,
                                                                  "geser ini ke kiri", scaffold_for("x-flash"))}
    finally:
        studio_context.reset(token)
    assert "[STUDIO PROTOCOL]" in block and "Playhead di 0:01.20" in block and '"Kopi Senja"' in block
    assert "Lapisan terpilih" in block and "kotak x1 y2" in block
    assert {"studio_get", "studio_save", "studio_check"} <= tools
    assert studio_context.prompt_block(tmp_path) == ""      # outside a Studio turn nothing is added


def test_tools_end_the_answer_with_a_studio_button(tmp_path: Path) -> None:
    from tool.execute.execute_toolbox import studio_tools as st
    from utils.workspace_context import _active_session

    token = _active_session.set("run-abc123")
    try:
        created = st.studio_create("Promo", workspace_path=str(tmp_path))
    finally:
        _active_session.reset(token)
    assert created["shared"] is True and created["open"] == "/studio?session=run-abc123&project=promo"
    assert created["markdown"].startswith("[▶ Lihat & edit video") and created["open"] in created["markdown"]


def test_a_team_result_gets_a_button_for_the_video_it_made(tmp_path: Path) -> None:
    from agent.team_orchestrator import TeamOrchestrator, TeamRun

    studio_doc.save(tmp_path, "intro", studio_doc.template())
    run = TeamRun(id="run-xyz", team_id="t", task="buat intro")
    run.started_at = 0
    run.result = "Selesai."
    assert "/studio?session=run-xyz&project=intro" in TeamOrchestrator._studio_buttons(tmp_path, run)
