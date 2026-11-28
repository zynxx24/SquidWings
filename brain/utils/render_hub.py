"""One place that decides how every render uses this machine: threads, memory, priority, GPU, and turn-taking.

Before, only the Studio add-on (its C engine, ``addons/studio/engine/sqengine.c``) had these rules; HyperFrames,
Remotion and the ffmpeg conversions ran at full priority, with Chrome workers chosen by the tool, on the CPU encoder,
and several at once. Now every render path asks here:

* ``threads()``: CPU threads for a renderer (all but one; ``RENDER_THREADS``). The C engine splits frames over them.
* ``chrome_workers()``: HyperFrames' Chrome workers (each one a browser, ~300 MB): at most 4, fewer on a small
  machine or with little free memory (``RENDER_WORKERS``).
* ``mem_mb()``: what the C engine may allocate (``RENDER_MEM_MB``, default 2048).
* ``low_priority()``: renders run below normal CPU and disk priority, so the laptop stays responsive
  (``RENDER_NICE=off`` to switch off).
* ``encoder()``: the H.264 encoder. ``RENDER_GPU=auto`` (default) tests NVENC, Quick Sync, VAAPI, VideoToolbox or AMF
  with a real one-second encode, once per ffmpeg build, and keeps the first that works; ``off`` means the CPU
  (libx264); a name forces that one. On a GPU encode the CPU stays nearly idle.
* ``render_slot()``: one heavy render at a time on this machine (across processes: terminal, web, Telegram); the
  next one waits its turn instead of halving the speed of both.

The older ``STUDIO_*`` names still work.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.utils.logger import get_logger
from brain.utils.platform_compat import IS_MAC, IS_WINDOWS, ProcessLock, low_priority_kwargs

logger = get_logger(__name__)


class RenderHubError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    """RENDER_<name>, else the older STUDIO_<name>."""
    return (os.getenv(f"RENDER_{name}") or os.getenv(f"STUDIO_{name}") or default).strip()


# ── how much of the machine ─────────────────────────────────────────────────

def threads() -> int:
    try:
        value = int(_env("THREADS", "0"))
    except ValueError:
        value = 0
    return value if value > 0 else max(1, (os.cpu_count() or 2) - 1)


def mem_mb() -> int:
    try:
        return max(64, int(_env("MEM_MB", "2048")))
    except ValueError:
        return 2048


def free_mb() -> int:
    """Memory still available, in MB (0 when the platform does not say)."""
    with contextlib.suppress(OSError, ValueError):
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    with contextlib.suppress(ImportError, AttributeError):
        import psutil

        return int(psutil.virtual_memory().available // 2**20)
    return 0


def chrome_workers() -> int:
    """Chrome workers for a HyperFrames render: 4 is the sweet spot; fewer on few cores or little free memory."""
    with contextlib.suppress(ValueError):
        forced = int(_env("WORKERS", "0"))
        if forced > 0:
            return min(forced, 24)
    by_cpu = max(1, (os.cpu_count() or 2) // 3)
    free = free_mb()
    by_memory = max(1, (free - 1024) // 350) if free else 4
    return max(1, min(4, by_cpu, by_memory))


def remotion_concurrency() -> int:
    with contextlib.suppress(ValueError):
        forced = int(os.getenv("VIDEO_RENDER_CONCURRENCY", "") or _env("WORKERS", "0"))
        if forced > 0:
            return forced
    return max(1, min(threads() // 2, 6))


def low_priority() -> dict[str, Any]:
    """Popen keyword arguments that start a render below normal priority."""
    if _env("NICE", "1").lower() in {"0", "off", "false", "no"}:
        return {}
    return low_priority_kwargs(10)


def ffmpeg() -> str:
    from brain.utils import toolkit

    exe = toolkit.ffmpeg_exe()
    if not exe:
        raise RenderHubError("ffmpeg tidak ditemukan. Pasang ffmpeg dulu (python main.py tools, atau paket sistem).")
    return exe


# ── the encoder ──────────────────────────────────────────────────────────────

@dataclass
class Encoder:
    name: str
    gpu: bool
    pre: list[str] = field(default_factory=list)     # before the inputs (device setup)
    vf: str = ""                                    # video filter before encoding
    args: list[str] = field(default_factory=list)    # codec and quality

    def argv(self, extra_vf: str = "") -> tuple[list[str], list[str]]:
        """(arguments before the inputs, arguments after them) for one ffmpeg command."""
        filters = ",".join(part for part in (extra_vf, self.vf) if part)
        return list(self.pre), [*(["-vf", filters] if filters else []), *self.args]


QUALITY = {"high": (18, 20), "normal": (23, 24), "small": (28, 30)}   # (crf for x264, qp for GPU encoders)


def vaapi_device() -> str:
    found = sorted(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").is_dir() else []
    return str(found[0]) if found else ""


def candidates(quality: str, width: int, height: int, fps: float) -> list[Encoder]:
    _, qp = QUALITY.get(quality, QUALITY["high"])
    bitrate = f"{max(1, int(width * height * fps * 0.12 / 1000))}k"
    gpus: list[Encoder] = [Encoder("nvenc", True, args=["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(qp),
                                                        "-pix_fmt", "yuv420p"])]
    if IS_MAC:
        gpus.append(Encoder("videotoolbox", True, args=["-c:v", "h264_videotoolbox", "-b:v", bitrate, "-pix_fmt", "yuv420p"]))
    if IS_WINDOWS:
        gpus.append(Encoder("amf", True, args=["-c:v", "h264_amf", "-rc", "cqp", "-qp_i", str(qp), "-qp_p", str(qp),
                                               "-pix_fmt", "yuv420p"]))
    gpus.append(Encoder("qsv", True, args=["-c:v", "h264_qsv", "-global_quality", str(qp + 2), "-pix_fmt", "nv12"]))
    device = vaapi_device()
    if device:
        gpus.append(Encoder("vaapi", True, pre=["-vaapi_device", device], vf="format=nv12,hwupload",
                            args=["-c:v", "h264_vaapi", "-qp", str(qp)]))
    return gpus


def cpu_encoder(quality: str = "high", preset: str = "medium") -> Encoder:
    crf, _ = QUALITY.get(quality, QUALITY["high"])
    return Encoder("cpu", False, args=["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
                                       "-threads", str(threads())])


def works(enc: Encoder) -> bool:
    argv = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", *enc.pre, "-f", "lavfi", "-i",
            "color=c=gray:s=320x240:d=0.3", "-frames:v", "5", *(["-vf", enc.vf] if enc.vf else []), *enc.args, "-f", "null", "-"]
    try:
        return subprocess.run(argv, capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _gpu_cache_file() -> Path:
    from brain.config import paths

    return paths.data_dir() / "render_gpu.json"


_NEGATIVE_TTL_S = 6 * 3600


def _device_state() -> str:
    """Which GPU devices this process may open now: the test result depends on it (a desktop session's ACL comes
    and goes; group membership does not)."""
    parts = []
    for device in (*sorted(Path("/dev/dri").glob("renderD*")), Path("/dev/nvidia0")):
        if device.exists():
            parts.append(f"{device.name}={int(os.access(device, os.R_OK | os.W_OK))}")
    return ",".join(parts)


def gpu_note() -> str:
    """A hint when the machine has a GPU this user cannot open (Linux: not in the render group)."""
    blocked = [d for d in sorted(Path("/dev/dri").glob("renderD*")) if not os.access(d, os.R_OK | os.W_OK)]
    if not blocked:
        return ""
    return ("GPU ada tapi tidak bisa dibuka oleh user ini, jadi render memakai CPU. Sekali saja: "
            "sudo usermod -aG render,video $USER lalu logout dan login lagi.")


def detected_gpus() -> list[str]:
    """GPU encoders that passed a real test on this machine. Cached per ffmpeg build and device access; a result
    with no working GPU is tested again after a few hours."""
    exe = ffmpeg()
    stamp = f"{exe}:{Path(exe).stat().st_mtime if Path(exe).exists() else 0}:{_device_state()}"
    cache = _gpu_cache_file()
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(cache.read_text(encoding="utf-8"))
        fresh = data.get("working") or time.time() - float(data.get("at", 0)) < _NEGATIVE_TTL_S
        if data.get("ffmpeg") == stamp and fresh:
            return list(data.get("working", []))
    working = [enc.name for enc in candidates("high", 1920, 1080, 30) if works(enc)]
    with contextlib.suppress(OSError):
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"ffmpeg": stamp, "working": working, "at": time.time()}), encoding="utf-8")
    return working


def encoder(gpu: str = "", quality: str = "high", width: int = 1920, height: int = 1080, fps: float = 30) -> Encoder:
    """The H.264 encoder for a render. gpu: auto (default), off, or an encoder name (nvenc, qsv, vaapi, ...)."""
    choice = (gpu or _env("GPU", "auto")).lower()
    if choice in {"off", "cpu", "0", "false", "no"}:
        return cpu_encoder(quality)
    options = {enc.name: enc for enc in candidates(quality, width, height, fps)}
    try:
        working = detected_gpus()
    except RenderHubError:
        return cpu_encoder(quality)
    if choice != "auto":
        if choice in options and choice in working:
            return options[choice]
        logger.warning(f"[render] GPU encoder {choice} not usable here; using the CPU")
        return cpu_encoder(quality)
    for name in working:
        if name in options:
            return options[name]
    return cpu_encoder(quality)


def gpu_ready() -> bool:
    """A hardware encoder works here (and is not switched off)."""
    return encoder().gpu


# ── turn-taking ──────────────────────────────────────────────────────────────

@contextlib.asynccontextmanager
async def render_slot(label: str = "render", poll_s: float = 2.0) -> AsyncIterator[None]:
    """Hold the machine's render slot: one heavy render at a time, across every SquidWings process and every event
    loop in this one (the web server's, the Telegram connector's). A file lock, so it binds to no loop."""
    from brain.config import paths

    lock = ProcessLock(paths.data_dir() / "render.lock")
    started = time.monotonic()
    while not lock.acquire():
        if time.monotonic() - started > 6 * 3600:
            raise RenderHubError("render lain sudah berjalan lebih dari 6 jam; coba lagi nanti")
        await asyncio.sleep(poll_s)
    try:
        logger.info(f"[render] {label}: started")
        yield
    finally:
        lock.release()


@contextlib.contextmanager
def slot_sync(label: str = "render", cancel: Any = None, poll_s: float = 2.0) -> Iterator[None]:
    """The same slot for a render that runs in a thread (the Studio's exports). ``cancel``: a threading.Event."""
    from brain.config import paths

    lock = ProcessLock(paths.data_dir() / "render.lock")
    started = time.monotonic()
    while not lock.acquire():
        if cancel is not None and cancel.is_set():
            raise RenderHubError("Dibatalkan")
        if time.monotonic() - started > 6 * 3600:
            raise RenderHubError("render lain sudah berjalan lebih dari 6 jam; coba lagi nanti")
        time.sleep(poll_s)
    try:
        logger.info(f"[render] {label}: started")
        yield
    finally:
        lock.release()


_RENDER_COMMAND = re.compile(
    r"(^|[\s;&|(/])(ffmpeg|sqengine\s+(video|still)|blender\b[^;&|]*\s-b\b)\b"
    r"|\b(hyperframes|remotion)\s+render\b|\bexport\.mjs\b|\bmotlab\b[^;&|]*\brender\b|\bcompose_into_clip\.py\b",
    re.IGNORECASE)


def is_render_command(command: str) -> bool:
    """A shell command that renders or encodes video (so it waits its turn and runs at low priority)."""
    return bool(_RENDER_COMMAND.search(command or ""))


def lower_priority(command: str) -> str:
    """The command run below normal CPU and disk priority (POSIX ``nice`` and ``ionice``); unchanged on Windows,
    when already lowered, or with RENDER_NICE=off."""
    if IS_WINDOWS or not low_priority() or command.lstrip().startswith(("nice ", "ionice ")):
        return command
    import shutil as _shutil

    io = "ionice -c3 " if _shutil.which("ionice") else ""
    return f"{io}nice -n 10 sh -c {shlex.quote(command)}"


def plan() -> dict[str, Any]:
    """What a render on this machine will use, for the tools' answers and the web page."""
    enc = encoder()
    return {"threads": threads(), "chrome_workers": chrome_workers(), "remotion_concurrency": remotion_concurrency(),
            "mem_mb": mem_mb(), "free_mb": free_mb(), "encoder": enc.name, "gpu": enc.gpu,
            "low_priority": bool(low_priority()), **({"gpu_note": note} if (note := gpu_note()) else {})}


def _main(argv: list[str]) -> int:
    """``python -m brain.utils.render_hub encoder [--quality high|normal|small]``: the encoder as JSON, for scripts
    (the animate skill's export.mjs) that run ffmpeg themselves. ``plan``: what a render here will use."""
    import argparse

    parser = argparse.ArgumentParser(prog="render_hub")
    parser.add_argument("what", choices=["encoder", "plan"])
    parser.add_argument("--quality", default="high", choices=sorted(QUALITY))
    args = parser.parse_args(argv)
    if args.what == "plan":
        print(json.dumps(plan()))
        return 0
    enc = encoder(quality=args.quality)
    print(json.dumps({"name": enc.name, "gpu": enc.gpu, "pre": enc.pre, "vf": enc.vf, "args": enc.args}))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
