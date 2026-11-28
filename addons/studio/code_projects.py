"""Video projects written as code (HyperFrames HTML, Remotion React, plain HTML), edited as blocks without coding.

``find(workspace)`` lists the folders in a chat that hold one. ``blocks`` asks the C engine (``sqengine blocks``)
for the pieces of one file a person may change: texts, colours, timings, media paths. ``save`` hands changes to
``sqengine patch``, which checks every span still holds what the page saw and replaces the file in one rename.

The server never trusts an offset from the page: a save scans the file again and only patches spans that are
blocks right now, after checking the file's stamp. Python here only finds files, turns raw text into what a
person reads and back (HTML entities, JSON and JS escapes), and validates each new value. The scanning and the
byte work stay in C, so a big project does not load the backend.
"""

from __future__ import annotations

import contextlib
import html
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

from addons.studio import renderer
from addons.studio.studio_doc import ConflictError, StudioError
from tools.local_ops.path_guard import PathGuardError, resolve_inside

EDITABLE = frozenset({".html", ".htm", ".css", ".json", ".tsx", ".jsx", ".ts", ".js", ".mjs"})
SKIP_DIRS = frozenset({"node_modules", ".git", ".agent_memory", ".vault", "__pycache__", "renders", "dist", "build",
                       "out", ".next", ".cache", ".venv", "vendor", "proxies", "uploads", "studio"})
