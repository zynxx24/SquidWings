"""Completion gate: check a claimed result before the user sees it.

Costs no tokens. Checks only what code can establish: changed files parse,
tests that already exist still pass, and the answer does not claim work that
no tool call performed.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import yaml

from security import sandbox
from tool.execute.execute_code_intel import quality_tools
from utils.logger import get_logger

from .guards import tool_failed

logger = get_logger(__name__)

_WRITE_TOOLS = {"file_write", "file_patch", "file_move", "dir_create", "vault_write", "memory_remember"}
_ACTION_TOOLS = _WRITE_TOOLS | {"execute_command", "file_delete"}
_PATH_ARGS = ("path", "file_path", "destination", "dest")
_TEST_TIMEOUT = 60

# "I did it" claims, in the two languages the agent is used in.
_CLAIM_RE = re.compile(
    r"\b(?:"
    r"(?:sudah|telah)\s+(?:saya\s+|aku\s+|kami\s+)?(?:buat|tulis|simpan|perbaiki|ubah|update|perbarui|jalankan|hapus|tambahkan)"
    r"|(?:berhasil|sukses)\s+(?:di)?(?:buat|tulis|simpan|perbaiki|ubah|update|perbarui|jalankan|hapus|tambahkan)"
    r"|i(?:'ve| have)?\s+(?:successfully\s+)?(?:created|wrote|written|saved|fixed|updated|ran|deleted|added|implemented)"
    r"|(?:file|the code|changes?)\s+(?:has|have)\s+been\s+(?:created|written|saved|fixed|updated|applied)"
    r")\b",
    re.IGNORECASE,
)


def changed_files(history: list[dict[str, Any]], workspace: str) -> list[Path]:
    """Files successfully written during this run, resolved against the workspace."""
    found: dict[str, Path] = {}
    for entry in history:
        call = entry.get("tool_call") or {}
        if call.get("name") not in {"file_write", "file_patch", "file_move"}:
            continue
        if tool_failed(entry.get("result") or {}):
            continue
        args = call.get("arguments") or {}
        if not isinstance(args, dict):
            continue
        for key in _PATH_ARGS:
            value = args.get(key)
            if isinstance(value, str) and value:
                path = Path(value)
                if not path.is_absolute() and workspace:
                    path = Path(workspace) / path
                found[str(path)] = path
                break
    return list(found.values())


def _syntax_error(path: Path) -> str | None:
    """Parse a file without executing it. None when fine or not checkable."""
    try:
        suffix = path.suffix.lower()
        if suffix == ".py":
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        elif suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        elif suffix in {".yaml", ".yml"}:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        elif suffix == ".toml":
            tomllib.loads(path.read_text(encoding="utf-8"))
        elif suffix in {".js", ".mjs", ".cjs", ".sh"}:
            tool = "node" if suffix != ".sh" else "bash"
            exe = shutil.which(tool)
            if exe is None:
                return None
            flag = "--check" if tool == "node" else "-n"
            proc = subprocess.run([exe, flag, str(path)], capture_output=True, text=True, timeout=15)
            if proc.returncode != 0:
                return (proc.stderr or proc.stdout).strip()[:400]
    except SyntaxError as exc:
        return f"line {exc.lineno}: {exc.msg}"
    except FileNotFoundError:
        return "file does not exist on disk"
    except (ValueError, yaml.YAMLError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return str(exc)[:400]
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug(f"[verifier] could not check {path}: {exc}")
    return None


def _run_tests(workspace: str) -> str | None:
    """Run existing pytest tests in a session workspace. Returns failure output."""
    root = Path(workspace)
    if not root.is_dir() or not ((root / "tests").is_dir() or any(root.glob("test_*.py"))):
        return None
    command = [sandbox.python_command(), "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider"]
    try:
        # Code the agent just wrote runs under the same limits as its commands.
        proc, _ = sandbox.run_blocking(command, ".", root=root, timeout=_TEST_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"tests did not finish within {_TEST_TIMEOUT}s"
    except (sandbox.SandboxError, OSError) as exc:
        logger.debug(f"[verifier] could not run tests: {exc}")
        return None
    if "No module named pytest" in proc.stderr:
        return None
    # 5 = no tests collected: nothing to report.
    if proc.returncode in {0, 5}:
        return None
    return (proc.stdout + proc.stderr).strip()[-1200:]


def _check_sync(
    answer: str, history: list[dict[str, Any]], workspace: str, tools_offered: bool
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    passed: list[str] = []

    files = changed_files(history, workspace)
    for path in files:
        error = _syntax_error(path)
        if error:
            issues.append(f"`{path.name}` is invalid: {error}")
    if files and not issues:
        passed.append(f"{len(files)} changed file(s) parse")

    if files and not issues and workspace:
        slop = quality_tools.findings_for_changes(workspace, files)
        if slop:
            listed = "\n".join(f"  {item}" for item in slop)
            issues.append(f"aislop found problems in the files you changed:\n{listed}")
        elif quality_tools.has_aislop():
            score = (quality_tools.last_score(workspace) or {}).get("score")
            if score is not None:
                passed.append(f"aislop {score}/100")

    if files and not issues and workspace:
        failure = _run_tests(workspace)
        if failure is not None:
            issues.append(f"Tests fail after your change:\n{failure}")
        elif any(Path(workspace).glob("test_*.py")) or (Path(workspace) / "tests").is_dir():
            passed.append("tests pass")

    used_action = any(
        (entry.get("tool_call") or {}).get("name") in _ACTION_TOOLS
        and not tool_failed(entry.get("result") or {})
        for entry in history
    )
    if tools_offered and not used_action and _CLAIM_RE.search(answer):
        issues.append(
            "Your answer claims work was done, but no tool call in this run did it. "
            "Do the work with a tool now, or say plainly that you did not do it."
        )

    # The last attempt at each tool is what counts; earlier failures were recovered.
    last: dict[str, tuple[Any, str | None]] = {}
    for entry in history:
        call = entry.get("tool_call") or {}
        name = str(call.get("name") or "")
        if name in _ACTION_TOOLS:
            last[name] = (call.get("arguments"), tool_failed(entry.get("result") or {}))
    for name, (_, error) in last.items():
        if error and _CLAIM_RE.search(answer):
            issues.append(f"Your last {name} call failed ({error[:300]}), yet the answer reports success.")

    return issues, passed


async def verify_turn(
    answer: str, history: list[dict[str, Any]], workspace: str, tools_offered: bool
) -> tuple[list[str], list[str]]:
    """Returns ``(issues, passed_checks)``. Never raises."""
    try:
        return await asyncio.to_thread(_check_sync, answer, history, workspace, tools_offered)
    except Exception as exc:  # noqa: BLE001 - verification must never break a turn
        logger.warning(f"[verifier] check failed: {exc}")
        return [], []
