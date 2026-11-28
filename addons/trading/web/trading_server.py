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
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles

from addons.trading.web.mt4_bridge import router as mt4_router
from addons.trading.web.mt4_bridge import store
from addons.trading.web.mt5_api import router as mt5_router
from addons.trading.web.quant_api import router as quant_router
from brain.config.paths import log_file
from brain.config.settings import get_settings
from brain.security.headers import LOOPBACK_FRAMES, SecurityHeadersMiddleware, embeddable_headers
from brain.security.web_access import WebSecurityMiddleware, is_loopback_host, set_public_mode
from brain.utils.platform_compat import IS_LINUX, IS_WINDOWS, find_port_hint, stop_hint

DEFAULT_PORT = 80
RATE_LIMIT_RPM = 6000
STATIC_DIR = Path(__file__).resolve().parents[3] / "web" / "static"   # shared CSS, JS, fonts
PAGE_DIR = Path(__file__).resolve().parent / "static"                    # this add-on's own page
PORT_HINT = (
    "Port {port} needs permission. Run this once (it lasts until reboot), then start again:\n"
    "  sudo sysctl -w net.ipv4.ip_unprivileged_port_start={port}"
)

app = FastAPI(title="SquidWings Trading", version="1.0.0")
app.include_router(mt5_router)
app.include_router(mt4_router)
app.include_router(quant_router)


@app.on_event("startup")
def _start_m5_collector() -> None:
    """Merge the EA's new bars and MT4's history files into the local M5 store every 15 minutes, market open or not."""
    from addons.trading.quant import m5store

    m5store.start_collector(lambda: list(store.ticks) or ["XAUUSD"])
# The same bridge at the root, so the EA's BridgeUrl can be a bare http://127.0.0.1. MT4 rejects
# a URL with a port as invalid (error 5200), and this keeps its address as short as possible.
for _route in mt4_router.routes:
    if not isinstance(_route, APIRoute):
        continue
    app.add_api_route(
        _route.path.removeprefix(mt4_router.prefix),
        _route.endpoint,
        methods=list(_route.methods or ()),
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
    page = PAGE_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="trading page not found")
    # The chat page on another loopback port shows this panel inside itself.
    headers = {"Cache-Control": "no-cache, no-store, must-revalidate", **embeddable_headers(LOOPBACK_FRAMES)}
    return HTMLResponse(content=page.read_text(encoding="utf-8"), headers=headers)


