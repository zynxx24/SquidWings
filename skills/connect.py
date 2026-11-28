"""Give every AI on this machine the SquidWings skills: register ``skills/mcp_server.py`` with each one found.

``connect()`` adds one MCP server named ``squidwings-skills`` to every coding agent installed here, the way that
agent expects it, and changes nothing else:

| Agent | Where |
|---|---|
| Claude Code | ``claude mcp add -s user`` (its own CLI) |
| Codex | ``[mcp_servers.squidwings-skills]`` in ``~/.codex/config.toml`` |
| Antigravity | ``mcpServers`` in ``~/.gemini/config/mcp_config.json`` |
| Gemini CLI | ``mcpServers`` in ``~/.gemini/settings.json`` |
| Cursor | ``mcpServers`` in ``~/.cursor/mcp.json`` |
| Kilo, OpenCode | ``mcp`` in ``~/.config/kilo/kilo.jsonc``, ``~/.config/opencode/opencode.json`` |

Running it again changes nothing when the entry is already right, and fixes it when the repo or its Python
moved. A file is backed up (``.squidwings.bak``) before the first change. A config that is not plain JSON (a
``.jsonc`` with comments) is left alone and reported, so a hand-written file is never rewritten wrongly.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

NAME = "squidwings-skills"
ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "skills" / "mcp_server.py"


def python() -> str:
    """The repo's own Python (its .venv), which has everything the server imports."""
    venv = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(venv if venv.is_file() else Path(sys.executable))


def command() -> list[str]:
    return [python(), str(SERVER)]


def _home(path: str) -> Path:
    return Path(path).expanduser()


def _backup(path: Path) -> None:
    spare = path.with_name(path.name + ".squidwings.bak")
    if path.is_file() and not spare.exists():
        shutil.copy2(path, spare)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _load_json(path: Path) -> dict[str, Any] | None:
    """The config as a dict ({} for a missing or empty file), or None when it is not plain JSON."""
    if not path.is_file() or not path.read_text(encoding="utf-8").strip():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _json_agent(path: Path, key: str, entry: dict[str, Any]) -> dict[str, str]:
    data = _load_json(path)
    if data is None:
        return {"action": "skipped", "detail": f"{path} is not plain JSON (comments?); add the server by hand: "
                                                f"{json.dumps({key: {NAME: entry}})}"}
    current = data.get(key)
    servers: dict[str, Any] = current if isinstance(current, dict) else {}
    if servers.get(NAME) == entry:
        return {"action": "unchanged", "detail": str(path)}
    data[key] = {**servers, NAME: entry}
    _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return {"action": "connected", "detail": str(path)}


def _stdio_entry() -> dict[str, Any]:
    exe, script = command()
    return {"command": exe, "args": [script]}


def _local_entry() -> dict[str, Any]:
    return {"type": "local", "command": command(), "enabled": True}


def _codex(path: Path) -> dict[str, str]:
    exe, script = command()
    block = f"[mcp_servers.{NAME}]\ncommand = {json.dumps(exe)}\nargs = [{json.dumps(script)}]\n"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    # The table header and every line up to the next table header (an args = [...] line is not one).
    header = re.compile(rf"^\[mcp_servers\.{re.escape(NAME)}\][^\n]*\n?(?:^(?!\[).*(?:\n|$))*", re.MULTILINE)
    found = header.search(text)
    if found and found.group(0).strip() == block.strip():
        return {"action": "unchanged", "detail": str(path)}
    # A function, not a string: re.sub would read the backslashes of a Windows path as escapes.
    new = header.sub(lambda _match: block + "\n", text, count=1) if found else (text.rstrip() + "\n\n" + block if text.strip() else block)
    _write(path, new)
    return {"action": "connected", "detail": str(path)}


def _claude() -> dict[str, str]:
    cli = shutil.which("claude")
    if cli is None:
        return {"action": "skipped", "detail": "claude CLI not on PATH"}
    current = subprocess.run([cli, "mcp", "get", NAME], capture_output=True, text=True, timeout=60, check=False)
    exe, script = command()
    if current.returncode == 0 and exe in current.stdout and script in current.stdout:
        return {"action": "unchanged", "detail": "claude mcp (user)"}
    if current.returncode == 0:
        subprocess.run([cli, "mcp", "remove", NAME, "-s", "user"], capture_output=True, text=True, timeout=60, check=False)
    done = subprocess.run([cli, "mcp", "add", "-s", "user", NAME, "--", exe, script], capture_output=True, text=True,
                          timeout=60, check=False)
    if done.returncode != 0:
        return {"action": "failed", "detail": (done.stderr or done.stdout).strip()[-300:]}
    return {"action": "connected", "detail": "claude mcp (user)"}


# (agent, installed?, connect)
AGENTS: list[tuple[str, Callable[[], bool], Callable[[], dict[str, str]]]] = [
    ("Claude Code", lambda: shutil.which("claude") is not None, _claude),
    ("Codex", lambda: shutil.which("codex") is not None or _home("~/.codex").is_dir(),
     lambda: _codex(_home("~/.codex/config.toml"))),
    ("Antigravity", lambda: _home("~/.gemini/antigravity").is_dir(),
     lambda: _json_agent(_home("~/.gemini/config/mcp_config.json"), "mcpServers", _stdio_entry())),
    ("Gemini CLI", lambda: shutil.which("gemini") is not None,
     lambda: _json_agent(_home("~/.gemini/settings.json"), "mcpServers", _stdio_entry())),
    ("Cursor", lambda: _home("~/.cursor").is_dir(),
     lambda: _json_agent(_home("~/.cursor/mcp.json"), "mcpServers", _stdio_entry())),
    ("Kilo", lambda: _home("~/.config/kilo").is_dir() or shutil.which("kilo") is not None,
     lambda: _json_agent(_home("~/.config/kilo/kilo.jsonc"), "mcp", _local_entry())),
    ("OpenCode", lambda: _home("~/.config/opencode").is_dir() or shutil.which("opencode") is not None,
     lambda: _json_agent(_home("~/.config/opencode/opencode.json"), "mcp", _local_entry())),
]


def connect() -> list[dict[str, str]]:
    """Register the skills server with every agent found here. Returns what happened per agent."""
    if os.getenv("SKILLS_MCP_CONNECT", "1").strip().lower() in {"0", "off", "false", "no"}:
        return []
    out = []
    for name, present, link in AGENTS:
        if not present():
            out.append({"agent": name, "action": "skipped", "detail": "not installed here"})
            continue
        try:
            out.append({"agent": name, **link()})
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            out.append({"agent": name, "action": "failed", "detail": f"{type(exc).__name__}: {exc}"[:300]})
    return out
