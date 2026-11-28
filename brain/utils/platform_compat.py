"""What differs between Windows, macOS and Linux, in one place.

The rest of the code asks here instead of calling POSIX-only functions (``os.killpg``,
``preexec_fn``, ``termios``) directly, so the harness starts and runs on all three. Where a
feature has no equivalent (the bubblewrap sandbox, ``sudo``), the caller checks ``IS_WINDOWS``
and says so instead of crashing.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from typing import Any

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def os_name() -> str:
    return "windows" if IS_WINDOWS else "macos" if IS_MAC else "linux"


def new_group_kwargs() -> dict[str, Any]:
    """Popen arguments that start the child in its own process group, so ``kill_tree`` reaches its children."""
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def low_priority_kwargs(niceness: int = 10) -> dict[str, Any]:
    """Popen arguments that run the child below normal priority (``nice`` on POSIX)."""
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)}

    def _nice() -> None:
        os.nice(niceness)

    return {"preexec_fn": _nice}


def lower_own_priority(target: int = 19) -> None:
    """Lower this process's own priority as far as ``target`` (POSIX niceness). Best effort."""
    with contextlib.suppress(OSError, AttributeError):
        if IS_WINDOWS:
            import ctypes

            idle = 0x00000040   # IDLE_PRIORITY_CLASS
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), idle)  # type: ignore[attr-defined]
            return
        os.nice(max(0, target - os.nice(0)))


def kill_tree(pid: int, hangup: bool = False) -> None:
    """Stop a process and everything it started. ``hangup`` sends SIGHUP instead of SIGKILL on POSIX
    (a shell closing its terminal). Never raises."""
    if pid <= 0:
        return
    if IS_WINDOWS:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=15, check=False)
        return
    sig = signal.SIGHUP if hangup else signal.SIGKILL
    try:
        os.killpg(os.getpgid(pid), sig)
    except (OSError, ProcessLookupError):
        with contextlib.suppress(OSError, ProcessLookupError):
            os.kill(pid, sig)


def preexec_or_none(fn: Callable[[], None] | None) -> Callable[[], None] | None:
    """``preexec_fn`` is refused on Windows; there it is dropped."""
    return None if IS_WINDOWS else fn


def flush_stdin() -> None:
    """Drop keys typed while the answer was streaming, so they do not become the next message."""
    if not sys.stdin.isatty():
        return
    if IS_WINDOWS:
        with contextlib.suppress(Exception):
            import msvcrt

            while msvcrt.kbhit():  # type: ignore[attr-defined]
                msvcrt.getwch()  # type: ignore[attr-defined]
        return
    with contextlib.suppress(Exception):
        import termios

        termios.tcflush(sys.stdin, termios.TCIFLUSH)


def stdin_ready(timeout: float) -> bool:
    """True when more input is already waiting (a multi-line paste). Always False on Windows,
    where ``select`` does not work on the console; a paste there arrives line by line instead."""
    if IS_WINDOWS or not sys.stdin.isatty():
        return False
    import select

    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except (OSError, ValueError):
        return False
    return bool(ready)


def find_port_hint(port: int) -> str:
    """The command that shows who listens on ``port``, for this OS."""
    if IS_WINDOWS:
        return f"netstat -ano | findstr :{port}"
    if IS_MAC:
        return f"lsof -nP -iTCP:{port} -sTCP:LISTEN"
    return f"ss -ltnp | grep :{port}"


def stop_hint(pid: int) -> str:
    return f"taskkill /PID {pid} /F" if IS_WINDOWS else f"kill {pid}"


__all__ = ["find_port_hint", "stop_hint", "IS_LINUX", "IS_MAC", "IS_WINDOWS", "flush_stdin", "kill_tree", "low_priority_kwargs",
           "lower_own_priority", "new_group_kwargs", "os_name", "preexec_or_none", "stdin_ready"]


class ProcessLock:
    """An exclusive lock on a file that other processes see (only one may hold it), released when this process
    ends. POSIX uses ``fcntl.flock``; Windows locks the first byte with ``msvcrt``."""

    def __init__(self, path: Any) -> None:
        self.path = path
        self._handle: Any = None

    def acquire(self) -> bool:
        """True when this process now holds the lock (or already did); False when another process holds it."""
        if self._handle is not None:
            return True
        from pathlib import Path

        target = Path(self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = open(target, "a+")  # noqa: SIM115 - kept open for as long as the lock is held
        try:
            if IS_WINDOWS:
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        with contextlib.suppress(OSError):
            if IS_WINDOWS:
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None
