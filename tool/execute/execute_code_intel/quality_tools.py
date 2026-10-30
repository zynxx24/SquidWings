"""The local code tools each session uses: graphify and aislop.

Both are command-line programs installed on this machine. They run inside the
sandbox against the session's own folder, so each chat has its own graph and its
own score. Neither needs a model. A missing program is reported, never fatal.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from config.settings import get_settings
from security import sandbox
from tool.execute.execute_local_ops.path_guard import PathGuardError, resolve_workspace
from utils import session_settings
from utils.logger import get_logger

logger = get_logger(__name__)

GRAPH_FILE = Path("graphify-out") / "graph.json"
_SCORE_FILE = Path(".agent_memory") / "aislop.json"
_GRAPH_TIMEOUT = 180
_QUERY_TIMEOUT = 15
_SCAN_TIMEOUT = 90
_OUTPUT_LIMIT = 6_000
_MAX_FINDINGS = 15
_MAX_VERIFY_FINDINGS = 8
_SCAN_EXCLUDES = "graphify-out,.vault,.agent_memory"
_AISLOP_INSTALL = "aislop is not installed. Install it with: npm install -g aislop"
_GRAPHIFY_INSTALL = "graphify is not installed. Install it with: uv tool install graphifyy"
_CODE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".php", ".cs", ".c", ".h", ".cpp", ".hpp",
}


def _workspace(workspace_path: str | None) -> Path:
    return resolve_workspace(workspace_path, get_settings().project_root)


def _auto(name: str) -> bool:
    return os.getenv(name, "true").lower() != "false"


def has_graphify() -> bool:
    return shutil.which("graphify") is not None


def has_aislop() -> bool:
    return shutil.which("aislop") is not None


# --------------------------------------------------------------------------- #
# graphify
# --------------------------------------------------------------------------- #
def _graphify(arguments: list[str], root: Path, timeout: float = _QUERY_TIMEOUT) -> subprocess.CompletedProcess[str]:
    return sandbox.run_blocking(["graphify", *arguments], ".", root=root, timeout=timeout)[0]


def graph_stats(workspace: Path) -> dict[str, Any]:
    """Size of this session's graph, without loading the whole file for big ones."""
    path = workspace / GRAPH_FILE
    if not path.is_file():
        return {"built": False}
    stats: dict[str, Any] = {"built": True, "updated": path.stat().st_mtime}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stats["nodes"] = len(data.get("nodes", []))
        stats["edges"] = len(data.get("links", data.get("edges", [])))
    except (OSError, ValueError) as exc:
        logger.debug(f"[graphify] could not read graph: {exc}")
    return stats


def refresh_graph(workspace: str | Path) -> bool:
    """Rebuild this session's graph if it has code. Never raises."""
    root = Path(workspace)
    if not _auto("GRAPHIFY_AUTO") or not has_graphify() or not session_settings.load(root).graphify:
        return False
    try:
        # --force: this graph follows the code, including after code is deleted.
        done = _graphify(["update", ".", "--force"], root, timeout=_GRAPH_TIMEOUT)
    except (sandbox.SandboxError, OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"[graphify] refresh skipped: {exc}")
        return False
    if done.returncode != 0:
        logger.debug(f"[graphify] refresh failed: {done.stderr[-300:]}")
    return done.returncode == 0


def graph_brief(query: str, workspace: str | Path, budget: int = 300) -> str:
    """What the graph says about a request, for the prompt. Never raises."""
    root = Path(workspace)
    if not (root / GRAPH_FILE).is_file() or not has_graphify() or not session_settings.load(root).graphify:
        return ""
    try:
        done = _graphify(["query", query[:300], "--budget", str(budget)], root)
    except (sandbox.SandboxError, OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"[graphify] query skipped: {exc}")
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _code_graph_sync(root: Path, action: str, target: str, other: str) -> dict[str, Any]:
    if action != "build" and not (root / GRAPH_FILE).is_file():
        built = _graphify(["update", ".", "--force"], root, timeout=_GRAPH_TIMEOUT)
        if built.returncode != 0:
            return {"ok": False, "error": (built.stderr or built.stdout).strip()[-800:] or "graph build failed"}
    commands = {
        "build": ["update", ".", "--force"],
        "hubs": ["god-nodes", "--top", "10"],
        "query": ["query", target, "--budget", "800"],
        "explain": ["explain", target],
        "affected": ["affected", target],
        "path": ["path", target, other],
    }
    arguments = commands.get(action)
    if arguments is None:
        return {"ok": False, "error": f"Unknown action '{action}'. Use {', '.join(commands)}."}
    if action in {"query", "explain", "affected", "path"} and not target:
        return {"ok": False, "error": f"Action '{action}' needs a target."}
    done = _graphify(arguments, root, timeout=_GRAPH_TIMEOUT if action == "build" else _QUERY_TIMEOUT * 2)
    # 3 and 4 are answers: an ambiguous name (with candidates), or nothing found.
    if done.returncode not in {0, 3, 4}:
        return {"ok": False, "error": (done.stderr or done.stdout).strip()[-800:]}
    return {"ok": True, "output": done.stdout.strip()[:_OUTPUT_LIMIT], "graph": graph_stats(root)}


