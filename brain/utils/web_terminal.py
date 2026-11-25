"""A real shell per chat, shown in the web page, that the agent can type into.

The sandbox keeps ``execute_command`` from touching the system, so it can never run
``sudo``: there is no password inside it and no way to type one. Some work needs the
system (``apt install php-xml``, starting MySQL, ``php artisan serve`` on a port the user
opens in the browser). This module gives each chat one bash on this machine, in the chat's
folder, that both the user and the agent use:

* The user sees it in the Terminal panel and types into it, a sudo password included.
  The password is typed into the terminal, never into the chat, so the model never sees it
  (the shell turns echo off while it asks).
* The agent sends a command with ``terminal_run``. It appears in the panel as if typed, and
  the tool waits until the shell is back at its prompt, then returns the output.

The wrapping that stays when the sandbox is not used:

* Only a browser on this machine can open the panel; a tunnel or another computer cannot,
  unless ``WEB_TERMINAL_PUBLIC=true`` says otherwise.
* Commands that would wreck the machine (``rm -rf /``, ``mkfs``, ``dd`` onto a disk, a fork
  bomb, shutdown) are refused from the agent.
* Every agent command is written to the audit log.
* Secrets the server loaded (API keys, tokens, passwords in the environment) are not passed
  to the shell, so ``env`` cannot leak them into the chat.

A shell finishing a command is recognised by an invisible marker its prompt prints
(``ESC ] 777 ; sw-done ; <exit code> BEL``); xterm.js ignores it, the tool strips it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.utils.logger import get_logger
from brain.utils.platform_compat import IS_WINDOWS, kill_tree

if not IS_WINDOWS:   # Windows has no pty: the terminal there runs PowerShell through pywinpty (ConPTY)
    import fcntl
    import pty
    import termios

logger = get_logger(__name__)

_BUFFER_BYTES = 256 * 1024
_MAX_TERMINALS = 8
_IDLE_SECONDS = 6 * 3600
_MAX_RESULT_CHARS = 900_000          # the whole output; only the model's own context window limits it
# Extra time a command gets once the shell asks for a password.
_PASSWORD_WAIT = 90.0
_DONE_RE = re.compile(rb"\x1b\]777;sw-done;(\d+)\x07")
_ANSI_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
_PASSWORD_RE = re.compile(r"(\[sudo\] password for [^:]*:|[Pp]assword( for [^:]*)?:|[Kk]ata sandi[^:]*:)\s*$")
_SECRET_NAME_RE = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|SESSION", re.IGNORECASE)
_FORBIDDEN: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+(-[a-zA-Z]*\s+)*(/|/\*|~|~/|\$HOME)(\s|$)", "deleting / or the home folder"),
    (r"\bmkfs(\.\w+)?\b", "formatting a disk"),
    (r"\bdd\b[^|;&]*\bof=/dev/(sd|nvme|hd|vd|mmcblk)", "writing straight onto a disk"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "a fork bomb"),
    (r"(^|[;&|]\s*)(sudo\s+)?(shutdown|reboot|poweroff|halt)\b", "turning the machine off"),
    (r">\s*/dev/(sd|nvme|hd|vd)[a-z0-9]*\b", "writing straight onto a disk"),
    (r"\bchmod\s+(-[a-zA-Z]*\s+)*-?R?\s*[0-7]*777\s+/(\s|$)", "opening every file on the system"),
)

_RC = """\
[ -f ~/.bashrc ] && . ~/.bashrc
__sw_done() { printf '\\033]777;sw-done;%s\\007' "$1"; }
PROMPT_COMMAND="__sw_done \\$?${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
"""

# PowerShell's version of _RC: the same invisible marker after every command.
_RC_PS = """\
if (Test-Path $PROFILE) { . $PROFILE }
function global:prompt {
  $ok = $?
  $code = if ($global:LASTEXITCODE) { $global:LASTEXITCODE } elseif ($ok) { 0 } else { 1 }
  $global:LASTEXITCODE = 0
  [Console]::Write([char]27 + "]777;sw-done;$code" + [char]7)
  "PS $($executionContext.SessionState.Path.CurrentLocation)> "
}
"""
WINDOWS_NEEDS = "Panel Terminal di Windows butuh pywinpty: pip install pywinpty"

_enabled = False


def enable(on: bool = True) -> None:
    """The web server turns this on; the CLI leaves it off (nobody could see the panel)."""
    global _enabled
    _enabled = on


def enabled() -> bool:
    return _enabled


def forbidden(command: str) -> str:
    """Why the agent may not run this, or "" when it may."""
    for pattern, reason in _FORBIDDEN:
        if re.search(pattern, command):
            return reason
    return ""


def clean(raw: bytes) -> str:
    """Terminal bytes as plain text: no colours, no cursor moves, no carriage-return redraws."""
    text = _ANSI_RE.sub("", raw.decode("utf-8", errors="replace"))
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        # A progress bar rewrites its line with \r; only the last version matters.
        lines.append(line.rsplit("\r", 1)[-1])
    return "\n".join(lines)


def waiting_for_password(text: str) -> bool:
    """Whether the last line of terminal text is a password prompt."""
    return bool(_PASSWORD_RE.search(text.rstrip()[-200:]))


def _shell_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not _SECRET_NAME_RE.search(k)}
    env.update(TERM="xterm-256color", COLORTERM="truecolor", SQUIDWINGS_TERMINAL="1")
    return env


@dataclass
class Terminal:
    key: str
    cwd: Path
    pid: int = 0
    fd: int = -1
    buffer: bytearray = field(default_factory=bytearray)
    total: int = 0                      # bytes ever read, so offsets survive the buffer trimming
    at_prompt: bool = False
    agent_command: str = ""
    last_read: int = 0                  # where the agent's last look at the output ended
    last_used: float = field(default_factory=time.monotonic)
    _listeners: set[Callable[[bytes], None]] = field(default_factory=set)
    _done: asyncio.Future[int] | None = None
    _carry: bytes = b""
    _rcfile: str = ""
    _proc: Any = None                   # Windows: the pywinpty process

    def start(self) -> None:
        self.cwd.mkdir(parents=True, exist_ok=True)
        if IS_WINDOWS:
            self._start_windows()
            return
        handle, self._rcfile = tempfile.mkstemp(prefix="squidwings-rc-", suffix=".sh")
        with os.fdopen(handle, "w", encoding="utf-8") as rc:
            rc.write(_RC)
        env = _shell_env()
        argv = ["bash", "--rcfile", self._rcfile, "-i"]
        setsid = shutil.which("setsid")
        if setsid:
            # fork+exec through subprocess is safe in a threaded server; `setsid -c` makes the
            # pty the shell's controlling terminal, so Ctrl+C, job control and sudo work.
            master, slave = pty.openpty()
            process = subprocess.Popen(
                [setsid, "-c", *argv], stdin=slave, stdout=slave, stderr=slave, cwd=self.cwd, env=env, close_fds=True,
            )
            os.close(slave)
            self.pid, self.fd = process.pid, master
        else:
            pid, fd = pty.fork()
            if pid == 0:  # the child: become the shell
                try:
                    os.chdir(self.cwd)
                    os.execvpe("bash", argv, env)
                finally:
                    os._exit(127)
            self.pid, self.fd = pid, fd
        self.resize(30, 110)
        asyncio.get_running_loop().add_reader(self.fd, self._on_readable)

    def _start_windows(self) -> None:
        try:
            from winpty import PtyProcess
        except ImportError as exc:
            raise RuntimeError(WINDOWS_NEEDS) from exc
        handle, self._rcfile = tempfile.mkstemp(prefix="squidwings-rc-", suffix=".ps1")
        with os.fdopen(handle, "w", encoding="utf-8") as rc:
            rc.write(_RC_PS)
        shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
        self._proc = PtyProcess.spawn(
            [shell, "-NoLogo", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", self._rcfile],
            cwd=str(self.cwd), env=_shell_env(), dimensions=(30, 110),
        )
        self.pid = int(getattr(self._proc, "pid", 0) or 0)
        loop = asyncio.get_running_loop()
        proc = self._proc

        def pump() -> None:   # ConPTY has no file descriptor to watch, so a thread reads it
            while True:
                try:
                    text = proc.read(65536)
                except Exception:  # noqa: BLE001 - EOF or a closed console both end the shell
                    text = ""
                if not text:
                    loop.call_soon_threadsafe(self.close)
                    return
                data = text.encode("utf-8", "replace") if isinstance(text, str) else bytes(text)
                loop.call_soon_threadsafe(self._feed, data)

        threading.Thread(target=pump, name=f"terminal-{self.key}", daemon=True).start()

    @property
    def alive(self) -> bool:
        if self._proc is not None:
            return bool(self._proc.isalive())
        if self.pid <= 0:
            return False
        try:
            done, _ = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return False
        return done == 0

    def _on_readable(self) -> None:
        try:
            data = os.read(self.fd, 65536)
        except OSError:
            data = b""
        if not data:
            self.close()
            return
        self._feed(data)

    def _feed(self, data: bytes) -> None:
        self.buffer += data
        self.total += len(data)
        if len(self.buffer) > _BUFFER_BYTES:
            del self.buffer[: len(self.buffer) - _BUFFER_BYTES]
        # The marker can arrive split across two reads: look at the end of the last read too, and count
        # only a marker that ends in this one (one ending earlier was counted then).
        window = self._carry + data
        marks = [m for m in _DONE_RE.finditer(window) if m.end() > len(self._carry)]
        self._carry = window[-32:]
        if marks:
            found = marks[-1]
            self.at_prompt = True
            if self._done is not None and not self._done.done():
                self._done.set_result(int(found.group(1)))
        for listener in list(self._listeners):
            try:
                listener(data)
            except Exception as exc:  # noqa: BLE001 - one broken browser tab must not stop the shell
                logger.debug(f"[terminal] listener failed: {exc}")
                self._listeners.discard(listener)

    def write(self, data: str) -> None:
        if self.fd < 0 and self._proc is None:
            return
        if "\r" in data or "\n" in data:
            self.at_prompt = False
        self.last_used = time.monotonic()
        if self._proc is not None:
            self._proc.write(data)
            return
        os.write(self.fd, data.encode("utf-8"))

    def resize(self, rows: int, cols: int) -> None:
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.setwinsize(max(5, rows), max(20, cols))
            return
        if self.fd >= 0:
            with contextlib.suppress(OSError):
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", max(5, rows), max(20, cols), 0, 0))

    def subscribe(self, listener: Callable[[bytes], None]) -> bytes:
        """Start sending output to ``listener``; returns what is on screen so far."""
        self._listeners.add(listener)
        return bytes(self.buffer)

    def unsubscribe(self, listener: Callable[[bytes], None]) -> None:
        self._listeners.discard(listener)

    def output_since(self, offset: int) -> bytes:
        start = max(0, len(self.buffer) - (self.total - offset))
        return bytes(self.buffer[start:])

    def interrupt(self) -> None:
        self.write("\x03")

    def close(self) -> None:
        if self.fd >= 0:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(self.fd)
            with contextlib.suppress(OSError):
                os.close(self.fd)
            self.fd = -1
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.terminate(force=True)
            self._proc = None
            self.pid = 0
        if self.pid > 0:
            kill_tree(self.pid, hangup=True)
            with contextlib.suppress(ChildProcessError, OSError):
                os.waitpid(self.pid, os.WNOHANG)
            self.pid = 0
        if self._rcfile:
            with contextlib.suppress(OSError):
                os.unlink(self._rcfile)
        if self._done is not None and not self._done.done():
            self._done.cancel()


_terminals: dict[str, Terminal] = {}


def get(key: str) -> Terminal | None:
    term = _terminals.get(key)
    return term if term and term.alive else None


async def open_terminal(key: str, cwd: Path) -> Terminal:
    """This chat's shell, started on first use."""
    term = get(key)
    if term is not None:
        return term
    _terminals.pop(key, None)
    _prune()
    term = Terminal(key=key, cwd=cwd)
    term.start()
    _terminals[key] = term
    # Wait for the first prompt so the first command is not typed into a half-started shell.
    deadline = time.monotonic() + 5
    while not term.at_prompt and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    return term


