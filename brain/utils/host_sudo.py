"""Run an agent command that needs ``sudo`` on this machine, without asking the user every time.

The owner's rule (2026-11-22): framework work (Laravel, PHP extensions, MySQL, nginx, apt) needs sudo often, and
the agent must not stop for it. When the sudo password is saved (Tim → Kredensial → Sistem, or ``SUDO_PASSWORD``
in ``.env``) or this machine allows ``sudo -n``, such a command runs here directly:

* The password goes to ``sudo -S -v`` on file descriptor 3, which is closed before the command itself starts,
  so the command (and so the model) cannot read it back; it is never in an argument, an environment variable,
  the audit log or a tool result.
* sudo then remembers the authentication for the rest of the same shell, so ``a && sudo b && sudo c`` works.
* API keys and tokens from this server's environment are not passed on (the sandbox's clean environment).
* The output is returned whole; only the provider's context window limits what the model reads.

Without a saved password the command still goes to the user's Terminal panel, as before.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import subprocess
import time
from typing import Any

from brain.utils.logger import get_logger
from brain.utils.platform_compat import kill_tree

logger = get_logger(__name__)

SUDO_RE = re.compile(r"(^|[;&|(]\s*|\s)sudo\s")
_MAX_OUTPUT = 4_000_000
_checked: dict[str, Any] = {"at": 0.0, "nopass": False}


def password() -> str:
    try:
        from brain.config import credentials

        return credentials.get("sudo_password")
    except Exception:  # noqa: BLE001 - no credential store: no password
        return os.getenv("SUDO_PASSWORD", "")


def nopass() -> bool:
    """Whether ``sudo -n`` works here (a NOPASSWD rule), checked at most once a minute."""
    if time.time() - _checked["at"] > 60:
        try:
            _checked["nopass"] = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0
        except (OSError, subprocess.SubprocessError):
            _checked["nopass"] = False
        _checked["at"] = time.time()
    return bool(_checked["nopass"])


def ready() -> bool:
    return bool(password()) or nopass()


def _env() -> dict[str, str]:
    try:
        from brain.security.sandbox import _clean_env

        env = _clean_env(os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), os.environ.get("HOME", "/tmp"))
    except Exception:  # noqa: BLE001
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C.UTF-8"}
    env["DEBIAN_FRONTEND"] = "noninteractive"     # apt never stops to ask
    return env


async def run(command: str, cwd: str = ".", timeout: int = 900) -> dict[str, Any]:
    """Run ``command`` (which uses sudo) in bash on this machine, authenticated once at the start."""
    secret = password()
    prime = 'sudo -S -p "" -v <&3 >/dev/null 2>&1; exec 3<&-; ' if secret else ""
    script = prime + command
    read_fd = write_fd = -1
    pass_fds: tuple[int, ...] = ()
    if secret:
        read_fd, write_fd = os.pipe()
        pass_fds = (read_fd,)
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", f"exec 3<&{read_fd}; {script}" if secret else script,
            cwd=cwd if os.path.isdir(cwd) else None, env=_env(), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, pass_fds=pass_fds, start_new_session=True)
    finally:
        if secret:
            os.close(read_fd)
            with contextlib.suppress(OSError):
                os.write(write_fd, (secret + "\n").encode())
            os.close(write_fd)
    timed_out = False
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        kill_tree(proc.pid)
        out, err = await proc.communicate()
    stdout = out.decode("utf-8", "replace")[:_MAX_OUTPUT]
    stderr = err.decode("utf-8", "replace")[:_MAX_OUTPUT]
    if secret:                                   # belt and braces: the password never reaches the model
        stdout, stderr = stdout.replace(secret, "••••"), stderr.replace(secret, "••••")
    logger.info(f"[sudo] rc={proc.returncode} in {time.monotonic() - started:.1f}s: {command[:120]}")
    return {"success": proc.returncode == 0 and not timed_out, "command": command, "stdout": stdout, "stderr": stderr,
            "return_code": proc.returncode, "duration": round(time.monotonic() - started, 3), "timed_out": timed_out,
            "cwd": cwd, "note": "Ran on this machine with sudo (password from Kredensial, never shown)."}
