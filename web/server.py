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
import datetime as dt
import inspect
import io
import json
import os
import shutil
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from agent import session_stack
from agent.orchestrator import Orchestrator
from agent.team_orchestrator import TeamOrchestrator, TeamStore
from brain.classifier.continuation import is_continuation_input
from config.settings import get_settings
from model.router.groups import group_of, provider_labels
from model.settings.model_picker import is_chat_model
from security import sandbox
from security.headers import embeddable_headers, shell_headers
from security.web_access import WebSecurityMiddleware, is_loopback_host, set_public_mode
from tool.execute.execute_code_intel import quality_tools
from utils import daily_brief, local_ocr, shared_files
from utils.extract import DOCUMENT_SUFFIXES, Extracted, extract, save_images
from utils.extract import Image as ExtractedImage
from utils.helpers import format_tokens, generate_session_id, is_valid_session_id
from utils.logger import get_logger
from utils.markdown_compact import compact_markdown, fit_markdown
from utils.session_names import quick_title
from utils.workspace_context import (
    forget_session_folder,
    name_session_folder,
    reserve_session_name,
    session_workspace,
)
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
    # Endpoints in .env move to the Router page once; from then on they are managed there.
    try:
        from model.router.store import get_store

        imported = get_store().import_env() if os.getenv("ROUTER_IMPORT_ENV", "1").strip() != "0" else []
        if imported:
            logger.info(f"[web] imported {len(imported)} provider(s) from .env into the Router page")
    except Exception as exc:  # noqa: BLE001 - a broken store must not stop the server
        logger.warning(f"[web] .env import into the Router page failed: {exc}")
    # Folders from before "[web] <title>" naming get their names (once; nothing runs in them yet).
    try:
        renamed = await asyncio.to_thread(_name_old_folders)
        if app.state.team_orchestrator:
            renamed += await asyncio.to_thread(app.state.team_orchestrator.name_old_folders)
        if renamed:
            logger.info(f"[web] named {renamed} old session folder(s) [web]/[team] <title>")
    except Exception as exc:  # noqa: BLE001 - naming is cosmetic
        logger.warning(f"[web] naming old folders failed: {exc}")
    # Daily, in the background: keeps graphify and aislop on their latest release.
    from tool.execute.execute_code_intel import tool_updates

    tool_updates.start_background(get_settings().project_root / ".agent_memory" / "tool_updates.json")
    # Teams validate models against the registry, which is empty until the first
    # sync. In the background: a slow provider must not delay startup.
    sync_task = asyncio.create_task(_startup_sync())
    refresh_task = asyncio.create_task(_model_refresh_loop())
    brief_task = asyncio.create_task(_brief_loop())
    if app.state.team_orchestrator:
        app.state.team_orchestrator.start_scheduler()
    yield
    sync_task.cancel()
    refresh_task.cancel()
    brief_task.cancel()
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

# Trading (Enterprise): the MT5 panel, the MT4 EA bridge and the quant API. They are mounted only
# when the Enterprise module is installed; the community edition has no trading routes at all.
# The MT5 order endpoints stay closed unless MT5_TRADE_TOKEN is set.
from config.edition import ENTERPRISE_NOTE, edition, trading_enabled  # noqa: E402

if trading_enabled():
    from web.mt4_bridge import router as mt4_router
    from web.mt5_api import router as mt5_router
    from web.quant_api import router as quant_router

    app.include_router(mt5_router)
    app.include_router(mt4_router)
    app.include_router(quant_router)
from web.router_api import router as model_router_api  # noqa: E402


def _router_models_sink(provider_id: str, models: list[dict[str, Any]] | None) -> int:
    """Put a Router-page provider's freshly fetched models in the registry, and drop the ones it no longer lists."""
    registry = get_orchestrator().registry
    keep = {m["id"] for m in models or []}
    pinned: set[str] = getattr(registry, "_static_ids", set())
    for model in list(registry.get_all()):
        if model.provider == provider_id and model.id not in keep and model.id not in pinned:
            registry.unregister(model.id)
    if models:
        registry.register_from_api_models(models)
    return len(models or [])


from model.router import sync as _router_sync  # noqa: E402

_router_sync.set_registry_sink(_router_models_sink)

app.include_router(model_router_api)


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


