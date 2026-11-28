"""HTTP API for the video Studio: projects, assets, export, and a Google Fonts cache.

The Studio page edits ``studio/<name>/motion.json`` in a chat's folder (see ``addons.studio.studio_doc``),
the same file the agent writes. Saves carry the stamp the page loaded, so an edit never silently
overwrites a change the agent made meanwhile (409 with the newer stamp).

Export runs on this machine with the C engine (``/render``), as a job the page only starts and
watches. The older browser export (WebCodecs, parts uploaded and muxed here) stays as a fallback.

Live updates: ``/events`` is one Server-Sent Events stream per open editor. It pushes the project's
stamp when the file changes (the agent or another tab saved), render progress, and proxy progress,
so the page does not poll. A phone that sleeps drops the stream; the browser reconnects on its own.

Proxies: big video clips get a small copy (``proxies/``) that the editor plays instead.

Fonts: the page's CSP only allows its own origin, so Google Fonts CSS and font files are fetched
through ``/api/fonts`` and kept on disk. A font used once keeps working offline and in exports.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from addons.studio import code_projects, renderer, studio_doc
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
    ".cube": "text/plain",
}
_ASSET_KIND = {".cube": "lut"}
_ASSET_MAX = 80 * 1024 * 1024
_VIDEO_ASSET_MAX = 600 * 1024 * 1024
_PART_MAX = {"video": 1024 * 1024 * 1024, "audio": 512 * 1024 * 1024}
_PART_EXT = {"h264": ".h264", "webm": ".webm", "wav": ".wav"}
_JOB_RE = re.compile(r"^[a-z0-9]{8,40}$")
_FONT_FAMILY_RE = re.compile(r"^[A-Za-z0-9 ]{1,60}(:wght@[0-9;]{1,40})?$")
_FONT_FILE_RE = re.compile(r"^[A-Za-z0-9/_.-]{5,200}$")
_EXPORT_SECONDS = 6 * 3600   # the browser export's final mux; the server render has no time limit


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
    _notify()
    return {"name": name, "doc": clean, "stamp": stamp}


# ── assets ──────────────────────────────────────────────────────────────────────
@router.get("/api/studio/{session}/project/{name}/assets")
async def list_assets(session: str, name: str) -> dict[str, Any]:
    folder = _project(session, name) / "assets"
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _ASSET_TYPES) if folder.is_dir() else []
    return {"assets": [{"src": f"assets/{p.name}", "size": p.stat().st_size, "kind": _kind(p.suffix)} for p in files]}


def _kind(suffix: str) -> str:
    suffix = suffix.lower()
    return _ASSET_KIND.get(suffix) or _ASSET_TYPES[suffix].split("/")[0]


@router.post("/api/studio/{session}/project/{name}/asset")
async def upload_asset(request: Request, session: str, name: str, file: str = Query(..., max_length=120)) -> dict[str, Any]:
    clean = re.sub(r"[^A-Za-z0-9 ._()-]", "_", Path(file).name).strip(" .")[:120] or "file"
    suffix = Path(clean).suffix.lower()
    if suffix not in _ASSET_TYPES:
        raise HTTPException(status_code=415, detail="Hanya gambar (png, jpg, webp, gif), audio (mp3, wav, ogg, m4a), "
                                                    "video (mp4, webm, mov) dan LUT warna (.cube)")
    data = await _body(request, _VIDEO_ASSET_MAX if _ASSET_TYPES[suffix].startswith("video/") else _ASSET_MAX)
    folder = _project(session, name) / "assets"
    folder.mkdir(parents=True, exist_ok=True)
    target, n = folder / clean, 2
    while target.exists():
        target, n = folder / f"{Path(clean).stem}-{n}{suffix}", n + 1
    await asyncio.to_thread(target.write_bytes, data)
    src = f"assets/{target.name}"
    kind = _kind(suffix)
    proxy = None
    if kind == "video":
        try:
            if await asyncio.to_thread(renderer.wants_proxy, target):
                proxy = _start_proxy(session, name, src).public()
        except renderer.RenderError as exc:   # no ffmpeg: the clip still works, only without a proxy
            logger.info(f"[studio] no proxy for {src}: {exc}")
    _notify()
    return {"src": src, "kind": kind, "proxy": proxy}


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
    relative = target.relative_to(workspace).as_posix()
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


# ── server render (the C engine) ───────────────────────────────────────────────
# An export runs on this machine as a job: the browser only starts it and watches. A phone that
# goes to sleep does not stop it, and reopening the editor shows where it is.
_render_jobs: dict[str, renderer.Job] = {}
_JOB_KEEP_S = 3600.0


def _forget_old_jobs() -> None:
    now = time.time()
    for job_id, job in list(_render_jobs.items()):
        if job.finished and now - job.finished > _JOB_KEEP_S:
            del _render_jobs[job_id]


@router.get("/api/studio/engine")
async def engine_status() -> dict[str, Any]:
    """Is the C engine built, which GPU encoders work here, the thread and memory limits."""
    return await asyncio.to_thread(renderer.status)


@router.post("/api/studio/{session}/project/{name}/render")
async def start_render(session: str, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    workspace = _workspace(session)
    _project(session, name)
    scale = float(payload.get("scale") or 1)
    if not 0.1 <= scale <= 2:
        raise HTTPException(status_code=400, detail="scale 0.1-2")
    quality = str(payload.get("quality") or "high")
    gpu = str(payload.get("gpu") or "")
    _forget_old_jobs()
    job = renderer.Job(id=os.urandom(8).hex(), session=session, project=name)
    _render_jobs[job.id] = job

    def work() -> None:
        job.state = "running"
        try:
            target = renderer.export(workspace, name, scale=scale, quality=quality, gpu=gpu, job=job)
            relative = target.relative_to(workspace).as_posix()
            shared_files.add(workspace, relative)
            job.file = relative
            job.state = "done"
        except renderer.RenderError as exc:
            job.state = "cancelled" if job.cancel.is_set() else "error"
            job.error = str(exc)
        except (StudioError, OSError) as exc:
            job.state, job.error = "error", str(exc)
        finally:
            job.finished = time.time()
            _notify()

    asyncio.get_running_loop().run_in_executor(None, work)
    _notify()
    return job.public()


def _job(session: str, job_id: str) -> renderer.Job:
    job = _render_jobs.get(job_id)
    if job is None or job.session != session:
        raise HTTPException(status_code=404, detail="Job render tidak ada")
    return job


@router.get("/api/studio/{session}/render/{job_id}")
async def render_status(session: str, job_id: str) -> dict[str, Any]:
    job = _job(session, job_id)
    out = job.public()
    if job.state == "done" and job.file:
        out.update(url=shared_files.url_for(session, job.file), markdown=shared_files.markdown(session, job.file, Path(job.file).name))
    return out


@router.get("/api/studio/{session}/renders")
async def render_list(session: str, project: str = "") -> dict[str, Any]:
    """Renders of this chat (running first), so a reopened editor finds the one it started."""
    jobs = [j for j in _render_jobs.values() if j.session == session and (not project or j.project == project)]
    jobs.sort(key=lambda j: (j.state != "running", -j.started))
    return {"jobs": [j.public() for j in jobs[:20]]}


@router.post("/api/studio/{session}/render/{job_id}/cancel")
async def render_cancel(session: str, job_id: str) -> dict[str, Any]:
    job = _job(session, job_id)
    await asyncio.to_thread(renderer.cancel, job)
    return job.public()


@router.post("/api/studio/{session}/project/{name}/still")
async def render_still(session: str, name: str, payload: dict[str, Any] | None = None) -> Response:
    """A PNG of the frame(s) at the given times, drawn by the engine (what the export will show)."""
    payload = payload or {}
    workspace = _workspace(session)
    times = [float(t) for t in (payload.get("times") or [payload.get("t") or 0])][:16]
    try:
        png = await asyncio.to_thread(renderer.still, workspace, name, times, scale=float(payload.get("scale") or 0.5))
    except (renderer.RenderError, StudioError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    data = png.read_bytes()
    png.unlink(missing_ok=True)
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


# ── proxies ─────────────────────────────────────────────────────────────────────
_proxy_jobs: dict[str, renderer.Job] = {}


def _proxy_key(session: str, name: str, src: str) -> str:
    return f"{session}/{name}/{src}"


def _start_proxy(session: str, name: str, src: str) -> renderer.Job:
    """A proxy job for this clip (the running one when there is one)."""
    key = _proxy_key(session, name, src)
    running = _proxy_jobs.get(key)
    if running and running.state in ("queued", "running"):
        return running
    project = _project(session, name)
    for old_key, old in list(_proxy_jobs.items()):
        if old.finished and time.time() - old.finished > _JOB_KEEP_S:
            del _proxy_jobs[old_key]
    job = renderer.Job(id=os.urandom(8).hex(), session=session, project=name, kind="proxy", src=src)
    _proxy_jobs[key] = job

    def work() -> None:
        job.state = "running"
        try:
            renderer.make_proxy(project, src, job)
            job.state = "done"
        except (renderer.RenderError, OSError) as exc:
            job.state, job.error = ("cancelled" if job.cancel.is_set() else "error"), str(exc)
        finally:
            job.finished = time.time()
            _notify()

    asyncio.get_running_loop().run_in_executor(None, work)
    return job


def _proxies(session: str, name: str, project: Path) -> dict[str, dict[str, Any]]:
    """Every video clip of the project: is its proxy ready, or being made."""
    folder = project / "assets"
    found: dict[str, dict[str, Any]] = {}
    if not folder.is_dir():
        return found
    for path in sorted(folder.iterdir()):
        if not path.is_file() or _ASSET_TYPES.get(path.suffix.lower(), "").split("/")[0] != "video":
            continue
        src = f"assets/{path.name}"
        job = _proxy_jobs.get(_proxy_key(session, name, src))
        ready = renderer.proxy_ready(project, src)
        found[src] = {"ready": ready, "state": "done" if ready else (job.state if job else "none"),
                      "progress": 1.0 if ready else (round(job.progress, 3) if job else 0.0),
                      "error": job.error if job and not ready else ""}
    return found


@router.get("/api/studio/{session}/project/{name}/proxies")
async def list_proxies(session: str, name: str) -> dict[str, Any]:
    return {"proxies": await asyncio.to_thread(_proxies, session, name, _project(session, name))}


@router.post("/api/studio/{session}/project/{name}/proxy")
async def make_proxy(session: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    src = str(payload.get("src") or "")
    if not studio_doc.SRC_RE.match(src) or not (_project(session, name) / src).is_file():
        raise HTTPException(status_code=404, detail="Klip tidak ada")
    if _ASSET_TYPES.get(Path(src).suffix.lower(), "").split("/")[0] != "video":
        raise HTTPException(status_code=400, detail="Proxy hanya untuk klip video")
    job = _start_proxy(session, name, src)
    _notify()
    return job.public()


@router.get("/api/studio/{session}/project/{name}/proxy/{filename}")
async def get_proxy(session: str, name: str, filename: str) -> FileResponse:
    if Path(filename).name != filename or filename.startswith("."):
        raise HTTPException(status_code=404, detail="Proxy tidak ada")
    project = _project(session, name)
    src = f"assets/{filename}"
    if not renderer.proxy_ready(project, src):
        raise HTTPException(status_code=404, detail="Proxy belum siap")
    return FileResponse(renderer.proxy_path(project, src), media_type="video/mp4",
                        headers={"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"})


# ── live updates (Server-Sent Events) ───────────────────────────────────────────
# One stream per open editor. Saves, finished jobs and new proxies wake every stream at once;
# otherwise each looks again twice a second while something is running, once a second when not
# (a stat of motion.json: cheap). The agent in a terminal writes the same file, so the stat stays.
_listeners: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()
_PING_S = 15.0


def _notify() -> None:
    """Wake every open event stream (safe from any thread)."""
    for loop, event in list(_listeners):
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:          # that loop has closed: the stream is gone
            _listeners.discard((loop, event))


def _live_state(session: str, name: str, project: Path) -> dict[str, Any]:
    renders = [j.public() for j in _render_jobs.values() if j.session == session and j.project == name]
    renders.sort(key=lambda j: (j["state"] != "running", -j["seconds"]))
    return {"doc": {"stamp": studio_doc.stamp(project / "motion.json")}, "render": renders[:5],
            "proxy": _proxies(session, name, project)}


@router.get("/api/studio/{session}/events")
async def live_events(request: Request, session: str, project: str = Query(""), once: bool = False) -> StreamingResponse:
    """Server-Sent Events: `doc` (stamp), `render` (jobs), `proxy` (clips), each sent when it changes."""
    folder = _project(session, project)

    async def stream():  # type: ignore[no-untyped-def]
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        entry = (loop, wake)
        _listeners.add(entry)
        last: dict[str, str] = {}
        quiet_since = time.monotonic()
        try:
            yield "retry: 2000\n\n"
            while True:
                state = await asyncio.to_thread(_live_state, session, project, folder)
                for event, value in state.items():
                    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    if last.get(event) != text:
                        last[event] = text
                        quiet_since = time.monotonic()
                        yield f"event: {event}\ndata: {text}\n\n"
                if once or await request.is_disconnected():
                    return
                if time.monotonic() - quiet_since > _PING_S:   # keeps tunnels and proxies from closing an idle stream
                    quiet_since = time.monotonic()
                    yield ": ping\n\n"
                busy = any(j["state"] in ("queued", "running") for j in state["render"]) or \
                    any(p["state"] in ("queued", "running") for p in state["proxy"].values())
                wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), timeout=0.5 if busy else 1.0)
        finally:
            _listeners.discard(entry)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


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



# ── code projects: HyperFrames, Remotion and HTML edited as blocks (addons.studio.code_projects) ─────────────
def _code_error(exc: StudioError) -> HTTPException:
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=409, detail={"message": "File ini sudah diubah (mungkin oleh AI). Blok dimuat ulang.",
                                                      "stamp": exc.current})
    return HTTPException(status_code=422, detail=str(exc))


_code_tasks: set[asyncio.Task[None]] = set()     # running code renders, kept so the loop does not drop them


def _ide_on() -> bool:
    from brain.config.edition import addon_enabled

    return addon_enabled("ide")


@router.get("/api/studio/{session}/code/projects")
async def code_projects_list(session: str) -> dict[str, Any]:
    workspace = _workspace(session)
    found = await asyncio.to_thread(code_projects.find, workspace)
    shared = set(shared_files.shared(workspace))
    for project in found:
        render = project.pop("render")
        project["render_url"] = shared_files.url_for(session, render) if render in shared else ""
    return {"projects": found, "ide": _ide_on()}


@router.get("/api/studio/{session}/code/files")
async def code_project_files(session: str, project: str = Query(..., max_length=500)) -> dict[str, Any]:
    workspace = _workspace(session)
    try:
        folder, kind = await asyncio.to_thread(code_projects.project_folder, workspace, project)
    except StudioError as exc:
        raise _code_error(exc) from exc
    files = await asyncio.to_thread(code_projects.editable_files, folder)
    return {"project": project, "kind": kind, "files": files, "ide": _ide_on()}


@router.get("/api/studio/{session}/code/blocks")
async def code_blocks(session: str, project: str = Query(..., max_length=500),
                      path: str = Query(..., max_length=500)) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(code_projects.blocks, _workspace(session), project, path)
    except StudioError as exc:
        raise _code_error(exc) from exc


@router.put("/api/studio/{session}/code/blocks")
async def code_blocks_save(session: str, payload: dict[str, Any]) -> dict[str, Any]:
    project, path = str(payload.get("project") or ""), str(payload.get("path") or "")
    if len(project) > 500 or not path or len(path) > 500:
        raise HTTPException(status_code=400, detail="project dan path wajib diisi")
    try:
        return await asyncio.to_thread(code_projects.save, _workspace(session), project, path,
                                       str(payload.get("base") or ""), payload.get("edits") or [])
    except StudioError as exc:
        raise _code_error(exc) from exc


@router.post("/api/studio/{session}/code/preview")
async def code_preview(session: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A frozen, sandboxed copy of an HTML page of the project (the same /a/ apps the chat shares)."""
    from brain.utils import app_links

    workspace = _workspace(session)
    project, path = str(payload.get("project") or ""), str(payload.get("path") or "index.html")
    try:
        folder, kind = await asyncio.to_thread(code_projects.project_folder, workspace, project)
    except StudioError as exc:
        raise _code_error(exc) from exc
    if kind == "remotion":
        raise HTTPException(status_code=422, detail="Proyek React (Remotion) dilihat lewat render. Tekan Render.")
    page = folder / path
    if not app_links.is_app(page):
        page = folder / "index.html"
    try:
        token = await asyncio.to_thread(app_links.publish, page)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Pratinjau tidak bisa dibuat: {exc}") from exc
    return {"url": app_links.path_for(token)}


