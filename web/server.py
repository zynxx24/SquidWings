"""
web.server — FastAPI web server for Agent Router GUI.

Provides REST + WebSocket endpoints for the agent:
- WebSocket /ws/chat — Streaming chat
- GET /api/models — List models
- GET /api/health — Health check
- GET /api/session/{id}/stats — Session stats
- POST /api/model/select — Select override model
- Static file serving for the frontend
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import shutil
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from agent import session_stack
from agent.orchestrator import Orchestrator
from agent.team_orchestrator import TeamOrchestrator, TeamStore
from brain.classifier.continuation import is_continuation_input
from config.settings import get_settings
from security import sandbox
from security.web_access import WebSecurityMiddleware, is_loopback_host, set_public_mode
from tool.execute.execute_code_intel import quality_tools
from utils.helpers import format_tokens, generate_session_id, is_valid_session_id
from utils.logger import get_logger
from utils.workspace_context import SESSIONS_ROOT, session_workspace
from web.team_api import router as team_router

logger = get_logger(__name__)

# ─── Global State ────────────────────────────────────────────────────
_orchestrator: Orchestrator | None = None
_override_models: dict[str, str] = {}  # session_id → model_id


async def shutdown_event() -> None:
    """Clean up on shutdown."""
    global _orchestrator
    if app.state.team_orchestrator:
        await app.state.team_orchestrator.close()
        app.state.team_orchestrator = None
    if _orchestrator:
        await _orchestrator.close()
        _orchestrator = None
    logger.info("[web] Server shutdown complete")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context manager for startup and shutdown."""
    # Build the shared backend at startup instead of on first request. The team
    # canvas streams runs through the same orchestrator, so a lazy build left
    # ``app.state.team_orchestrator`` as ``None`` for any team request that
    # arrived before the first chat call (the 500 on ``None.store``).
    try:
        get_orchestrator()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[web] eager orchestrator init failed: {exc}")
    # Teams validate models against the registry, which is empty until the first
    # sync. In the background: a slow provider must not delay startup.
    sync_task = asyncio.create_task(ensure_models())
    yield
    sync_task.cancel()
    await shutdown_event()


