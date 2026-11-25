"""The Studio's server-side renderer: builds the C engine, turns motion.json into a scene, exports.

Why on the server: the editor used to render and encode every frame in the browser, so an export
from a phone ran on the phone and died when its screen went off. Now the machine that serves the
editor does the work with ``engine/sqengine.c`` (CPU, threads, memory cap) and ffmpeg (decode and
encode, on the GPU when there is one), and an export keeps going whoever is watching.

Pieces:

* ``build()``: compiles the engine once (and again when the source changes) with the first C compiler
  it finds: ``$CC``, cc, gcc, clang, MSVC ``cl``, or ``python -m ziglang cc`` (a compiler from pip,
  installed with the add-on, so Windows needs no Visual Studio).
* ``scene()``: motion.json -> the engine's scene file (fonts, pictures, video decoders, layers, keys).
* ``encoder()``: the video encoder to use. ``STUDIO_GPU=auto`` (default) tries NVENC, Quick Sync,
  VAAPI, VideoToolbox or AMF with a one-second test and keeps the first that works; ``off`` is CPU
  (libx264); a name forces that one.
* ``export()`` and ``still()``: the work itself, with progress callbacks.
* ``make_proxy()``: a small copy of a big video clip (960 px, a keyframe every half second) that the
  editor plays and scrubs instead of the original, so editing on a phone stays smooth. Exports and
  stills always read the original.

Resource control: ``STUDIO_THREADS`` (default: all cores but one), ``STUDIO_MEM_MB`` (default 2048),
``STUDIO_NICE`` (default on: the render runs below normal priority so the machine stays responsive).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from addons.studio import fonts, studio_doc
from brain.config.settings import get_settings
from brain.utils.logger import get_logger
from brain.utils.platform_compat import IS_MAC, IS_WINDOWS, kill_tree, low_priority_kwargs

logger = get_logger(__name__)

ENGINE_DIR = Path(__file__).resolve().parent / "engine"
SOURCE = ENGINE_DIR / "sqengine.c"
AUDIO_RATE = 48000

Progress = Callable[[float, str], None]


class RenderError(RuntimeError):
    pass


# ── settings ─────────────────────────────────────────────────────────────────

def threads() -> int:
    try:
        value = int(os.getenv("STUDIO_THREADS", "0"))
    except ValueError:
        value = 0
    return value if value > 0 else max(1, (os.cpu_count() or 2) - 1)


def mem_mb() -> int:
    try:
        return max(64, int(os.getenv("STUDIO_MEM_MB", "2048")))
    except ValueError:
        return 2048


def _popen_kwargs() -> dict[str, Any]:
    nice = os.getenv("STUDIO_NICE", "1").strip().lower() not in {"0", "off", "false", "no"}
    return low_priority_kwargs(10) if nice else {}


def ffmpeg() -> str:
    from brain.utils import toolkit

    exe = toolkit.ffmpeg_exe()
    if not exe:
        raise RenderError("ffmpeg tidak ditemukan. Pasang dengan: python main.py addon install studio")
    return exe


def command_line(argv: list[str]) -> str:
    """One shell string for popen() in the engine: cmd.exe rules on Windows, sh rules elsewhere."""
    return subprocess.list2cmdline(argv) if IS_WINDOWS else shlex.join(argv)


# ── building the engine ──────────────────────────────────────────────────────

def binary_path() -> Path:
    machine = platform.machine().lower() or "cpu"
    return ENGINE_DIR / "bin" / f"sqengine-{sys.platform}-{machine}{'.exe' if IS_WINDOWS else ''}"


def _compilers(out: Path) -> list[list[str]]:
    src = str(SOURCE)
    unix_flags = ["-O2", "-std=c99", "-o", str(out), src, "-lm"]
    found: list[list[str]] = []
    env_cc = os.getenv("CC", "").strip()
    if env_cc:
        found.append([*shlex.split(env_cc), *unix_flags, *([] if IS_WINDOWS else ["-lpthread"])])
    for name in ("cc", "gcc", "clang"):
        if shutil.which(name):
            found.append([name, *unix_flags, *([] if IS_WINDOWS else ["-lpthread"])])
    if IS_WINDOWS and shutil.which("cl"):
        found.append(["cl", "/nologo", "/O2", src, f"/Fe:{out}"])
    try:
        import importlib.util

        if importlib.util.find_spec("ziglang") is not None:
            found.append([sys.executable, "-m", "ziglang", "cc", *unix_flags, *([] if IS_WINDOWS else ["-lpthread"])])
    except (ImportError, ValueError):
        pass
    return found


_build_lock = threading.Lock()


def build(force: bool = False) -> Path:
    """The engine binary, compiled when missing or older than its source."""
    out = binary_path()
    with _build_lock:
        if not force and out.exists() and out.stat().st_mtime >= SOURCE.stat().st_mtime:
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        errors = []
        for argv in _compilers(out):
            try:
                done = subprocess.run(argv, cwd=ENGINE_DIR, capture_output=True, text=True, timeout=600, check=False)
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f"{argv[0]}: {exc}")
                continue
            if done.returncode == 0 and out.exists():
                logger.info(f"[studio] engine built with {argv[0]}: {out.name}")
                return out
            errors.append(f"{' '.join(argv[:3])}: {(done.stderr or done.stdout).strip()[-300:]}")
        hint = "pip install ziglang (compiler C tanpa admin, juga untuk Windows)"
        raise RenderError("Engine Studio belum bisa dikompilasi. " + hint + ("\n" + "\n".join(errors) if errors else ""))


def available() -> bool:
    try:
        return build().exists()
    except RenderError:
        return False


# ── probing media ────────────────────────────────────────────────────────────

@dataclass
class Media:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    audio: bool = False


_probe_cache: dict[tuple[str, float], Media] = {}


def probe(path: Path) -> Media:
    """Duration, size and whether there is sound, read from ffmpeg's own report."""
    key = (str(path), path.stat().st_mtime if path.exists() else 0)
    if key in _probe_cache:
        return _probe_cache[key]
    done = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)], capture_output=True, text=True,
                          timeout=60, check=False)
    text = done.stderr
    media = Media()
    if m := re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text):
        media.duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    if m := re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", text):
        media.width, media.height = int(m.group(1)), int(m.group(2))
    media.audio = "Audio:" in text
    _probe_cache[key] = media
    return media


