"""The Windows and macOS paths, checked on Linux with the platform switched by hand. Offline."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import types
from pathlib import Path

import pytest

from brain.security import sandbox
from brain.utils import platform_compat, web_terminal


def test_shell_on_windows_prefers_git_bash_then_powershell(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sandbox, "IS_WINDOWS", True)
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: r"C:\Git\bin\bash.exe" if name == "bash" else None)
    assert sandbox.shell_argv("ls -la") == [r"C:\Git\bin\bash.exe", "-c", "ls -la"]
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: r"C:\pwsh.exe" if name == "pwsh" else None)
    assert sandbox.shell_argv("dir")[:2] == [r"C:\pwsh.exe", "-NoProfile"]
    monkeypatch.setattr(sandbox, "IS_WINDOWS", False)
    assert sandbox.shell_argv("ls") == ["sh", "-c", "ls"]


def test_windows_commands_keep_the_system_variables_but_not_secrets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sandbox, "IS_WINDOWS", True)
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    env = sandbox._clean_env("C:/bin", "C:/Users/me")
    assert env["SYSTEMROOT"] == r"C:\Windows" and "OPENAI_API_KEY" not in env
    assert sandbox._limits(sandbox.load_policy()) is None, "no rlimits on Windows"


def test_kill_tree_uses_taskkill_on_windows(monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)
    monkeypatch.setattr(platform_compat.subprocess, "run", lambda argv, **_: calls.append(argv))
    platform_compat.kill_tree(4321)
    assert calls == [["taskkill", "/T", "/F", "/PID", "4321"]]
    platform_compat.kill_tree(0)
    assert len(calls) == 1


def test_kill_tree_stops_a_real_process_group_here():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **platform_compat.new_group_kwargs())
    platform_compat.kill_tree(proc.pid)
    assert proc.wait(timeout=10) != 0


def test_priority_and_stdin_helpers_never_raise(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)
    assert "preexec_fn" not in platform_compat.low_priority_kwargs()
    assert platform_compat.preexec_or_none(lambda: None) is None
    assert platform_compat.stdin_ready(0) is False
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", False)
    assert callable(platform_compat.low_priority_kwargs()["preexec_fn"])


class _FakePty:
    """Stands in for pywinpty's PtyProcess: echoes a command and then the prompt's marker."""

    spawned: list[list[str]] = []

    def __init__(self) -> None:
        self.pid = 999
        self.written: list[str] = []
        self.size = (0, 0)
        self._out: asyncio.Queue[str] | None = None
        self.alive = True
        self.lines = ["PS> \x1b]777;sw-done;0\x07"]

    @classmethod
    def spawn(cls, argv, cwd=None, env=None, dimensions=(24, 80)):
        cls.spawned.append(argv)
        proc = cls()
        proc.size = dimensions
        return proc

    def read(self, size: int) -> str:
        import time

        while self.alive and not self.lines:
            time.sleep(0.01)
        return self.lines.pop(0) if self.lines else ""

    def write(self, text: str) -> None:
        self.written.append(text)
        if text.endswith("\r"):
            self.lines.append(f"{text.strip()}\r\nhello\r\n\x1b]777;sw-done;0\x07")

    def setwinsize(self, rows: int, cols: int) -> None:
        self.size = (rows, cols)

    def isalive(self) -> bool:
        return self.alive

    def terminate(self, force: bool = False) -> None:
        self.alive = False


async def test_the_terminal_runs_powershell_through_conpty_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(web_terminal, "IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "winpty", types.SimpleNamespace(PtyProcess=_FakePty))
    term = web_terminal.Terminal(key="w1", cwd=tmp_path / "chat")
    term.start()
    try:
        assert term.alive and term.pid == 999
        assert "-NoExit" in _FakePty.spawned[-1] and _FakePty.spawned[-1][-1].endswith(".ps1")
        for _ in range(100):
            if term.at_prompt:
                break
            await asyncio.sleep(0.01)
        assert term.at_prompt, "the prompt's marker is recognised"
        result = await web_terminal.run(term, "echo hello", timeout=5)
        assert "hello" in str(result.get("output", "")), result
        term.resize(40, 120)
        assert term._proc.size == (40, 120)
    finally:
        rc = term._rcfile
        term.close()
    assert not term.alive and not Path(rc).exists()


def test_the_terminal_explains_what_windows_needs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(web_terminal, "IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "winpty", None)
    with pytest.raises(RuntimeError, match="pywinpty"):
        web_terminal.Terminal(key="w2", cwd=tmp_path)._start_windows()