# ─── App Setup ───────────────────────────────────────────────────────
app = FastAPI(
    title="Agent Router",
    description="Multi-Model AI Agent with Web GUI",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.team_orchestrator = None

# The team canvas is a development-optional feature: it is not mounted at all
# when disabled, so the endpoints 404 instead of leaking a half-wired surface.
_TEAMS_ENABLED = get_settings().teams_enabled
if _TEAMS_ENABLED:
    app.include_router(team_router)


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


_settings = get_settings()
_allowed_origins = _csv(_settings.web_allowed_origins)

# The agent can run tools, so the web edge fails closed: this import is not
# wrapped, and a server that cannot load it must not start.
app.add_middleware(
    WebSecurityMiddleware,
    max_users=_settings.web_max_users,
    allowed_origins=_allowed_origins,
    allowed_hosts=_csv(_settings.web_allowed_hosts),
    requests_per_minute=_settings.web_rate_limit_rpm,
)
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_allowed_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Security headers (CSP, X-Frame-Options, HSTS, nosniff, referrer-policy).
# Imported lazily so the web layer still boots if the security package is
# unavailable; a failure is logged rather than fatal.
try:
    from security.headers import SecurityHeadersMiddleware

    app.add_middleware(SecurityHeadersMiddleware)
except Exception as _sec_exc:  # pragma: no cover - defensive
    logger.warning(f"[web] security headers middleware unavailable: {_sec_exc}")


def open_to_public() -> None:
    """Prepare for access from other machines. There is no login.

    With no working sandbox, commands would run on this machine for anyone who has
    the URL, so the tools that run commands are switched off for public use.
    """
    set_public_mode(True)
    try:
        backend = sandbox.active_backend()
    except sandbox.SandboxError:
        backend = sandbox.BACKEND_NONE
    if backend == sandbox.BACKEND_NONE:
        disabled = {name for name in os.getenv("AGENT_DISABLED_TOOLS", "").split(",") if name}
        os.environ["AGENT_DISABLED_TOOLS"] = ",".join(disabled | {"execute_command", "run_checks"})
        logger.warning("[web] Public access without a sandbox: command tools are disabled")


def get_orchestrator() -> Orchestrator:
    """Get or create the global Orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        settings = get_settings()
        _orchestrator = Orchestrator.from_settings(settings)
        if settings.teams_enabled:
            app.state.team_orchestrator = TeamOrchestrator(
                _orchestrator, TeamStore(Path(__file__).parent / "data" / "teams")
            )
        logger.info("[web] Orchestrator initialized")
    return _orchestrator


# ─── Static Files ────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", response_class=HTMLResponse)
async def serve_index() -> HTMLResponse:
    """Serve the main index.html page."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    resp = HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# Mount static files (CSS, JS, assets)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── Dynamic Models (auto-fetched from /models) ─────────────────────

_models_synced = False
_models_lock = asyncio.Lock()
_FALLBACK_DEFAULT = "ag/gemini-3.8-flash-high"
AUTO_MODEL = "auto"


async def ensure_models(force: bool = False) -> dict[str, int]:
    """Fetch /models from every endpoint once, then reuse the registry."""
    global _models_synced
    orch = get_orchestrator()
    async with _models_lock:
        if _models_synced and not force:
            return {}
        syncer = getattr(orch.api_client, "sync_all_models", None)
        if not callable(syncer):
            _models_synced = True
            return {}
        try:
            result = syncer(orch.registry)
            if inspect.isawaitable(result):
                result = await result
            counts = result if isinstance(result, dict) else {}
        except Exception as exc:  # offline / mocked client must not break the UI
            logger.warning(f"[web] Model sync failed: {exc}")
            counts = {}
        _models_synced = True
        logger.info(f"[web] Models synced: {counts}")
        return counts


def default_model_id() -> str:
    """Configured default if registered, otherwise the first known model."""
    orch = get_orchestrator()
    for candidate in (get_settings().default_model, _FALLBACK_DEFAULT):
        if candidate and orch.registry.get(candidate):
            return candidate
    ids = orch.registry.model_ids
    ids = ids() if callable(ids) else ids
    return next(iter(ids), _FALLBACK_DEFAULT)


def accepted_uploads(model: Any) -> list[str]:
    """File kinds a model can take. Text/code is always accepted."""
    kinds = ["text"]
    if model is None:
        return kinds
    if model.supports_vision:
        kinds.append("image")
    if model.capabilities.pdf:
        kinds.append("pdf")
    if model.supports_audio:
        kinds.append("audio")
    if model.supports_video:
        kinds.append("video")
    return kinds


def model_payload(m: Any) -> dict[str, Any]:
    return {
        "id": m.id,
        "display_name": m.display_name,
        "provider": m.provider or "unknown",
        "supports_vision": m.supports_vision,
        "supports_audio": m.supports_audio,
        "supports_search": m.supports_search,
        "supports_tools": m.supports_tools,
        "supports_reasoning": m.supports_reasoning,
        "context_window": m.context_window,
        "context_window_display": format_tokens(m.context_window),
        "max_output": m.max_output,
        "max_output_display": format_tokens(m.max_output),
        "thinking_format": m.thinking_format,
        "uploads": accepted_uploads(m),
    }


# ─── REST Endpoints ──────────────────────────────────────────────────

@app.get("/api/models")
async def list_models(refresh: bool = False) -> dict[str, Any]:
    """List every model fetched from the configured endpoints."""
    await ensure_models(force=refresh)
    orch = get_orchestrator()
    models = sorted(orch.registry.get_all(), key=lambda m: (m.provider or "", m.id))
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for m in models:
        by_provider.setdefault(m.provider or "unknown", []).append(model_payload(m))
    return {
        "models": by_provider,
        "total": len(models),
        "providers": list(by_provider.keys()),
        "default_model": default_model_id(),
    }


@app.post("/api/models/refresh")
async def refresh_models() -> dict[str, Any]:
    """Re-fetch /models from all endpoints."""
    counts = await ensure_models(force=True)
    return {"status": "ok", "synced": counts, "total": get_orchestrator().registry.count}


def _env_bool(name: str, default: bool) -> bool:
    import os

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    import os

    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


MAX_UPLOAD_BYTES = _env_int("MAX_UPLOAD_MB", 20) * 1024 * 1024
MAX_UPLOAD_CHARS = _env_int("MAX_UPLOAD_CHARS", 120_000)
MAX_CHAT_IMAGES = _env_int("MAX_CHAT_IMAGES", 4)
IMAGE_MAX_SIDE = _env_int("IMAGE_MAX_SIDE", 1536)
_DOC_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".json", ".xml", ".html", ".htm",
    ".epub", ".zip", ".txt", ".md", ".markdown", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".css", ".yaml", ".yml", ".toml", ".ini", ".log", ".sql", ".sh", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".rb", ".php",
}
_PLAIN_EXTS = {
    ".txt", ".md", ".markdown", ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".yaml",
    ".yml", ".toml", ".ini", ".log", ".sql", ".sh", ".go", ".rs", ".java", ".c",
    ".cpp", ".h", ".rb", ".php", ".csv", ".json", ".xml",
}
_markitdown: Any = None
# Audio upload is intentionally unsupported. Video is a draft feature: it is
# off unless VIDEO_UPLOAD_ENABLED=true (needs ffmpeg + speech transcription).
_AUDIO_EXTS: set[str] = set()
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_DOC_EXTS |= _IMAGE_EXTS
if _env_bool("VIDEO_UPLOAD_ENABLED", False):
    _DOC_EXTS |= _VIDEO_EXTS


