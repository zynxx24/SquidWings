"""Every SquidWings skill for any AI on this machine, as a small MCP server (stdio, no extra packages).

Claude Code, Codex, Antigravity, Kilo, Gemini CLI, Cursor and OpenCode all speak MCP. Registered once
(``python main.py skills connect``), each of them gets three tools instead of hundreds of skill descriptions in
its context:

* ``skills_search(query)``: the skills for a need, one BM25 ranking over active and stored skills (builtin,
  GitHub sources, add-ons, the user's own uploads), the same search the harness uses.
* ``skill_read(name, section, file)``: a guide; a long one comes as an outline whose sections are read one at a
  time, and a file the guide names (a script, a reference) is read from the skill's folder.
* ``skills_list()``: every skill name with one line, active first.

Run by the agent as ``<repo>/.venv/bin/python <repo>/skills/mcp_server.py``. stdout carries only the protocol:
anything else that prints (logs, warnings) goes to stderr.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER = {"name": "squidwings-skills", "version": "1.0.0"}
MAX_FILE_BYTES = 200_000
INSTRUCTIONS = (
    "SquidWings skills: tested guides for building, debugging, design (no AI slop), video, documents, slides, "
    "security and more. Before a task in one of those areas, call skills_search with what you need, then "
    "skill_read the best match and follow it. Long guides come as an outline: read only the sections you need."
)

TOOLS = [
    {
        "name": "skills_search",
        "description": "Find SquidWings skills (guides) for a need, best match first: name, status, one line, and "
                       "where it lives. Search before work in an area a skill may cover (web, UI, video, docs, "
                       "slides, debugging, testing, security, writing).",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What you need, in a few words (any language)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8}},
            "required": ["query"]},
    },
    {
        "name": "skill_read",
        "description": "Read one SquidWings skill. A long guide comes as an outline: call again with section=<a "
                       "title> for that part. file=<path> reads a file the guide names, from the skill's folder.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "The skill name from skills_search or skills_list."},
            "section": {"type": "string", "description": "A ## section title of the guide (optional)."},
            "file": {"type": "string", "description": "A file inside the skill's folder (optional)."}},
            "required": ["name"]},
    },
    {
        "name": "skills_list",
        "description": "Every SquidWings skill with one line, active first. Prefer skills_search for a task.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class ToolError(ValueError):
    """A call the tool cannot answer, said so the model can correct it."""


# ── the skills ───────────────────────────────────────────────────────────────────
def _all() -> tuple[dict[str, Any], dict[str, Any]]:
    from skills import load_all, sources

    return load_all(), sources.stored()


def _skill(name: str) -> tuple[Any, str]:
    active, stored = _all()
    key = (name or "").strip()
    found = active.get(key) or stored.get(key)
    if found is None:
        lowered = {k.lower(): k for k in [*active, *stored]}
        real = lowered.get(key.lower())
        found = (active.get(real) or stored.get(real)) if real else None
    if found is None:
        raise ToolError(f"No skill named '{name}'. Use skills_search to find the name.")
    return found, "active" if found.name in active else "stored"


def search(query: str, limit: int = 8) -> str:
    from skills import sources

    found = sources.find(query, limit=max(1, min(int(limit or 8), 25))) if query.strip() else []
    if not found:
        return f"No skill matches '{query}'. Try other words, or skills_list."
    return "\n".join(f"- {item['name']} [{item['status']}]: {item['description']}" for item in found)


def read(name: str, section: str = "", file: str = "") -> str:
    from skills import sections

    skill, status = _skill(name)
    root = skill.root
    if file:
        if root is None:
            raise ToolError(f"Skill '{skill.name}' is a single file; it has no other files.")
        base = Path(root).resolve()
        target = (base / file).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise ToolError(f"No file '{file}' in skill '{skill.name}'.")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise ToolError(f"{file} is larger than {MAX_FILE_BYTES // 1000} kB; it is at {target}.")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"{file} is not a text file; it is at {target}.") from None
    body = skill.body
    if section:
        part = sections.section(body, section)
        if part is None:
            titles = ", ".join(sections.headings(body)) or "(none)"
            raise ToolError(f"No section '{section}' in '{skill.name}'. Sections: {titles}")
        return part
    where = f"Files it names are in {root}." if root else ""
    head = (f"[SquidWings skill '{skill.name}' ({status}). {where} Where it says load_skill(...), use skill_read "
            "instead. Tools it names such as style_pick or contrast_check belong to the SquidWings harness: use the "
            "scripts in the skill's folder or an equivalent command.]\n\n")
    return head + sections.outline(skill.name, body).replace("load_skill(", "skill_read(")


def listing() -> str:
    active, stored = _all()
    rows = [f"- {s.name} [active]: {s.description[:120]}" for s in sorted(active.values(), key=lambda s: s.name)]
    rows += [f"- {s.name} [stored]: {s.description[:120]}" for s in sorted(stored.values(), key=lambda s: s.name)
             if s.name not in active]
    return f"{len(rows)} skills.\n" + "\n".join(rows)


def call(name: str, arguments: dict[str, Any]) -> str:
    if name == "skills_search":
        return search(str(arguments.get("query") or ""), int(arguments.get("limit") or 8))
    if name == "skill_read":
        return read(str(arguments.get("name") or ""), str(arguments.get("section") or ""), str(arguments.get("file") or ""))
    if name == "skills_list":
        return listing()
    raise ToolError(f"Unknown tool {name}.")


# ── the protocol ────────────────────────────────────────────────────────────────
def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC message in, the reply out (None for a notification)."""
    method = message.get("method")
    ident = message.get("id")
    params = message.get("params") or {}
    if ident is None:
        return None                                   # notifications/initialized and the like
    if method == "initialize":
        asked = str(params.get("protocolVersion") or "")
        result: dict[str, Any] = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                                  "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER,
                                  "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            text = call(str(params.get("name") or ""), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except ToolError as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except Exception as exc:  # noqa: BLE001 - one failing call must not end the server
            print(f"[squidwings-skills] {type(exc).__name__}: {exc}", file=sys.stderr)
            result = {"content": [{"type": "text", "text": f"Skill lookup failed: {type(exc).__name__}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def serve() -> None:
    out = sys.stdout.buffer
    sys.stdout = sys.stderr                           # nothing but the protocol on stdout
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except ValueError:
            reply: dict[str, Any] | None = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(message) if isinstance(message, dict) else None
        if reply is not None:
            out.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            out.flush()


if __name__ == "__main__":
    serve()