_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}
# The pages the chat page opens inside itself, and their files.
_APP_PAGES = {"trading": STATIC_DIR / "trading" / "index.html", "router": STATIC_DIR / "router" / "index.html"}
_ENTERPRISE_PAGE = """<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Trading · Enterprise</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:15px/1.6 system-ui,sans-serif;
background:#0f1115;color:#e6e8ee}main{max-width:30rem;padding:2rem;text-align:center}
h1{font-size:1.2rem;margin:0 0 .5rem}p{color:#a6adbb;margin:0}</style></head>
<body><main><h1>Trading tersedia di SquidWings Enterprise</h1><p>%s</p></main></body></html>"""


def _page(path: Path, headers: dict[str, str]) -> HTMLResponse:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{path.parent.name or path.name} page not found")
    return HTMLResponse(content=path.read_text(encoding="utf-8"), headers={**_NO_CACHE, **headers})


def _shell() -> HTMLResponse:
    return _page(STATIC_DIR / "index.html", shell_headers())


@app.get("/", response_class=HTMLResponse)
async def serve_index() -> HTMLResponse:
    """The main page: chat, agent teams, and the trading and router pages inside it."""
    return _shell()


@app.get("/trading", response_class=HTMLResponse)
async def serve_trading(embed: bool = False) -> HTMLResponse:
    """The main page opened on the trading panel. ``?embed=1`` is the panel itself, for its frame."""
    if not trading_enabled():
        if not embed:
            return _shell()
        return HTMLResponse(content=_ENTERPRISE_PAGE % ENTERPRISE_NOTE, headers={**_NO_CACHE, **embeddable_headers()})
    return _page(_APP_PAGES["trading"], embeddable_headers()) if embed else _shell()


@app.get("/router", response_class=HTMLResponse)
async def serve_router(embed: bool = False) -> HTMLResponse:
    """The main page opened on the model router. ``?embed=1`` is the router page itself, for its frame."""
    return _page(_APP_PAGES["router"], embeddable_headers()) if embed else _shell()


# Mount static files (CSS, JS, assets)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── Dynamic Models (auto-fetched from /models) ─────────────────────

_models_synced = False
_models_synced_at = 0.0
# The registry is re-read from the API this often, so models the provider adds appear in routing
# without a restart or a manual refresh. 0 turns it off.
MODEL_SYNC_EVERY_S = float(os.getenv("MODEL_SYNC_EVERY_S", "1800") or 0)
_models_lock = asyncio.Lock()
AUTO_MODEL = "auto"


def _registry_size() -> int:
    """Number of registered models; 0 if the registry is unavailable or not countable."""
    try:
        n = get_orchestrator().registry.count
    except Exception:  # noqa: BLE001
        return 0
    return n if isinstance(n, int) else 0


async def _model_refresh_loop() -> None:
    """Re-sync the model list on a timer, so the routing pool sees what the providers list now."""
    if MODEL_SYNC_EVERY_S <= 0:
        return
    while True:
        await asyncio.sleep(MODEL_SYNC_EVERY_S)
        try:
            await ensure_models(force=True)
        except Exception as exc:  # noqa: BLE001 - a failed sync keeps the last good list
            logger.warning(f"[web] scheduled model refresh failed: {exc}")


async def _brief_loop() -> None:
    """Write the daily brief when the server starts and again at BRIEF_TIME every day."""
    if not daily_brief.enabled():
        return
    first = True
    while True:
        try:
            store = get_orchestrator().conversation_store
            await asyncio.to_thread(daily_brief.ensure_today, store, None, None, force=not first)
        except Exception as exc:  # noqa: BLE001 - a brief is a nicety; the server must not care
            logger.warning(f"[web] daily brief failed: {exc}")
        first = False
        await asyncio.sleep(daily_brief.seconds_until(daily_brief.brief_time()) + 1)


async def _startup_sync() -> None:
    """Startup model sync with retries for unreachable providers."""
    for attempt in range(1, 4):
        counts = await ensure_models()
        total = sum(counts.values()) if counts else 0
        if total > 0 or _registry_size() > 0:
            return
        delay = attempt * 5
        logger.warning(
            f"[web] Startup sync attempt {attempt}/3 got 0 models; "
            f"retrying in {delay}s..."
        )
        await asyncio.sleep(delay)
    logger.error("[web] All startup sync attempts failed; models may be incomplete")


async def ensure_models(force: bool = False) -> dict[str, int]:
    """Fetch /models from every endpoint once, then reuse the registry.

    Only marks as synced when at least one provider returned models.
    If every provider fails the flag stays False so the next request retries.
    """
    global _models_synced, _models_synced_at
    orch = get_orchestrator()
    async with _models_lock:
        stale = MODEL_SYNC_EVERY_S > 0 and time.time() - _models_synced_at >= MODEL_SYNC_EVERY_S
        if _models_synced and not force and not stale:
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
        total_fetched = sum(counts.values()) if counts else 0
        # Only mark synced when at least one provider returned models.
        # This lets the next /api/models request retry automatically.
        if total_fetched > 0 or _registry_size() > 0:
            _models_synced = True
            _models_synced_at = time.time()
            logger.info(f"[web] Models synced: {counts}")
        else:
            logger.warning(
                f"[web] Model sync returned 0 models from all providers; "
                f"will retry on next request. Counts: {counts}"
            )
        return counts