app.mount("/static/trading", StaticFiles(directory=str(PAGE_DIR)), name="static-trading")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def port_problem(host: str, port: int) -> str | None:
    """Why this address cannot be used, in words a user can act on. None when it is free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if not IS_WINDOWS:   # on Windows it would let this probe bind a port another program holds
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except PermissionError:
            if IS_WINDOWS:
                # Windows needs no administrator for any port: refused means another program holds it for itself,
                # or Windows reserved it (Hyper-V, WSL).
                return (f"Port {port} dipakai program lain atau dicadangkan Windows. Cek dengan: netstat -ano | "
                        f"findstr :{port} dan netsh interface ipv4 show excludedportrange protocol=tcp. "
                        f"Atau pakai --port lain dan ubah BridgeUrl EA.")
            if not IS_LINUX:
                return (f"Port {port} butuh izin administrator di sistem ini. Jalankan terminal sebagai "
                        f"Administrator/root, atau pakai --port lain dan ubah BridgeUrl EA.")
            return PORT_HINT.format(port=port)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                return f"Port {port} is already in use. Stop whatever is listening there first."
            return f"Cannot use {host}:{port}: {exc}"
    return None


# ── making the port usable ──────────────────────────────────────────────────
# Starting again used to fail with "Port 80 is already in use" while an older copy of this very
# server kept running the code from hours before. Now the older copy is stopped and replaced,
# and a port Linux does not allow yet is opened once with sudo, in the terminal.
SYSCTL_FILE = "/etc/sysctl.d/99-squidwings.conf"


def is_our_trading_server(command: str) -> bool:
    """A process started as ``python main.py trading`` (this app's trading server)."""
    words = command.split()
    return any(w.endswith("main.py") for w in words) and "trading" in words


def code_is_stale(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> bool | None:
    """Whether the trading server on ``port`` runs older code than the files on disk. None: it did not answer."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/quant/server", timeout=2) as resp:  # noqa: S310 - loopback
            return bool(json.loads(resp.read()).get("stale"))
    except urllib.error.HTTPError as exc:
        return True if exc.code == 404 else None          # no such endpoint: a server from before it existed
    except (OSError, ValueError):
        return None


def open_low_port(port: int, say: Callable[[str], None] = print) -> bool:
    """Let normal programs use ports from ``port`` up, once and for good, through sudo in this terminal."""
    if not IS_LINUX or not sys.stdin.isatty() or shutil.which("sudo") is None:
        return False   # the sysctl exists only on Linux; Windows and recent macOS allow low ports already
    setting = f"net.ipv4.ip_unprivileged_port_start={port}"
    say(f"Port {port} perlu izin Linux sekali saja. Meminta sudo untuk: sysctl -w {setting} (disimpan di {SYSCTL_FILE})")
    try:
        done = subprocess.run(["sudo", "sh", "-c", f"sysctl -w {setting} && echo {setting} > {SYSCTL_FILE}"], check=False)
    except OSError:
        return False
    return done.returncode == 0


def prepare_port(host: str, port: int, *, say: Callable[[str], None] = print, interactive: bool | None = None) -> str | None:
    """Make ``host:port`` usable. None when it is; otherwise why not, in words a user can act on.

    Busy with an older copy of this trading server: that copy is stopped (its virtual runs end)
    and the port is taken. Not allowed yet: sudo is asked once, when there is a terminal to ask in.
    """
    from brain.utils import ports

    problem = port_problem(host, port)
    if problem is None:
        return None
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if "already in use" in problem:
        holder = ports.holder(host, port)
        if holder.pid and is_our_trading_server(holder.command):
            say(f"Server trading lama (PID {holder.pid}) masih memegang port {port}: dihentikan dan diganti dengan kode terbaru.")
            if ports.stop(holder.pid, port, host):
                return None
            return f"Server trading lama (PID {holder.pid}) belum berhenti. Hentikan sendiri: {stop_hint(holder.pid)}"
        who = holder.describe() if holder.pid or holder.command else "program lain"
        return (f"Port {port} dipakai {who}, bukan server trading ini. "
                f"Hentikan dulu{f' ({stop_hint(holder.pid)})' if holder.pid else f' (cek: {find_port_hint(port)})'}.")
    if interactive and port < 1024 and open_low_port(port, say):
        return port_problem(host, port)
    return problem


TUNNEL_LOG = "tunnel-trading.log"


def run(host: str = "127.0.0.1", port: int = DEFAULT_PORT, tunnel: bool = False) -> int:
    """Start the server. Returns a process exit code.

    ``tunnel`` opens this server (the one holding the EA's data) through a Cloudflare quick
    tunnel, so the panel can be watched from a phone. Watching needs no token; sending orders
    from the tunnel follows the guard in addons.trading.web.mt5_api.
    """
    import uvicorn

    load_dotenv(_settings.project_root / ".env")
    problem = prepare_port(host, port)
    if problem:
        print(problem)
        return 1
    if tunnel or not is_loopback_host(host):
        set_public_mode(True)      # any Host is accepted; the guard tells tunnel visitors from this machine
    public = None
    if tunnel:
        from brain.utils.tunnel import CloudflareTunnel
        public = CloudflareTunnel(port=port)
        public._log_file = log_file(TUNNEL_LOG)
        url = public.start()
        print(f"Trading panel publik: {url}/trading" if url else f"Tunnel berjalan, tapi URL belum muncul. Cek {TUNNEL_LOG}.")
        if os.getenv("TRADING_REMOTE_NO_TOKEN", "0").strip() == "1":
            print("PERINGATAN: TRADING_REMOTE_NO_TOKEN=1, siapa pun yang punya URL ini bisa kirim order.")
        elif os.getenv("MT5_TRADE_TOKEN", "").strip():
            print("Lihat dari luar: tanpa token. Kirim order dari luar: pakai MT5_TRADE_TOKEN.")
        else:
            print("Lihat dari luar: tanpa token. Kirim order dari luar: mati (set MT5_TRADE_TOKEN atau TRADING_REMOTE_NO_TOKEN=1).")
    store.restore()
    try:
        # No access log: the EA makes several requests a second, and each line would cost time.
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        if public:
            public.stop()
    return 0


__all__ = ["app", "run", "port_problem", "prepare_port", "code_is_stale", "is_our_trading_server"]
