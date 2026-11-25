"""Agent sudo on this machine: the saved password reaches sudo only, never the command or its output."""

from __future__ import annotations

import asyncio
import os
import stat

from brain.utils import host_sudo

FAKE_SUDO = """#!/bin/bash
# -S -v: read the password from stdin and remember it; anything else: run the command.
if [ "$1" = "-S" ]; then read -r pw; echo "$pw" > "$SUDO_LOG"; exit 0; fi
exec "$@"
"""


def _fake(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    sudo.write_text(FAKE_SUDO.replace("$SUDO_LOG", str(tmp_path / "got")))
    sudo.chmod(sudo.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(host_sudo, "_env", lambda: {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)})
    monkeypatch.setattr(host_sudo, "password", lambda: "s3cret-pw")


def test_sudo_gets_the_password_and_the_command_cannot_read_it(tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch)
    out = asyncio.run(host_sudo.run("sudo echo halo && cat <&3; echo s3cret-pw", cwd=str(tmp_path), timeout=20))
    assert (tmp_path / "got").read_text().strip() == "s3cret-pw"      # sudo -S -v was fed
    assert "halo" in out["stdout"]
    assert "s3cret-pw" not in out["stdout"] + out["stderr"]            # fd 3 closed, and the echo is masked
    assert "Bad file descriptor" in out["stderr"] or "3" in out["stderr"]


def test_ready_needs_a_password_or_nopasswd(monkeypatch):
    monkeypatch.setattr(host_sudo, "password", lambda: "")
    monkeypatch.setattr(host_sudo, "nopass", lambda: False)
    assert host_sudo.ready() is False
    monkeypatch.setattr(host_sudo, "password", lambda: "x")
    assert host_sudo.ready() is True


def test_execute_command_runs_sudo_here_when_ready(monkeypatch):
    from tools.command_prompt import run_command_tool

    calls = []

    async def fake_run(command, cwd=".", timeout=900):
        calls.append(command)
        return {"success": True, "stdout": "ok"}

    monkeypatch.setattr(host_sudo, "ready", lambda: True)
    monkeypatch.setattr(host_sudo, "run", fake_run)
    out = asyncio.run(run_command_tool.execute_command("sudo apt install -y php-xml"))
    assert out["success"] and calls == ["sudo apt install -y php-xml"]