def close(key: str) -> bool:
    term = _terminals.pop(key, None)
    if term is None:
        return False
    term.close()
    return True


def close_all() -> None:
    for key in list(_terminals):
        close(key)


def _prune() -> None:
    now = time.monotonic()
    for key, term in list(_terminals.items()):
        if not term.alive or now - term.last_used > _IDLE_SECONDS:
            close(key)
    while len(_terminals) >= _MAX_TERMINALS:
        oldest = min(_terminals.values(), key=lambda t: t.last_used)
        close(oldest.key)


async def run(term: Terminal, command: str, timeout: float) -> dict[str, object]:
    """Type a command into the shell and wait for the prompt to come back.

    A command still running at the timeout (a dev server, a password nobody typed yet)
    is left running; the result says so, and ``command=""`` later reads what came since.
    """
    if term.agent_command and not term.at_prompt:
        return {"ok": False, "busy": True, "running": term.agent_command,
                "output": _tail(clean(term.output_since(max(0, term.total - 4000)))),
                "note": "The terminal is still running that command. Wait with terminal_run(command=''), "
                        "or stop it with interrupt=true if the user agrees."}
    if not term.at_prompt:
        return {"ok": False, "busy": True,
                "note": "Something the user started is running in the terminal. Ask them to finish or stop it (Ctrl+C)."}
    loop = asyncio.get_running_loop()
    term._done = loop.create_future()
    start = term.total
    term.agent_command = command
    term.write(command + "\r")
    return await _wait(term, start, command, timeout)


