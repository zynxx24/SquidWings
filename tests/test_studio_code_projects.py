"""The Video Editor for code projects: the C engine finds the editable blocks of HyperFrames, React and JSON
files and patches them; Python decodes, validates and guards; the API, the team template and its buttons. Offline."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from addons.studio import code_projects
from addons.studio.studio_doc import ConflictError

needs_cc = pytest.mark.skipif(not (shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")),
                              reason="needs a C compiler to build the engine")

HTML = """<!doctype html><html><head><style>
:root { --latar: #0B3C49; --aksen:#C2553B; --font: "Anton", sans-serif; } /* --mati: #fff; */
</style></head><body>
<div id="root" data-composition-id="main" data-start="0" data-duration="12">
  <h1 id="judul" class="clip" data-start="0.5" data-duration="4">Antrean &amp; harapan</h1>
  <p id="campur" class="clip" data-start="1" data-duration="3"><b>tebal</b> teks</p>
  <img id="foto" class="clip" src="assets/foto.jpg" data-start="0" data-duration="6">
  <!-- <h2 id="komentar">x</h2> -->
  <script>const s = "<h3 id='js'>bukan</h3>";</script>
</div></body></html>
"""

TSX = """import {AbsoluteFill, Sequence, Img, staticFile} from "remotion";
export const defaultProps = {title: "Hikayat Antrean", subtitle: 'Jangan serobot'};
const n = useState<number>(0); // <b>bukan jsx</b>
export const Main = ({title}) => {
  if (n < 10 && n > 2) return null;
  return (
    <AbsoluteFill style={{backgroundColor: "#0B3C49"}}>
      <Sequence from={30} durationInFrames={90}>
        <h1>Antre itu adil, don't skip</h1>
        <p>{title} hari ini</p>
        <Img src={staticFile("foto.jpg")} />
      </Sequence>
      {n > 20 && <span>muncul</span>}
    </AbsoluteFill>
  );
};
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    hf = tmp_path / "video" / "antrean"
    (hf / "assets").mkdir(parents=True)
    (hf / "index.html").write_text(HTML, encoding="utf-8")
    (hf / "hyperframes.json").write_text("{}", encoding="utf-8")
    (hf / "assets" / "foto.jpg").write_bytes(b"jpg")
    (hf / "assets" / "baru.jpg").write_bytes(b"jpg")
    (hf / "node_modules" / "x").mkdir(parents=True)
    (hf / "node_modules" / "x" / "index.html").write_text("<p id='a'>x</p>", encoding="utf-8")
    rm = tmp_path / "remo"
    (rm / "src").mkdir(parents=True)
    (rm / "public").mkdir()
    (rm / "public" / "foto.jpg").write_bytes(b"jpg")
    (rm / "package.json").write_text(json.dumps({"dependencies": {"remotion": "4.0.0"}}), encoding="utf-8")
    (rm / "src" / "Main.tsx").write_text(TSX, encoding="utf-8")
    studio = tmp_path / "studio" / "intro"
    studio.mkdir(parents=True)
    (studio / "motion.json").write_text("{}", encoding="utf-8")
    (studio / "index.html").write_text("<p id='x'>studio</p>", encoding="utf-8")
    return tmp_path


def _by(blocks: list[dict], key: str) -> dict:
    return next(b for b in blocks if b["key"] == key)


def test_projects_are_found_by_kind_and_studio_and_node_modules_are_skipped(workspace: Path) -> None:
    found = {p["path"]: p["kind"] for p in code_projects.find(workspace)}
    assert found == {"video/antrean": "hyperframes", "remo": "remotion"}
    files = [f["path"] for f in code_projects.editable_files(workspace / "video" / "antrean")]
    assert files[0] == "index.html" and not any("node_modules" in f for f in files)
    with pytest.raises(code_projects.CodeProjectError):
        code_projects.project_folder(workspace, "../")
    with pytest.raises(code_projects.CodeProjectError):
        code_projects.project_folder(workspace, "studio/intro")


@needs_cc
def test_html_blocks_come_from_the_engine_decoded(workspace: Path) -> None:
    data = code_projects.blocks(workspace, "video/antrean", "index.html")
    keys = {b["key"] for b in data["blocks"]}
    assert {"--latar", "--aksen", "--font", "judul", "judul.data-start", "judul.data-duration", "foto.src"} <= keys
    assert not keys & {"--mati", "campur", "komentar", "js"}, "comments, scripts and mixed markup are not blocks"
    assert _by(data["blocks"], "judul")["value"] == "Antrean & harapan"
    assert _by(data["blocks"], "--latar")["kind"] == "color"


@needs_cc
def test_a_save_patches_only_blocks_escapes_and_validates(workspace: Path) -> None:
    data = code_projects.blocks(workspace, "video/antrean", "index.html")
    edit = lambda key, value: {"id": _by(data["blocks"], key)["id"], "value": value}  # noqa: E731
    saved = code_projects.save(workspace, "video/antrean", "index.html", data["stamp"],
                               [edit("judul", "Adil <untuk> semua & cepat"), edit("--latar", "#101010"),
                                edit("judul.data-duration", "5,5"), edit("foto.src", "assets/baru.jpg")])
    text = (workspace / "video" / "antrean" / "index.html").read_text(encoding="utf-8")
    assert "Adil &lt;untuk&gt; semua &amp; cepat</h1>" in text and "--latar: #101010;" in text
    assert 'data-duration="5.5">Adil' in text and 'src="assets/baru.jpg"' in text
    assert _by(saved["blocks"], "judul")["value"] == "Adil <untuk> semua & cepat"

    fresh = code_projects.blocks(workspace, "video/antrean", "index.html")
    for key, value, words in [("--aksen", "merah", "hex"), ("judul.data-start", "abc", "angka"),
                              ("foto.src", "https://x.test/a.jpg", "path"), ("foto.src", "assets/tidak-ada.jpg", "tidak ada"),
                              ("foto.src", "../../etc/passwd", "path")]:
        with pytest.raises(code_projects.CodeProjectError, match=words):
            code_projects.save(workspace, "video/antrean", "index.html", fresh["stamp"],
                               [{"id": _by(fresh["blocks"], key)["id"], "value": value}])
    with pytest.raises(ConflictError):          # an offset that is not a block right now is never patched
        code_projects.save(workspace, "video/antrean", "index.html", fresh["stamp"], [{"id": "0:15", "value": "x"}])
    assert (workspace / "video" / "antrean" / "index.html").read_text(encoding="utf-8") == text, "refused saves change nothing"


@needs_cc
def test_a_file_changed_meanwhile_is_not_overwritten(workspace: Path) -> None:
    data = code_projects.blocks(workspace, "video/antrean", "index.html")
    page = workspace / "video" / "antrean" / "index.html"
    page.write_text(page.read_text(encoding="utf-8").replace("Antrean", "Antre"), encoding="utf-8")
    os.utime(page, ns=(time.time_ns(), time.time_ns() + 5_000_000))
    with pytest.raises(ConflictError) as caught:
        code_projects.save(workspace, "video/antrean", "index.html", data["stamp"],
                           [{"id": _by(data["blocks"], "judul")["id"], "value": "Baru"}])
    assert caught.value.current == code_projects.stamp(page)
    assert "Antre &amp; harapan" in page.read_text(encoding="utf-8")


@needs_cc
def test_react_blocks_text_props_colours_frames_and_media(workspace: Path) -> None:
    data = code_projects.blocks(workspace, "remo", "src/Main.tsx")
    seen = {(b["kind"], b["key"].split("@")[0], b["value"]) for b in data["blocks"]}
    for expected in [("text", "title", "Hikayat Antrean"), ("text", "subtitle", "Jangan serobot"),
                     ("color", "backgroundColor", "#0B3C49"), ("time", "from", "30"), ("time", "durationInFrames", "90"),
                     ("text", "h1", "Antre itu adil, don't skip"), ("text", "p", "hari ini"), ("text", "span", "muncul"),
                     ("media", "staticFile", "foto.jpg")]:
        assert expected in seen, expected
    assert not any(b["value"] in {"}", "bukan jsx"} for b in data["blocks"]), "code is never taken for JSX text"
    edit = lambda key, value: {"id": _by(data["blocks"], key)["id"], "value": value}  # noqa: E731
    with pytest.raises(code_projects.CodeProjectError, match="bulat"):
        code_projects.save(workspace, "remo", "src/Main.tsx", data["stamp"], [edit(_key(data, "durationInFrames"), "90.5")])
    with pytest.raises(code_projects.CodeProjectError, match="JSX"):
        code_projects.save(workspace, "remo", "src/Main.tsx", data["stamp"], [edit(_key(data, "h1"), "a {b}")])
    code_projects.save(workspace, "remo", "src/Main.tsx", data["stamp"],
                       [edit(_key(data, "subtitle"), "Jangan 'serobot'"), edit(_key(data, "durationInFrames"), "120"),
                        edit(_key(data, "h1"), "Antre itu adil & cepat")])
    text = (workspace / "remo" / "src" / "Main.tsx").read_text(encoding="utf-8")
    assert "subtitle: 'Jangan \\'serobot\\''" in text and "durationInFrames={120}" in text
    assert "<h1>Antre itu adil &amp; cepat</h1>" in text


def _key(data: dict, name: str) -> str:
    return next(b["key"] for b in data["blocks"] if b["key"].split("@")[0] == name)


@needs_cc
def test_json_leaves_round_trip(workspace: Path) -> None:
    data_file = workspace / "video" / "antrean" / "data.json"
    data_file.write_text('{"judul": "Halo \\"dunia\\"", "adegan": [{"dur": 2.5, "warna": "#157A80"}]}', encoding="utf-8")
    data = code_projects.blocks(workspace, "video/antrean", "data.json")
    assert _by(data["blocks"], "judul")["value"] == 'Halo "dunia"'
    assert _by(data["blocks"], "adegan[0].warna")["kind"] == "color"
    code_projects.save(workspace, "video/antrean", "data.json", data["stamp"],
                       [{"id": _by(data["blocks"], "judul")["id"], "value": 'Hai "semua"\nbaris'},
                        {"id": _by(data["blocks"], "adegan[0].dur")["id"], "value": "3"}])
    assert json.loads(data_file.read_text(encoding="utf-8")) == {"judul": 'Hai "semua"\nbaris', "adegan": [{"dur": 3, "warna": "#157A80"}]}


@needs_cc
def test_the_editor_api(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from web.server import app

    monkeypatch.setenv("SQUIDWINGS_WORKSPACE", str(workspace))
    client = TestClient(app, client=("127.0.0.1", 50000))
    session = "sess-abcdef123456"
    base = f"/api/studio/{session}/code"
    listed = client.get(f"{base}/projects").json()
    assert {p["path"] for p in listed["projects"]} == {"video/antrean", "remo"}
    files = client.get(f"{base}/files", params={"project": "remo"}).json()
    assert files["kind"] == "remotion" and files["files"][0]["path"] == "src/Main.tsx"
    got = client.get(f"{base}/blocks", params={"project": "video/antrean", "path": "index.html"}).json()
    judul = _by(got["blocks"], "judul")
    put = client.put(f"{base}/blocks", json={"project": "video/antrean", "path": "index.html", "base": got["stamp"],
                                             "edits": [{"id": judul["id"], "value": "Dari editor"}]})
    assert put.status_code == 200 and _by(put.json()["blocks"], "judul")["value"] == "Dari editor"
    stale = client.put(f"{base}/blocks", json={"project": "video/antrean", "path": "index.html", "base": got["stamp"],
                                               "edits": [{"id": judul["id"], "value": "Lagi"}]})
    assert stale.status_code == 409 and stale.json()["detail"]["stamp"] == put.json()["stamp"]
    bad = client.put(f"{base}/blocks", json={"project": "video/antrean", "path": "index.html", "base": put.json()["stamp"],
                                             "edits": [{"id": _by(put.json()["blocks"], "--latar")["id"], "value": "biru"}]})
    assert bad.status_code == 422 and "hex" in bad.json()["detail"]
    assert client.get(f"{base}/blocks", params={"project": "video/antrean", "path": "../../x.html"}).status_code == 422
    assert client.post(f"{base}/preview", json={"project": "remo"}).status_code == 422
    preview = client.post(f"{base}/preview", json={"project": "video/antrean", "path": "index.html"}).json()
    assert preview["url"].startswith("/a/") and client.get(preview["url"]).status_code == 200
    page = client.get("/studio/code")
    assert page.status_code == 200 and "code.js" in page.text and "frame-src 'self'" in page.headers["content-security-policy"]


def test_the_team_template_plans_first_and_its_mode_is_valid() -> None:
    from brain.agent.team_orchestrator import TEAM_MODES, TeamOrchestrator, team_templates

    template = next(t for t in team_templates(["m"]) if t["key"] == "code-video")
    assert template["mode"] == "codevideo" in TEAM_MODES and TeamOrchestrator._mode("codevideo") == "codevideo"
    planner, builder, checker = template["members"]
    assert "naskah.md" in planner["instructions"] and planner["depends_on"] == []
    assert builder["depends_on"] == ["planner"] and "id" in builder["instructions"] and ":root" in builder["instructions"]
    assert checker["depends_on"] == ["builder"] and template["lead_id"] == "checker"
    assert TeamOrchestrator._budget(type("T", (), {"mode": "codevideo"})())["mode"] == "video"


def test_a_run_gets_editor_and_ide_buttons_for_the_projects_it_changed(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.agent import team_orchestrator as orchestrator

    monkeypatch.setattr(orchestrator, "studio_module", lambda name: code_projects)
    monkeypatch.setattr(orchestrator, "addon_enabled", lambda name: name == "ide")
    old = time.time() - 3600
    for path in (workspace / "remo").rglob("*"):
        os.utime(path, (old, old))
    run = type("Run", (), {"session": "sess-abcdef123456", "id": "r1", "started_at": time.time() - 60, "result": ""})()
    buttons = orchestrator.TeamOrchestrator._code_buttons(workspace, run)
    assert "/studio/code?session=sess-abcdef123456&project=video%2Fantrean" in buttons
    assert "/ide?session=sess-abcdef123456&path=video%2Fantrean" in buttons
    assert "remo" not in buttons, "a project the run did not touch gets no button"


# ── what the full run showed: a cut ending, flaws only a rendered check sees, a video pasted twice ─────────────
CHECK_REPORT = {
    "ok": True, "browserSkipped": False,
    "lint": {"errorCount": 0, "findings": [], "unmountedCompositions": []},
    "runtime": {"errorCount": 0, "findings": []},
    "layout": {"errorCount": 0, "findings": [{"severity": "info", "code": "panel_out_of_canvas", "message": "x"}]},
    "contrast": {"errorCount": 0, "findings": [{"severity": "warning", "code": "contrast_aa_failure",
                                                "message": "Contrast is 3.84:1", "text": "LOKET 1"}]},
    "craft": {"errorCount": 0, "findings": [{"severity": "warning", "code": "line_unread", "time": 12.4,
                                             "message": "The film's last line is on screen 0.77 s"}]},
}


async def test_video_check_plays_the_film_and_flags_a_root_without_duration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.toolbox import media_tools

    project = tmp_path / "vid"
    project.mkdir()
    (project / "hyperframes.json").write_text("{}")
    (project / "index.html").write_text('<div id="root" data-composition-id="root" data-width="1920"></div>')
    calls: list[list[str]] = []

    async def fake_run(argv, cwd, timeout_s, env=None, low_priority=False):
        calls.append(argv)
        # The real CLI pretty-prints the report and logs to stderr, which _run merges in, after it as well.
        return 0, ("[hyperframes] check: analyze 1/3\n" + json.dumps(CHECK_REPORT, indent=2)
                   + "\n[hyperframes] check: report saved to x.json\n"), False

    async def fake_probe(folder: Path) -> dict[str, Any]:
        return {"findings": [], "samples": 0}

    monkeypatch.setattr(media_tools, "_run", fake_run)
    monkeypatch.setattr(media_tools, "_hyperframes", lambda: ["hyperframes"])
    monkeypatch.setattr(media_tools.video_probe, "probe", fake_probe)
    result = await media_tools.video_check("vid", workspace_path=str(tmp_path))
    assert calls[0][1:] == ["check", "--json"]
    codes = [f["code"] for f in result["findings"]]
    assert codes == ["root_duration_missing", "contrast_aa_failure", "line_unread"], "infos left out, root first"
    assert result["ok"] and result["warnings"] == 3 and result["declared_seconds"] is None

    (project / "index.html").write_text('<div id="root" data-composition-id="root" data-duration="15"></div>')

    async def no_browser(argv, cwd, timeout_s, env=None, low_priority=False):
        if argv[1] == "check":
            return 0, json.dumps({"browserSkipped": True}), False
        return 0, json.dumps({"ok": True, "errorCount": 0, "warningCount": 0, "findings": []}), False

    monkeypatch.setattr(media_tools, "_run", no_browser)
    plain = await media_tools.video_check("vid", workspace_path=str(tmp_path))
    assert plain["ok"] and plain["declared_seconds"] == 15 and plain["findings"] == [], "lint still runs without a browser"


def test_the_render_reports_its_real_length(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.toolbox import media_tools

    (tmp_path / "index.html").write_text('<div data-composition-id="root" data-start="0">')
    monkeypatch.setattr(media_tools, "_video_seconds", lambda path: 13.3)
    note = media_tools._length_report(tmp_path / "v.mp4", tmp_path)
    assert note["duration_s"] == 13.3 and "no data-duration" in note["duration_note"]
    (tmp_path / "index.html").write_text('<div data-composition-id="root" data-duration="15">')
    assert "declares 15" in media_tools._length_report(tmp_path / "v.mp4", tmp_path)["duration_note"]
    monkeypatch.setattr(media_tools, "_video_seconds", lambda path: 15.03)
    assert media_tools._length_report(tmp_path / "v.mp4", tmp_path) == {"duration_s": 15.03}


def test_a_video_pasted_twice_shows_once() -> None:
    from brain.agent.team_orchestrator import once_per_media

    text = "Selesai.\n\n![a](/api/session/s/files/r/v.mp4)\n\n![Video panjang](/api/session/s/files/r/v.mp4)\n\n![b](/x.png)"
    assert once_per_media(text) == "Selesai.\n\n![a](/api/session/s/files/r/v.mp4)\n\n![b](/x.png)"


def test_the_builder_must_declare_the_length_and_the_checker_measure_it() -> None:
    from brain.agent.team_orchestrator import team_templates

    template = next(t for t in team_templates(["m"]) if t["key"] == "code-video")
    _planner, builder, checker = template["members"]
    assert "data-duration" in builder["instructions"] and "duration_note" in builder["instructions"]
    assert "duration_s" in checker["instructions"] and "tanpa angka" in checker["instructions"]


async def test_a_code_video_left_unrendered_is_rendered_by_the_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.agent.team_orchestrator import TeamOrchestrator
    from tools.toolbox import media_tools

    old = tmp_path / "video" / "lama"
    new = tmp_path / "video" / "baru"
    for folder in (old, new):
        folder.mkdir(parents=True)
        (folder / "hyperframes.json").write_text("{}")
        (folder / "index.html").write_text('<div data-composition-id="root" data-duration="5"></div>')
    os.utime(old / "index.html", (time.time() - 7200, time.time() - 7200))
    rendered: list[str] = []

    async def fake_render(project: str, share: bool = True, workspace_path: str | None = None, **_: Any) -> dict[str, Any]:
        rendered.append(project)
        return {"ok": True, "saved": f"renders/{Path(project).name}.mp4"}

    monkeypatch.setattr(media_tools, "video_render", fake_render)
    video, note = await TeamOrchestrator._render_code_video(tmp_path, time.time() - 600)
    assert rendered == ["video/baru"] and video == "renders/baru.mp4" and note == ""
    assert (await TeamOrchestrator._render_code_video(tmp_path, time.time() + 600))[0] is None


def test_lint_advice_on_file_structure_is_not_a_warning() -> None:
    from tools.toolbox import media_tools

    report = {"lint": {"findings": [
        {"severity": "warning", "code": "nested_structure_needs_subcomposition", "message": "split it"},
        {"severity": "warning", "code": "timeline_track_too_dense", "message": "5 timed elements"},
        {"severity": "warning", "code": "missing_alt", "message": "real"}]}}
    assert [f["code"] for f in media_tools._check_findings(report)] == ["missing_alt"]