def _ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return str(imageio_ffmpeg.get_ffmpeg_exe())


def _transcribe(name: str, data: bytes, is_video: bool) -> str:
    """Extract mono 16k WAV with ffmpeg, then transcribe it via MarkItDown."""
    import io
    import subprocess
    import tempfile

    global _markitdown
    from markitdown import MarkItDown, StreamInfo

    if _markitdown is None:
        _markitdown = MarkItDown(enable_plugins=False)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / ("in" + Path(name).suffix.lower())
        wav = Path(tmp) / "out.wav"
        src.write_bytes(data)
        proc = subprocess.run(
            [_ffmpeg_exe(), "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-t", "600", str(wav)],
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0 or not wav.exists():
            raise RuntimeError("No audio track could be extracted")
        try:
            result = _markitdown.convert_stream(io.BytesIO(wav.read_bytes()), stream_info=StreamInfo(extension=".wav"))
            text = result.markdown.strip()
        except Exception as exc:
            if "UnknownValueError" not in str(exc):
                raise
            text = ""  # audio had no recognisable speech
    kind = "Video" if is_video else "Audio"
    return f"[{kind} transcript of {name}]\n{text or '(no speech detected)'}"


def _describe_image(name: str, data: bytes) -> str:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        return f"[Image {name}: {im.format} {im.width}x{im.height}, mode {im.mode}. Pixel content is not sent as text.]"


def _image_data_url(data: bytes, max_side: int = 1536) -> str:
    """Downscale an image and return it as a base64 data URL for vision models."""
    import base64
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im.load()
        im.thumbnail((max_side, max_side))
        has_alpha = im.mode in ("RGBA", "LA", "P")
        buf = io.BytesIO()
        if has_alpha:
            im.convert("RGBA").save(buf, "PNG", optimize=True)
            mime = "image/png"
        else:
            im.convert("RGB").save(buf, "JPEG", quality=85)
            mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


def _convert_to_markdown(name: str, data: bytes) -> str:
    """Convert uploaded bytes to Markdown using MarkItDown (stream mode only)."""
    global _markitdown
    import io

    ext = Path(name).suffix.lower()
    if ext in _PLAIN_EXTS:
        return data.decode("utf-8", errors="replace")
    if ext in _AUDIO_EXTS or ext in _VIDEO_EXTS:
        return _transcribe(name, data, ext in _VIDEO_EXTS)
    if ext in _IMAGE_EXTS:
        return _describe_image(name, data)
    from markitdown import MarkItDown, StreamInfo

    if _markitdown is None:
        _markitdown = MarkItDown(enable_plugins=False)
    result = _markitdown.convert_stream(io.BytesIO(data), stream_info=StreamInfo(extension=ext))
    return str(result.markdown)


@app.post("/api/upload")
async def upload_file(request: Request, name: str = "file.txt") -> dict[str, Any]:
    """Convert an uploaded document to Markdown. Body is the raw file bytes."""
    safe_name = Path(name).name[:120] or "file.txt"
    ext = Path(safe_name).suffix.lower()
    if ext not in _DOC_EXTS:
        raise HTTPException(status_code=415, detail=f"File type '{ext or 'unknown'}' is not supported")
    too_large = HTTPException(
        status_code=413, detail=f"File larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
    )
    # Refuse before buffering: Content-Length can be absent or wrong, so the
    # running total below is what actually enforces the limit.
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise too_large
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > MAX_UPLOAD_BYTES:
            raise too_large
    data = bytes(buffer)
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        markdown = await asyncio.to_thread(_convert_to_markdown, safe_name, data)
    except Exception as exc:
        logger.error(f"[upload] {safe_name}: {exc}")
        raise HTTPException(status_code=422, detail=f"Could not read file: {exc}") from exc
    markdown = markdown.strip()
    truncated = len(markdown) > MAX_UPLOAD_CHARS
    if truncated:
        markdown = markdown[:MAX_UPLOAD_CHARS]
    result: dict[str, Any] = {"name": safe_name, "markdown": markdown, "chars": len(markdown), "truncated": truncated}
    if ext in _IMAGE_EXTS:
        try:
            result["image_data_url"] = await asyncio.to_thread(_image_data_url, data, IMAGE_MAX_SIDE)
        except Exception as exc:
            logger.warning(f"[upload] {safe_name}: image encode failed: {exc}")
    return result


@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    """Check API health."""
    try:
        orch = get_orchestrator()
        healthy = await orch.api_client.health_check()
        return {"status": "healthy" if healthy else "unhealthy", "healthy": healthy}
    except Exception as e:
        return {"status": "error", "healthy": False, "error": str(e)}


@app.get("/api/config")
async def client_config() -> dict[str, Any]:
    """Feature flags the frontend needs to render its navigation."""
    settings = get_settings()
    return {"features": {"teams": settings.teams_enabled}}


@app.get("/api/security")
async def security_status() -> dict[str, Any]:
    """Expose the security layer snapshot (policy, audit chain, rate)."""
    try:
        from security.guard import get_guard_facade

        return get_guard_facade().status()
    except Exception as e:
        return {"enabled": False, "error": str(e)}


@app.get("/api/tunnel")
async def tunnel_status() -> dict[str, Any]:
    """Get Cloudflare Tunnel status and public URL."""
    from utils.tunnel import get_current_tunnel, get_public_url
    url = get_public_url()
    tunnel = get_current_tunnel()
    active = bool(tunnel and tunnel.process and tunnel.process.poll() is None and url)
    return {
        "active": active,
        "public_url": url,
    }


@app.get("/api/session/{session_id}/stats")
async def session_stats(session_id: str) -> dict[str, Any]:
    """Get session statistics."""
    orch = get_orchestrator()
    orch.session_manager.get_or_create_session(session_id)
    stats = orch.session_manager.get_session_stats(session_id)
    return stats


@app.get("/api/sessions")
async def list_sessions() -> dict[str, Any]:
    """List all saved chat sessions with metadata."""
    orch = get_orchestrator()
    sessions = orch.conversation_store.list_sessions()
    return {"sessions": sessions, "total": len(sessions)}


@app.get("/api/session/{session_id}/messages")
async def session_messages(session_id: str) -> dict[str, Any]:
    """Get conversation history messages and metadata for a session."""
    orch = get_orchestrator()
    history = orch.conversation_store.get_history(session_id)
    meta = orch.conversation_store.get_session_meta(session_id)
    model = _override_models.get(session_id) or meta.get("model_used") or default_model_id()
    model_info = None if model == AUTO_MODEL else orch.registry.get(model)
    return {
        "session_id": session_id,
        "title": meta.get("title", "Chat"),
        "created_at": meta.get("created_at"),
        "updated_at": meta.get("updated_at"),
        "model_used": model,
        "model_display": model_info.display_name if model_info else model,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp,
                "model_used": m.model_used,
                "token_count": m.token_count,
                "tool_trace": m.tool_trace,
            }
            for m in history
        ],
    }


