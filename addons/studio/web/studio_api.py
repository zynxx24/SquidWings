"""HTTP API for the video Studio: projects, assets, export, and a Google Fonts cache.

The Studio page edits ``studio/<name>/motion.json`` in a chat's folder (see ``addons.studio.studio_doc``),
the same file the agent writes. Saves carry the stamp the page loaded, so an edit never silently
overwrites a change the agent made meanwhile (409 with the newer stamp).

Export happens in the browser: the page renders every frame with the same renderer it previews
with, encodes H.264 with WebCodecs (or records WebM where that is missing) and mixes the audio to
WAV. It uploads the parts here, and ffmpeg puts them into one MP4 the chat can show.

Fonts: the page's CSP only allows its own origin, so Google Fonts CSS and font files are fetched
through ``/api/fonts`` and kept on disk. A font used once keeps working offline and in exports.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from addons.studio import studio_doc
from addons.studio.studio_doc import ConflictError, StudioError
from brain.config.settings import get_settings
from brain.utils import shared_files
from brain.utils.helpers import is_valid_session_id
from brain.utils.logger import get_logger
from brain.utils.workspace_context import session_workspace

logger = get_logger(__name__)
router = APIRouter(tags=["Studio"])

_ASSET_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".m4v": "video/mp4",
}
_ASSET_MAX = 80 * 1024 * 1024
_VIDEO_ASSET_MAX = 600 * 1024 * 1024
_PART_MAX = {"video": 1024 * 1024 * 1024, "audio": 512 * 1024 * 1024}
_PART_EXT = {"h264": ".h264", "webm": ".webm", "wav": ".wav"}
_JOB_RE = re.compile(r"^[a-z0-9]{8,40}$")
_FONT_FAMILY_RE = re.compile(r"^[A-Za-z0-9 ]{1,60}(:wght@[0-9;]{1,40})?$")
_FONT_FILE_RE = re.compile(r"^[A-Za-z0-9/_.-]{5,200}$")
_EXPORT_SECONDS = 600


def _workspace(session: str) -> Path:
    if not is_valid_session_id(session):
        raise HTTPException(status_code=400, detail="Sesi tidak valid")
    explicit = os.getenv("SQUIDWINGS_WORKSPACE", "").strip()
    return Path(explicit) if explicit else session_workspace(session)


def _project(session: str, name: str) -> Path:
    try:
        return studio_doc.folder(_workspace(session), name)
    except StudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=f"File lebih dari {limit // (1024 * 1024)} MB")
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise HTTPException(status_code=413, detail=f"File lebih dari {limit // (1024 * 1024)} MB")
    if not buffer:
        raise HTTPException(status_code=400, detail="File kosong")
    return bytes(buffer)


# ── projects ────────────────────────────────────────────────────────────────────
@router.get("/api/studio/{session}/projects")
async def list_projects(session: str) -> dict[str, Any]:
    return {"projects": await asyncio.to_thread(studio_doc.projects, _workspace(session))}


@router.post("/api/studio/{session}/projects")
async def create_project(session: str, payload: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace(session)
    name = studio_doc.slug(str(payload.get("name") or "video"))
    base, n = name, 2
    while studio_doc.doc_path(workspace, name).exists():
        name, n = f"{base[:44]}-{n}", n + 1
    doc = studio_doc.template(int(payload.get("width") or 1080), int(payload.get("height") or 1920),
                              int(payload.get("fps") or 30), float(payload.get("duration") or 8),
                              str(payload.get("title") or "Judul video"))
    try:
        clean, stamp = await asyncio.to_thread(studio_doc.save, workspace, name, doc)
    except StudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"name": name, "doc": clean, "stamp": stamp}


@router.get("/api/studio/{session}/project/{name}")
async def get_project(session: str, name: str) -> dict[str, Any]:
    try:
        doc, stamp = await asyncio.to_thread(studio_doc.load, _workspace(session), name)
    except StudioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"name": name, "doc": doc, "stamp": stamp}


@router.get("/api/studio/{session}/project/{name}/stamp")
async def project_stamp(session: str, name: str) -> dict[str, Any]:
    return {"stamp": studio_doc.stamp(_project(session, name) / "motion.json")}


@router.put("/api/studio/{session}/project/{name}")
async def put_project(session: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    base = payload.get("base")
    try:
        clean, stamp = await asyncio.to_thread(
            studio_doc.save, _workspace(session), name, payload.get("doc"), str(base) if base is not None else None)
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "stamp": exc.current}) from exc
    except StudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"name": name, "doc": clean, "stamp": stamp}


# ── assets ──────────────────────────────────────────────────────────────────────
@router.get("/api/studio/{session}/project/{name}/assets")
async def list_assets(session: str, name: str) -> dict[str, Any]:
    folder = _project(session, name) / "assets"
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _ASSET_TYPES) if folder.is_dir() else []
    return {"assets": [{"src": f"assets/{p.name}", "size": p.stat().st_size,
                        "kind": _ASSET_TYPES[p.suffix.lower()].split("/")[0]} for p in files]}


@router.post("/api/studio/{session}/project/{name}/asset")
async def upload_asset(request: Request, session: str, name: str, file: str = Query(..., max_length=120)) -> dict[str, Any]:
    clean = re.sub(r"[^A-Za-z0-9 ._()-]", "_", Path(file).name).strip(" .")[:120] or "file"
    suffix = Path(clean).suffix.lower()
    if suffix not in _ASSET_TYPES:
        raise HTTPException(status_code=415, detail="Hanya gambar (png, jpg, webp, gif), audio (mp3, wav, ogg, m4a) "
                                                    "dan video (mp4, webm, mov)")
    data = await _body(request, _VIDEO_ASSET_MAX if _ASSET_TYPES[suffix].startswith("video/") else _ASSET_MAX)
    folder = _project(session, name) / "assets"
    folder.mkdir(parents=True, exist_ok=True)
    target, n = folder / clean, 2
    while target.exists():
        target, n = folder / f"{Path(clean).stem}-{n}{suffix}", n + 1
    await asyncio.to_thread(target.write_bytes, data)
    return {"src": f"assets/{target.name}", "kind": _ASSET_TYPES[suffix].split("/")[0]}


@router.get("/api/studio/{session}/project/{name}/asset/{filename}")
async def get_asset(session: str, name: str, filename: str) -> FileResponse:
    if Path(filename).name != filename or filename.startswith("."):
        raise HTTPException(status_code=404, detail="Aset tidak ada")
    path = _project(session, name) / "assets" / filename
    media = _ASSET_TYPES.get(path.suffix.lower())
    if media is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Aset tidak ada")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=60",
                                                          "X-Content-Type-Options": "nosniff"})


# ── export ──────────────────────────────────────────────────────────────────────
@router.post("/api/studio/{session}/project/{name}/export/part")
async def export_part(request: Request, session: str, name: str, job: str = Query(...),
                      kind: str = Query(..., pattern="^(video|audio)$"),
                      encoding: str = Query(..., alias="format", pattern="^(h264|webm|wav)$")) -> dict[str, Any]:
    if not _JOB_RE.match(job):
        raise HTTPException(status_code=400, detail="Job tidak valid")
    data = await _body(request, _PART_MAX[kind])
    out = _project(session, name) / "out"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f".part-{job}-{kind}{_PART_EXT[encoding]}"
    await asyncio.to_thread(target.write_bytes, data)
    return {"ok": True, "bytes": len(data)}


@router.post("/api/studio/{session}/project/{name}/export/finish")
async def export_finish(session: str, name: str, job: str = Query(...), fps: int = Query(30, ge=1, le=120),
                        duration: float = Query(0, ge=0, le=7200)) -> dict[str, Any]:
    if not _JOB_RE.match(job):
        raise HTTPException(status_code=400, detail="Job tidak valid")
    workspace = _workspace(session)
    out = _project(session, name) / "out"
    video = next((p for p in (out / f".part-{job}-video.h264", out / f".part-{job}-video.webm") if p.exists()), None)
    if video is None:
        raise HTTPException(status_code=400, detail="Bagian video belum dikirim")
    audio = out / f".part-{job}-audio.wav"
    target = out / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    try:
        # WebCodecs H.264 is copied as it is: no quality lost, no time spent. If this ffmpeg will not
        # take the raw stream, the frames are encoded again with libx264.
        error = await _mux(video, audio if audio.exists() else None, target, fps, duration, copy=video.suffix == ".h264")
        if error and video.suffix == ".h264":
            logger.warning(f"[studio] stream copy failed, encoding again: {error[-200:]}")
            error = await _mux(video, audio if audio.exists() else None, target, fps, duration, copy=False)
    finally:
        for part in out.glob(f".part-{job}-*"):
            part.unlink(missing_ok=True)
    if error or not target.exists():
        raise HTTPException(status_code=500, detail=f"ffmpeg gagal: {(error or 'tidak ada file')[-400:]}")
    relative = str(target.relative_to(workspace))
    await asyncio.to_thread(shared_files.add, workspace, relative)
    return {"file": relative, "url": shared_files.url_for(session, relative),
            "markdown": shared_files.markdown(session, relative, target.name), "bytes": target.stat().st_size}


async def _mux(video: Path, audio: Path | None, target: Path, fps: int, duration: float, copy: bool) -> str:
    """Put the video (and the mixed audio) in one MP4. Returns ffmpeg's error, or "" on success."""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    # Raw H.264 has no timestamps; -r on the input gives every frame its time. (-framerate is ignored
    # for this stream, and -shortest would then drop the audio, so the length comes from -t.)
    args += ["-f", "h264", "-r", str(fps), "-i", str(video)] if video.suffix == ".h264" else ["-i", str(video)]
    if audio is not None:
        args += ["-i", str(audio)]
    args += ["-map", "0:v:0"] + (["-map", "1:a:0"] if audio is not None else [])
    args += ["-c:v", "copy"] if copy else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "medium",
                                           "-r", str(fps)]
    if audio is not None:
        args += ["-c:a", "aac", "-b:a", "192k"]
    if duration > 0:
        args += ["-t", f"{duration:.3f}"]
    args += ["-movflags", "+faststart", str(target)]
    try:
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        return "ffmpeg belum terpasang di server"
    try:
        _, err = await asyncio.wait_for(process.communicate(), timeout=_EXPORT_SECONDS)
    except TimeoutError:
        process.kill()
        return "ffmpeg terlalu lama"
    return "" if process.returncode == 0 and target.exists() else (err.decode("utf-8", "replace") or "ffmpeg gagal")


