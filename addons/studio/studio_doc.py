"""Studio projects: motion videos kept as data, so both the agent and the editor can change them.

A project is a folder in the chat's workspace::

    studio/<name>/motion.json     the video: size, fps, duration, layers with keyframes, audio, notes
    studio/<name>/assets/         pictures and music it uses
    studio/<name>/out/            exported MP4 files

``motion.json`` (version 1)::

    {"version": 1, "width": 1080, "height": 1920, "fps": 30, "duration": 12,
     "background": "#F4EBDD", "fonts": ["Fraunces", "Plus Jakarta Sans"],
     "layers": [{"id": "title", "type": "text", "name": "Judul", "start": 0, "end": 4,
                 "text": "Kopi Senja", "font": "Fraunces", "weight": 700, "size": 120, "fill": "#2B1B12",
                 "props": {"x": 540, "y": 800, "scale": 1, "rotation": 0, "opacity": 1},
                 "keys": {"opacity": [{"t": 0, "v": 0, "ease": "out"}, {"t": 0.6, "v": 1}]}}],
     "audio": [{"id": "music", "src": "assets/musik.mp3", "start": 0, "offset": 0, "volume": 0.8,
                "fadeIn": 0.5, "fadeOut": 1.5}],
     "notes": [{"n": 1, "t": 2.4, "box": [300, 700, 480, 200], "text": "Judul terlalu kecil", "status": "open"}]}

Layers draw bottom to top in list order. ``x``/``y`` are the layer's centre in video pixels.
A key's ``ease`` shapes the move from that key to the next. Times are seconds on the video's
timeline. The browser renderer (``addons/studio/web/static/render.js``) follows the same rules.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

VERSION = 1
# Sanity bounds against a broken file, not limits on what a video may hold (no artificial limits).
MAX_BYTES = 256 * 1024 * 1024
MAX_LAYERS = 1_000_000
MAX_KEYS = 1_000_000
MAX_NOTES = 1_000_000
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
SRC_RE = re.compile(r"^assets/[A-Za-z0-9 ._()-]{1,120}$")

LAYER_TYPES = ("text", "rect", "ellipse", "polygon", "path", "image", "video")
EASES = ("linear", "in", "out", "inOut", "outBack", "outElastic", "hold")
# Properties a key can animate, and the kind of value each takes.
ANIMATABLE: dict[str, str] = {
    "x": "num", "y": "num", "scale": "num", "scaleX": "num", "scaleY": "num", "rotation": "num",
    "opacity": "unit", "w": "num", "h": "num", "radius": "num", "size": "num", "strokeWidth": "num",
    "draw": "unit", "blur": "num", "fill": "color", "stroke": "color", "volume": "gain",
}
TRANSFORM = ("x", "y", "scale", "rotation", "opacity")
STYLE_KEYS = {
    "text": ("text", "font", "weight", "size", "align", "fill", "stroke", "strokeWidth", "lineHeight", "width"),
    "rect": ("w", "h", "radius", "fill", "stroke", "strokeWidth"),
    "ellipse": ("w", "h", "fill", "stroke", "strokeWidth"),
    "polygon": ("radius", "sides", "star", "inner", "fill", "stroke", "strokeWidth"),
    "path": ("d", "w", "h", "fill", "stroke", "strokeWidth"),
    "image": ("src", "w", "h", "fit", "radius"),
    "video": ("src", "w", "h", "fit", "radius", "clipStart", "speed", "volume", "muted"),
}


class StudioError(ValueError):
    """A project that does not exist, or a document that does not follow the format."""


class ConflictError(StudioError):
    """The file changed on disk since the writer loaded it (the agent and the editor both write)."""

    def __init__(self, current: str) -> None:
        self.current = current
        super().__init__("proyek sudah diubah di tempat lain")


# ── paths ──────────────────────────────────────────────────────────────────────
def slug(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")[:48]
    return cleaned or "video"


def folder(workspace: str | Path, name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise StudioError(f"nama proyek tidak valid: {name!r} (huruf kecil, angka, tanda hubung)")
    return Path(workspace) / "studio" / name


def doc_path(workspace: str | Path, name: str) -> Path:
    return folder(workspace, name) / "motion.json"


def projects(workspace: str | Path) -> list[dict[str, Any]]:
    root = Path(workspace) / "studio"
    out = []
    if root.is_dir():
        for path in sorted(root.glob("*/motion.json"), key=lambda p: -p.stat().st_mtime):
            if NAME_RE.match(path.parent.name):
                out.append({"name": path.parent.name, "modified": path.stat().st_mtime})
    return out


# ── validation ─────────────────────────────────────────────────────────────────
def _num(value: Any, where: str, errors: list[str], low: float | None = None, high: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append(f"{where}: harus angka, bukan {value!r}")
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        errors.append(f"{where}: angka tidak valid")
        return 0.0
    if low is not None and number < low:
        errors.append(f"{where}: minimal {low}")
    if high is not None and number > high:
        errors.append(f"{where}: maksimal {high}")
    return number


def _color(value: Any, where: str, errors: list[str]) -> Any:
    if value in (None, "", "none", "transparent"):
        return None
    if not isinstance(value, str) or not COLOR_RE.match(value):
        errors.append(f"{where}: warna harus hex seperti #1A2B3C, bukan {value!r}")
    return value


def _value(kind: str, value: Any, where: str, errors: list[str]) -> Any:
    if kind == "color":
        return _color(value, where, errors)
    if kind == "unit":
        return _num(value, where, errors, 0, 1)
    if kind == "gain":
        return _num(value, where, errors, 0, 4)
    return _num(value, where, errors)


def _keys(keys: Any, where: str, errors: list[str], allowed: set[str]) -> dict[str, list[dict[str, Any]]]:
    if keys in (None, {}):
        return {}
    if not isinstance(keys, dict):
        errors.append(f"{where}: keys harus objek {{properti: [keyframe]}}")
        return {}
    clean: dict[str, list[dict[str, Any]]] = {}
    for prop, frames in keys.items():
        if prop not in allowed:
            errors.append(f"{where}.{prop}: properti ini tidak bisa dianimasikan (bisa: {', '.join(sorted(allowed))})")
            continue
        if not isinstance(frames, list) or not frames:
            errors.append(f"{where}.{prop}: harus daftar keyframe yang tidak kosong")
            continue
        if len(frames) > MAX_KEYS:
            errors.append(f"{where}.{prop}: paling banyak {MAX_KEYS} keyframe")
        out = []
        for i, frame in enumerate(frames):
            spot = f"{where}.{prop}[{i}]"
            if not isinstance(frame, dict) or "t" not in frame or "v" not in frame:
                errors.append(f"{spot}: keyframe butuh t (detik) dan v (nilai)")
                continue
            item = {"t": _num(frame["t"], f"{spot}.t", errors, 0), "v": _value(ANIMATABLE[prop], frame["v"], f"{spot}.v", errors)}
            ease = frame.get("ease", "linear")
            if ease not in EASES:
                errors.append(f"{spot}.ease: pilih salah satu {', '.join(EASES)}")
            item["ease"] = ease
            out.append(item)
        out.sort(key=lambda f: f["t"])
        clean[prop] = out
    return clean


def validate(doc: Any) -> tuple[dict[str, Any], list[str]]:
    """The document with numbers coerced and keys sorted, and every problem found (empty when valid)."""
    errors: list[str] = []
    if not isinstance(doc, dict):
        return {}, ["dokumen harus objek JSON"]
    out = dict(doc)
    out["version"] = VERSION
    for field, low, high in (("width", 16, 4096), ("height", 16, 4096)):
        value = int(_num(doc.get(field, 1080), field, errors, low, high))
        out[field] = value + (value % 2)          # H.264 needs even sizes
    out["fps"] = int(_num(doc.get("fps", 30), "fps", errors, 1, 120))
    out["duration"] = _num(doc.get("duration", 10), "duration", errors, 0.1, 1800)
    out["background"] = _color(doc.get("background", "#000000"), "background", errors) or "#000000"
    fonts = doc.get("fonts") or []
    if not isinstance(fonts, list) or not all(isinstance(f, str) and re.fullmatch(r"[A-Za-z0-9 ]{1,60}", f) for f in fonts):
        errors.append("fonts: daftar nama Google Font, misalnya [\"Fraunces\", \"Plus Jakarta Sans\"]")
        fonts = []
    out["fonts"] = list(dict.fromkeys(fonts))

    layers = doc.get("layers") or []
    if not isinstance(layers, list):
        errors.append("layers: harus daftar")
        layers = []
    if len(layers) > MAX_LAYERS:
        errors.append(f"layers: paling banyak {MAX_LAYERS}")
    seen: set[str] = set()
    clean_layers = []
    for i, layer in enumerate(layers):
        where = f"layers[{i}]"
        if not isinstance(layer, dict):
            errors.append(f"{where}: harus objek")
            continue
        item = dict(layer)
        lid = str(layer.get("id") or f"layer{i + 1}")
        if not ID_RE.match(lid) or lid in seen:
            errors.append(f"{where}.id: {lid!r} harus unik, huruf/angka/_/-")
        seen.add(lid)
        item["id"] = lid
        kind = layer.get("type")
        if kind not in LAYER_TYPES:
            errors.append(f"{where}.type: pilih salah satu {', '.join(LAYER_TYPES)}")
            continue
        item["start"] = _num(layer.get("start", 0), f"{where}.start", errors, 0)
        item["end"] = _num(layer.get("end", out["duration"]), f"{where}.end", errors, 0)
        if item["end"] <= item["start"]:
            errors.append(f"{where}: end harus lebih besar dari start")
        props = layer.get("props") or {}
        if not isinstance(props, dict):
            errors.append(f"{where}.props: harus objek")
            props = {}
        defaults = {"x": out["width"] / 2, "y": out["height"] / 2, "scale": 1, "rotation": 0, "opacity": 1}
        item["props"] = {k: _value(ANIMATABLE.get(k, "num"), props.get(k, defaults.get(k, 0)), f"{where}.props.{k}", errors)
                         for k in {*TRANSFORM, *props}}
        for key in ("fill", "stroke"):
            if key in layer:
                item[key] = _color(layer[key], f"{where}.{key}", errors)
        if kind == "text" and not isinstance(layer.get("text", ""), str):
            errors.append(f"{where}.text: harus teks")
        if kind == "path" and not isinstance(layer.get("d"), str):
            errors.append(f"{where}.d: path SVG wajib, misalnya \"M0 0 L100 0 L50 80 Z\"")
        if kind in ("image", "video") and not (isinstance(layer.get("src"), str) and SRC_RE.match(layer["src"])):
            errors.append(f"{where}.src: file harus di folder assets, misalnya \"assets/logo.png\" atau \"assets/klip.mp4\"")
        if kind == "video":
            item["clipStart"] = _num(layer.get("clipStart", 0), f"{where}.clipStart", errors, 0)
            item["speed"] = _num(layer.get("speed", 1), f"{where}.speed", errors, 0.1, 4)
            item["volume"] = _num(layer.get("volume", 1), f"{where}.volume", errors, 0, 4)
        allowed = {k for k, v in ANIMATABLE.items() if v != "gain"} | ({"volume"} if kind == "video" else set())
        item["keys"] = _keys(layer.get("keys"), f"{where}.keys", errors, allowed)
        clean_layers.append(item)
    out["layers"] = clean_layers

    audio = doc.get("audio") or []
    if not isinstance(audio, list):
        errors.append("audio: harus daftar")
        audio = []
    clean_audio = []
    for i, clip in enumerate(audio):
        where = f"audio[{i}]"
        if not isinstance(clip, dict):
            errors.append(f"{where}: harus objek")
            continue
        item = dict(clip)
        item["id"] = str(clip.get("id") or f"audio{i + 1}")
        if not (isinstance(clip.get("src"), str) and SRC_RE.match(clip["src"])):
            errors.append(f"{where}.src: audio harus di folder assets, misalnya \"assets/musik.mp3\"")
        item["start"] = _num(clip.get("start", 0), f"{where}.start", errors, 0)
        item["offset"] = _num(clip.get("offset", 0), f"{where}.offset", errors, 0)
        if clip.get("duration") is not None:
            item["duration"] = _num(clip["duration"], f"{where}.duration", errors, 0.05)
        item["volume"] = _num(clip.get("volume", 1), f"{where}.volume", errors, 0, 4)
        item["fadeIn"] = _num(clip.get("fadeIn", 0), f"{where}.fadeIn", errors, 0)
        item["fadeOut"] = _num(clip.get("fadeOut", 0), f"{where}.fadeOut", errors, 0)
        item["keys"] = _keys(clip.get("keys"), f"{where}.keys", errors, {"volume"})
        clean_audio.append(item)
    out["audio"] = clean_audio

    notes = doc.get("notes") or []
    if not isinstance(notes, list) or len(notes) > MAX_NOTES:
        errors.append(f"notes: daftar, paling banyak {MAX_NOTES}")
        notes = []
    clean_notes = []
    for i, note in enumerate(notes):
        if not isinstance(note, dict) or not isinstance(note.get("text", ""), str):
            errors.append(f"notes[{i}]: butuh text")
            continue
        item = dict(note)
        item["n"] = int(_num(note.get("n", i + 1), f"notes[{i}].n", errors, 1))
        item["t"] = _num(note.get("t", 0), f"notes[{i}].t", errors, 0)
        item["status"] = note.get("status") if note.get("status") in ("open", "done") else "open"
        clean_notes.append(item)
    out["notes"] = clean_notes
    return out, errors


# ── load and save ──────────────────────────────────────────────────────────────
def stamp(path: Path) -> str:
    """The version a client saw, so a save over someone else's newer change can be refused."""
    return str(path.stat().st_mtime_ns) if path.exists() else "0"