@app.post("/api/session/{session_id}/title")
async def update_session_title(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Update title of a chat session."""
    title = payload.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    orch = get_orchestrator()
    orch.conversation_store.set_title(session_id, title)
    return {"status": "ok", "session_id": session_id, "title": title}


@app.delete("/api/session/{session_id}")
@app.post("/api/session/{session_id}/delete")
async def delete_session(session_id: str) -> dict[str, Any]:
    """Delete a chat session completely from memory and disk."""
    orch = get_orchestrator()
    orch.conversation_store.delete_session(session_id)
    if hasattr(orch.session_manager, "delete_session"):
        orch.session_manager.delete_session(session_id)
    _override_models.pop(session_id, None)
    # The chat's folder holds its vault, graph, memory, and files. Only ever a
    # direct child of the sessions folder, under an id already checked to be plain.
    if is_valid_session_id(session_id):
        folder = SESSIONS_ROOT / session_id
        if folder.is_dir():
            await asyncio.to_thread(shutil.rmtree, folder, ignore_errors=True)
    return {"status": "ok", "session_id": session_id, "deleted": True}


def _stack(session_id: str) -> session_stack.SessionStack:
    if not is_valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session id")
    # Just looking at a chat must not leave an empty folder behind.
    return session_stack.SessionStack(session_id, session_workspace(session_id, create=False))


@app.get("/api/session/{session_id}/stack")
async def session_stack_status(session_id: str) -> dict[str, Any]:
    """Everything this chat owns: memory, vault, graph, score, and Ponytail mode."""
    return await asyncio.to_thread(_stack(session_id).status)


@app.post("/api/session/{session_id}/stack")
async def update_session_stack(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Change this chat's Ponytail mode, or switch graphify or aislop on and off."""
    stack = _stack(session_id)
    await asyncio.to_thread(stack.update, **payload)
    return await asyncio.to_thread(stack.status)


@app.post("/api/session/{session_id}/graph")
async def build_session_graph(session_id: str) -> dict[str, Any]:
    """Build or rebuild this chat's code graph now."""
    stack = _stack(session_id)
    stack.workspace.mkdir(parents=True, exist_ok=True)
    result = await quality_tools.code_graph("build", workspace_path=str(stack.workspace))
    return {"result": result, "stack": await asyncio.to_thread(stack.status)}


@app.post("/api/session/{session_id}/aislop")
async def scan_session(session_id: str) -> dict[str, Any]:
    """Run aislop over this chat's folder now."""
    stack = _stack(session_id)
    stack.workspace.mkdir(parents=True, exist_ok=True)
    result = await quality_tools.aislop_scan(workspace_path=str(stack.workspace))
    return {"result": result, "stack": await asyncio.to_thread(stack.status)}


@app.post("/api/session/{session_id}/clear")
async def clear_session(session_id: str) -> dict[str, Any]:
    """Clear conversation history for a session."""
    orch = get_orchestrator()
    orch.conversation_store.clear_session(session_id)
    return {"status": "ok", "session_id": session_id}


@app.post("/api/model/select")
async def select_model(payload: dict[str, Any]) -> dict[str, Any]:
    """Set the model override for a session, restricted strictly to allowed models."""
    session_id = payload.get("session_id", "")
    if not is_valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session id")
    raw_model_id = payload.get("model_id")

    if raw_model_id == AUTO_MODEL:
        _override_models[session_id] = AUTO_MODEL
        return {"status": "ok", "model_id": AUTO_MODEL, "display_name": "Auto"}

    if raw_model_id:
        normalized_id = raw_model_id
        await ensure_models()
        orch = get_orchestrator()
        model = orch.registry.get(normalized_id)
        if not model:
            raise HTTPException(status_code=404, detail=f"Model '{normalized_id}' not found in registry")
        _override_models[session_id] = normalized_id
        return {"status": "ok", "model_id": normalized_id, "display_name": model.display_name, "uploads": accepted_uploads(model)}
    else:
        # Default back to DEFAULT_MODEL
        default_id = default_model_id()
        _override_models[session_id] = default_id
        return {"status": "ok", "model_id": default_id, "display_name": default_id}


@app.get("/api/session/new")
async def new_session() -> dict[str, Any]:
    """Create a new session with DEFAULT_MODEL pre-assigned."""
    orch = get_orchestrator()
    await ensure_models()
    session = orch.session_manager.create_session()
    default_id = default_model_id()
    _override_models[session.session_id] = default_id
    return {"session_id": session.session_id, "default_model": default_id}


# ─── WebSocket Chat ──────────────────────────────────────────────────

async def _await_turn(websocket: WebSocket, turn: asyncio.Task[Any]) -> None:
    """Wait for a turn while listening, so the user can stop it.

    Closing the page stops it too, rather than spending tokens on an answer nobody
    will read.

    Raises:
        asyncio.CancelledError: If the user pressed stop.
        WebSocketDisconnect: If the browser went away.
    """
    while not turn.done():
        incoming = asyncio.create_task(websocket.receive_text())
        await asyncio.wait({turn, incoming}, return_when=asyncio.FIRST_COMPLETED)
        if not incoming.done():
            incoming.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await incoming
            return
        try:
            raw = incoming.result()
        except WebSocketDisconnect:
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn
            raise
        with contextlib.suppress(ValueError):
            if json.loads(raw).get("type") == "stop":
                turn.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await turn
                raise asyncio.CancelledError


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time streaming chat.

    Client sends: {"message": "...", "session_id": "..."}
    Server sends:
        {"type": "meta", ...}
        {"type": "thinking", "content": "..."}
        {"type": "content", "content": "..."}
        {"type": "done", ...}
        {"type": "error", "content": "..."}
    """
    await websocket.accept()
    logger.info("[ws] Client connected")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = None
            # A stop that arrives after the turn already finished has nothing to stop.
            if isinstance(data, dict) and data.get("type") == "stop":
                continue
            if not isinstance(data, dict) or not isinstance(data.get("message"), str):
                await websocket.send_json({"type": "error", "content": "Malformed request"})
                continue

            message = data["message"].strip()
            session_id = data.get("session_id") or generate_session_id()

            if not message:
                await websocket.send_json({"type": "error", "content": "Empty message"})
                continue
            # The id becomes a file name on disk; never trust the client's value.
            if not is_valid_session_id(session_id):
                await websocket.send_json({"type": "error", "content": "Invalid session id"})
                continue

            orch = get_orchestrator()
            selected = _override_models.get(session_id) or default_model_id()
            # None lets the orchestrator route by task type and difficulty.
            override: str | None = None if selected == AUTO_MODEL else selected
            if override and not get_orchestrator().registry.get(override):
                override = default_model_id()

            # A bare "lanjutkan" carries no intent of its own: keep it on the
            # model that produced the previous answer so the thread of the
            # truncated work is not lost to a fresh routing decision.
            is_continue = is_continuation_input(message)
            if is_continue and override in (None, default_model_id()):
                resume_model = orch.conversation_store.get_last_used_model(session_id)
                if resume_model and orch.registry.get(resume_model):
                    override = resume_model

            # Send classification info first
            classification, difficulty = orch.classifier.classify_with_difficulty(message)
            await websocket.send_json({
                "type": "meta",
                "event": "classified",
                "task_type": classification.task_type.value,
                "difficulty": difficulty,
                "confidence": round(classification.confidence, 2),
                "continuation": is_continue,
                "model": override or AUTO_MODEL,
            })

            # Queue for thread-safe ordered streaming chunks
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            async def queue_sender(q: asyncio.Queue[dict[str, Any] | None] = queue) -> None:
                try:
                    while True:
                        item = await q.get()
                        if item is None:
                            break
                        await websocket.send_json(item)
                except Exception as ex:
                    logger.debug(f"[ws] Sender error: {ex}")

            sender_task = asyncio.create_task(queue_sender())

            _content_buf: list[str] = []
            _thinking_buf: list[str] = []

            def on_content(
                chunk: str,
                buf: list[str] = _content_buf,
                q: asyncio.Queue[dict[str, Any] | None] = queue,
            ) -> None:
                buf.append(chunk)
                q.put_nowait({"type": "content", "content": chunk})

            def on_reset(
                buf: list[str] = _content_buf,
                q: asyncio.Queue[dict[str, Any] | None] = queue,
            ) -> None:
                buf.clear()
                q.put_nowait({"type": "reset"})

            def on_event(
                event: dict[str, Any],
                q: asyncio.Queue[dict[str, Any] | None] = queue,
            ) -> None:
                q.put_nowait({**event, "kind": event.get("type"), "type": "progress"})

            def on_thinking(
                chunk: str,
                buf: list[str] = _thinking_buf,
                q: asyncio.Queue[dict[str, Any] | None] = queue,
            ) -> None:
                buf.append(chunk)
                q.put_nowait({"type": "thinking", "content": chunk})

            start_time = time.time()
            # This chat's folder, vault, graph, and Ponytail mode.
            session_stack.enter(session_id)

            try:
                turn = asyncio.create_task(orch.process(
                    user_input=message,
                    session_id=session_id,
                    override_model=override,
                    stream=True,
                    images=[
                        u for u in (data.get("images") or [])
                        if isinstance(u, str) and u.startswith("data:image/")
                    ][:MAX_CHAT_IMAGES],
                    on_content_chunk=on_content,
                    on_thinking_chunk=on_thinking,
                    classification=classification,
                    difficulty=difficulty,
                    on_reset=on_reset,
                    on_event=on_event,
                ))
                await _await_turn(websocket, turn)
                response = turn.result()

                # Send fallback buffered items if streaming didn't output chunks
                if not _thinking_buf and response.thinking:
                    queue.put_nowait({"type": "thinking", "content": response.thinking})

                if not _content_buf and response.content:
                    queue.put_nowait({"type": "content", "content": response.content})

                latency = (time.time() - start_time) * 1000
                queue.put_nowait({
                    "type": "done",
                    "model_used": response.model_used,
                    "task_type": response.task_type,
                    "difficulty": response.difficulty,
                    "confidence": response.confidence,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.total_tokens,
                    "latency_ms": round(latency, 0),
                    "iterations": response.iterations,
                    "status": response.status.value if hasattr(response.status, "value") else str(response.status),
                    "fallback_used": response.fallback_used,
                    "harness": response.harness,
                })
            except asyncio.CancelledError:
                queue.put_nowait({"type": "stopped"})
            except WebSocketDisconnect:
                raise
            except Exception as proc_err:
                logger.error(f"[ws] Orchestrator processing error: {proc_err}")
                queue.put_nowait({"type": "error", "content": str(proc_err)})
            finally:
                queue.put_nowait(None)
                await sender_task

    except WebSocketDisconnect:
        logger.info("[ws] Client disconnected")
    except Exception as e:
        logger.error(f"[ws] Error: {e}")
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "error", "content": str(e)})


# ─── Run ─────────────────────────────────────────────────────────────

def run_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    enable_tunnel: bool = False,
) -> None:
    """Run a local web server; public tunneling is explicitly opt-in."""
    import uvicorn

    from utils.tunnel import CloudflareTunnel, get_current_tunnel

    if enable_tunnel or not is_loopback_host(host):
        open_to_public()

    tunnel_instance = None
    if enable_tunnel and get_current_tunnel() is None:
        tunnel = CloudflareTunnel(port=port)
        public_url = tunnel.start()
        if public_url:
            logger.info(f"[web] Cloudflare Tunnel active: {public_url}")
        tunnel_instance = tunnel

    try:
        uvicorn.run(app, host=host, port=port)
    finally:
        if tunnel_instance:
            tunnel_instance.stop()


if __name__ == "__main__":
    run_server()