def default_model_id() -> str:
    """The model chosen on the Router page, else DEFAULT_MODEL, if registered; otherwise the first known model."""
    from model.router.store import get_store

    orch = get_orchestrator()
    for configured in (get_store().meta("default_model", ""), get_settings().default_model):
        if configured and orch.registry.get(configured):
            return configured
    ids = orch.registry.model_ids
    ids = ids() if callable(ids) else ids
    return next((mid for mid in ids if is_chat_model(mid)), "")


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
    # Endpoints also list embedding and speech models, which cannot hold a chat.
    chat_models = [m for m in orch.registry.get_all() if is_chat_model(m.id)]
    models = sorted(chat_models, key=lambda m: (m.provider or "", m.id))
    names, prefixes = provider_labels()
    by_provider: dict[str, list[dict[str, Any]]] = {}
    groups: dict[str, dict[str, Any]] = {}
    for m in models:
        group = group_of(m.id, m.provider or "unknown", names, prefixes)
        payload = {**model_payload(m), "group": group.key, "group_label": group.label}
        by_provider.setdefault(m.provider or "unknown", []).append(payload)
        entry = groups.setdefault(group.key, {"key": group.key, "label": group.label, "provider": group.provider,
                                              "family": group.family, "count": 0})
        entry["count"] += 1
    return {
        "models": by_provider,
        "groups": sorted(groups.values(), key=lambda g: g["label"].lower()),
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
# What goes into the message. A longer document is fitted to this with its headings kept, and the
# whole text waits in the chat's folder for the agent to read on demand.
UPLOAD_INLINE_CHARS = _env_int("UPLOAD_INLINE_CHARS", 30_000)
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
_DOC_EXTS |= DOCUMENT_SUFFIXES
if _env_bool("VIDEO_UPLOAD_ENABLED", False):
    _DOC_EXTS |= _VIDEO_EXTS


def _ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return str(imageio_ffmpeg.get_ffmpeg_exe())


def _transcribe(name: str, data: bytes, is_video: bool) -> str:
    """Extract mono 16k WAV with ffmpeg, then transcribe it via MarkItDown."""
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

    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        return f"[Image {name}: {im.format} {im.width}x{im.height}, mode {im.mode}. Pixel content is not sent as text.]"


def _image_data_url(data: bytes, max_side: int = 1536) -> str:
    """Downscale an image and return it as a base64 data URL for vision models."""
    import base64

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


def _image_text(data: bytes) -> str:
    """The text in an uploaded picture. Empty when OCR is off or the picture holds none."""
    return (local_ocr.read_images([data])[0] or "") if local_ocr.enabled() else ""


def _upload_folder(session: str) -> Path | None:
    return session_workspace(session) / "uploads" if session else None


def _upload_payload(name: str, ext: str, data: bytes, session: str) -> dict[str, Any]:
    """Everything the file holds: its text, its images, and the full copy in the chat's folder."""
    folder = _upload_folder(session)
    stem = Path(name).stem[:80]
    found = Extracted(_convert_to_markdown(name, data))
    if ext in DOCUMENT_SUFFIXES:
        found = extract(data, name, f"uploads/{stem}_img/", ocr=True)
    elif ext in _IMAGE_EXTS:
        found = Extracted(found.markdown, images=[ExtractedImage(f"gambar{ext}", data)])
        found.images[0].text = _image_text(data)
        found.markdown = "\n\n".join(part for part in (found.markdown, found.images[0].text) if part)
    full = compact_markdown(found.markdown.strip())[:MAX_UPLOAD_CHARS]
    fitted = fit_markdown(full, UPLOAD_INLINE_CHARS)
    result: dict[str, Any] = {
        "name": name, "pages": found.pages, "scanned": found.scanned, "markdown": fitted, "chars": len(fitted),
        "total_chars": len(full), "truncated": len(fitted) < len(full), "images": len(found.images), "saved": "",
        "ocr_read": sum(bool(item.text) for item in found.images), "ocr_failed": found.ocr_failed,
    }
    if folder and full:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{stem}.md").write_text(full, encoding="utf-8")
        save_images(found, folder / f"{stem}_img")
        result["saved"] = f"uploads/{stem}.md"
    return result


def _convert_to_markdown(name: str, data: bytes) -> str:
    """Convert uploaded bytes to Markdown using MarkItDown (stream mode only)."""
    global _markitdown

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
    return compact_markdown(str(result.markdown))


@app.post("/api/upload")
async def upload_file(request: Request, name: str = "file.txt", session: str = "") -> dict[str, Any]:
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
        result = await asyncio.to_thread(_upload_payload, safe_name, ext, data, session)
    except Exception as exc:
        logger.error(f"[upload] {safe_name}: {exc}")
        raise HTTPException(status_code=422, detail=f"Could not read file: {exc}") from exc
    # The models take text only, so a picture goes as what OCR read. SEND_IMAGES=true is for a model that can see.
    if ext in _IMAGE_EXTS and _env_bool("SEND_IMAGES", False):
        try:
            result["image_data_url"] = await asyncio.to_thread(_image_data_url, data, IMAGE_MAX_SIDE)
        except Exception as exc:
            logger.warning(f"[upload] {safe_name}: image encode failed: {exc}")
    return result


TRADING_SERVER_URL = "http://127.0.0.1"   # where `python main.py trading` listens; MT4 reaches port 80 only


async def trading_server_running() -> bool:
    """True when the standalone trading server (the one holding the EA's data) answers on port 80."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=0.8) as client:
            resp = await client.get(f"{TRADING_SERVER_URL}/trading")
    except httpx.HTTPError:
        return False
    return resp.status_code == 200 and "/static/trading/trading.js" in resp.text


@app.get("/api/trading/panel")
async def trading_panel(request: Request) -> dict[str, Any]:
    """Which trading panel the chat page shows: the port-80 server's (with the EA data) or this one's."""
    if not trading_enabled():
        return {"external": False, "hint": ENTERPRISE_NOTE}
    local = is_loopback_host(request.client.host if request.client else "")
    external = local and await trading_server_running()
    return {
        "external": external,
        "hint": "" if external else (
            "Data EA MT4 ada di server trading port 80, yang belum jalan. Panel ini dari server web, "
            "cocok untuk backend sim atau MT5."),
    }


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
    return {"edition": edition(), "features": {"teams": settings.teams_enabled, "trading": trading_enabled()}}


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


@app.get("/api/brief")
async def get_brief(refresh: bool = False) -> dict[str, Any]:
    """Today's brief: what happened since yesterday and what is still hanging."""
    store = get_orchestrator().conversation_store
    brief = await asyncio.to_thread(daily_brief.ensure_today, store, None, None, force=refresh)
    return {"enabled": daily_brief.enabled(), "brief": brief, "markdown": daily_brief.render(brief),
            "shown": not daily_brief.due(channel="web")}


@app.post("/api/brief/seen")
async def brief_seen() -> dict[str, Any]:
    """The page showed today's brief; it stays closed until tomorrow's."""
    day = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    daily_brief.mark_seen(day, channel="web")
    return {"ok": True, "day": day}


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
        "title": orch.conversation_store.label(session_id),
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
        folder = session_workspace(session_id, create=False)
        if folder.is_dir():
            await asyncio.to_thread(shutil.rmtree, folder, ignore_errors=True)
        forget_session_folder(session_id)
    return {"status": "ok", "session_id": session_id, "deleted": True}


def _stack(session_id: str) -> session_stack.SessionStack:
    if not is_valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session id")
    # Just looking at a chat must not leave an empty folder behind.
    return session_stack.SessionStack(session_id, session_workspace(session_id, create=False))


@app.get("/api/session/{session_id}/files/{relative:path}")
async def session_file(session_id: str, relative: str) -> FileResponse:
    """A file the agent shared in this chat. Nothing else in the session can be read here."""
    if not is_valid_session_id(session_id):
        raise HTTPException(status_code=404, detail="File not shared in this chat")
    path = shared_files.resolve(session_workspace(session_id, create=False), relative)
    if path is None:
        raise HTTPException(status_code=404, detail="File not shared in this chat")
    kind = shared_files.kind_of(path.name)
    # Media plays inline. Anything else downloads, so an HTML file cannot run on this origin.
    disposition = "inline" if kind != "file" else "attachment"
    # Opened directly in a tab, the file still gets no script, forms, or same-origin access.
    return FileResponse(
        path, filename=path.name, content_disposition_type=disposition,
        headers={"Content-Security-Policy": "sandbox"},
    )


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


# Media a session's files reference (``![chart](plot.png)``, ``![video](renders/share.mp4)``).
# Only raster images, video and audio, only from this session's own folder, and only through the
# path guard, so a link cannot reach another chat or the rest of the disk. Video and audio play
# in the chat (FileResponse answers Range requests, so seeking works); HTML or SVG never does,
# because served from this origin it could run script.
_SESSION_IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}
_SESSION_MEDIA_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
}
_SESSION_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_SESSION_MEDIA_MAX_BYTES = shared_files.MAX_SHARE_BYTES
_SESSION_FILE_HEADERS = {
    "Cache-Control": "private, max-age=300",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
}


