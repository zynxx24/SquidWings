"""Every render asks one hub: workers sized to the machine, low priority, the GPU when it works, one at a time."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from brain.utils import render_hub


def test_chrome_workers_fit_the_cores_and_the_free_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RENDER_WORKERS", raising=False)
    monkeypatch.setattr(render_hub.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(render_hub, "free_mb", lambda: 7000)
    assert render_hub.chrome_workers() == 4, "the sweet spot on a roomy machine"
    monkeypatch.setattr(render_hub, "free_mb", lambda: 1500)
    assert render_hub.chrome_workers() == 1, "little free memory: one browser"
    monkeypatch.setattr(render_hub.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(render_hub, "free_mb", lambda: 9000)
    assert render_hub.chrome_workers() == 1
    monkeypatch.setenv("RENDER_WORKERS", "6")
    assert render_hub.chrome_workers() == 6


@pytest.mark.parametrize("command,render", [
    ("ffmpeg -i a.mp4 b.mp4", True), ("npx hyperframes render -o out.mp4", True), ("npx remotion render Main out.mp4", True),
    ("node tools/export.mjs pieces/x --share", True), ("cd x && /usr/bin/ffmpeg -y -i a b", True),
    ("npm run dev", False), ("ffprobe a.mp4", False), ("ls renders", False), ("python3 make_ffmpeg_list.py", False),
])
def test_render_commands_are_recognised(command: str, render: bool) -> None:
    assert render_hub.is_render_command(command) is render


def test_a_render_command_is_lowered_once(monkeypatch: pytest.MonkeyPatch) -> None:
    if render_hub.IS_WINDOWS:
        pytest.skip("nice is POSIX")
    monkeypatch.delenv("RENDER_NICE", raising=False)
    lowered = render_hub.lower_priority("ffmpeg -i 'a b.mp4' out.mp4")
    assert "nice -n 10 sh -c" in lowered and "'a b.mp4'" in lowered.replace("'\"'\"'", "'")
    assert render_hub.lower_priority(lowered) == lowered
    monkeypatch.setenv("RENDER_NICE", "off")
    assert render_hub.lower_priority("ffmpeg -i a b") == "ffmpeg -i a b"


def test_the_encoder_follows_the_test_and_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render_hub, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(render_hub, "vaapi_device", lambda: "/dev/dri/renderD128")
    monkeypatch.setattr(render_hub, "detected_gpus", lambda: ["vaapi"])
    enc = render_hub.encoder()
    assert enc.name == "vaapi" and enc.gpu and enc.argv("scale=640:-2")[1][:2] == ["-vf", "scale=640:-2,format=nv12,hwupload"]
    assert render_hub.encoder("off").name == "cpu"
    monkeypatch.setattr(render_hub, "detected_gpus", lambda: [])
    assert render_hub.encoder().name == "cpu" and not render_hub.gpu_ready()


def test_a_failed_gpu_test_is_retried_when_device_access_changes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(render_hub, "ffmpeg", lambda: "/usr/bin/true")
    monkeypatch.setattr(render_hub, "_gpu_cache_file", lambda: tmp_path / "gpu.json")
    monkeypatch.setattr(render_hub, "candidates", lambda *a: [render_hub.Encoder("vaapi", True)])
    state = {"value": "renderD128=0"}
    monkeypatch.setattr(render_hub, "_device_state", lambda: state["value"])
    monkeypatch.setattr(render_hub, "works", lambda enc: calls.append(enc.name) or state["value"].endswith("1"))
    assert render_hub.detected_gpus() == [] and calls == ["vaapi"]
    assert render_hub.detected_gpus() == [] and calls == ["vaapi"], "cached while nothing changed"
    state["value"] = "renderD128=1"          # the user joined the render group
    assert render_hub.detected_gpus() == ["vaapi"] and len(calls) == 2
    cached = json.loads((tmp_path / "gpu.json").read_text())
    assert cached["working"] == ["vaapi"]


async def test_renders_take_turns() -> None:
    order: list[str] = []

    async def job(name: str) -> None:
        async with render_hub.render_slot(name, poll_s=0.01):
            order.append(f"{name}+")
            await asyncio.sleep(0.05)
            order.append(f"{name}-")

    await asyncio.gather(job("a"), job("b"))
    assert order in (["a+", "a-", "b+", "b-"], ["b+", "b-", "a+", "a-"]), "never two renders at once"


async def test_video_render_uses_the_hub_and_falls_back_to_the_cpu(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools.toolbox import media_tools

    project = tmp_path / "vid"
    project.mkdir()
    (project / "hyperframes.json").write_text("{}")
    runs: list[list[str]] = []

    async def fake_run(argv: list[str], cwd: Path, timeout_s: float, env: Any = None, low_priority: bool = False):
        assert low_priority, "renders run below normal priority"
        runs.append(argv)
        if "--gpu" in argv:
            return 1, "gpu encoder failed", False
        dest = Path(argv[argv.index("-o") + 1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00" * 10)
        return 0, "", False

    monkeypatch.setattr(media_tools, "_run", fake_run)
    monkeypatch.setattr(media_tools, "_hyperframes", lambda: ["hyperframes"])
    monkeypatch.setattr(render_hub, "gpu_ready", lambda: True)
    monkeypatch.setattr(render_hub, "chrome_workers", lambda: 3)
    result = await media_tools.video_render("vid", share=False, workspace_path=str(tmp_path))
    assert result["ok"] and result["render"] == {"workers": 3, "gpu_encode": False}
    assert "--gpu" in runs[0] and "--gpu" not in runs[1] and runs[0][runs[0].index("--workers") + 1] == "3"


def test_the_slot_works_from_two_event_loops_at_once() -> None:
    import threading

    order: list[str] = []

    async def job(name: str) -> None:
        async with render_hub.render_slot(name, poll_s=0.01):
            order.append(f"{name}+")
            await asyncio.sleep(0.05)
            order.append(f"{name}-")

    other = threading.Thread(target=lambda: asyncio.run(job("telegram")))
    other.start()
    asyncio.run(job("web"))
    other.join()
    assert len(order) == 4 and order[0][:-1] == order[1][:-1], "one render finishes before the next starts"
