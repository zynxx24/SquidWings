"""Code navigation and checking, so a small model can work like a careful engineer.

Reading whole files is what burns a cheap model's context and attention. These
tools answer the questions it would otherwise answer by reading everything: what
kind of project is this, what is in this file, where is this symbol, and does it
still work.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from config.settings import get_settings
from security import sandbox
from tool.execute.execute_local_ops.path_guard import (
    PathGuardError,
    is_binary_file,
    resolve_inside,
    resolve_workspace,
)
from utils.logger import get_logger

logger = get_logger(__name__)

_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".next", "target",
    ".agent_memory", ".vault", "graphify-out", ".mypy_cache", ".pytest_cache", ".ruff_cache", "coverage", ".idea", ".vscode",
})
_CODE_SUFFIXES = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".rb",
    ".php", ".c", ".h", ".cpp", ".hpp", ".cs", ".swift", ".vue", ".svelte", ".html", ".css", ".scss", ".sql",
})
_MAX_FILE_BYTES = 1_000_000
_MAX_FILES_SCANNED = 5_000
_MAX_RESULTS = 40
_CHECK_TIMEOUT = 120
_OUTPUT_TAIL = 3_000

_OUTLINE_PATTERNS = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:"
    r"(?:function\*?|class|interface|type|enum|struct|trait|impl|def|fn|func|module)\s+[\w$.]+"
    r"|(?:const|let|var)\s+[\w$]+\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*(?::[^=]+)?=>"
    r"|(?:public|private|protected|static)\s+[\w<>\[\], ]+\s+\w+\s*\("
    r")"
)


def _workspace(workspace_path: str | None) -> Path:
    return resolve_workspace(workspace_path, get_settings().project_root)


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _iter_code_files(root: Path) -> Any:
    """Source files under root, skipping dependencies and build output."""
    seen = 0
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix.lower() in _CODE_SUFFIXES:
                seen += 1
                if seen > _MAX_FILES_SCANNED:
                    return
                yield path


# --------------------------------------------------------------------------- #
# project_overview
# --------------------------------------------------------------------------- #
def _python_commands(root: Path) -> dict[str, str]:
    commands: dict[str, str] = {}
    python = _python_for(root)
    if (root / "tests").is_dir() or any(root.glob("test_*.py")):
        commands["test"] = f"{python} -m pytest -x -q"
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            tools = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {})
        except (OSError, tomllib.TOMLDecodeError):
            tools = {}
        if "ruff" in tools:
            commands["lint"] = f"{python} -m ruff check ."
        if "mypy" in tools:
            commands["types"] = f"{python} -m mypy ."
    return commands


def _python_for(root: Path) -> str:
    venv = root / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sandbox.python_command()


def _node_commands(root: Path) -> dict[str, str]:
    try:
        scripts = json.loads((root / "package.json").read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, ValueError):
        return {}
    runner = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
    commands: dict[str, str] = {}
    for kind, names in {"test": ("test",), "lint": ("lint",), "types": ("typecheck", "type-check", "tsc"), "build": ("build",)}.items():
        for name in names:
            if name in scripts:
                commands[kind] = f"{runner} run {name}"
                break
    if "types" not in commands and (root / "tsconfig.json").exists():
        commands["types"] = "npx tsc --noEmit"
    return commands


def _detect_commands(root: Path) -> dict[str, str]:
    if (root / "package.json").exists():
        return _node_commands(root)
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists() or any(root.glob("*.py")):
        return _python_commands(root)
    if (root / "go.mod").exists():
        return {"test": "go test ./...", "lint": "go vet ./..."}
    if (root / "Cargo.toml").exists():
        return {"test": "cargo test", "lint": "cargo clippy", "types": "cargo check"}
    return {}


def _overview_sync(root: Path) -> dict[str, Any]:
    manifests = [
        name for name in (
            "package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml", "pom.xml",
            "composer.json", "Gemfile", "Dockerfile", "docker-compose.yml", "Makefile", "README.md",
        ) if (root / name).exists()
    ]
    counts: dict[str, int] = {}
    for path in _iter_code_files(root):
        counts[path.suffix.lower()] = counts.get(path.suffix.lower(), 0) + 1
    top_level = sorted(
        f"{p.name}/" if p.is_dir() else p.name
        for p in root.iterdir()
        if p.name not in _SKIP_DIRS and not p.name.startswith(".")
    )[:40]
    return {
        "ok": True,
        "root": str(root),
        "manifests": manifests,
        "languages": dict(sorted(counts.items(), key=lambda kv: -kv[1])[:8]),
        "top_level": top_level,
        "commands": _detect_commands(root),
        "is_git_repo": (root / ".git").exists(),
    }


async def project_overview(workspace_path: str | None = None) -> dict[str, Any]:
    """Stack, layout, and how to run tests. The first call on an unfamiliar project."""
    try:
        return await asyncio.to_thread(_overview_sync, _workspace(workspace_path))
    except (PathGuardError, OSError) as exc:
        return _error(str(exc))


# --------------------------------------------------------------------------- #
# code_outline
# --------------------------------------------------------------------------- #
def _python_outline(source: str) -> list[str]:
    tree = ast.parse(source)
    lines: list[str] = []

    def visit(node: ast.AST, depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                lines.append(f"{child.lineno}: {'  ' * depth}class {child.name}")
                visit(child, depth + 1)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = ", ".join(a.arg for a in child.args.args)
                prefix = "async def" if isinstance(child, ast.AsyncFunctionDef) else "def"
                lines.append(f"{child.lineno}-{child.end_lineno}: {'  ' * depth}{prefix} {child.name}({arguments})")

    visit(tree, 0)
    return lines


def _outline_sync(target: Path) -> dict[str, Any]:
    if target.stat().st_size > _MAX_FILE_BYTES or is_binary_file(target):
        return _error("file is binary or too large to outline")
    source = target.read_text(encoding="utf-8", errors="replace")
    total_lines = source.count("\n") + 1
    outline: list[str] = []
    if target.suffix == ".py":
        try:
            outline = _python_outline(source)
        except SyntaxError as exc:
            return {"ok": True, "lines": total_lines, "outline": [], "syntax_error": f"line {exc.lineno}: {exc.msg}"}
    else:
        for number, line in enumerate(source.splitlines(), 1):
            if _OUTLINE_PATTERNS.match(line):
                outline.append(f"{number}: {line.strip()[:100]}")
    return {"ok": True, "lines": total_lines, "outline": outline[:200]}


async def code_outline(path: str, workspace_path: str | None = None) -> dict[str, Any]:
    """Functions and classes in a file with line numbers, without reading it all."""
    try:
        target = resolve_inside(_workspace(workspace_path), path, must_exist=True)
        return await asyncio.to_thread(_outline_sync, target)
    except (PathGuardError, OSError) as exc:
        return _error(str(exc))


# --------------------------------------------------------------------------- #
# find_symbol
# --------------------------------------------------------------------------- #
def _find_sync(root: Path, name: str) -> dict[str, Any]:
    word = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
    definition = re.compile(
        rf"(?:\b(?:def|class|function\*?|fn|func|interface|type|enum|struct|trait|const|let|var)\s+"
        rf"|^\s*(?:export\s+)?(?:public|private|protected|static)?\s*){re.escape(name)}(?![\w$])\s*[:=(<{{]?"
    )
    definitions: list[str] = []
    references: list[str] = []
    for path in _iter_code_files(root):
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name not in text:
            continue
        relative = path.relative_to(root)
        for number, line in enumerate(text.splitlines(), 1):
            if word.search(line):
                entry = f"{relative}:{number}: {line.strip()[:120]}"
                is_definition = re.search(r"\b(def|class|function|fn|func|interface|type|enum|struct|trait|const|let|var)\b", line) and definition.search(line)
                (definitions if is_definition else references).append(entry)
    return {
        "ok": True,
        "definitions": definitions[:_MAX_RESULTS],
        "references": references[:_MAX_RESULTS],
        "reference_count": len(references),
    }


async def find_symbol(name: str, workspace_path: str | None = None) -> dict[str, Any]:
    """Where a function, class, or variable is defined and used, as file:line."""
    if not name.strip():
        return _error("name must not be empty")
    try:
        return await asyncio.to_thread(_find_sync, _workspace(workspace_path), name.strip())
    except (PathGuardError, OSError) as exc:
        return _error(str(exc))


# --------------------------------------------------------------------------- #
# run_checks
# --------------------------------------------------------------------------- #
def _run_one(command: str, root: Path) -> dict[str, Any]:
    executable = command.split()[0]
    if not Path(executable).is_absolute() and shutil.which(executable) is None:
        return {"command": command, "skipped": f"{executable} is not installed"}
    try:
        proc, _ = sandbox.run_blocking(command.split(), ".", root=root, timeout=_CHECK_TIMEOUT)
    except sandbox.SandboxError as exc:
        return {"command": command, "skipped": str(exc)}
    except subprocess.TimeoutExpired:
        return {"command": command, "passed": False, "output": f"timed out after {_CHECK_TIMEOUT}s"}
    except OSError as exc:
        return {"command": command, "skipped": str(exc)}
    if proc.returncode == 0:
        return {"command": command, "passed": True}
    # pytest exits 5 when it collects nothing, which is not a failure.
    if proc.returncode == 5 and "pytest" in command:
        return {"command": command, "skipped": "no tests collected"}
    # Failures are summarised at the end of the output.
    output = (proc.stdout + "\n" + proc.stderr).strip()
    return {"command": command, "passed": False, "output": output[-_OUTPUT_TAIL:]}


def _checks_sync(root: Path, kinds: list[str]) -> dict[str, Any]:
    available = _detect_commands(root)
    selected = {k: v for k, v in available.items() if k in kinds} if kinds else {
        k: v for k, v in available.items() if k != "build"
    }
    if not selected:
        return {
            "ok": True,
            "passed": None,
            "note": f"No {', '.join(kinds) or 'checks'} configured here. Detected: {sorted(available) or 'nothing'}.",
        }
    results = {kind: _run_one(command, root) for kind, command in selected.items()}
    failed = [kind for kind, result in results.items() if result.get("passed") is False]
    return {"ok": True, "passed": not failed, "failed": failed, "results": results}


async def run_checks(kinds: list[str] | None = None, workspace_path: str | None = None) -> dict[str, Any]:
    """Run the project's own tests, linter, and type checker. Reports only failures."""
    try:
        return await asyncio.to_thread(_checks_sync, _workspace(workspace_path), kinds or [])
    except (PathGuardError, OSError) as exc:
        return _error(str(exc))


__all__ = ["code_outline", "find_symbol", "project_overview", "run_checks"]
