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


# ── phase 2: transitions, colour, key, LUT, mask, tracks, curves, proxies, live events ──

def _fx_doc(layer: dict, **extra) -> dict:
    base = {"id": "a", "type": "rect", "w": 100, "h": 100, "fill": "#C83232", "props": {"x": 50, "y": 50}, "start": 0, "end": 2}
    return {"version": 1, "width": 100, "height": 100, "fps": 10, "duration": 2, "background": "#000000",
            "fonts": [], "audio": [], "notes": [], "layers": [{**base, **layer}], **extra}


def test_effects_and_tracks_are_validated_and_normalised():
    doc = _fx_doc({"transIn": {"type": "wipe-left", "duration": 0.4, "ease": "cubic-bezier( .2, 1.3 ,.4,1 )"},
                   "transOut": {"type": "none"}, "chroma": {"color": "#00ff00"}, "lut": "assets/look.cube",
                   "mask": {"shape": "ellipse", "w": 50, "h": 40}, "track": "v1",
                   "props": {"x": 50, "y": 50, "saturation": -0.5, "hue": 400},
                   "keys": {"contrast": [{"t": 0, "v": 0.2, "ease": "outBounce"}, {"t": 1, "v": -0.2}]}},
                  tracks=[{"id": "v1", "kind": "video"}])
    clean, errors = studio_doc.validate(doc)
    assert errors == []
    layer = clean["layers"][0]
    assert layer["transIn"] == {"type": "wipe-left", "duration": 0.4, "ease": "cubic-bezier(0.2,1.3,0.4,1)"}
    assert "transOut" not in layer, "a 'none' transition is dropped"
    assert layer["chroma"]["similarity"] == 0.25 and layer["lut"] == {"src": "assets/look.cube", "mix": 1.0}
    assert layer["mask"]["shape"] == "ellipse" and layer["mask"]["invert"] is False
    assert clean["tracks"][0]["name"] == "v1" and clean["tracks"][0]["hidden"] is False

    bad = _fx_doc({"transIn": {"type": "spin"}, "props": {"x": 50, "y": 50, "brightness": 3}, "track": "nope",
                   "keys": {"x": [{"t": 0, "v": 1, "ease": "cubic-bezier(2,0,1,1)"}]}, "mask": {"shape": "star"}})
    _, errors = studio_doc.validate(bad)
    text = " | ".join(errors)
    for part in ("transIn.type", "brightness", "track", "cubic-bezier", "mask.shape"):
        assert part in text, f"{part} missing from: {text}"


def test_scene_carries_effects_and_respects_tracks(tmp_path: Path):
    folder = tmp_path / "p"
    (folder / "assets").mkdir(parents=True)
    (folder / "assets" / "look.cube").write_text("LUT_3D_SIZE 2\n" + "0 0 0\n" * 8)
    doc = _fx_doc({"transOut": {"type": "iris", "duration": 0.3, "ease": "in"}, "chroma": {"color": "#00FF00"},
                   "lut": {"src": "assets/look.cube", "mix": 0.5}, "mask": {"shape": "rect", "w": 20, "h": 10, "invert": True},
                   "props": {"x": 50, "y": 50, "temperature": 0.4}},
                  tracks=[{"id": "v1", "kind": "video", "hidden": True}, {"id": "a1", "kind": "audio", "muted": True}])
    doc["layers"].append({"id": "gone", "type": "rect", "track": "v1", "props": {}})
    text = renderer.scene(doc, folder).text
    assert "trans\tout\tiris\t0.3\tin" in text and "chroma\t0.00000\t1.00000\t0.00000\t0.25" in text
    assert "lutfile\tl0\t" in text and "lut\tl0\t0.5" in text and "mask\trect\t0.0\t0.0\t20.0\t10.0\t0.0\t0.0\t1" in text
    assert "num\ttemperature\t0.4" in text and text.count("layer\t") == 1, "a hidden track's layers are not drawn"

    for name in ("m.mp3",):
        (folder / "assets" / name).write_bytes(b"x")
    doc["audio"] = [{"id": "m", "src": "assets/m.mp3", "track": "a1"}]
    assert renderer.audio_scene(doc, folder, tmp_path / "mix.wav") is None, "a muted track is silent"


