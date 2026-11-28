"""Pages a Telegram button opens: the full content behind a short message, in Telegram's in-app window.

A page is saved once, under a random token, and read by anyone holding the link; nothing else can be reached
through it. Telegram needs a public https address for such a window, found in this order:

1. ``telegram_web_url`` in Kredensial (the owner's own domain or tunnel pointing at the web server),
2. the tunnel of the web server itself (``main.py web --tunnel``),
3. otherwise a small server of its own that serves only these pages, behind its own Cloudflare quick tunnel,
   started the first time a page needs it. The rest of the app is never put on the internet for this.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import socket
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from brain.agent.telegram_format import page_html
from brain.config import credentials
from brain.utils.logger import get_logger

logger = get_logger(__name__)

KEEP_DAYS = 60
PRUNE_EVERY_SECONDS = 3600
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")


class ViewStore:
    """One JSON file per page. Pages older than ``KEEP_DAYS`` are removed now and then."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._pruned = 0.0

    def save(self, title: str, subtitle: str, text: str) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(18)
        path = self.directory / f"{token}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"title": title, "subtitle": subtitle, "text": text, "created_at": time.time()},
                                        ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
        self.prune()
        return token

    def get(self, token: str) -> dict[str, Any] | None:
        if not _TOKEN_RE.fullmatch(token or ""):
            return None
        try:
            data = json.loads((self.directory / f"{token}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def render(self, token: str) -> str | None:
        page = self.get(token)
        if page is None:
            return None
        return page_html(str(page.get("title") or "Hasil"), str(page.get("subtitle") or ""), str(page.get("text") or ""))

    def prune(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        if now - self._pruned < PRUNE_EVERY_SECONDS:
            return 0
        self._pruned = now
        gone = 0
        for path in self.directory.glob("*.json"):
            with contextlib.suppress(OSError):
                if now - path.stat().st_mtime > KEEP_DAYS * 86400:
                    path.unlink()
                    gone += 1
        return gone


def subtitle_for(team_name: str, when: float | None = None) -> str:
    moment = datetime.fromtimestamp(when or time.time())
    return f"{team_name} · {moment.strftime('%d-%m-%Y %H:%M')}"


def make_app(store: ViewStore) -> Any:
    """The pages, and nothing else."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/v/{token}", response_class=HTMLResponse)
    async def page(token: str) -> HTMLResponse:
        body = store.render(token)
        if body is None:
            return HTMLResponse("<!doctype html><meta charset=utf-8><p>Halaman ini tidak ada atau sudah kedaluwarsa.</p>", status_code=404)
        return HTMLResponse(body, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"})

    @app.get("/a/{token}/{path:path}")
    async def html_app(token: str, path: str = "") -> Any:
        from brain.utils import app_links

        return app_links.response(token, path)

    return app


class _QuietServer:
    """uvicorn inside our event loop, without taking over Ctrl+C from the main web server."""

    def __init__(self, app: Any, port: int) -> None:
        import uvicorn

        class Server(uvicorn.Server):
            @contextlib.contextmanager
            def capture_signals(self) -> Iterator[None]:
                yield

        self.server = Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> bool:
        self.task = asyncio.create_task(self.server.serve(), name="telegram-pages")
        for _ in range(100):
            if self.server.started:
                return True
            if self.task.done():
                return False
            await asyncio.sleep(0.05)
        return False


class PublicPages:
    """Where a page is reachable from the internet, and the means to make it so."""

    def __init__(self, store: ViewStore) -> None:
        self.store = store
        self._lock = asyncio.Lock()
        self._server: _QuietServer | None = None
        self._tunnel: Any = None

    def configured(self) -> str:
        """An address that needs nothing started: the owner's own, or the web server's tunnel."""
        own = credentials.get("telegram_web_url").strip().rstrip("/")
        if own:
            return own
        from brain.utils.tunnel import get_public_url

        return (get_public_url() or "").rstrip("/")

    def status(self) -> dict[str, str]:
        if credentials.get("telegram_web_url").strip():
            return {"mode": "own", "url": self.configured()}
        if self.configured():
            return {"mode": "tunnel", "url": self.configured()}
        alive = self._tunnel is not None and self._tunnel.process is not None and self._tunnel.process.poll() is None
        return {"mode": "auto", "url": self._tunnel.public_url if alive and self._tunnel.public_url else ""}

    async def base_url(self) -> str:
        """An https address that serves the pages. Raises RuntimeError with a reason when there is none."""
        found = self.configured()
        if found:
            return found
        async with self._lock:
            tunnel = self._tunnel
            if tunnel is not None and tunnel.process is not None and tunnel.process.poll() is None and tunnel.public_url:
                return str(tunnel.public_url).rstrip("/")
            if self._server is None or self._server.task is None or self._server.task.done():
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                self._server = _QuietServer(make_app(self.store), port)
                if not await self._server.start():
                    raise RuntimeError("Server halaman Telegram tidak mau menyala.")
            from brain.config.paths import log_file
            from brain.utils.tunnel import CloudflareTunnel

            port = self._server.server.config.port
            self._tunnel = CloudflareTunnel(port, log_file=log_file("tunnel-telegram.log"), primary=False)
            url = await asyncio.to_thread(self._tunnel.start)
            if not url:
                raise RuntimeError("Tidak bisa membuat alamat publik (cloudflared). Isi Alamat web publik di Kredensial > Telegram.")
            logger.info("[teams] Telegram pages are public at %s", url)
            return str(url).rstrip("/")

    async def publish_app(self, html: Path) -> str:
        """Freeze an HTML file and its folder; return the public link that opens it (a Telegram Mini App)."""
        from brain.utils import app_links

        token = await asyncio.to_thread(app_links.publish, html)
        return f"{await self.base_url()}{app_links.path_for(token)}"

    async def publish(self, title: str, subtitle: str, text: str) -> str:
        """Save a page and return its public link."""
        token = self.store.save(title, subtitle, text)
        return f"{await self.base_url()}/v/{token}"

    def stop(self) -> None:
        if self._tunnel is not None:
            self._tunnel.stop()
        if self._server is not None:
            self._server.server.should_exit = True