async def wait_more(term: Terminal, timeout: float) -> dict[str, object]:
    """Keep waiting for the command the agent started earlier."""
    if not term.agent_command or term.at_prompt:
        done = term._done
        code = done.result() if done is not None and done.done() and not done.cancelled() else None
        tail = clean(_DONE_RE.sub(b"", term.output_since(term.last_read or max(0, term.total - 6000))))
        term.agent_command = ""
        term.last_read = term.total
        return {"ok": code in (None, 0), "finished": True, "exit_code": code, "output": _tail(tail.strip())}
    start = term.last_read or term.total
    return await _wait(term, start, term.agent_command, timeout)


async def _wait(term: Terminal, start: int, command: str, timeout: float) -> dict[str, object]:
    done = term._done
    code: int | None = None
    waited_for_password = False
    deadline = time.monotonic() + timeout
    while done is not None and not done.done() and time.monotonic() < deadline:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(done), timeout=0.5)
        if not waited_for_password and _PASSWORD_RE.search(clean(term.output_since(start)).rstrip("\n")[-200:]):
            waited_for_password = True
            tail = clean(term.output_since(start)).rstrip("\n")[-200:]
            from brain.utils import host_sudo

            secret = host_sudo.password() if "sudo" in tail.lower() else ""
            if secret:
                # The saved sudo password (Kredensial), typed for the agent; the shell does not echo it.
                term.write(secret + "\n")
            else:
                # Once: the user needs time to notice the panel and type.
                deadline = max(deadline, time.monotonic() + _PASSWORD_WAIT)
    if done is not None and done.done() and not done.cancelled():
        code = done.result()
    raw = term.output_since(start)
    term.last_read = term.total
    text = clean(_DONE_RE.sub(b"", raw))
    first, _, rest = text.partition("\n")
    if command and command.strip()[:40] in first:
        text = rest   # the shell echoing the command back
    result: dict[str, object] = {"output": _tail(text.strip("\n")), "cwd": str(term.cwd)}
    if code is None:
        result.update(ok=True, finished=False, still_running=command)
        if _PASSWORD_RE.search(text.rstrip()[-200:]):
            result["waiting_for"] = "password"
            result["note"] = ("The terminal is asking for a password. Tell the user to type it in the Terminal panel "
                              "(never in the chat), then call terminal_run(command='') to read the rest.")
        else:
            result["note"] = ("Still running (a server or a long job). It keeps running in the user's Terminal panel. "
                              "terminal_run(command='') reads new output; interrupt=true stops it.")
    else:
        term.agent_command = ""
        result.update(ok=code == 0, finished=True, exit_code=code)
        if waited_for_password:
            result["password_typed_by_user"] = True
    return result


def _tail(text: str) -> str:
    if len(text) <= _MAX_RESULT_CHARS:
        return text
    return "[... earlier output cut ...]\n" + text[-_MAX_RESULT_CHARS:]


__all__ = ["Terminal", "clean", "close", "close_all", "enable", "enabled", "forbidden", "get", "open_terminal", "run",
           "wait_more", "waiting_for_password"]
