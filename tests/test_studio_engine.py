"""The Studio's C render engine and its Python bridge: scene, audio mix, encoder choice, captions,
server render jobs, and a real build + still + export when a compiler and ffmpeg are present. Offline."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from addons.studio import captions, fonts, renderer, studio_doc


def _doc(**extra):
    doc = {"version": 1, "width": 64, "height": 48, "fps": 10, "duration": 1.0, "background": "#102030",
           "fonts": [], "audio": [], "notes": [],
           "layers": [{"id": "box", "type": "rect", "w": 32, "h": 24, "radius": 4, "fill": "#FF0000",
                       "props": {"x": 32, "y": 24}, "keys": {"opacity": [{"t": 0, "v": 0, "ease": "out"}, {"t": 0.5, "v": 1}]},
                       "start": 0, "end": 1}]}
    doc.update(extra)
    return doc


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(fonts, "ttf", lambda family, weight=400: fonts.system_font())
    studio_doc.save(tmp_path, "demo", _doc())
    return tmp_path


def test_scene_carries_layers_keys_and_colours(project: Path):
    doc, _ = studio_doc.load(project, "demo")
    text = renderer.scene(doc, studio_doc.folder(project, "demo"), out_size=(32, 24)).text
    assert text.startswith("SQSCENE\t1\ncanvas\t64\t48\t10.0\t1.0\noutput\t32\t24\n")
    assert "layer\trect\t0.0\t1.0" in text and "num\tw\t32.0" in text and "num\tradius\t4.0" in text
    assert "key\topacity\t0.0\t0.0\tout" in text and "col\tfill\t1.00000\t0.00000\t0.00000\t1.00000" in text


def test_strings_are_escaped_for_the_tab_format():
    assert renderer._esc("a\tb\nc%d") == "a%09b%0Ac%25d"


def test_audio_mix_covers_music_and_the_sound_of_video_clips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    folder = tmp_path / "p"
    (folder / "assets").mkdir(parents=True)
    for name in ("m.mp3", "v.mp4"):
        (folder / "assets" / name).write_bytes(b"x")
    monkeypatch.setattr(renderer, "probe", lambda path: renderer.Media(duration=10, width=640, height=360, audio=True))
    monkeypatch.setattr(renderer, "ffmpeg", lambda: "ffmpeg")
    doc = _doc(audio=[{"id": "m", "src": "assets/m.mp3", "start": 0.5, "offset": 1, "volume": 0.8, "fadeIn": 0.2,
                       "fadeOut": 0.3, "keys": {"volume": [{"t": 0.5, "v": 0.8}, {"t": 0.9, "v": 0.2, "ease": "inOut"}]}}])
    doc["layers"].append({"id": "v", "type": "video", "src": "assets/v.mp4", "start": 0, "end": 1, "speed": 2, "clipStart": 0,
                          "props": {}})
    text = renderer.audio_scene(doc, folder, tmp_path / "mix.wav")
    assert text is not None and text.count("aclip\t") == 2
    assert "akey\t0.9\t0.2\tinOut" in text and "atempo=2.0" in text


def test_encoder_choice_follows_the_setting(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(renderer, "detected_gpus", lambda: ["qsv"])
    monkeypatch.setattr(renderer, "ffmpeg", lambda: "ffmpeg")
    assert renderer.encoder("auto").name == "qsv"
    assert renderer.encoder("off").name == "cpu"
    assert renderer.encoder("nvenc").name == "cpu", "a GPU that failed its test is never forced"
    monkeypatch.setattr(renderer, "detected_gpus", lambda: [])
    assert renderer.encoder("auto").name == "cpu"


def test_caption_lines_break_at_pauses_and_never_overlap():
    words = [(0.0, 0.3, "Kopi"), (0.3, 0.6, "senja"), (0.6, 0.9, "hadir."), (0.9, 1.2, "Mulai"), (2.5, 2.8, "lagi")]
    lines = captions.group_words(words, max_words=4)
    assert [line[2] for line in lines] == ["Kopi senja hadir.", "Mulai", "lagi"]
    layers = captions.caption_layers(_doc(duration=4), lines)
    assert all(a["end"] <= b["start"] for a, b in zip(layers, layers[1:], strict=False))
    assert layers[0]["id"] == "cap-1" and layers[0]["stroke"] and layers[0]["keys"]["scale"]


def test_post_install_hook_runs(monkeypatch: pytest.MonkeyPatch):
    from addons import manager

    ok, log = manager._post_install("json:dumps")
    assert not ok, "a hook that needs arguments fails cleanly"
    ok, log = manager._post_install("os:getcwd")
    assert ok and log


def _png_pixel(path: Path, x: int, y: int) -> tuple[int, ...]:
    from PIL import Image

    with Image.open(path) as img:
        return tuple(img.convert("RGBA").getpixel((x, y)))


needs_tools = pytest.mark.skipif(not (shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")) or not shutil.which("ffmpeg"),
                                 reason="needs a C compiler and ffmpeg")


@needs_tools
def test_the_engine_builds_draws_and_exports(project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("STUDIO_GPU", "off")
    binary = renderer.build()
    assert binary.exists()
    png = renderer.still(project, "demo", [0.9], scale=1.0, out=tmp_path / "s.png")
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    centre, corner = _png_pixel(png, 32, 24), _png_pixel(png, 1, 1)
    assert centre[0] > 200 and centre[1] < 40, "the red box is drawn"
    assert corner[:3] == (0x10, 0x20, 0x30), "the background colour is exact"
    job = renderer.Job(id="t", session="s", project="demo")
    out = renderer.export(project, "demo", job=job)
    assert out.exists() and out.stat().st_size > 500 and job.encoder == "cpu" and job.progress == 1.0


def test_server_render_jobs_run_without_the_page(project: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from addons.studio.web import studio_api
    from web import server

    monkeypatch.setenv("SQUIDWINGS_WORKSPACE", str(project))
    target = studio_doc.folder(project, "demo") / "out" / "demo-x.mp4"

    def fake_export(workspace, name, *, scale, quality, gpu, job):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"mp4")
        job.encoder, job.progress = "cpu", 1.0
        return target

    monkeypatch.setattr(renderer, "export", fake_export)
    client = TestClient(server.app)
    session = "sess-abcdef123456"
    started = client.post(f"/api/studio/{session}/project/demo/render", json={"scale": 0.5, "gpu": "off"}).json()
    import time

    for _ in range(100):
        state = client.get(f"/api/studio/{session}/render/{started['id']}").json()
        if state["state"] in {"done", "error"}:
            break
        time.sleep(0.02)
    assert state["state"] == "done" and state["file"].endswith("demo-x.mp4") and state["url"]
    listed = client.get(f"/api/studio/{session}/renders?project=demo").json()["jobs"]
    assert listed[0]["id"] == started["id"]
    assert client.get(f"/api/studio/{session}/render/nope").status_code == 404
    assert studio_api._render_jobs[started["id"]].session == session
    assert json.loads(json.dumps(state))