# ── Google Fonts, through this server ───────────────────────────────────────────
def _font_dir() -> Path:
    path = get_settings().project_root / ".agent_memory" / "fonts"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.get("/api/fonts/css2")
async def font_css(request: Request) -> Response:
    families = [f for f in request.query_params.getlist("family") if f][:12]
    if not families or not all(_FONT_FAMILY_RE.match(f) for f in families):
        raise HTTPException(status_code=400, detail="family tidak valid")
    key = re.sub(r"[^A-Za-z0-9]+", "_", "|".join(sorted(families)))[:180] + ".css"
    cached = _font_dir() / key
    if not cached.exists():
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                query: list[tuple[str, str | int | float | bool | None]] = [("family", f) for f in families]
                answer = await client.get("https://fonts.googleapis.com/css2", params=[*query, ("display", "swap")])
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Google Fonts tidak bisa dihubungi") from exc
        if answer.status_code != 200:
            raise HTTPException(status_code=404, detail="Font tidak ditemukan di Google Fonts")
        css = re.sub(r"https://fonts\.gstatic\.com/", "/api/fonts/file/", answer.text)
        await asyncio.to_thread(cached.write_text, css, "utf-8")
    return Response(cached.read_text(encoding="utf-8"), media_type="text/css",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/fonts/file/{path:path}")
async def font_file(path: str) -> Response:
    if not _FONT_FILE_RE.match(path) or ".." in path:
        raise HTTPException(status_code=400, detail="path tidak valid")
    cached = _font_dir() / "files" / path.replace("/", "_")
    if not cached.exists():
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                answer = await client.get(f"https://fonts.gstatic.com/{path}")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Google Fonts tidak bisa dihubungi") from exc
        if answer.status_code != 200:
            raise HTTPException(status_code=404, detail="file font tidak ada")
        cached.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(cached.write_bytes, answer.content)
    kind = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf"}.get(
        Path(path).suffix.lower(), "application/octet-stream")
    return Response(cached.read_bytes(), media_type=kind, headers={"Cache-Control": "public, max-age=31536000, immutable"})


__all__ = ["router"]
