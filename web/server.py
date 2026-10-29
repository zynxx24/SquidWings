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
import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from agent.orchestrator import Orchestrator
from agent.team_orchestrator import TeamOrchestrator, TeamStore
from config.settings import get_settings
from utils.helpers import format_tokens, generate_session_id
from utils.logger import get_logger
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
    yield
    await shutdown_event()


# ─── App Setup ───────────────────────────────────────────────────────
app = FastAPI(
    title="Agent Router",
    description="Multi-Model AI Agent with Web GUI",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.team_orchestrator = None

app.include_router(team_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


def get_orchestrator() -> Orchestrator:
    """Get or create the global Orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        settings = get_settings()
        _orchestrator = Orchestrator.from_settings(settings)
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
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


# Mount static files (CSS, JS, assets)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── Allowed Models for UI ──────────────────────────────────────────
ALLOWED_UI_MODELS: dict[str, str] = {
    "ag/gemini-3.8-flash-high": "Gemini 3.8 Flash",
    "gemini/gemini-4-argon": "Gemini 4 Argon (High)",
    "kg/deepseek-4-1-flash": "DeepSeek 4.1 Flash",
    "kg/dp4.1ftest": "DeepSeek 4.1 Flash Test",
    "kg/deepseek-v4-pro": "DeepSeek V4 Pro",
    "kg/qwen3.8max": "Qwen 3.8 Max",
    "kg/gemini-3.7-flash": "Gemini 3.7 Flash",
    "kg/gpt-5-5": "GPT-5.5",
    "kg/claude-opus-5.5": "Claude Opus 5.5",
    "kg/mimo-v2.6-flash": "Xiaomi MiMo 2.6 Flash",
    "kg/hy3": "Tencent Hunyuan 3",
    "kg/hy4-preview": "Tencent Hunyuan 4 Preview",
    "bansos/deepseek-v4.1-flash-1017": "🆓 DeepSeek V4.1 Flash (Bansos)",
}

MODEL_ALIASES: dict[str, str] = {
    "ag/gemini-3.8-flash": "ag/gemini-3.8-flash-high",
    "gemini-3.8-flash": "ag/gemini-3.8-flash-high",
    "gemini/gemini-4-argon": "gemini/gemini-4-argon",
    "gemini-4-argon": "gemini/gemini-4-argon",
    "gemini/gemini-4-argon(high)": "gemini/gemini-4-argon",
    "kg/deepseek 4.1 flash": "kg/deepseek-4-1-flash",
    "deepseek 4.1 flash": "kg/deepseek-4-1-flash",
    "deepseek-4.1-flash": "kg/deepseek-4-1-flash",
    "kg/dp4.1ftest": "kg/dp4.1ftest",
    "dp4.1ftest": "kg/dp4.1ftest",
    "kg/deepseek-v4-pro": "kg/deepseek-v4-pro",
    "deepseek-v4-pro": "kg/deepseek-v4-pro",
    "deepseek v4 pro": "kg/deepseek-v4-pro",
    "kg/qwen3.8max": "kg/qwen3.8max",
    "qwen3.8max": "kg/qwen3.8max",
    "qwen-3.8-max": "kg/qwen3.8max",
    "qwen 3.8 max": "kg/qwen3.8max",
    "kg/gemini-3.7-flash": "kg/gemini-3.7-flash",
    "gemini-3.7-flash": "kg/gemini-3.7-flash",
    "kg/gpt-5-5": "kg/gpt-5-5",
    "gpt-5-5": "kg/gpt-5-5",
    "gpt 5.5": "kg/gpt-5-5",
    "kg/claude-opus-5.5": "kg/claude-opus-5.5",
    "claude-opus-5.5": "kg/claude-opus-5.5",
    "claude-opus-5-5": "kg/claude-opus-5.5",
    "claude opus 5.5": "kg/claude-opus-5.5",
    "kg/mimo-v2.6-flash": "kg/mimo-v2.6-flash",
    "mimo-v2.6-flash": "kg/mimo-v2.6-flash",
    "mimo 2.6 flash": "kg/mimo-v2.6-flash",
    "kg/hy3": "kg/hy3",
    "hy3": "kg/hy3",
    "hunyuan 3": "kg/hy3",
    "kg/hy4-preview": "kg/hy4-preview",
    "hy4-preview": "kg/hy4-preview",
    "hunyuan 4": "kg/hy4-preview",
    "bansos/deepseek-v4.1-flash-1017": "bansos/deepseek-v4.1-flash-1017",
    "bansos": "bansos/deepseek-v4.1-flash-1017",
    "deepseek bansos": "bansos/deepseek-v4.1-flash-1017",
    "bansos deepseek": "bansos/deepseek-v4.1-flash-1017",
}

DEFAULT_MODEL = "ag/gemini-3.8-flash-high"


# ─── REST Endpoints ──────────────────────────────────────────────────

@app.get("/api/models")
async def list_models() -> dict[str, Any]:
    """List models allowed for the UI."""
    orch = get_orchestrator()
    models = orch.registry.get_all()

    # Filter strictly to allowed models
    filtered = [m for m in models if m.id in ALLOWED_UI_MODELS]

    # Group by provider
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for m in filtered:
        provider = m.provider or "unknown"
        if provider not in by_provider:
            by_provider[provider] = []
        by_provider[provider].append({
            "id": m.id,
            "display_name": ALLOWED_UI_MODELS.get(m.id, m.display_name),
            "provider": m.provider,
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
        })

    return {
        "models": by_provider,
        "total": len(filtered),
        "providers": list(by_provider.keys()),
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
    model = _override_models.get(session_id) or meta.get("model_used") or DEFAULT_MODEL
    return {
        "session_id": session_id,
        "title": meta.get("title", "Chat"),
        "created_at": meta.get("created_at"),
        "updated_at": meta.get("updated_at"),
        "model_used": model,
        "model_display": ALLOWED_UI_MODELS.get(model, model),
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp,
                "model_used": m.model_used,
                "token_count": m.token_count,
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
    return {"status": "ok", "session_id": session_id, "deleted": True}


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
    raw_model_id = payload.get("model_id")

    if raw_model_id:
        normalized_id = MODEL_ALIASES.get(raw_model_id, raw_model_id)
        if normalized_id not in ALLOWED_UI_MODELS:
            raise HTTPException(
                status_code=400,
                detail=f"Model '{raw_model_id}' is not permitted. Allowed models: {list(ALLOWED_UI_MODELS.keys())}",
            )
        orch = get_orchestrator()
        model = orch.registry.get(normalized_id)
        if not model:
            raise HTTPException(status_code=404, detail=f"Model '{normalized_id}' not found in registry")
        _override_models[session_id] = normalized_id
        display_name = ALLOWED_UI_MODELS.get(normalized_id, model.display_name)
        return {"status": "ok", "model_id": normalized_id, "display_name": display_name}
    else:
        # Default back to DEFAULT_MODEL
        _override_models[session_id] = DEFAULT_MODEL
        return {"status": "ok", "model_id": DEFAULT_MODEL, "display_name": ALLOWED_UI_MODELS[DEFAULT_MODEL]}


@app.get("/api/session/new")
async def new_session() -> dict[str, Any]:
    """Create a new session with DEFAULT_MODEL pre-assigned."""
    orch = get_orchestrator()
    session = orch.session_manager.create_session()
    _override_models[session.session_id] = DEFAULT_MODEL
    return {"session_id": session.session_id, "default_model": DEFAULT_MODEL}


# ─── WebSocket Chat ──────────────────────────────────────────────────

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
            data = json.loads(raw)

            message = data.get("message", "").strip()
            session_id = data.get("session_id", generate_session_id())

            if not message:
                await websocket.send_json({"type": "error", "content": "Empty message"})
                continue

            orch = get_orchestrator()
            override = _override_models.get(session_id, DEFAULT_MODEL)
            if override not in ALLOWED_UI_MODELS:
                override = DEFAULT_MODEL

            # Send classification info first
            classification, difficulty = orch.classifier.classify_with_difficulty(message)
            await websocket.send_json({
                "type": "meta",
                "event": "classified",
                "task_type": classification.task_type.value,
                "difficulty": difficulty,
                "confidence": round(classification.confidence, 2),
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

            def on_thinking(
                chunk: str,
                buf: list[str] = _thinking_buf,
                q: asyncio.Queue[dict[str, Any] | None] = queue,
            ) -> None:
                buf.append(chunk)
                q.put_nowait({"type": "thinking", "content": chunk})

            start_time = time.time()

            try:
                response = await orch.process(
                    user_input=message,
                    session_id=session_id,
                    override_model=override,
                    stream=True,
                    on_content_chunk=on_content,
                    on_thinking_chunk=on_thinking,
                )

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
                })
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
        import contextlib
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