async def code_graph(
    action: str = "hubs",
    target: str = "",
    other: str = "",
    workspace_path: str | None = None,
) -> dict[str, Any]:
    """Ask this session's code graph how things connect."""
    if not has_graphify():
        return {"ok": False, "error": _GRAPHIFY_INSTALL}
    try:
        return await asyncio.to_thread(_code_graph_sync, _workspace(workspace_path), action, target, other)
    except (PathGuardError, sandbox.SandboxError, OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)}


# --------------------------------------------------------------------------- #
# aislop
# --------------------------------------------------------------------------- #
def _scan(root: Path) -> dict[str, Any]:
    """Run a scan and return its JSON report, remembering the score."""
    done = sandbox.run_blocking(
        ["aislop", "scan", ".", "--json", "--exclude", _SCAN_EXCLUDES], ".", root=root, timeout=_SCAN_TIMEOUT
    )[0]
    start = done.stdout.find("{")
    if start == -1:
        raise ValueError((done.stderr or done.stdout).strip()[-400:] or "aislop produced no report")
    report: dict[str, Any] = json.loads(done.stdout[start:])
    path = root / _SCORE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = report.get("summary") or {}
    path.write_text(json.dumps({
        "score": report.get("score"),
        "label": report.get("label"),
        "errors": summary.get("errors", 0),
        "warnings": summary.get("warnings", 0),
        "ts": time.time(),
    }), encoding="utf-8")
    return report


def last_score(workspace: str | Path) -> dict[str, Any] | None:
    """The most recent score for this session, if it has been scanned."""
    try:
        data = json.loads((Path(workspace) / _SCORE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _finding(item: dict[str, Any]) -> str:
    place = f"{item.get('filePath', '?')}:{item.get('line', 0)}"
    help_text = f" Fix: {item['help']}" if item.get("help") else ""
    return f"{place} {item.get('rule', '?')}: {item.get('message', '')}{help_text}"


def _scan_sync(root: Path) -> dict[str, Any]:
    report = _scan(root)
    diagnostics = [d for d in report.get("diagnostics", []) if isinstance(d, dict)]
    diagnostics.sort(key=lambda d: 0 if d.get("severity") == "error" else 1)
    missing = sorted({t for e in (report.get("engines") or {}).values() for t in (e.get("missingTools") or [])})
    return {
        "ok": True,
        "score": report.get("score"),
        "label": report.get("label"),
        "summary": report.get("summary"),
        "findings": [_finding(d) for d in diagnostics[:_MAX_FINDINGS]],
        "more": max(0, len(diagnostics) - _MAX_FINDINGS),
        "missing_tools": missing,
    }


async def aislop_scan(workspace_path: str | None = None) -> dict[str, Any]:
    """Score the session's code for the patterns AI agents leave behind."""
    if not has_aislop():
        return {"ok": False, "error": _AISLOP_INSTALL}
    try:
        return await asyncio.to_thread(_scan_sync, _workspace(workspace_path))
    except (PathGuardError, sandbox.SandboxError, OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)}


def findings_for_changes(workspace: str | Path, changed: list[Path]) -> list[str]:
    """Problems in files this turn touched, for the completion gate. Never raises.

    Only errors and AI-slop warnings. Formatting noise is not worth a retry, and a
    file the turn did not touch is not this turn's problem.
    """
    root = Path(workspace)
    code = [path for path in changed if path.suffix.lower() in _CODE_SUFFIXES]
    if not code or not _auto("AISLOP_AUTO") or not has_aislop() or not session_settings.load(root).aislop:
        return []
    try:
        report = _scan(root)
    except (sandbox.SandboxError, OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.debug(f"[aislop] scan skipped: {exc}")
        return []
    names = {str(path.resolve().relative_to(root.resolve())) for path in code if path.resolve().is_relative_to(root.resolve())}
    found = [
        _finding(item)
        for item in report.get("diagnostics", [])
        if isinstance(item, dict)
        and str(item.get("filePath", "")).removeprefix("./") in names
        and (item.get("severity") == "error" or str(item.get("rule", "")).startswith("ai-slop/"))
    ]
    return found[:_MAX_VERIFY_FINDINGS]