SKIP_FILES = frozenset({"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "tsconfig.json", "jsconfig.json"})
MAX_DEPTH = 4
MAX_PROJECTS = 200
MAX_FILES = 300
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_BLOCKS = 3000
MAX_EDITS = 500
MAX_TEXT = 4000
ENGINE_SECONDS = 30

_NUMBER = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")
_HEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_MEDIA = re.compile(r"^[A-Za-z0-9 _./()+-]{1,240}$")
_FRAME_PROPS = ("durationInFrames", "from", "fps", "startFrom", "endAt")


class CodeProjectError(StudioError):
    """A request the editor cannot carry out, with the reason in words a person can act on."""


# ── finding projects ─────────────────────────────────────────────────────────────
def detect(folder: Path) -> str:
    """'hyperframes', 'remotion', 'html', or '' for a folder that is not a video or page project."""
    if (folder / "motion.json").is_file():
        return ""                                   # a Studio project: the main editor handles it
    if (folder / "hyperframes.json").is_file():
        return "hyperframes"
    package = folder / "package.json"
    if package.is_file() and package.stat().st_size < 512_000:
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(package.read_text(encoding="utf-8"))
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            if "remotion" in deps or "@remotion/cli" in deps:
                return "remotion"
    index = folder / "index.html"
    if index.is_file() and index.stat().st_size < MAX_FILE_BYTES:
        with contextlib.suppress(OSError):
            return "hyperframes" if "data-composition-id" in index.read_text(encoding="utf-8", errors="replace") else "html"
    return ""


def _walk(root: Path) -> Iterator[Path]:
    for current, dirs, _files in os.walk(root):
        depth = len(Path(current).relative_to(root).parts)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        if depth >= MAX_DEPTH:
            dirs[:] = []
        yield Path(current)


def find(workspace: Path) -> list[dict[str, Any]]:
    """Every code video or page project in the chat's folder, newest first."""
    root = workspace.resolve()
    if not root.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for folder in _walk(root):
        kind = detect(folder)
        if not kind:
            continue
        files = editable_files(folder)
        if not files:
            continue
        modified = max(f["modified"] for f in files)
        renders = sorted((root / "renders").glob(f"{folder.name}*.mp4")) if (root / "renders").is_dir() else []
        found.append({"path": folder.relative_to(root).as_posix() or ".", "name": folder.name, "kind": kind,
                      "files": len(files), "modified": modified,
                      "render": renders[-1].relative_to(root).as_posix() if renders else ""})
        if len(found) >= MAX_PROJECTS:
            break
    return sorted(found, key=lambda p: p["modified"], reverse=True)


def project_folder(workspace: Path, project: str) -> tuple[Path, str]:
    root = workspace.resolve()
    try:
        folder = resolve_inside(root, project or ".", must_exist=True)
    except PathGuardError as exc:
        raise CodeProjectError("Proyek tidak ada di folder chat ini.") from exc
    kind = detect(folder) if folder.is_dir() else ""
    if not kind:
        raise CodeProjectError("Folder ini bukan proyek video atau halaman (HyperFrames, Remotion, atau HTML).")
    return folder, kind


def _rank(path: str) -> tuple[int, str]:
    name = path.rsplit("/", 1)[-1]
    first = name in {"index.html", "Root.tsx", "Composition.tsx", "Video.tsx"}
    return (0 if first else 1 if path.endswith((".html", ".tsx", ".jsx")) else 2, path)


def editable_files(folder: Path) -> list[dict[str, Any]]:
    """The HTML, CSS, JSON and React files of one project, the entry file first."""
    out: list[dict[str, Any]] = []
    for current in _walk(folder):
        if current != folder and detect(current):
            continue                                # a nested project lists its own files
        for entry in sorted(current.iterdir()):
            if not entry.is_file() or entry.suffix.lower() not in EDITABLE or entry.name in SKIP_FILES:
                continue
            if entry.name.endswith((".min.js", ".d.ts", ".config.js", ".config.ts", ".config.mjs")):
                continue
            stat = entry.stat()
            if stat.st_size > MAX_FILE_BYTES:
                continue
            out.append({"path": entry.relative_to(folder).as_posix(), "size": stat.st_size, "modified": stat.st_mtime})
            if len(out) >= MAX_FILES:
                return sorted(out, key=lambda f: _rank(f["path"]))
    return sorted(out, key=lambda f: _rank(f["path"]))


def _file(folder: Path, path: str) -> Path:
    try:
        target = resolve_inside(folder.resolve(), path, must_exist=True)
    except PathGuardError as exc:
        raise CodeProjectError("File tidak ada di proyek ini.") from exc
    if not target.is_file() or target.suffix.lower() not in EDITABLE:
        raise CodeProjectError("Hanya file .html, .css, .json, .tsx, .jsx, .ts, dan .js yang bisa diedit di sini.")
    if any(part in SKIP_DIRS for part in target.relative_to(folder.resolve()).parts[:-1]):
        raise CodeProjectError("File di folder itu tidak diedit dari Video Editor.")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise CodeProjectError("File lebih dari 2 MB; buka di IDE.")
    return target


def stamp(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}-{stat.st_size}"


# ── the engine ───────────────────────────────────────────────────────────────────
def _engine(*args: str) -> str:
    try:
        done = subprocess.run([str(renderer.build()), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=ENGINE_SECONDS, check=False)
    except subprocess.TimeoutExpired as exc:
        raise CodeProjectError("Engine terlalu lama membaca file ini.") from exc
    except OSError as exc:
        raise CodeProjectError(f"Engine tidak bisa dijalankan: {exc}") from exc
    if done.returncode == 4:
        raise ConflictError("")
    if done.returncode != 0:
        reason = (done.stderr or "").strip().removeprefix("sqengine: ")
        raise CodeProjectError(f"Engine menolak: {reason or 'kode ' + str(done.returncode)}")
    return done.stdout


def _scan(target: Path) -> list[dict[str, Any]]:
    rows = []
    for line in _engine("blocks", str(target)).splitlines():
        with contextlib.suppress(ValueError):
            rows.append(json.loads(line))
        if len(rows) >= MAX_BLOCKS:
            break
    return rows


# ── raw text <-> what a person reads ────────────────────────────────────────────
def _family(target: Path) -> str:
    suffix = target.suffix.lower()
    return {".html": "html", ".htm": "html", ".css": "css", ".json": "json"}.get(suffix, "js")


def _is_json_string(data: bytes, start: int) -> bool:
    return start > 0 and data[start - 1:start] == b'"'


def _shown(block: dict[str, Any], family: str, data: bytes) -> tuple[str, bool]:
    """(value as a person reads it, whether it can be edited here)."""
    raw, kind = block["value"], block["kind"]
    if family == "html" and kind in {"text", "time", "media"}:
        return html.unescape(raw), True
    if family == "json" and _is_json_string(data, block["start"]):
        try:
            return json.loads(f'"{raw}"'), True
        except ValueError:
            return raw, False
    if family == "js" and block.get("quote"):
        if "\\" in raw:
            return raw, False                       # escapes beyond a plain string: edit in the IDE
        return raw, True
    if family == "js" and kind == "text":
        return html.unescape(raw), True
    return raw, True


def _label(block: dict[str, Any]) -> str:
    key = str(block["key"])
    name, _, line = key.rpartition("@") if "@" in key else (key, "", "")
    return f"{name} (baris {line})" if line.isdigit() else key


def _number(value: str, *, whole: bool, low: float, high: float, what: str) -> str:
    text = value.strip().replace(",", ".")
    if not _NUMBER.fullmatch(text):
        raise CodeProjectError(f"{what}: isi angka, misalnya {'90' if whole else '2.5'}.")
    number = float(text)
    if whole and not number.is_integer():
        raise CodeProjectError(f"{what}: isi bilangan bulat (jumlah frame), misalnya 90.")
    if not low <= number <= high:
        raise CodeProjectError(f"{what}: isi antara {low:g} dan {high:g}.")
    return str(int(number)) if whole else text


def _media_ok(value: str, folder: Path, target: Path) -> None:
    path = value.strip()
    if not _MEDIA.fullmatch(path) or path.startswith(("/", "\\")) or ".." in Path(path).parts or ":" in path:
        raise CodeProjectError("Media: isi path di dalam proyek, misalnya assets/foto.jpg (tanpa http:// dan tanpa ..).")
    for base in (folder, folder / "public", target.parent):
        with contextlib.suppress(PathGuardError):
            if resolve_inside(folder.resolve(), str((base / path).relative_to(folder)), must_exist=True).is_file():
                return
    raise CodeProjectError(f"Media: file {path} tidak ada di proyek. Unggah dulu ke folder assets/ atau public/.")


def encode(block: dict[str, Any], given: Any, family: str, data: bytes, folder: Path, target: Path) -> str:
    """The raw text that replaces ``block`` in the file, or CodeProjectError with how to fix the value."""
    if not isinstance(given, str):
        raise CodeProjectError("Nilai harus teks.")
    value: str = given
    kind, quote_char = block["kind"], block.get("quote") or ""
    label = _label(block)
    if kind == "color":
        if not _HEX.fullmatch(value.strip()):
            raise CodeProjectError(f"{label}: warna harus kode hex, misalnya #0B3C49.")
        return value.strip()
    if kind == "time":
        frames = family == "js" and str(block["key"]).split("@")[0] in _FRAME_PROPS
        return _number(value, whole=frames, low=0, high=86_400 * (60 if frames else 1), what=label)
    if kind == "media":
        _media_ok(value, folder, target)
        value = value.strip()
    if len(value) > MAX_TEXT:
        raise CodeProjectError(f"{label}: paling panjang {MAX_TEXT} karakter.")
    if family == "css" or (family == "html" and kind == "css"):
        if re.search(r"[;{}<>\n]", value):
            raise CodeProjectError(f"{label}: nilai CSS tidak boleh berisi ; {{ }} < > atau baris baru.")
        return value.strip()
    if family == "json":
        if _is_json_string(data, block["start"]):
            return json.dumps(value, ensure_ascii=False)[1:-1]
        return _number(value, whole=False, low=-1e12, high=1e12, what=label)
    if family == "html":
        return html.escape(value, quote=kind in {"time", "media"})
    if quote_char:                                   # a JS string: title="...", staticFile('...')
        if "\n" in value:
            raise CodeProjectError(f"{label}: teks ini satu baris saja.")
        return value.replace("\\", "\\\\").replace(quote_char, "\\" + quote_char)
    if re.search(r"[{}]", value):                    # JSX text
        raise CodeProjectError(f"{label}: teks JSX tidak boleh berisi {{ atau }}. Ubah lewat IDE kalau perlu.")
    return html.escape(value, quote=False)


# ── read and write ───────────────────────────────────────────────────────────────
def blocks(workspace: Path, project: str, path: str) -> dict[str, Any]:
    folder, kind = project_folder(workspace, project)
    target = _file(folder, path)
    data = target.read_bytes()
    family = _family(target)
    out = []
    for block in _scan(target):
        shown, editable = _shown(block, family, data)
        out.append({"id": f"{block['start']}:{block['len']}", "kind": block["kind"], "key": block["key"],
                    "label": _label(block), "value": shown, "editable": editable})
    return {"project": project, "kind": kind, "path": target.relative_to(folder.resolve()).as_posix(),
            "stamp": stamp(target), "blocks": out}


def save(workspace: Path, project: str, path: str, base: str, edits: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply ``edits`` ([{id, value}]) when the file still has stamp ``base``; returns the fresh blocks."""
    folder, _kind = project_folder(workspace, project)
    target = _file(folder, path)
    if not isinstance(edits, list) or not edits:
        raise CodeProjectError("Tidak ada perubahan untuk disimpan.")
    if len(edits) > MAX_EDITS:
        raise CodeProjectError(f"Paling banyak {MAX_EDITS} perubahan sekali simpan.")
    if stamp(target) != base:
        raise ConflictError(stamp(target))
    data = target.read_bytes()
    family = _family(target)
    current = {f"{b['start']}:{b['len']}": b for b in _scan(target)}
    lines = []
    for edit in edits:
        block = current.get(str((edit or {}).get("id", "")))
        if block is None:
            raise ConflictError(stamp(target))
        if not _shown(block, family, data)[1]:
            raise CodeProjectError(f"{_label(block)}: bagian ini hanya bisa diubah di IDE.")
        new = encode(block, (edit or {}).get("value"), family, data, folder, target)
        lines.append(f"{block['start']}\t{block['len']}\t{quote(block['value'], safe='')}\t{quote(new, safe='')}\n")
    with tempfile.TemporaryDirectory(prefix="sq-patch-") as scratch:
        plan = Path(scratch) / "edits.tsv"
        plan.write_text("".join(lines), encoding="utf-8")
        try:
            _engine("patch", str(target), str(plan))
        except ConflictError:
            raise ConflictError(stamp(target)) from None
    return blocks(workspace, project, path)
