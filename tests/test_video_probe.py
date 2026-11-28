"""The layout probe measures what `hyperframes check` misses: text out of its box (SVG too), frames that are
mostly empty, and one layout in every scene. Real headless Chrome on a local page; offline."""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.utils import video_probe

needs_chrome = pytest.mark.skipif(video_probe.browser() is None, reason="needs Chrome or Chromium")

SCENE = """<div id="s{i}" class="clip" data-start="{start}" data-duration="3" style="position:absolute;inset:0">
  <div class="card" style="position:absolute;left:560px;top:340px;width:800px;height:400px;background:#fff;border-radius:24px">
    <h2 style="font:700 72px sans-serif;margin:60px;color:#123">Tips {i}</h2>
    {extra}
  </div>
</div>"""

TICKET = """<svg width="280" height="280" viewBox="0 0 200 200"><rect x="30" y="45" width="140" height="100" fill="#222"/>
  <rect x="42" y="57" width="116" height="76" fill="#fff"/>
  <text x="100" y="98" font-family="serif" font-size="28" font-weight="bold" text-anchor="middle">NOMOR ANTREAN 042</text></svg>"""


def _project(tmp_path: Path, scenes: str) -> Path:
    folder = tmp_path / "vid"
    folder.mkdir()
    (folder / "index.html").write_text(f"""<!doctype html><html><body style="margin:0;background:#eee">
<div id="root" data-composition-id="root" data-width="1920" data-height="1080" data-duration="9"
     style="position:relative;width:1920px;height:1080px;overflow:hidden">{scenes}</div></body></html>""", encoding="utf-8")
    return folder


@needs_chrome
@pytest.mark.usefixtures("loopback_only")
async def test_the_probe_finds_an_svg_label_out_of_its_box_and_one_card_for_every_scene(tmp_path: Path) -> None:
    scenes = "".join(SCENE.format(i=i, start=i * 3, extra=TICKET if i == 1 else "") for i in range(3))
    result = await video_probe.probe(_project(tmp_path, scenes), timeout=30)
    codes = [f["code"] for f in result["findings"] if f["code"] != "no_motion"]   # the fixture has no animation
    assert result["samples"] == 3
    assert "text_overflows_box" in codes and "same_layout_every_scene" in codes
    overflow = next(f for f in result["findings"] if f["code"] == "text_overflows_box")
    assert overflow["text"] == "NOMOR ANTREAN 042" and overflow["time"] == 5.1


@needs_chrome
@pytest.mark.usefixtures("loopback_only")
async def test_varied_scenes_that_fit_raise_nothing(tmp_path: Path) -> None:
    varied = [
        '<h1 style="position:absolute;left:120px;top:300px;font:800 160px sans-serif;margin:0">Antre tertib</h1>',
        '<div style="position:absolute;left:0;top:0;width:900px;height:1080px;background:#0B3C49"></div>'
        '<p style="position:absolute;left:1000px;top:420px;font:600 72px sans-serif;margin:0">Ambil nomor</p>',
        '<div style="position:absolute;left:660px;top:190px;width:600px;height:600px;border-radius:50%;background:#C2553B"></div>'
        '<p style="position:absolute;left:760px;top:820px;font:600 64px sans-serif;margin:0">Lansia dulu</p>',
    ]
    scenes = "".join(f'<div class="clip" data-start="{i * 3}" data-duration="3" style="position:absolute;inset:0">{body}</div>'
                     for i, body in enumerate(varied))
    result = await video_probe.probe(_project(tmp_path, scenes), timeout=30)
    assert result["samples"] == 3 and [f["code"] for f in result["findings"]] == ["no_motion"], "only the missing motion"


async def test_without_a_browser_the_probe_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(video_probe, "browser", lambda: None)
    assert (await video_probe.probe(_project(tmp_path, ""))) == {"skipped": "no Chrome or Chromium on this machine"}


def test_a_blank_cut_is_not_an_empty_frame_and_small_drift_is_the_same_card() -> None:
    samples = [{"time": t, "painted": 0.3, "mainBox": [7, 9 + (t % 2), 36, 11], "overflow": [], "offCanvas": []} for t in range(4)]
    codes = [f["code"] for f in video_probe.findings({"samples": samples, "timelines": 1})]
    assert codes == ["same_layout_every_scene"]
    samples[1]["painted"] = 0.05
    assert "frame_mostly_empty" in [f["code"] for f in video_probe.findings({"samples": samples})]
    still = [f["code"] for f in video_probe.findings({"samples": samples, "timelines": 0, "animations": 0, "silenced": 4})]
    assert "no_motion" in still and "check_silenced" in still, "a slideshow of stills and a muted check are named"
    samples[1]["bigText"] = 0.15                         # a big headline alone is a title card, not an empty frame
    assert "frame_mostly_empty" not in [f["code"] for f in video_probe.findings({"samples": samples})]


async def test_templates_wait_for_the_first_model_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from web import server, team_api

    registry: list[SimpleNamespace] = []
    manager = SimpleNamespace(orchestrator=SimpleNamespace(registry=SimpleNamespace(get_all=lambda: registry)))
    monkeypatch.setattr(team_api, "service", lambda request: manager)

    async def sync(force: bool = False) -> dict[str, int]:
        registry.append(SimpleNamespace(id="m/model", supports_tools=True))
        return {"router": 1}

    monkeypatch.setattr(server, "ensure_models", sync)
    listed = await team_api.list_templates(None)  # type: ignore[arg-type]
    assert any(t["key"] == "code-video" for t in listed["templates"]), "not an empty list just after a start"