# ── encoders ─────────────────────────────────────────────────────────────────

@dataclass
class Encoder:
    name: str
    gpu: bool
    pre: list[str] = field(default_factory=list)     # before the inputs (device setup)
    vf: str = ""                                    # video filter before encoding
    args: list[str] = field(default_factory=list)    # codec and quality


_QUALITY = {"high": (18, 20), "normal": (23, 24), "small": (28, 30)}   # (crf for x264, qp for GPU encoders)


def _vaapi_device() -> str:
    found = sorted(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").is_dir() else []
    return str(found[0]) if found else ""


def _candidates(quality: str, width: int, height: int, fps: float) -> list[Encoder]:
    crf, qp = _QUALITY.get(quality, _QUALITY["high"])
    bitrate = f"{max(1, int(width * height * fps * 0.12 / 1000))}k"
    gpus: list[Encoder] = [Encoder("nvenc", True, args=["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(qp), "-pix_fmt", "yuv420p"])]
    if IS_MAC:
        gpus.append(Encoder("videotoolbox", True, args=["-c:v", "h264_videotoolbox", "-b:v", bitrate, "-pix_fmt", "yuv420p"]))
    if IS_WINDOWS:
        gpus.append(Encoder("amf", True, args=["-c:v", "h264_amf", "-rc", "cqp", "-qp_i", str(qp), "-qp_p", str(qp), "-pix_fmt", "yuv420p"]))
    gpus.append(Encoder("qsv", True, args=["-c:v", "h264_qsv", "-global_quality", str(qp + 2), "-pix_fmt", "nv12"]))
    device = _vaapi_device()
    if device:
        gpus.append(Encoder("vaapi", True, pre=["-vaapi_device", device], vf="format=nv12,hwupload",
                            args=["-c:v", "h264_vaapi", "-qp", str(qp)]))
    return gpus


def cpu_encoder(quality: str = "high") -> Encoder:
    crf, _ = _QUALITY.get(quality, _QUALITY["high"])
    return Encoder("cpu", False, args=["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
                                       "-threads", str(threads())])


def _works(enc: Encoder) -> bool:
    argv = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", *enc.pre, "-f", "lavfi", "-i",
            "color=c=gray:s=320x240:d=0.3", "-frames:v", "5", *(["-vf", enc.vf] if enc.vf else []), *enc.args, "-f", "null", "-"]
    try:
        return subprocess.run(argv, capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _gpu_cache_file() -> Path:
    return get_settings().project_root / ".agent_memory" / "studio_gpu.json"


def detected_gpus() -> list[str]:
    """GPU encoders that passed a real test on this machine (cached until ffmpeg changes)."""
    exe = ffmpeg()
    stamp = f"{exe}:{Path(exe).stat().st_mtime if Path(exe).exists() else 0}"
    cache = _gpu_cache_file()
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("ffmpeg") == stamp:
            return list(data.get("working", []))
    except (OSError, ValueError):
        pass
    working = [enc.name for enc in _candidates("high", 1920, 1080, 30) if _works(enc)]
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"ffmpeg": stamp, "working": working}), encoding="utf-8")
    except OSError:
        pass
    return working


def encoder(gpu: str = "", quality: str = "high", width: int = 1920, height: int = 1080, fps: float = 30) -> Encoder:
    """The encoder for this export. gpu: auto (default), off, or an encoder name (nvenc, qsv, vaapi, ...)."""
    choice = (gpu or os.getenv("STUDIO_GPU", "auto")).strip().lower()
    if choice in {"off", "cpu", "0", "false", "no"}:
        return cpu_encoder(quality)
    options = {enc.name: enc for enc in _candidates(quality, width, height, fps)}
    working = detected_gpus()
    if choice != "auto":
        if choice in options and choice in working:
            return options[choice]
        logger.warning(f"[studio] GPU encoder {choice} not usable here; using the CPU")
        return cpu_encoder(quality)
    for name in working:
        if name in options:
            return options[name]
    return cpu_encoder(quality)


# ── scene ────────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return str(text).replace("%", "%25").replace("\t", "%09").replace("\n", "%0A").replace("\r", "%0D")


def _color(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("#"):
        return "none"
    h = value[1:]
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
    except ValueError:
        return "none"
    return f"{r:.5f}\t{g:.5f}\t{b:.5f}\t{a:.5f}"


_NUM_PROPS = ("x", "y", "scale", "scaleX", "scaleY", "rotation", "opacity", "blur", "draw",
              "brightness", "contrast", "saturation", "hue", "temperature")
_SIZE_PROPS = ("w", "h", "radius", "size", "strokeWidth")
_PROP_DEFAULTS = {"x": 0, "y": 0, "scale": 1, "scaleX": 1, "scaleY": 1, "rotation": 0, "opacity": 1, "blur": 0, "draw": 1,
                  "brightness": 0, "contrast": 0, "saturation": 0, "hue": 0, "temperature": 0}


def _tracks(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(t.get("id")): t for t in doc.get("tracks") or [] if isinstance(t, dict)}


def _off_track(item: dict[str, Any], tracks: dict[str, dict[str, Any]], *flags: str) -> bool:
    """True when the item sits on a track that is hidden (or muted, ...)."""
    track = tracks.get(str(item.get("track") or ""))
    return bool(track and any(track.get(flag) for flag in flags))


def _effects(layer: dict[str, Any], project: Path, lut_ids: dict[str, str], lines: list[str]) -> list[str]:
    """Scene lines for a layer's transitions, chroma key, LUT and mask (the colour props go as `num`)."""
    body: list[str] = []
    for side, field_name in (("in", "transIn"), ("out", "transOut")):
        tr = layer.get(field_name)
        if isinstance(tr, dict) and tr.get("type") not in (None, "none") and float(tr.get("duration") or 0) > 0:
            body.append(f"trans\t{side}\t{tr['type']}\t{float(tr['duration'])}\t{tr.get('ease') or 'out'}")
    chroma = layer.get("chroma")
    if isinstance(chroma, dict) and chroma.get("color"):
        rgb = _color(chroma["color"]).split("\t")[:3]
        if len(rgb) == 3:
            body.append("\t".join(["chroma", *rgb, str(float(chroma.get("similarity", 0.25))),
                                   str(float(chroma.get("smoothness", 0.08))), str(float(chroma.get("spill", 0.5)))]))
    lut = layer.get("lut")
    if isinstance(lut, dict) and lut.get("src") and (project / str(lut["src"])).is_file():
        src = str(lut["src"])
        lid = lut_ids.get(src)
        if lid is None:
            lid = lut_ids[src] = f"l{len(lut_ids)}"
            lines.append(f"lutfile\t{lid}\t{_esc(str(project / src))}")
        body.append(f"lut\t{lid}\t{float(lut.get('mix', 1))}")
    mask = layer.get("mask")
    if isinstance(mask, dict) and mask.get("shape") in ("rect", "ellipse"):
        body.append("\t".join(["mask", mask["shape"], *(str(float(mask.get(k) or 0)) for k in ("x", "y")),
                               str(float(mask.get("w", 200))), str(float(mask.get("h", 200))),
                               *(str(float(mask.get(k) or 0)) for k in ("radius", "feather")), "1" if mask.get("invert") else "0"]))
    return body


def _visible_times(layer: dict[str, Any], times: list[float]) -> list[float]:
    start, end = float(layer.get("start") or 0), layer.get("end")
    return [t for t in times if t >= start and (end is None or t < float(end))]


@dataclass
class Scene:
    text: str
    width: int
    height: int
    fps: float
    duration: float


def scene(doc: dict[str, Any], project: Path, *, times: list[float] | None = None, out_size: tuple[int, int] | None = None,
          gpu_decode: bool = False) -> Scene:
    """The engine's scene for this document. ``times``: the moments a still will show (video clips
    then decode from the first of them); None means the whole video from 0."""
    width_px, height_px = int(doc["width"]), int(doc["height"])
    fps, duration = float(doc.get("fps") or 30), float(doc.get("duration") or 1)
    lines = ["SQSCENE\t1", f"canvas\t{width_px}\t{height_px}\t{fps}\t{duration}"]
    if out_size:
        lines.append(f"output\t{out_size[0]}\t{out_size[1]}")
    bg = _color(doc.get("background") or "#000000")
    lines.append("background\t" + (bg if bg != "none" else "0\t0\t0\t1"))
    span = sorted(times) if times else [0.0]
    out_long = max(out_size or (width_px, height_px))
    font_ids: dict[tuple[str, int], str] = {}
    image_ids: dict[str, str] = {}
    lut_ids: dict[str, str] = {}
    tracks = _tracks(doc)
    body: list[str] = []
    for index, layer in enumerate(doc.get("layers") or []):
        if layer.get("hidden") or _off_track(layer, tracks, "hidden"):
            continue
        kind = layer.get("type")
        end = layer.get("end")
        body.append(f"layer\t{kind}\t{float(layer.get('start') or 0)}\t{'inf' if end is None else float(end)}")
        props = layer.get("props") or {}
        for name in _NUM_PROPS:
            value = props.get(name)
            body.append(f"num\t{name}\t{float(value) if isinstance(value, (int, float)) else _PROP_DEFAULTS[name]}")
        for name in _SIZE_PROPS:
            if isinstance(layer.get(name), (int, float)):
                body.append(f"num\t{name}\t{float(layer[name])}")
        if kind != "image" and kind != "video":
            body.append(f"col\tfill\t{_color(layer.get('fill'))}")
            body.append(f"col\tstroke\t{_color(layer.get('stroke'))}")
        for prop, frames in (layer.get("keys") or {}).items():
            for key in frames or []:
                ease = key.get("ease") or "linear"
                if prop in ("fill", "stroke"):
                    body.append(f"ckey\t{prop}\t{float(key['t'])}\t{ease}\t{_color(key.get('v'))}")
                elif prop != "volume" and isinstance(key.get("v"), (int, float)):
                    body.append(f"key\t{prop}\t{float(key['t'])}\t{float(key['v'])}\t{ease}")
        body += _effects(layer, project, lut_ids, lines)
        if kind == "text":
            weight = int(layer.get("weight") or 400)
            family = str(layer.get("font") or "")
            fid = font_ids.get((family, weight))
            if fid is None:
                path = fonts.ttf(family, weight)
                fid = f"f{len(font_ids)}"
                font_ids[(family, weight)] = fid
                lines.append(f"font\t{fid}\t{_esc(str(path) if path else '')}")
            body += [f"text\t{_esc(layer.get('text') or '')}", f"font\t{fid}", f"align\t{layer.get('align') or 'center'}",
                     f"lineh\t{float(layer.get('lineHeight') or 1.15)}", f"width\t{float(layer.get('width') or 0)}"]
        elif kind == "polygon":
            body.append(f"poly\t{int(layer.get('sides') or 5)}\t{1 if layer.get('star') else 0}\t{float(layer.get('inner') if layer.get('inner') is not None else 0.5)}")
        elif kind == "path":
            body.append(f"d\t{_esc(layer.get('d') or '')}")
        elif kind == "image" and layer.get("src"):
            src = str(layer["src"])
            iid = image_ids.get(src)
            if iid is None:
                iid = image_ids[src] = f"i{len(image_ids)}"
                lines.append(f"image\t{iid}\t{_esc(str(project / src))}")
            body += [f"asset\t{iid}", f"fit\t{layer.get('fit') or 'contain'}"]
        elif kind == "video" and layer.get("src"):
            vid = f"v{index}"
            path = project / str(layer["src"])
            decoder = _video_decoder(layer, path, fps, span, out_long, gpu_decode)
            if decoder:
                vw, vh, t0, cmd = decoder
                lines.append(f"video\t{vid}\t{vw}\t{vh}\t{t0}\t{_esc(cmd)}")
            body += [f"asset\t{vid}", f"fit\t{layer.get('fit') or 'contain'}"]
        body.append("endlayer")
    return Scene("\n".join(lines + body) + "\n", width_px, height_px, fps, duration)


def _video_decoder(layer: dict[str, Any], path: Path, fps: float, span: list[float], out_long: int,
                   gpu_decode: bool) -> tuple[int, int, float, str] | None:
    if not path.is_file():
        return None
    media = probe(path)
    if not media.width or not media.height:
        return None
    visible = _visible_times(layer, span) or [float(layer.get("start") or 0)]
    first = max(float(layer.get("start") or 0), min(visible))
    speed = float(layer.get("speed") or 1) or 1
    offset = float(layer.get("clipStart") or 0) + (first - float(layer.get("start") or 0)) * speed
    # Decode at the size it is shown at most (the output's long side), keeping the shape; even numbers.
    k = min(1.0, out_long / max(media.width, media.height))
    vw, vh = max(2, int(media.width * k) // 2 * 2), max(2, int(media.height * k) // 2 * 2)
    # fatal only: the engine stops reading when it has its frames, and ffmpeg would call that a broken pipe.
    argv = [ffmpeg(), "-hide_banner", "-loglevel", "fatal", "-nostdin", *(["-hwaccel", "auto"] if gpu_decode else []),
            "-ss", f"{max(0.0, offset):.4f}", "-i", str(path), "-an",
            "-vf", f"setpts=(PTS-STARTPTS)/{speed},fps={fps},scale={vw}:{vh}:flags=bicubic",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
    return vw, vh, first, command_line(argv)


def _atempo(rate: float) -> str:
    parts = []
    while rate > 2.0:
        parts.append("atempo=2.0")
        rate /= 2.0
    while rate < 0.5:
        parts.append("atempo=0.5")
        rate /= 0.5
    parts.append(f"atempo={rate:.6f}")
    return ",".join(parts)


def audio_scene(doc: dict[str, Any], project: Path, wav: Path) -> str | None:
    """The engine's audio mix: music clips and the sound of video clips, with fades and volume keys."""
    duration = float(doc.get("duration") or 1)
    tracks = _tracks(doc)
    clips: list[dict[str, Any]] = [dict(c) for c in doc.get("audio") or [] if not _off_track(c, tracks, "muted")]
    for layer in doc.get("layers") or []:
        volume = layer.get("volume")
        if layer.get("type") == "video" and not layer.get("muted") and not layer.get("hidden") and \
                not _off_track(layer, tracks, "muted", "hidden") and (volume is None or float(volume) > 0):
            end = min(float(layer.get("end") if layer.get("end") is not None else duration), duration)
            clips.append({"src": layer.get("src"), "start": float(layer.get("start") or 0), "offset": float(layer.get("clipStart") or 0),
                          "rate": float(layer.get("speed") or 1), "volume": float(layer.get("volume", 1)), "keys": layer.get("keys"),
                          "duration": (end - float(layer.get("start") or 0)) * float(layer.get("speed") or 1), "video": True})
    lines = ["SQSCENE\t1", f"audio\t{_esc(str(wav))}\t{AUDIO_RATE}\t{duration}"]
    used = 0
    for clip in clips:
        if clip.get("muted") or not clip.get("src"):
            continue
        path = project / str(clip["src"])
        if not path.is_file():
            continue
        media = probe(path)
        if not media.audio:
            continue
        rate = float(clip.get("rate") or 1) or 1
        offset = float(clip.get("offset") or 0)
        source_len = float(clip["duration"]) if clip.get("duration") is not None else max(0.0, media.duration - offset)
        length = source_len / rate
        if length <= 0:
            continue
        argv = [ffmpeg(), "-hide_banner", "-loglevel", "fatal", "-nostdin", "-ss", f"{offset:.4f}", "-i", str(path),
                "-t", f"{source_len:.4f}", "-vn", "-af", _atempo(rate), "-f", "f32le", "-ac", "2", "-ar", str(AUDIO_RATE), "-"]
        lines.append("\t".join(["aclip", _esc(command_line(argv)), str(float(clip.get("start") or 0)), f"{length:.6f}",
                                str(float(clip.get("volume", 1))), str(float(clip.get("fadeIn") or 0)), str(float(clip.get("fadeOut") or 0))]))
        for key in ((clip.get("keys") or {}).get("volume") or []):
            if isinstance(key.get("v"), (int, float)):
                lines.append(f"akey\t{float(key['t'])}\t{float(key['v'])}\t{key.get('ease') or 'linear'}")
        used += 1
    return "\n".join(lines) + "\n" if used else None


# ── running ──────────────────────────────────────────────────────────────────

@dataclass
class Job:
    id: str
    session: str
    project: str
    state: str = "queued"          # queued, running, done, error, cancelled
    kind: str = "render"           # render, proxy
    src: str = ""                  # proxy: the clip it is made from
    progress: float = 0.0
    message: str = ""
    file: str = ""
    encoder: str = ""
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    cancel: threading.Event = field(default_factory=threading.Event)
    procs: list[subprocess.Popen[bytes]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "src": self.src, "project": self.project, "state": self.state,
                "progress": round(self.progress, 4),
                "message": self.message, "file": self.file, "encoder": self.encoder, "error": self.error,
                "seconds": round((self.finished or time.time()) - self.started, 1)}


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def export(workspace: Path, name: str, *, scale: float = 1.0, quality: str = "high", gpu: str = "",
           job: Job | None = None, progress: Progress | None = None) -> Path:
    """Render and encode the whole video. Returns the MP4. Raises RenderError with a readable reason."""
    def say(fraction: float, text: str) -> None:
        if job:
            job.progress, job.message = fraction, text
        if progress:
            progress(fraction, text)

    engine = build()
    doc, _ = studio_doc.load(workspace, name)
    project = studio_doc.folder(workspace, name)
    out_dir = project / "out"
    work = out_dir / f".render-{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        width_px, height_px = _even(doc["width"] * scale), _even(doc["height"] * scale)
        fps = float(doc.get("fps") or 30)
        enc = encoder(gpu, quality, width_px, height_px, fps)
        if job:
            job.encoder = enc.name
        say(0.01, "Menyiapkan scene")
        video_scene = scene(doc, project, out_size=(width_px, height_px), gpu_decode=enc.gpu)
        scene_file = work / "scene.txt"
        scene_file.write_text(video_scene.text, encoding="utf-8")
        wav = work / "mix.wav"
        audio = audio_scene(doc, project, wav)
        if audio:
            say(0.02, "Mencampur audio")
            audio_file = work / "audio.txt"
            audio_file.write_text(audio, encoding="utf-8")
            _run([str(engine), "audio", str(audio_file)], job)
        target = out_dir / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        try:
            _encode(engine, scene_file, wav if audio else None, target, width_px, height_px, fps, video_scene.duration, enc, job, say)
        except RenderError:
            if not enc.gpu or (job and job.cancel.is_set()):
                raise
            logger.warning(f"[studio] {enc.name} failed; encoding on the CPU")
            enc = cpu_encoder(quality)
            if job:
                job.encoder = enc.name
            _encode(engine, scene_file, wav if audio else None, target, width_px, height_px, fps, video_scene.duration, enc, job, say)
        say(1.0, "Selesai")
        return target
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run(argv: list[str], job: Job | None) -> None:
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **_popen_kwargs())
    if job:
        job.procs.append(proc)
    _, err = proc.communicate()
    if proc.returncode != 0:
        if job and job.cancel.is_set():
            raise RenderError("Dibatalkan")
        raise RenderError((err or b"").decode("utf-8", "replace").strip()[-600:] or f"{argv[1]} gagal")


def _encode(engine: Path, scene_file: Path, wav: Path | None, target: Path, width_px: int, height_px: int, fps: float, duration: float,
            enc: Encoder, job: Job | None, say: Progress) -> None:
    render = [str(engine), "video", str(scene_file), "--threads", str(threads()), "--mem", str(mem_mb())]
    encode = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *enc.pre,
              "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width_px}x{height_px}", "-r", f"{fps}", "-i", "-",
              *(["-i", str(wav)] if wav else []), "-map", "0:v:0", *(["-map", "1:a:0"] if wav else []),
              *(["-vf", enc.vf] if enc.vf else []), *enc.args,
              *(["-c:a", "aac", "-b:a", "192k"] if wav else []), "-t", f"{duration:.3f}", "-movflags", "+faststart", str(target)]
    renderer = subprocess.Popen(render, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_popen_kwargs())
    encoder_proc = subprocess.Popen(encode, stdin=renderer.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    **_popen_kwargs())
    if renderer.stdout:
        renderer.stdout.close()   # the encoder owns the pipe now
    if job:
        job.procs += [renderer, encoder_proc]
    tail: list[str] = []
    started = time.time()
    assert renderer.stderr is not None
    for raw in renderer.stderr:
        line = raw.decode("utf-8", "replace").strip()
        if m := re.match(r"frame (\d+) (\d+)", line):
            done, total = int(m.group(1)), int(m.group(2))
            rate = done / max(0.001, time.time() - started)
            left = (total - done) / rate if rate > 0 else 0
            say(0.03 + 0.95 * done / total, f"Frame {done}/{total} · {rate:.1f} fps · sisa ~{int(left)} dtk · {enc.name}")
        elif line:
            tail.append(line)
            tail = tail[-8:]
        if job and job.cancel.is_set():
            break
    renderer.wait()
    _, enc_err = encoder_proc.communicate()
    if job and job.cancel.is_set():
        target.unlink(missing_ok=True)
        raise RenderError("Dibatalkan")
    if renderer.returncode != 0:
        target.unlink(missing_ok=True)
        raise RenderError("Engine gagal: " + (" ".join(tail)[-600:] or f"kode {renderer.returncode}"))
    if encoder_proc.returncode != 0 or not target.exists():
        target.unlink(missing_ok=True)
        raise RenderError(f"Encoder {enc.name} gagal: " + (enc_err or b"").decode("utf-8", "replace").strip()[-600:])


def cancel(job: Job) -> None:
    job.cancel.set()
    for proc in job.procs:
        if proc.poll() is None:
            kill_tree(proc.pid)


def still(workspace: Path, name: str, times: list[float], *, scale: float = 0.5, cols: int = 0, out: Path | None = None) -> Path:
    """One PNG with the frames at ``times`` side by side (what the agent looks at)."""
    engine = build()
    doc, _ = studio_doc.load(workspace, name)
    project = studio_doc.folder(workspace, name)
    times = sorted(max(0.0, min(float(t), float(doc.get("duration") or 1) - 1e-3)) for t in (times or [0.0]))
    sc = scene(doc, project, times=times)
    work = project / "out" / f".still-{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    target = out or (project / "out" / f"still-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}.png")
    try:
        (work / "scene.txt").write_text(sc.text, encoding="utf-8")
        argv = [str(engine), "still", str(work / "scene.txt"), str(target), "--times", ",".join(f"{t:.4f}" for t in times),
                "--scale", f"{scale}", "--threads", str(threads()), "--mem", str(mem_mb())]
        if cols:
            argv += ["--cols", str(cols)]
        done = subprocess.run(argv, capture_output=True, timeout=600, check=False, **_popen_kwargs())
        if done.returncode != 0 or not target.exists():
            raise RenderError((done.stderr or b"").decode("utf-8", "replace").strip()[-600:] or "still gagal")
        return target
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ── proxies ──────────────────────────────────────────────────────────────────

PROXY_LONG_SIDE = 960
PROXY_FROM_BYTES = 40 * 1024 * 1024


def proxy_path(project: Path, src: str) -> Path:
    return project / "proxies" / f"{Path(src).name}.proxy.mp4"


def proxy_ready(project: Path, src: str) -> bool:
    """A proxy that exists and is newer than its clip (a replaced clip needs a new one)."""
    proxy, original = proxy_path(project, src), project / src
    return proxy.is_file() and original.is_file() and proxy.stat().st_mtime >= original.stat().st_mtime


def wants_proxy(path: Path) -> bool:
    """Big clips get a proxy: larger than the proxy size, or heavy files."""
    media = probe(path)
    return bool(media.width) and (max(media.width, media.height) > PROXY_LONG_SIDE or path.stat().st_size > PROXY_FROM_BYTES)


def make_proxy(project: Path, src: str, job: Job | None = None) -> Path:
    """Write the clip's proxy: 960 px on the long side, H.264 with a keyframe every half second (fast
    seeking), its sound as AAC. Runs below normal priority. Returns the proxy file."""
    source = project / src
    if not source.is_file():
        raise RenderError(f"{src} tidak ada")
    media = probe(source)
    if not media.width or not media.height:
        raise RenderError(f"{src} bukan video yang bisa dibaca ffmpeg")
    k = min(1.0, PROXY_LONG_SIDE / max(media.width, media.height))
    width_px, height_px = _even(media.width * k), _even(media.height * k)
    target = proxy_path(project, src)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part.mp4")
    argv = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
            "-vf", f"scale={width_px}:{height_px}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-g", "15", "-keyint_min", "15", "-pix_fmt", "yuv420p",
            *(["-c:a", "aac", "-b:a", "96k", "-ac", "2"] if media.audio else ["-an"]),
            "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(part)]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_popen_kwargs())
    if job:
        job.procs.append(proc)
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("out_time_us=") and media.duration > 0 and job:
            try:
                job.progress = min(0.99, int(line.split("=", 1)[1]) / 1e6 / media.duration)
                job.message = f"Proxy {Path(src).name}: {int(job.progress * 100)}%"
            except ValueError:
                pass
    _, err = proc.communicate()
    if proc.returncode != 0 or not part.exists():
        part.unlink(missing_ok=True)
        if job and job.cancel.is_set():
            raise RenderError("Dibatalkan")
        raise RenderError("Proxy gagal: " + (err or b"").decode("utf-8", "replace").strip()[-400:])
    os.replace(part, target)
    if job:
        job.progress, job.message = 1.0, "Proxy siap"
    return target


def status() -> dict[str, Any]:
    """For the editor and `addon list`: is the engine there, what GPU encoders work, the limits."""
    info: dict[str, Any] = {"engine": False, "threads": threads(), "mem_mb": mem_mb(),
                            "gpu_setting": os.getenv("STUDIO_GPU", "auto"), "gpus": [], "error": ""}
    try:
        info["engine"] = build().exists()
    except RenderError as exc:
        info["error"] = str(exc)
    try:
        info["gpus"] = detected_gpus()
    except RenderError as exc:
        info["error"] = info["error"] or str(exc)
    return info


__all__ = ["Job", "RenderError", "audio_scene", "available", "build", "cancel", "encoder", "export", "make_proxy", "probe",
           "proxy_path", "proxy_ready", "scene", "status", "still", "wants_proxy"]