@app.get("/api/session/{session_id}/file")
async def session_image(session_id: str, path: str = Query(..., min_length=1, max_length=400)) -> FileResponse:
    from tool.execute.execute_local_ops.path_guard import PathGuardError, resolve_inside

    workspace = session_workspace(session_id, create=False).resolve()
    try:
        target = resolve_inside(workspace, path, must_exist=True)
    except PathGuardError as exc:
        raise HTTPException(status_code=404, detail="file not found in this session") from exc
    relative = target.relative_to(workspace).parts
    if relative and relative[0] in shared_files.PRIVATE_DIRS:
        raise HTTPException(status_code=404, detail="file not found in this session")
    suffix = target.suffix.lower()
    image = _SESSION_IMAGE_TYPES.get(suffix)
    media = image or _SESSION_MEDIA_TYPES.get(suffix)
    if media is None or not target.is_file():
        raise HTTPException(
            status_code=415,
            detail="only images (png, jpg, gif, webp, bmp), video (mp4, webm, mov) and audio (mp3, wav, ogg, m4a) can be shown",
        )
    limit = _SESSION_IMAGE_MAX_BYTES if image else _SESSION_MEDIA_MAX_BYTES
    if target.stat().st_size > limit:
        raise HTTPException(status_code=413, detail=f"file larger than {limit // (1024 * 1024)} MB")
    return FileResponse(target, media_type=media, headers=_SESSION_FILE_HEADERS)


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