def _row_edge(png: Path, y: int) -> float:
    """Where the bright bar starts on row y, to a fraction of a pixel (anti-aliased coverage)."""
    from PIL import Image

    with Image.open(png) as img:
        row = [img.convert("RGB").getpixel((x, y))[0] for x in range(img.width)]
    first = next(i for i, v in enumerate(row) if v > 0)
    return first + 1 - row[first] / 255


def _node_render(cases: list) -> list:
    """Run render.js (the editor's renderer) in node and return what it computes for each case."""
    import subprocess

    script = r"""
      global.window = {}; global.document = { createElement: () => ({}) };
      require(process.argv[1]);
      const R = window.StudioRender, out = [];
      for (const c of JSON.parse(process.argv[2])) {
        const layer = c.doc.layers[0];
        const s = R.layerState(layer, c.t, c.doc);
        if (c.kind === 'edge') { out.push(s.x - (layer.w / 2) * s.scale); continue; }
        const luts = {};
        if (c.cube && layer.lut) luts[layer.lut.src] = R.parseCube(c.cube);
        const fx = R.effectsOf(layer, s, { luts });
        const [r, g, b] = R.hexToRgba(layer.fill), rgb = [r / 255, g / 255, b / 255];
        const a = fx ? R.effectPixel(fx, rgb, 1) : 1;
        out.push(rgb.map(v => v * a * 255));
      }
      console.log(JSON.stringify(out));
    """
    render_js = Path(__file__).resolve().parents[1] / "addons" / "studio" / "web" / "static" / "render.js"
    done = subprocess.run(["node", "-e", script, str(render_js), json.dumps(cases)], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(done.stdout)


@needs_tools
def test_the_engine_draws_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fonts, "ttf", lambda family, weight=400: fonts.system_font())

    def frame(layer: dict, t: float, **extra) -> Path:
        studio_doc.save(tmp_path, "fx", _fx_doc(layer, **extra))
        return renderer.still(tmp_path, "fx", [t], scale=1.0, out=tmp_path / "fx.png")

    grey = _png_pixel(frame({"props": {"x": 50, "y": 50, "saturation": -1}}, 0.5), 50, 50)
    assert grey[0] == grey[1] == grey[2] and 70 < grey[0] < 95, "saturation -1 is grey (luma)"
    assert _png_pixel(frame({"fill": "#00FF00", "chroma": {"color": "#00FF00"}}, 0.5), 50, 50)[:3] == (0, 0, 0), "green is keyed out"
    wiped = frame({"transIn": {"type": "wipe-right", "duration": 1, "ease": "linear"}}, 0.5)
    assert _png_pixel(wiped, 25, 50)[0] > 150 and _png_pixel(wiped, 75, 50)[:3] == (0, 0, 0), "half wiped in"
    masked = frame({"mask": {"shape": "ellipse", "w": 50, "h": 50}}, 0.5)
    assert _png_pixel(masked, 50, 50)[0] > 150 and _png_pixel(masked, 5, 5)[:3] == (0, 0, 0)
    assert _png_pixel(frame({"transOut": {"type": "fade", "duration": 1, "ease": "linear"}}, 1.5), 50, 50)[0] in range(98, 103)
    hidden = frame({"track": "v1"}, 0.5, tracks=[{"id": "v1", "kind": "video", "hidden": True}])
    assert _png_pixel(hidden, 50, 50)[:3] == (0, 0, 0), "a hidden track draws nothing"
    lut_dir = studio_doc.folder(tmp_path, "fx") / "assets"
    lut_dir.mkdir(parents=True, exist_ok=True)
    (lut_dir / "inv.cube").write_text("LUT_3D_SIZE 2\n" + "\n".join(f"{1 - r} {1 - g} {1 - b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)))
    assert _png_pixel(frame({"lut": {"src": "assets/inv.cube"}}, 0.5), 50, 50)[:3] == (55, 205, 205), "an inverting LUT inverts"


needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node to run the editor's renderer")


@needs_tools
@needs_node
def test_preview_and_export_compute_the_same_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """render.js (what the editor shows) and sqengine.c (what the export gets) agree on colour, key, LUT, eases and transitions."""
    monkeypatch.setattr(fonts, "ttf", lambda family, weight=400: fonts.system_font())
    cube = "LUT_3D_SIZE 3\n" + "\n".join(f"{(r / 2) ** 0.6:.4f} {(g / 2) ** 1.4:.4f} {1 - b / 2:.4f}"
                                          for b in range(3) for g in range(3) for r in range(3))
    colour_cases = [
        _fx_doc({"props": {"x": 50, "y": 50, "brightness": 0.15, "contrast": 0.3, "saturation": -0.4, "hue": 40, "temperature": 0.5}}),
        _fx_doc({"fill": "#30D050", "chroma": {"color": "#00FF00", "similarity": 0.1, "smoothness": 0.5, "spill": 0.7}}),
        _fx_doc({"fill": "#6496C8", "lut": {"src": "assets/look.cube", "mix": 0.7}}),
    ]
    folder = studio_doc.folder(tmp_path, "fx")
    (folder / "assets").mkdir(parents=True, exist_ok=True)
    (folder / "assets" / "look.cube").write_text(cube)
    expected = _node_render([{"doc": d, "t": 0.5, "kind": "colour", "cube": cube} for d in colour_cases])
    for doc, want in zip(colour_cases, expected, strict=True):
        studio_doc.save(tmp_path, "fx", doc)
        got = _png_pixel(renderer.still(tmp_path, "fx", [0.5], scale=1.0, out=tmp_path / "c.png"), 50, 50)
        assert all(abs(g - w) <= 2 for g, w in zip(got[:3], want, strict=True)), (doc["layers"][0], got, want)

    def moving(**layer) -> dict:
        doc = _fx_doc({"w": 200, "h": 16, "fill": "#FFFFFF", "props": {"x": 500, "y": 8}, **layer})
        doc.update(width=1000, height=16)
        return doc

    edge_cases = [(moving(keys={"x": [{"t": 0, "v": 250, "ease": e}, {"t": 1, "v": 850}]}), 0.3)
                  for e in ("cubic-bezier(0.3,1.4,0.6,1)", "outBounce", "inBack", "outElastic", "inOut")]
    edge_cases.append((moving(transIn={"type": "slide-right", "duration": 1, "ease": "outBack"}), 0.4))
    edge_cases.append((moving(transOut={"type": "slide-left", "duration": 1, "ease": "cubic-bezier(0.1,0.7,0.2,1)"}), 1.6))
    expected = _node_render([{"doc": d, "t": t, "kind": "edge"} for d, t in edge_cases])
    for (doc, t), want in zip(edge_cases, expected, strict=True):
        studio_doc.save(tmp_path, "fx", doc)
        got = _row_edge(renderer.still(tmp_path, "fx", [t], scale=1.0, out=tmp_path / "e.png"), 8)
        assert abs(got - want) < 0.6, (doc["layers"][0], got, want)


@needs_tools
def test_big_clips_get_a_proxy_and_the_editor_hears_about_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import subprocess
    import time

    from fastapi.testclient import TestClient

    from web import server

    clip = tmp_path / "big.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=10:duration=1",
                    "-pix_fmt", "yuv420p", str(clip)], check=True, timeout=60)
    monkeypatch.setenv("SQUIDWINGS_WORKSPACE", str(tmp_path))
    studio_doc.save(tmp_path, "demo", _doc())
    client = TestClient(server.app)
    session = "sess-abcdef123456"
    up = client.post(f"/api/studio/{session}/project/demo/asset?file=big.mp4", content=clip.read_bytes()).json()
    assert up["kind"] == "video" and up["proxy"] and up["proxy"]["kind"] == "proxy", "a 1280 px clip gets a proxy"
    for _ in range(300):
        state = client.get(f"/api/studio/{session}/project/demo/proxies").json()["proxies"]["assets/big.mp4"]
        if state["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert state["ready"], state
    proxy = client.get(f"/api/studio/{session}/project/demo/proxy/big.mp4")
    assert proxy.status_code == 200 and proxy.headers["content-type"] == "video/mp4"
    made = renderer.proxy_path(studio_doc.folder(tmp_path, "demo"), "assets/big.mp4")
    assert renderer.probe(made).width == 960 and renderer.probe(made).height == 540

    events = client.get(f"/api/studio/{session}/events?project=demo&once=1")
    assert events.headers["content-type"].startswith("text/event-stream")
    body = events.text
    assert "event: doc\ndata: {\"stamp\":" in body and "event: render" in body
    assert '"assets/big.mp4":{"ready":true' in body


def test_filters_fade_and_smooth_keys_are_validated():
    doc = _fx_doc({"props": {"x": 50, "y": 50, "fade": 0.3}, "filter": {"preset": "Vintage", "amount": 0.8},
                   "keys": {"x": [{"t": 0, "v": 10, "ease": "smooth"}, {"t": 1, "v": 90, "ease": "smooth"}]}},
                  grade={"preset": "Noir", "amount": 1, "saturation": -1, "contrast": 0.45, "vignette": 0.6})
    clean, errors = studio_doc.validate(doc)
    assert errors == []
    assert clean["grade"]["vignette"] == 0.6 and clean["grade"]["hue"] == 0.0 and clean["layers"][0]["filter"]["preset"] == "Vintage"
    _, errors = studio_doc.validate(_fx_doc({"props": {"x": 1, "y": 1, "fade": 2}}, grade={"vignette": -1}))
    assert any("fade" in e for e in errors) and any("grade.vignette" in e for e in errors)
    clean, _ = studio_doc.validate(_fx_doc({}, grade={}))
    assert "grade" not in clean, "an empty filter is dropped"


def _node_eval(expr_cases: list) -> list:
    """Evaluate small expressions against render.js in node (R = window.StudioRender, c = the case)."""
    import subprocess

    script = r"""
      global.window = {}; global.document = { createElement: () => ({}) };
      require(process.argv[1]);
      const R = window.StudioRender;
      console.log(JSON.stringify(JSON.parse(process.argv[2]).map(([expr, c]) => Function('R', 'c', 'return ' + expr)(R, c))));
    """
    render_js = Path(__file__).resolve().parents[1] / "addons" / "studio" / "web" / "static" / "render.js"
    done = subprocess.run(["node", "-e", script, str(render_js), json.dumps(expr_cases)], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(done.stdout)


@needs_tools
@needs_node
def test_smooth_keys_fade_and_the_video_filter_match_between_preview_and_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fonts, "ttf", lambda family, weight=400: fonts.system_font())
    keys = {"x": [{"t": 0, "v": 250, "ease": "smooth"}, {"t": 0.5, "v": 650, "ease": "smooth"}, {"t": 1.2, "v": 420, "ease": "smooth"},
                  {"t": 2, "v": 700}]}
    doc = _fx_doc({"w": 200, "h": 16, "fill": "#FFFFFF", "props": {"x": 500, "y": 8}, "keys": keys})
    doc.update(width=1000, height=16)
    times = [0.2, 0.7, 1.5]
    expected = _node_eval([["R.valueAt(c.keys, 'x', c.t, 0) - 100", {"keys": keys, "t": t}] for t in times])
    studio_doc.save(tmp_path, "fx", doc)
    for t, want in zip(times, expected, strict=True):
        got = _row_edge(renderer.still(tmp_path, "fx", [t], scale=1.0, out=tmp_path / "s.png"), 8)
        assert abs(got - want) < 0.6, (t, got, want)
    assert expected[0] > 250 and not any(abs(e + 100 - 650) < 1e-6 for e in expected), "the curve passes through, it does not stop"

    grade = {"brightness": 0.05, "contrast": 0.2, "saturation": -0.3, "hue": 12, "temperature": 0.25, "fade": 0.35, "vignette": 0.7}
    doc = _fx_doc({"fill": "#5A8CC8", "props": {"x": 50, "y": 50, "fade": 0.4}}, grade=grade)
    studio_doc.save(tmp_path, "fx", doc)
    png = renderer.still(tmp_path, "fx", [0.5], scale=1.0, out=tmp_path / "g.png")
    order = ["brightness", "contrast", "saturation", "hue", "temperature", "fade", "vignette"]
    expr = ("(() => { const rgb = [0x5A / 255, 0x8C / 255, 0xC8 / 255]; R.colorOps([0, 0, 0, 0, 0, 0.4], rgb); R.colorOps(c.g, rgb);"
            " const k = R.vignetteAt(c.g[6], c.x, c.y, 100, 100); return rgb.map(v => v * k * 255); })()")
    points = [(50, 50), (4, 4), (80, 20)]
    expected = _node_eval([[expr, {"g": [grade[k] for k in order], "x": x, "y": y}] for x, y in points])
    for (x, y), want in zip(points, expected, strict=True):
        got = _png_pixel(png, x, y)
        assert all(abs(g - w) <= 2 for g, w in zip(got[:3], want, strict=True)), ((x, y), got, want)