def load(workspace: str | Path, name: str) -> tuple[dict[str, Any], str]:
    path = doc_path(workspace, name)
    if not path.is_file():
        raise StudioError(f"proyek {name} belum ada")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise StudioError(f"motion.json rusak: {exc}") from exc
    return doc, stamp(path)


def save(workspace: str | Path, name: str, doc: Any, base: str | None = None) -> tuple[dict[str, Any], str]:
    """Validate and write. ``base`` is the stamp the writer loaded; a newer file on disk is refused."""
    clean, errors = validate(doc)
    if errors:
        raise StudioError("; ".join(errors[:12]) + (f" (+{len(errors) - 12} lagi)" if len(errors) > 12 else ""))
    text = json.dumps(clean, ensure_ascii=False, indent=1)
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise StudioError("motion.json lebih dari 2 MB")
    path = doc_path(workspace, name)
    if base is not None and stamp(path) != base:
        raise ConflictError(stamp(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "assets").mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return clean, stamp(path)


def template(width: int = 1080, height: int = 1920, fps: int = 30, duration: float = 8.0,
             title: str = "Judul video") -> dict[str, Any]:
    """A small starting video: a background, a title that fades and rises in, and a shape."""
    return {
        "version": VERSION, "width": width, "height": height, "fps": fps, "duration": duration,
        "background": "#F4EBDD", "fonts": ["Fraunces"], "audio": [], "notes": [],
        "layers": [
            {"id": "block", "type": "rect", "name": "Blok", "w": width * 0.8, "h": height * 0.18, "radius": 24,
             "fill": "#C8553D", "props": {"x": width / 2, "y": height * 0.62, "scale": 1, "rotation": 0, "opacity": 1},
             "keys": {"scale": [{"t": 0.2, "v": 0, "ease": "outBack"}, {"t": 0.9, "v": 1}]}},
            {"id": "title", "type": "text", "name": "Judul", "text": title, "font": "Fraunces", "weight": 700,
             "size": round(width / 9), "fill": "#2B1B12", "align": "center",
             "props": {"x": width / 2, "y": height * 0.42, "scale": 1, "rotation": 0, "opacity": 1},
             "keys": {"opacity": [{"t": 0, "v": 0, "ease": "out"}, {"t": 0.8, "v": 1}],
                      "y": [{"t": 0, "v": height * 0.45, "ease": "out"}, {"t": 0.8, "v": height * 0.42}]}},
        ],
    }


# ── checks: what the agent cannot see ───────────────────────────────────────────
def _hex_luminance(colour: str) -> float:
    text = colour.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    channels = [int(text[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_hex_luminance(a), _hex_luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _at(layer: dict[str, Any], prop: str, t: float, default: Any) -> Any:
    """A property's value at time t (linear between keys: enough for bounds and colours)."""
    base = (layer.get("props") or {}).get(prop, layer.get(prop, default)) if prop in TRANSFORM or prop in ("blur", "draw") \
        else layer.get(prop, default)
    frames = (layer.get("keys") or {}).get(prop) or []
    if not frames:
        return base
    if t <= frames[0]["t"]:
        return frames[0]["v"]
    for a, b in zip(frames, frames[1:], strict=False):
        if a["t"] <= t < b["t"]:
            if isinstance(a["v"], (int, float)) and isinstance(b["v"], (int, float)) and a.get("ease") != "hold":
                return a["v"] + (b["v"] - a["v"]) * (t - a["t"]) / ((b["t"] - a["t"]) or 1)
            return a["v"]
    return frames[-1]["v"]


def _box(layer: dict[str, Any], t: float) -> tuple[float, float, float, float]:
    """Approximate bounds (left, top, right, bottom) in video pixels at time t."""
    scale = float(_at(layer, "scale", t, 1) or 0) * float(_at(layer, "scaleX", t, 1) or 1)
    scale_y = float(_at(layer, "scale", t, 1) or 0) * float(_at(layer, "scaleY", t, 1) or 1)
    kind = layer.get("type")
    if kind == "text":
        size = float(_at(layer, "size", t, 48) or 48)
        lines = str(layer.get("text") or "").split("\n")
        width = float(layer.get("width") or 0) or max((len(line) for line in lines), default=1) * size * 0.56
        if layer.get("width"):
            chars_per_line = max(1, int(float(layer["width"]) / (size * 0.56)))
            count = sum(max(1, -(-len(line) // chars_per_line)) for line in lines)
        else:
            count = len(lines)
        w, h = width, count * size * float(layer.get("lineHeight") or 1.15)
    elif kind == "polygon":
        w = h = 2 * float(_at(layer, "radius", t, 100) or 100)
    else:
        w, h = float(_at(layer, "w", t, 100) or 100), float(_at(layer, "h", t, 100) or 100)
    x, y = float(_at(layer, "x", t, 0) or 0), float(_at(layer, "y", t, 0) or 0)
    w, h = abs(w * scale), abs(h * scale_y)
    return x - w / 2, y - h / 2, x + w / 2, y + h / 2


def _settled(layer: dict[str, Any]) -> float:
    """A moment the layer is fully in: after its last entrance key, inside its time on screen."""
    start, end = float(layer.get("start") or 0), float(layer.get("end") or 0)
    keys = [k["t"] for frames in (layer.get("keys") or {}).values() for k in frames if start <= k["t"] <= end]
    first_half = [t for t in keys if t <= start + (end - start) / 2]
    moment = max(first_half) if first_half else start + min(0.5, (end - start) / 2)
    return min(max(moment, start), max(start, end - 1e-3))


def check(doc: dict[str, Any], workspace: str | Path | None = None, name: str = "") -> list[dict[str, Any]]:
    """Problems a viewer would see: things outside the frame, unreadable or overlapping text, text gone
    before it can be read, missing files, keys after the end. The agent's eyes on its own video."""
    clean, errors = validate(doc)
    if errors:
        return [{"level": "error", "message": e} for e in errors]
    width, height, duration = clean["width"], clean["height"], clean["duration"]
    found: list[dict[str, Any]] = []

    def add(level: str, layer: str | None, t: float | None, message: str) -> None:
        found.append({"level": level, "layer": layer, **({"t": round(t, 2)} if t is not None else {}), "message": message})

    layers = clean["layers"]
    for index, layer in enumerate(layers):
        lid, kind = layer["id"], layer["type"]
        if layer["start"] >= duration:
            add("warn", lid, layer["start"], "mulai setelah video selesai, tidak pernah tampil")
            continue
        for prop, frames in (layer.get("keys") or {}).items():
            late = [k["t"] for k in frames if k["t"] > duration + 1e-6]
            if late:
                add("warn", lid, late[0], f"keyframe {prop} di luar durasi video")
        t = _settled(layer)
        if float(_at(layer, "opacity", t, 1) or 0) <= 0.01 or layer.get("hidden"):
            continue
        left, top, right, bottom = _box(layer, t)
        outside = max(0.0, -left) + max(0.0, right - width) + max(0.0, -top) + max(0.0, bottom - height)
        size_sum = (right - left) + (bottom - top)
        if outside > 0 and kind == "text":
            add("error", lid, t, f"teks terpotong di tepi frame (kotak {left:.0f},{top:.0f} sampai {right:.0f},{bottom:.0f})")
        elif outside > 0.25 * size_sum and kind not in ("rect", "ellipse", "image", "video"):
            add("warn", lid, t, "sebagian besar keluar frame")
        if kind == "text":
            margin = 0.04 * min(width, height)
            if outside == 0 and (left < margin or right > width - margin or top < margin or bottom > height - margin):
                add("warn", lid, t, "teks terlalu mepet tepi; beri jarak aman sekitar 4%")
            fill = _at(layer, "fill", t, None)
            behind = clean["background"]
            cx, cy = (left + right) / 2, (top + bottom) / 2
            for under in layers[:index]:
                if under["type"] in ("rect", "ellipse", "polygon") and under.get("fill") and \
                        under["start"] <= t < under["end"] and not under.get("hidden"):
                    ul, ut, ur, ub = _box(under, t)
                    if ul <= cx <= ur and ut <= cy <= ub and float(_at(under, "opacity", t, 1) or 0) > 0.6:
                        behind = str(_at(under, "fill", t, under["fill"]))
                if under["type"] in ("image", "video") and under["start"] <= t < under["end"]:
                    ul, ut, ur, ub = _box(under, t)
                    if ul <= cx <= ur and ut <= cy <= ub:
                        behind = ""          # a picture behind: contrast cannot be judged from here
            if fill and behind and isinstance(fill, str) and COLOR_RE.match(fill) and COLOR_RE.match(behind):
                ratio = _contrast(fill[:7], behind[:7])
                size = float(_at(layer, "size", t, 48) or 48)
                need = 3.0 if size >= 48 else 4.5
                if ratio < need:
                    add("error", lid, t, f"kontras teks {ratio:.1f}:1 terhadap {behind} (butuh {need}:1)")
            words = len(str(layer.get("text") or "").split())
            on_screen = layer["end"] - layer["start"]
            if words and on_screen < max(0.8, words / 3.2):
                add("warn", lid, layer["start"], f"{words} kata hanya tampil {on_screen:.1f} dtk; butuh sekitar {max(0.8, words / 3.2):.1f} dtk")
    texts = [layer for layer in layers if layer["type"] == "text" and not layer.get("hidden")]
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            start, end = max(a["start"], b["start"]), min(a["end"], b["end"])
            if end - start < 0.2:
                continue
            t = (start + end) / 2
            al, at, ar, ab = _box(a, t)
            bl, bt, br, bb = _box(b, t)
            overlap = max(0.0, min(ar, br) - max(al, bl)) * max(0.0, min(ab, bb) - max(at, bt))
            smaller = min((ar - al) * (ab - at), (br - bl) * (bb - bt)) or 1
            if overlap / smaller > 0.15 and float(_at(a, "opacity", t, 1)) > 0.1 and float(_at(b, "opacity", t, 1)) > 0.1:
                add("error", a["id"], t, f"teks {a['id']} dan {b['id']} bertumpuk")
    if workspace and name:
        root = folder(workspace, name)
        for layer in layers:
            if layer["type"] in ("image", "video") and not (root / layer["src"]).is_file():
                add("error", layer["id"], None, f"file {layer['src']} belum ada di folder proyek")
        for clip in clean["audio"]:
            if not (root / clip["src"]).is_file():
                add("error", clip["id"], None, f"file audio {clip['src']} belum ada")
    if not layers:
        add("warn", None, None, "video belum punya lapisan")
    return found


# ── notes ──────────────────────────────────────────────────────────────────────
def open_notes(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in doc.get("notes") or [] if n.get("status", "open") == "open"]


def resolve_note(doc: dict[str, Any], number: int, reply: str) -> bool:
    for note in doc.get("notes") or []:
        if int(note.get("n", 0)) == int(number):
            note["status"] = "done"
            note["reply"] = reply.strip()[:2000]
            note["doneAt"] = time.strftime("%Y-%m-%d %H:%M")
            return True
    return False


__all__ = ["ANIMATABLE", "ConflictError", "EASES", "LAYER_TYPES", "StudioError", "check", "doc_path", "folder", "load",
           "open_notes", "projects", "resolve_note", "save", "slug", "stamp", "template", "validate"]
