"""Links to what the agent builds and runs: a Laravel app, a Flask/Django server, a static site.

A dev server listens on this machine (``php artisan serve`` on 8000, ``npm run dev`` on 5173,
``python -m http.server`` on 8090). On this machine the link is simply ``http://127.0.0.1:<port>``.
From far away (the agent page opened through a Cloudflare tunnel) that address means nothing,
so each previewed port gets its own Cloudflare quick tunnel: ``https://<words>.trycloudflare.com``
serves the whole app at its root, so absolute paths (/css/app.css, /build/assets) keep working,
which a sub-path proxy would break.

Tunnels live until the server stops, ``close`` is called, or the dev server goes away.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from brain.utils.logger import get_logger
from brain.utils.tunnel import CloudflareTunnel

logger = get_logger(__name__)

_MAX_PREVIEWS = 6
# A new trycloudflare name takes a little while to resolve everywhere; opened sooner, the browser
# shows "site not found" and may remember that for a minute.
_WARMUP_SECONDS = 25.0
_RESERVED_PORTS = {22}


@dataclass
class Preview:
    port: int
    tunnel: CloudflareTunnel
    started: float

    @property
    def url(self) -> str:
        return self.tunnel.public_url or ""

    @property
    def alive(self) -> bool:
        return bool(self.tunnel.process and self.tunnel.process.poll() is None and self.url)

    @property
    def ready_in(self) -> int:
        """Seconds until the new link is likely to resolve, 0 once it should."""
        return max(0, int(self.started + _WARMUP_SECONDS - time.monotonic()))


_previews: dict[int, Preview] = {}
_lock = asyncio.Lock()


def listening(port: int, host: str = "127.0.0.1") -> bool:
    """Whether something answers on this port."""
    with contextlib.suppress(OSError), socket.create_connection((host, port), timeout=0.6):
        return True
    return False


def local_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _check(port: int) -> int:
    port = int(port)
    if not 1 <= port <= 65535 or port in _RESERVED_PORTS:
        raise ValueError(f"port {port} cannot be previewed")
    return port


def ready_in(port: int) -> int:
    preview = _previews.get(int(port))
    return preview.ready_in if preview else 0


async def open_public(port: int) -> str:
    """A public https link to the server on this port (reused while it lives). See ``ready_in``."""
    port = _check(port)
    async with _lock:
        current = _previews.get(port)
        if current and current.alive:
            return current.url
        if current:
            current.tunnel.stop()
        while len(_previews) >= _MAX_PREVIEWS:
            oldest = min(_previews.values(), key=lambda p: p.started)
            close(oldest.port)
        log = Path(tempfile.gettempdir()) / f"squidwings-preview-{port}.log"
        tunnel = CloudflareTunnel(port, log_file=log, primary=False)
        url = await asyncio.to_thread(tunnel.start, 25.0)
        if not url:
            tunnel.stop()
            raise RuntimeError(tunnel.last_error or "cloudflared did not give a link (is the machine online?)")
        _previews[port] = Preview(port, tunnel, time.monotonic())
        logger.info(f"[preview] port {port} -> {url}")
        return url


def close(port: int) -> bool:
    preview = _previews.pop(int(port), None)
    if preview is None:
        return False
    preview.tunnel.stop()
    return True


def close_all() -> None:
    for port in list(_previews):
        close(port)


def active() -> list[dict[str, object]]:
    """Previews still serving, for the page and the agent."""
    out = []
    for port, preview in list(_previews.items()):
        if not preview.alive or not listening(port):
            close(port)
            continue
        out.append({"port": port, "url": preview.url, "local": local_url(port)})
    return out


__all__ = ["active", "close", "close_all", "listening", "local_url", "open_public", "ready_in"]
