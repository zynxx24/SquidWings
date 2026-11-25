"""Busy ports: is a port free, who holds it, and which port to use instead.

The web server checks its port before it starts, so a second ``python main.py web`` does
not die with ``[Errno 98] address already in use`` after loading everything.
"""

from __future__ import annotations

import contextlib
import errno
import os
import signal
import socket
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

MAX_TRIES = 20


@dataclass(frozen=True)
class Holder:
    """The process listening on a port, as far as this user can see it."""

    pid: int | None
    command: str
    squidwings: bool       # it answers like this app's web server

    def describe(self) -> str:
        what = "SquidWings" if self.squidwings else (self.command.split(" ")[0].rsplit("/", 1)[-1] or "program lain")
        return f"{what} (PID {self.pid})" if self.pid else what


def port_free(host: str, port: int) -> bool:
    """True when a server could bind ``host:port`` now."""
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as probe:
        # The same option uvicorn uses, so a port in TIME_WAIT counts as free, as it does for uvicorn.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, errno.EACCES) or isinstance(exc, PermissionError):
                return False
            raise
    return True


def next_free_port(host: str, start: int, tries: int = MAX_TRIES) -> int | None:
    """The first free port after ``start``, or None when the next ``tries`` are all taken."""
    for port in range(start + 1, min(start + 1 + tries, 65536)):
        if port_free(host, port):
            return port
    return None


def _listening_inodes(port: int) -> set[str]:
    inodes: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        with contextlib.suppress(OSError):
            for line in Path(table).read_text().splitlines()[1:]:
                parts = line.split()
                # local address is HEXIP:HEXPORT; state 0A is LISTEN
                if len(parts) > 9 and parts[3] == "0A" and int(parts[1].rsplit(":", 1)[1], 16) == port:
                    inodes.add(parts[9])
    return inodes


def _pid_for(port: int) -> int | None:
    """The PID listening on ``port`` (Linux, own processes only). None when unknown."""
    inodes = _listening_inodes(port)
    if not inodes:
        return None
    wanted = {f"socket:[{inode}]" for inode in inodes}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        with contextlib.suppress(OSError):
            for fd in (proc / "fd").iterdir():
                with contextlib.suppress(OSError):
                    if os.readlink(fd) in wanted:
                        return int(proc.name)
    return None


def _command(pid: int | None) -> str:
    if not pid:
        return ""
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        return ""


def _answers_like_squidwings(host: str, port: int) -> bool:
    target = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    url = f"http://{'[' + target + ']' if ':' in target else target}:{port}/"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:  # noqa: S310 - loopback, fixed scheme
            page = resp.read(20000).decode("utf-8", errors="replace")
    except Exception:
        return False
    return "SquidWings" in page and "/static/js/app.js" in page


def holder(host: str, port: int) -> Holder:
    pid = _pid_for(port)
    command = _command(pid)
    ours = _answers_like_squidwings(host, port) or ("main.py" in command and " web" in command)
    return Holder(pid, command, ours)


def stop(pid: int, port: int, host: str, wait_s: float = 8.0) -> bool:
    """Ask ``pid`` to stop (SIGTERM), then wait for the port to be free. True when it is."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        return False
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if port_free(host, port):
            return True
        time.sleep(0.2)
    return port_free(host, port)