def _start_naming(orch: Any, session_id: str, tag: str, message: str) -> asyncio.Task[str | None] | None:
    """Tag the chat, give its folder a quick name, and ask the AI for a title while the turn runs.

    Naming is cosmetic: any failure here leaves the chat unnamed, never stops the turn.
    """
    try:
        store = orch.conversation_store
        store.set_source(session_id, tag)
        if store.needs_ai_title(session_id) is not True:
            return None
        first = next((m.content for m in store.get_history(session_id)
                      if m.role == "user" and str(m.content).strip()), message)
        reserve_session_name(session_id, tag, quick_title(str(first)))
        pending = orch.title_for(str(first))
        return asyncio.ensure_future(pending) if inspect.isawaitable(pending) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[ws] chat naming skipped: {exc}")
        return None


async def _finish_naming(orch: Any, session_id: str, tag: str, task: asyncio.Task[str | None] | None) -> str | None:
    """After the turn: keep the AI's title and rename the folder to match. Returns the new label."""
    if task is None:
        return None
    try:
        title = await asyncio.wait_for(task, timeout=5)
    except Exception:  # noqa: BLE001 - slow or failed: the quick title stays and the next turn asks again
        task.cancel()
        return None
    try:
        store = orch.conversation_store
        if isinstance(title, str) and title:
            store.set_title(session_id, title, ai=True)
        await asyncio.to_thread(name_session_folder, session_id, tag, str(store.get_session_meta(session_id).get("title", "")))
        return str(store.label(session_id))
    except Exception as exc:  # noqa: BLE001 - a folder that cannot be renamed keeps its name
        logger.debug(f"[ws] chat naming skipped: {exc}")
        return None


def _name_old_folders() -> int:
    """Give folders made before the naming a ``[web] <title>`` name. Runs once at start, nothing works in them then."""
    from utils import workspace_context as wc

    store = get_orchestrator().conversation_store
    names = wc._read_names()
    done = 0
    for item in store.list_sessions():
        sid = item["session_id"]
        if not is_valid_session_id(sid) or sid in names or not (wc.SESSIONS_ROOT / sid).is_dir():
            continue
        if name_session_folder(sid, item.get("source") or "web", item.get("name") or ""):
            done += 1
    return done


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
            # "[web] <title>": a quick title now, the AI's own (at most 28 characters) once it answers
            title_task = _start_naming(orch, session_id, "web", message)
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
                titled = await _finish_naming(orch, session_id, "web", title_task)
                if titled:
                    queue.put_nowait({"type": "meta", "event": "title", "session_id": session_id, "title": titled})
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
