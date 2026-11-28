"""rtk for every agent (checked install, the rewrite SquidWings runs, the Windows shells) and the SquidWings skills
for every AI (the MCP server, its registration in each agent's config). Offline, and runs on Windows too."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from brain.utils import rtk
from skills import connect, mcp_server


# ── rtk ──────────────────────────────────────────────────────────────────────────
def _fake_rtk(monkeypatch: pytest.MonkeyPatch, answers: dict[str, tuple[int, str]]) -> list[list[str]]:
    seen: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        seen.append(argv)
        code, out = answers.get(argv[-1], (1, ""))
        return SimpleNamespace(returncode=code, stdout=out, stderr="")

    monkeypatch.setattr(rtk, "binary", lambda: "/opt/rtk bin/rtk")
    monkeypatch.setattr(rtk.subprocess, "run", run)
    monkeypatch.setattr(rtk.os, "name", "posix")
    return seen


def test_rewrite_points_at_the_binary_and_leaves_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_rtk(monkeypatch, {"git status && ls": (0, "rtk git status && rtk ls"), "pytest -q": (3, "rtk pytest -q")})
    assert rtk.rewrite("git status && ls") == "'/opt/rtk bin/rtk' git status && '/opt/rtk bin/rtk' ls"
    assert rtk.rewrite("pytest -q") == "'/opt/rtk bin/rtk' pytest -q", "3 = rewritten, our own guard already ran"
    assert rtk.rewrite("echo rtk") == "echo rtk", "no filter: unchanged"
    monkeypatch.setenv("RTK", "off")
    assert rtk.rewrite("git status && ls") == "git status && ls"


def test_rewrite_survives_a_missing_or_slow_rtk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rtk, "binary", lambda: None)
    assert rtk.rewrite("git status") == "git status"

    def slow(*_a: Any, **_k: Any) -> None:
        raise subprocess.TimeoutExpired("rtk", 3)

    monkeypatch.setattr(rtk, "binary", lambda: "/x/rtk")
    monkeypatch.setattr(rtk.subprocess, "run", slow)
    assert rtk.rewrite("git status") == "git status"


def test_the_binary_is_quoted_for_each_windows_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rtk.os, "name", "nt")
    monkeypatch.setattr(rtk.shutil, "which", lambda name: "C:/Git/bin/bash.exe")
    assert rtk.command_word(r"C:\Users\Zyn Wira\.local\bin\rtk.exe") == "'C:/Users/Zyn Wira/.local/bin/rtk.exe'"
    monkeypatch.setattr(rtk.shutil, "which", lambda name: None)                # PowerShell
    assert rtk.command_word(r"C:\Users\O'Brien\bin\rtk.exe") == r"& 'C:\Users\O''Brien\bin\rtk.exe'"


def _release(binary: bytes) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("rtk-v1/rtk")
        info.size = len(binary)
        archive.addfile(info, io.BytesIO(binary))
        notes = tarfile.TarInfo("rtk-v1/README")
        notes.size = 2
        archive.addfile(notes, io.BytesIO(b"hi"))
    data = buffer.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def test_install_takes_only_a_release_that_matches_its_checksum(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    archive, digest = _release(b"\x7fELF rtk")
    asset = "rtk-oss-x86_64-unknown-linux-musl.tar.gz"
    files = {"checksums.txt": f"{digest}  {asset}\n".encode(), asset: archive}
    monkeypatch.setattr(rtk, "target", lambda: ("x86_64-unknown-linux-musl", "tar.gz"))
    monkeypatch.setattr(rtk, "_get", lambda url: files[url.rsplit("/", 1)[-1]])
    monkeypatch.setenv("RTK_INSTALL_DIR", str(tmp_path / "bin"))
    done = rtk.install("v1.2.1")
    assert done["sha256"] == digest and (tmp_path / "bin" / "rtk").read_bytes() == b"\x7fELF rtk"
    assert sorted(p.name for p in (tmp_path / "bin").iterdir()) == ["rtk"], "only the binary is written"
    files[asset] = archive + b"tampered"
    with pytest.raises(rtk.RtkError, match="Checksum"):
        rtk.install("v1.2.1")
    files["checksums.txt"] = b"abc  other.tar.gz\n"
    with pytest.raises(rtk.RtkError, match="checksums.txt"):
        rtk.install("v1.2.1")


def _shell_works() -> bool:
    """Whether this machine's command shell really runs a command (Wine's powershell.exe is a stub that does not)."""
    from brain.security import sandbox

    try:
        done = subprocess.run(sandbox.shell_argv("echo shell-ok"), capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return "shell-ok" in done.stdout


async def test_agent_commands_go_through_rtk_and_say_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools.command_prompt import run_command_tool

    if not _shell_works():
        pytest.skip("no working command shell here (on Windows: Git Bash or PowerShell)")

    monkeypatch.setattr(rtk, "rewrite", lambda command: "echo pendek" if command == "echo panjang" else command)
    result = await run_command_tool.execute_command("echo panjang", cwd=str(tmp_path))
    assert result["return_code"] == 0 and "pendek" in result["stdout"]
    assert result["rtk"] is True and "rtk bypass echo panjang" in result["note"] and result["command"] == "echo panjang"
    plain = await run_command_tool.execute_command("echo biasa", cwd=str(tmp_path))
    assert "rtk" not in plain and "biasa" in plain["stdout"]


# ── the skills server ───────────────────────────────────────────────────────────
def _call(tool: str, **arguments: Any) -> dict[str, Any]:
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": tool, "arguments": arguments}})
    assert reply is not None
    return dict(reply["result"])


def test_the_server_speaks_mcp() -> None:
    hello = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    assert hello is not None and hello["result"]["protocolVersion"] == "2024-11-05" and "tools" in hello["result"]["capabilities"]
    newer = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}})
    assert newer is not None and newer["result"]["protocolVersion"] == mcp_server.PROTOCOLS[0]
    assert mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    listed = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed is not None and [t["name"] for t in listed["result"]["tools"]] == ["skills_search", "skill_read", "skills_list"]
    missing = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    assert missing is not None and missing["error"]["code"] == -32601


def test_the_skills_are_found_read_by_section_and_kept_inside_their_folder() -> None:
    found = _call("skills_search", query="desain tampilan tanpa ai slop", limit=3)
    assert not found["isError"] and "aislop" in found["content"][0]["text"]
    guide = _call("skill_read", name="aislop")["content"][0]["text"]
    assert guide.startswith("[SquidWings skill 'aislop'") and "use skill_read instead" in guide
    escape = _call("skill_read", name="aislop", file="../../../.env")
    assert escape["isError"] and "No file" in escape["content"][0]["text"]
    unknown = _call("skill_read", name="tidak-ada-skill-ini")
    assert unknown["isError"] and "skills_search" in unknown["content"][0]["text"]
    assert _call("skills_list")["content"][0]["text"].split(" ", 1)[0].isdigit()


# ── registration in each agent ──────────────────────────────────────────────────
@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(connect, "command", lambda: [r"C:\repo\.venv\Scripts\python.exe", r"C:\repo\skills\mcp_server.py"])
    return tmp_path


def test_json_agents_get_one_entry_and_keep_the_rest(home: Path) -> None:
    config = home / ".gemini" / "config" / "mcp_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    entry = connect._stdio_entry()
    assert connect._json_agent(config, "mcpServers", entry)["action"] == "connected"
    data = json.loads(config.read_text())
    assert data["theme"] == "dark" and data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["squidwings-skills"] == {"command": r"C:\repo\.venv\Scripts\python.exe", "args": [r"C:\repo\skills\mcp_server.py"]}
    assert connect._json_agent(config, "mcpServers", entry)["action"] == "unchanged"
    assert (home / ".gemini" / "config" / "mcp_config.json.squidwings.bak").is_file()
    commented = home / "kilo.jsonc"
    commented.write_text('{\n  // my own note\n  "mcp": {}\n}\n')
    assert connect._json_agent(commented, "mcp", connect._local_entry())["action"] == "skipped"
    assert "// my own note" in commented.read_text(), "a file with comments is never rewritten"


def test_codex_gets_a_toml_table_that_is_replaced_not_duplicated(home: Path) -> None:
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('model = "gpt"\n\n[mcp_servers.squidwings-skills]\ncommand = "/old"\nargs = ["/old.py"]\n\n[features]\nhooks = true\n')
    assert connect._codex(config)["action"] == "connected"
    text = config.read_text()
    assert text.count("[mcp_servers.squidwings-skills]") == 1 and "/old" not in text and "[features]\nhooks = true" in text
    assert 'command = "C:\\\\repo\\\\.venv\\\\Scripts\\\\python.exe"' in text, "Windows paths are escaped for TOML"
    assert connect._codex(config)["action"] == "unchanged"


def test_connect_reports_every_agent(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILLS_MCP_CONNECT", "1")
    monkeypatch.setattr(connect, "AGENTS", [
        ("Present", lambda: True, lambda: {"action": "connected", "detail": "x"}),
        ("Absent", lambda: False, lambda: {"action": "connected", "detail": "never"}),
        ("Broken", lambda: True, lambda: (_ for _ in ()).throw(OSError("disk"))),
    ])
    assert [(r["agent"], r["action"]) for r in connect.connect()] == [("Present", "connected"), ("Absent", "skipped"), ("Broken", "failed")]
    monkeypatch.setenv("SKILLS_MCP_CONNECT", "off")
    assert connect.connect() == []