@router.post("/api/studio/{session}/code/render")
async def code_render(session: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Render a code project through the render hub (video_render); the page watches it like any Studio job."""
    from tools.toolbox import media_tools

    workspace = _workspace(session)
    project = str(payload.get("project") or "")
    composition = str(payload.get("composition") or "")[:200]
    try:
        folder, kind = await asyncio.to_thread(code_projects.project_folder, workspace, project)
    except StudioError as exc:
        raise _code_error(exc) from exc
    if kind == "html":
        raise HTTPException(status_code=422, detail="Halaman HTML biasa tidak dirender. Jadikan proyek HyperFrames dulu (minta AI).")
    _forget_old_jobs()
    job = renderer.Job(id=os.urandom(8).hex(), session=session, project=project, message="Menunggu giliran render")
    _render_jobs[job.id] = job

    async def work() -> None:
        job.state, job.message = "running", f"Merender {kind}"
        try:
            result = await media_tools.video_render(folder.relative_to(workspace.resolve()).as_posix() or ".",
                                                    composition=composition, share=False, workspace_path=str(workspace))
        except Exception as exc:  # noqa: BLE001 - any failure of the external renderer is shown to the user as is
            result = {"ok": False, "error": str(exc)}
        if result.get("ok") and result.get("saved"):
            job.file = str(result["saved"])
            await asyncio.to_thread(shared_files.add, workspace, job.file)
            job.state, job.progress = "done", 1.0
        else:
            job.state = "error"
            choices = result.get("compositions")
            job.error = (f"Pilih komposisi: {', '.join(choices)}" if choices else
                         str(result.get("error") or "render gagal") + (f"\n{result.get('log_tail')}" if result.get("log_tail") else ""))
        job.finished = time.time()
        _notify()

    task = asyncio.get_running_loop().create_task(work())
    _code_tasks.add(task)
    task.add_done_callback(_code_tasks.discard)
    _notify()
    return job.public()


__all__ = ["router"]
