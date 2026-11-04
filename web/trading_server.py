"""The trading backend on its own: the /trading panel, the MT5 and quant APIs, and the MT4 EA bridge.

It runs apart from the main web server because MT4's WebRequest list takes a plain
``http://127.0.0.1`` and so reaches port 80, while the agent UI stays on its own port. Nothing
here loads the agent, so restarting the agent does not drop the EA.

The EA's prices and commands live in this process's memory. Open the panel on this server's
address: the /trading page on the main web server has its own empty bridge state.

The Host and Origin checks are the same as the main server's. Only the rate limit differs,
because one EA alone sends about 390 requests a minute.
"""

from __future__ import annotations

import errno
import os
import socket
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from config.settings import get_settings
from security.headers import SecurityHeadersMiddleware
from security.web_access import WebSecurityMiddleware, is_loopback_host, set_public_mode
from web.mt4_bridge import router as mt4_router
from web.mt5_api import router as mt5_router
from web.quant_api import router as quant_router

DEFAULT_PORT = 80
RATE_LIMIT_RPM = 6000
STATIC_DIR = Path(__file__).parent / "static"
PORT_HINT = (
    "Port {port} needs permission. Run this once (it lasts until reboot), then start again:\n"
    "  sudo sysctl -w net.ipv4.ip_unprivileged_port_start={port}"
)

app = FastAPI(title="SquidWings Trading", version="1.0.0")
app.include_router(mt5_router)
app.include_router(mt4_router)
app.include_router(quant_router)
# The same bridge at the root, so the EA's BridgeUrl can be a bare http://127.0.0.1. MT4 rejects
# a URL with a port as invalid (error 5200), and this keeps its address as short as possible.
for _route in mt4_router.routes:
    app.add_api_route(
        _route.path.removeprefix(mt4_router.prefix),
        _route.endpoint,
        methods=list(_route.methods),
        response_class=_route.response_class,
        include_in_schema=False,
    )


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


_settings = get_settings()
app.add_middleware(
    WebSecurityMiddleware,
    max_users=_settings.web_max_users,
    allowed_origins=_csv(_settings.web_allowed_origins),
    allowed_hosts=_csv(_settings.web_allowed_hosts),
    requests_per_minute=int(os.getenv("TRADING_RATE_LIMIT_RPM", "") or RATE_LIMIT_RPM),
)
app.add_middleware(SecurityHeadersMiddleware)


@app.get("/", response_class=HTMLResponse)
@app.get("/trading", response_class=HTMLResponse)
async def serve_trading() -> HTMLResponse:
    page = STATIC_DIR / "trading" / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="trading page not found")
    return HTMLResponse(content=page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def port_problem(host: str, port: int) -> str | None:
    """Why this address cannot be used, in words a user can act on. None when it is free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except PermissionError:
            return PORT_HINT.format(port=port)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                return f"Port {port} is already in use. Stop whatever is listening there first."
            return f"Cannot use {host}:{port}: {exc}"
    return None


def run(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> int:
    """Start the server. Returns a process exit code."""
    import uvicorn

    load_dotenv(_settings.project_root / ".env")
    problem = port_problem(host, port)
    if problem:
        print(problem)
        return 1
    if not is_loopback_host(host):
        set_public_mode(True)      # the trading guard then demands MT5_TRADE_TOKEN
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


__all__ = ["app", "run", "port_problem"]
