"""
main.py — Agent Router entry point.

Provides a rich interactive terminal IDE with:
- Model selector (switch models interactively)
- Project workspace system (organize projects by date/name)
- File browser / project tree
- File preview & editing from terminal
- AI file read/write/edit authority in active workspace
- Beautiful Rich-powered UI with panels and themes

Usage:
    python main.py chat          # Interactive chat mode (IDE)
    python main.py ask "query"   # One-shot question
    python main.py models        # List available models
    python main.py health        # Check API health
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import re
import select
import sys
import termios
import time
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.tree import Tree

from brain.classifier.continuation import (
    continuation_hint,
    is_continuation_input,
)
from brain.classifier.intent_classifier import IntentClassifier
from brain.context.context_assembler import ContextAssembler
from brain.memory.continuation_memory import ContinuationMemory
from brain.memory.conversation_store import ConversationStore
from brain.memory.persistent_memory import PersistentMemory
from brain.memory.workspace_memory import WorkspaceMemory
from brain.prompt_must_think.base_think_prompt import ThinkPromptManager
from config.constants import TaskType
from config.secrets import get_secrets
from config.settings import get_settings
from model.connection.api_client import APIClient
from model.connection.graphify_client import GraphifyClient
from model.connection.health_check import HealthChecker
from model.connection.response_handler import ParsedResponse, ResponseHandler
from model.parser.tool_call_parser import ToolCallParser
from model.registry.model_info import ModelInfo
from model.registry.model_registry import ModelRegistry
from model.settings.fallback import FallbackStrategy
from model.settings.routing_type import Difficulty, RoutingType
from model.stream.stream_handler import StreamAggregator
from model.type.activity import ActivityHandler
from model.type.base_handler import BaseTypeHandler
from model.type.code import CodeHandler
from model.type.generate_img import GenerateImgHandler
from model.type.math import MathHandler
from model.type.multimodal import MultimodalHandler
from model.type.reason import ReasonHandler
from model.type.school_task import SchoolTaskHandler
from tool.execute.execute_edit_change_file.file_editor import FileEditor
from tool.execute.execute_local_memory.vault_context import (
    build_context as build_vault_context,
)
from tool.execute.execute_local_memory.session_distill import distill_session
from tool.register_tools import build_skills_prompt, register_all_tools
from utils.cli_formatter import (
    SESSION_CODE_TRACKER,
    TerminalFormatConfig,
    TerminalStreamRenderer,
    handle_code_command,
    handle_format_command,
    render_terminal_markdown,
)
from utils.helpers import format_tokens, generate_session_id
from utils.logger import get_logger
from utils.timer import Timer

logger = get_logger(__name__)
console = Console()

# Turn counters for session distillation (memory), kept module-level so the
# CLI loop and the fallback path share one cadence per session.
_CLI_TURNS: dict[str, int] = {}

# Handler registry
HANDLERS: dict[TaskType, BaseTypeHandler] = {
    TaskType.CODE: CodeHandler(),
    TaskType.MATH: MathHandler(),
    TaskType.ACTIVITY: ActivityHandler(),
    TaskType.SCHOOL_TASK: SchoolTaskHandler(),
    TaskType.GENERATE_IMG: GenerateImgHandler(),
    TaskType.REASON: ReasonHandler(),
    TaskType.MULTIMODAL: MultimodalHandler(),
}

# Provider colors and icons for pretty display
PROVIDER_THEME: dict[str, dict[str, str]] = {
    "ag": {"color": "bright_cyan", "icon": "🔷", "label": "Agent"},
    "kg": {"color": "bright_magenta", "icon": "🟣", "label": "Kagiro Market"},
    "TH": {"color": "bright_yellow", "icon": "🟡", "label": "TH"},
    "gemini": {"color": "bright_blue", "icon": "♊", "label": "Google Gemini"},
    "bansos": {"color": "bright_green", "icon": "🆓", "label": "Bansos (Free)"},
    "vk": {"color": "bright_red", "icon": "🔺", "label": "Vikey Router"},
}

# File extension → syntax language mapping
EXT_LANG_MAP: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "jsx", ".tsx": "tsx", ".json": "json",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown", ".html": "html", ".css": "css",
    ".sh": "bash", ".bash": "bash", ".sql": "sql",
    ".xml": "xml", ".ini": "ini", ".cfg": "ini",
    ".txt": "text", ".env": "bash", ".gitignore": "text",
    ".rs": "rust", ".go": "go", ".java": "java",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
    ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".kt": "kotlin", ".dart": "dart", ".lua": "lua",
    ".r": "r", ".R": "r", ".csv": "text",
}

# Ignored directories for file browser
IGNORED_DIRS: set[str] = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".pytest_cache", ".mypy_cache", ".tox", ".eggs",
    "dist", "build", ".egg-info", ".idea", ".vscode",
    ".agent_backups", "vendor", "storage", ".gradle",
}

# Ignored file patterns
IGNORED_FILES: set[str] = {
    ".DS_Store", "Thumbs.db", ".gitkeep",
}



def _flush_stdin_buffer() -> None:
    """Flush any buffered stdin input after AI streaming ends.

    Saat AI streaming teks panjang (mis. CSS/kode besar), ada jeda
    beberapa detik di tengah streaming. User yang tidak sadar masih
    dalam mode input mungkin menekan Enter atau mengetik sesuatu.
    Tombol tersebut masuk ke buffer stdin dan akan langsung dikonsumsi
    oleh console.input() berikutnya — menyebabkan AI "lanjut sendiri".

    Fungsi ini membuang semua keystroke yang terakumulasi di buffer
    stdin sejak AI mulai merespons, sehingga prompt berikutnya
    benar-benar menunggu input baru dari user.
    """
    try:
        # Unix/Linux/Mac: gunakan termios untuk flush buffer stdin
        if sys.stdin.isatty():
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        # Fallback: drain dengan select (non-blocking read loop)
        try:
            while True:
                ready, _, _ = select.select([sys.stdin], [], [], 0)
                if not ready:
                    break
                sys.stdin.read(1)
        except Exception:
            pass


def _read_user_input(prompt: str) -> str:
    """Read user input, seamlessly combining multi-line pastes into a single message."""
    try:
        first_line = console.input(prompt)
    except (EOFError, KeyboardInterrupt):
        raise

    is_bracketed_paste = "\x1b[200~" in first_line
    first_line = first_line.replace("\x1b[200~", "").replace("\x1b[201~", "")

    lines = [first_line]

    # Check if more lines are immediately buffered in stdin (multi-line paste)
    timeout = 0.1 if is_bracketed_paste else 0.05
    while sys.stdin.isatty():
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if not r:
            break
        extra_line = sys.stdin.readline()
        if not extra_line:
            break
        cleaned = extra_line.replace("\x1b[200~", "").replace("\x1b[201~", "").rstrip("\r\n")
        lines.append(cleaned)
        if "\x1b[201~" in extra_line:
            break

    total_text = "\n".join(lines).strip()
    if len(lines) > 1 and total_text:
        console.print(f"  [dim cyan]📋 Multi-line paste terdeteksi ({len(lines)} baris)[/dim cyan]")

    return total_text


def _get_provider_theme(provider: str) -> dict[str, str]:
    """Get color theme and icon for a provider prefix."""
    return PROVIDER_THEME.get(provider, {"color": "white", "icon": "⚡", "label": provider.upper()})


def _build_project_tree(
    root_path: Path,
    tree: Tree | None = None,
    max_depth: int = 3,
    current_depth: int = 0,
) -> Tree:
    """Recursively build a Rich Tree from the directory.

    Args:
        root_path: Root directory path.
        tree: Parent tree node (or None for root).
        max_depth: Maximum recursion depth.
        current_depth: Current recursion depth.

    Returns:
        Rich Tree object representing the directory structure.
    """
    if tree is None:
        tree = Tree(
            f"[bold bright_cyan]📁 {root_path.name}/[/bold bright_cyan]",
            guide_style="dim cyan",
        )

    if current_depth >= max_depth:
        tree.add("[dim]...[/dim]")
        return tree

    try:
        entries = sorted(
            root_path.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except PermissionError:
        tree.add("[dim red]⛔ Permission denied[/dim red]")
        return tree

    dirs: list[Path] = []
    files: list[Path] = []

    for entry in entries:
        if entry.name.startswith(".") and entry.name not in (".env", ".env.example", ".gitignore"):
            continue
        if entry.is_dir() and entry.name in IGNORED_DIRS:
            continue
        if entry.is_file() and entry.name in IGNORED_FILES:
            continue

        if entry.is_dir():
            dirs.append(entry)
        else:
            files.append(entry)

    for d in dirs:
        subtree = tree.add(
            f"[bold bright_yellow]📂 {d.name}/[/bold bright_yellow]"
        )
        _build_project_tree(d, subtree, max_depth, current_depth + 1)

    for f in files:
        ext = f.suffix.lower()
        size = f.stat().st_size
        size_str = _format_file_size(size)

        if ext == ".py":
            icon, color = "🐍", "green"
        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            icon, color = "📜", "yellow"
        elif ext in (".yaml", ".yml", ".toml", ".json"):
            icon, color = "⚙️ ", "bright_blue"
        elif ext == ".md":
            icon, color = "📝", "white"
        elif ext in (".html", ".css"):
            icon, color = "🌐", "bright_magenta"
        elif ext in (".txt", ".env", ".gitignore"):
            icon, color = "📄", "dim"
        elif ext in (".png", ".jpg", ".svg", ".webp"):
            icon, color = "🖼️ ", "bright_magenta"
        else:
            icon, color = "📄", "dim white"

        tree.add(
            f"{icon} [{color}]{f.name}[/{color}] [dim]({size_str})[/dim]"
        )

    return tree


def _format_file_size(size: int) -> str:
    """Format file size in human-readable format."""
    if size >= 1_048_576:
        return f"{size / 1_048_576:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _preview_file(file_path: Path, max_lines: int = 50) -> None:
    """Preview a file with syntax highlighting.

    Args:
        file_path: Path to the file.
        max_lines: Maximum number of lines to show.
    """
    if not file_path.exists():
        console.print(f"[red]❌ File not found: {file_path}[/red]")
        return

    if not file_path.is_file():
        console.print(f"[red]❌ Not a file: {file_path}[/red]")
        return

    size = file_path.stat().st_size
    if size > 512_000:
        console.print(f"[yellow]⚠️  File too large to preview ({_format_file_size(size)})[/yellow]")
        return

    ext = file_path.suffix.lower()
    lang = EXT_LANG_MAP.get(ext, "text")

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        console.print(f"[red]❌ Cannot read file: {escape(str(e))}[/red]")
        return

    lines = content.split("\n")
    total_lines = len(lines)
    truncated = total_lines > max_lines

    if truncated:
        content = "\n".join(lines[:max_lines])

    syntax = Syntax(
        content,
        lexer=lang,
        theme="monokai",
        line_numbers=True,
        word_wrap=True,
    )

    title = f"📄 {file_path.name} ({lang}) [{_format_file_size(size)}]"
    subtitle = f"Showing {min(max_lines, total_lines)} of {total_lines} lines" if truncated else f"{total_lines} lines"

    console.print()
    console.print(Panel(
        syntax,
        title=f"[bold bright_cyan]{title}[/bold bright_cyan]",
        subtitle=f"[dim]{subtitle}[/dim]",
        border_style="cyan",
        padding=(0, 1),
    ))


def _display_model_selector(registry: ModelRegistry, current_model: str | None = None) -> str | None:
    """Display an interactive model selector.

    Shows all available models grouped by provider with capabilities.
    Returns the selected model ID or None if cancelled.

    Args:
        registry: The model registry.
        current_model: Currently active model ID.

    Returns:
        Selected model ID or None.
    """
    models = registry.get_all()

    # Group by provider
    by_provider: dict[str, list[ModelInfo]] = {}
    for m in models:
        provider = m.provider or "unknown"
        by_provider.setdefault(provider, []).append(m)

    console.print()
    console.print(Rule("[bold bright_cyan]🤖 Model Selector[/bold bright_cyan]", style="cyan"))
    console.print()

    all_models: list[ModelInfo] = []
    idx = 1

    for provider, provider_models in sorted(by_provider.items()):
        theme = _get_provider_theme(provider)

        table = Table(
            title=f"{theme['icon']} {theme['label']} Provider ({provider}/)",
            title_style=f"bold {theme['color']}",
            border_style=theme["color"],
            box=box.ROUNDED,
            show_lines=False,
            pad_edge=True,
            expand=True,
        )

        table.add_column("#", style="bold bright_white", width=4, justify="right")
        table.add_column("Model ID", style="bold", min_width=30)
        table.add_column("Display Name", style="bright_white")
        table.add_column("👁️ ", width=3, justify="center")
        table.add_column("🔊", width=3, justify="center")
        table.add_column("🔍", width=3, justify="center")
        table.add_column("🔧", width=3, justify="center")
        table.add_column("🧠", width=3, justify="center")
        table.add_column("Context", justify="right")
        table.add_column("Max Out", justify="right")

        for m in sorted(provider_models, key=lambda x: x.id):
            is_current = m.id == current_model
            marker = " ◀ [bold green]ACTIVE[/bold green]" if is_current else ""
            row_style = "on grey15" if is_current else ""
            model_id_display = f"[bold bright_green]{m.id}[/bold bright_green]" if is_current else m.id

            table.add_row(
                str(idx),
                model_id_display + marker,
                m.display_name,
                "✅" if m.supports_vision else "·",
                "✅" if m.supports_audio else "·",
                "✅" if m.supports_search else "·",
                "✅" if m.supports_tools else "·",
                "✅" if m.supports_reasoning else "·",
                f"[dim]{format_tokens(m.context_window)}[/dim]",
                f"[dim]{format_tokens(m.max_output)}[/dim]",
                style=row_style,
            )
            all_models.append(m)
            idx += 1

        console.print(table)
        console.print()

    console.print(
        "[dim]Legend: 👁️ =Vision  🔊=Audio  🔍=Search  🔧=Tools  🧠=Reasoning[/dim]"
    )
    console.print()

    # Get user selection
    try:
        choice = console.input(
            "[bold bright_cyan]Select model number (or 'c' to cancel): [/bold bright_cyan]"
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if choice.lower() in ("c", "cancel", ""):
        return None

    try:
        choice_idx = int(choice) - 1
        if 0 <= choice_idx < len(all_models):
            selected = all_models[choice_idx]
            return selected.id
        else:
            console.print("[red]❌ Invalid selection[/red]")
            return None
    except ValueError:
        # Try matching by model ID directly
        for m in all_models:
            if choice in m.id:
                return m.id
        console.print("[red]❌ Invalid selection[/red]")
        return None


def _display_file_browser(target_path: Path, title: str | None = None) -> None:
    """Show the file browser with tree view.

    Args:
        target_path: Directory path to show.
        title: Optional custom header title.
    """
    display_title = title or f"📁 {target_path.name}"
    console.print()
    console.print(Rule(f"[bold bright_cyan]{display_title}[/bold bright_cyan]", style="cyan"))
    console.print()

    tree = _build_project_tree(target_path, max_depth=4)
    console.print(Panel(
        tree,
        title=f"[bold bright_cyan]{display_title}[/bold bright_cyan]",
        subtitle=f"[dim]{target_path}[/dim]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()


def _display_workspace_list(workspace_dir: Path, active_workspace: Path | None) -> None:
    """Display list of all workspace projects grouped by date/name."""
    projects = sorted([p for p in workspace_dir.iterdir() if p.is_dir()]) if workspace_dir.exists() else []

    console.print()
    console.print(Rule("[bold bright_cyan]📁 Workspace Projects[/bold bright_cyan]", style="cyan"))
    console.print()

    if not projects:
        console.print(Panel(
            "[yellow]Belum ada proyek di folder workspace/.[/yellow]\n\n"
            "Buat proyek baru dengan perintah:\n"
            "  [bold cyan]/tree YYYYMMDD-namaproyek[/bold cyan] (contoh: [cyan]/tree 20261009-appcash[/cyan])",
            title="[bold yellow]Workspace Masih Kosong[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        ))
        return

    table = Table(
        title="📂 Daftar Proyek di workspace/",
        title_style="bold bright_cyan",
        border_style="cyan",
        box=box.ROUNDED,
        expand=True,
    )
    table.add_column("#", style="bold", width=4, justify="right")
    table.add_column("Project Folder", style="bold bright_white", min_width=25)
    table.add_column("Status", width=12, justify="center")
    table.add_column("Files", width=8, justify="right")
    table.add_column("Size", width=10, justify="right")
    table.add_column("Modified", style="dim", justify="right")

    for i, p in enumerate(projects, 1):
        is_active = active_workspace and p.resolve() == active_workspace.resolve()
        status = "[bold green]▶ ACTIVE[/bold green]" if is_active else "[dim]—[/dim]"
        files = [f for f in p.rglob("*") if f.is_file() and not any(part in IGNORED_DIRS for part in f.parts)]
        file_count = len(files)
        total_size = sum(f.stat().st_size for f in files)
        try:
            mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:
            mtime = "—"

        row_style = "on grey15" if is_active else ""
        table.add_row(
            str(i),
            f"📁 {p.name}",
            status,
            str(file_count),
            _format_file_size(total_size),
            mtime,
            style=row_style,
        )

    console.print(table)
    console.print(
        "[dim]Beralih / buat proyek baru: [bold cyan]/tree <nama-proyek>[/bold cyan] "
        "(contoh: [cyan]/tree 20261009-appcash[/cyan])[/dim]\n"
    )


def _build_workspace_context(workspace_path: Path, user_input: str = "") -> str:
    """Build context string describing active workspace files for the AI.

    Uses WorkspaceMemory for smart, token-efficient context.
    """
    if not workspace_path.exists() or not workspace_path.is_dir():
        return ""

    wm = WorkspaceMemory(workspace_path)
    return wm.build_smart_context(user_input=user_input)


def _is_safe_rel_path(rel_path: str) -> bool:
    """Validate that a parsed relative path is a real file path, not prose/regex garbage.

    Blocks:
    - Empty or whitespace-only
    - Path traversal (../)
    - Absolute paths
    - Paths > 200 chars (Errno 36: File name too long)
    - Paths containing markdown/regex noise: |, <, >, *, \\n, \\, backtick, #
    - Paths with multiple consecutive spaces (prose, not filenames)
    - Paths starting with punctuation that indicates prose: (, ), [, ], {, }
    - Paths containing common prose indicators: ' — ', ' | ', '...'
    """
    if not rel_path or not rel_path.strip():
        return False
    if len(rel_path) > 200:
        return False
    if rel_path.startswith("/") or rel_path.startswith("~"):
        return False
    if ".." in rel_path.split("/"):
        return False
    # Block markdown/regex noise characters
    _BANNED_CHARS = set('|<>*\\`#\n\r\t"\'')
    if _BANNED_CHARS & set(rel_path):
        return False
    # Block prose-like paths (multiple spaces, ellipsis, em-dash)
    if "  " in rel_path or "..." in rel_path or " — " in rel_path:
        return False
    # Block paths starting with brackets/parens (prose fragments)
    if rel_path[0] in "()[]{}!?;:,":
        return False
    # Each path component must be reasonable
    for part in rel_path.split("/"):
        if not part:
            continue
        # Component with spaces must end in known extension
        if " " in part:
            _VALID_EXT = (
                ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json",
                ".md", ".txt", ".yaml", ".yml", ".sh", ".toml", ".cfg", ".ini",
                ".xml", ".csv", ".sql", ".env", ".php", ".rb", ".go", ".rs",
                ".java", ".kt", ".swift", ".c", ".cpp", ".h", ".hpp",
                ".vue", ".svelte", ".scss", ".less", ".lock", ".blade.php",
            )
            if not any(part.endswith(ext) for ext in _VALID_EXT):
                return False
    return True


def _apply_workspace_changes(workspace_path: Path, response_text: str) -> list[str]:
    """Parse and automatically apply file changes from AI response into workspace."""
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    editor = FileEditor(backup_before_edit=True)
    actions: list[str] = []

    # 1. Parse replace blocks: ```replace:<path>\n<<<<<<< SEARCH\n...\n=======\n...\n>>>>>>>\n```
    replace_pattern = re.compile(
        r"```replace:([^\n]+)\s*\n<<<<<<<\s*SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>>\s*```",
        re.DOTALL
    )
    for match in replace_pattern.finditer(response_text):
        rel_path = match.group(1).strip()
        if not _is_safe_rel_path(rel_path):
            logger.warning(f"[workspace] Skipped replace — invalid path: {rel_path!r}")
            continue
        target = match.group(2)
        replacement = match.group(3)
        file_path = workspace_path / rel_path

        try:
            res = editor.replace_content(file_path, target, replacement)
            if res.success:
                actions.append(f"✏️  Replaced in: {rel_path}")
                console.print(f"  [bold green]✅ Applied replace in:[/bold green] [cyan]{rel_path}[/cyan]")
            else:
                console.print(f"  [yellow]⚠️  Replace notice for {rel_path}: {res.message}[/yellow]")
        except Exception as e:
            console.print(f"  [red]❌ Replace error in {escape(str(rel_path))}: {escape(str(e))}[/red]")

    # 2. Parse file blocks: ```file:<path>\ncontent\n```
    file_pattern_1 = re.compile(r"```file:([^\n]+)\n(.*?)```", re.DOTALL)
    for match in file_pattern_1.finditer(response_text):
        rel_path = match.group(1).strip()
        if not _is_safe_rel_path(rel_path):
            logger.warning(f"[workspace] Skipped file block — invalid path: {rel_path!r}")
            continue
        content = match.group(2)
        file_path = workspace_path / rel_path
        try:
            res = editor.create_file(file_path, content, overwrite=True)
            actions.append(f"💾 Saved: {rel_path} ({res.lines_changed} lines)")
            console.print(f"  [bold green]✅ Saved file:[/bold green] [cyan]{rel_path}[/cyan] [dim]({res.lines_changed} lines)[/dim]")
        except Exception as e:
            console.print(f"  [red]❌ File write error {escape(str(rel_path))}: {escape(str(e))}[/red]")

    # 3. Parse headers: ### File: <path>\n```lang\ncontent\n``` or **File:** <path>\n```lang\ncontent\n```
    file_pattern_2 = re.compile(
        r"(?:###\s*File:|\*\*File:\*\*)\s*([^\n\*\#]+?)\s*\n```[a-zA-Z0-9_\-\.]*\n(.*?)```",
        re.DOTALL
    )
    for match in file_pattern_2.finditer(response_text):
        rel_path = match.group(1).strip().strip("`").strip("*")
        if not _is_safe_rel_path(rel_path):
            logger.warning(f"[workspace] Skipped header file — invalid path: {rel_path!r}")
            continue
        content = match.group(2)
        file_path = workspace_path / rel_path
        try:
            res = editor.create_file(file_path, content, overwrite=True)
            actions.append(f"💾 Saved: {rel_path} ({res.lines_changed} lines)")
            console.print(f"  [bold green]✅ Saved file:[/bold green] [cyan]{rel_path}[/cyan] [dim]({res.lines_changed} lines)[/dim]")
        except Exception as e:
            console.print(f"  [red]❌ File write error {escape(str(rel_path))}: {escape(str(e))}[/red]")

    return actions



def _handle_ide_command(
    cmd: str,
    registry: ModelRegistry,
    project_root: Path,
    workspace_dir: Path,
    current_model: str | None,
    active_workspace: Path | None,
    terminal_config: TerminalFormatConfig | None = None,
    terminal_cfg_path: Path | None = None,
) -> tuple[bool, str | None, Path | None]:
    """Handle IDE slash commands.

    Args:
        cmd: The command string (without leading /).
        registry: Model registry.
        project_root: Project root path.
        workspace_dir: Root directory for workspace projects.
        current_model: Currently selected model.
        active_workspace: Currently active project workspace.

    Returns:
        Tuple of (was_handled, new_model_id_or_None, new_workspace_or_None).
    """
    parts = cmd.strip().split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if command in ("model", "models", "m"):
        selected = _display_model_selector(registry, current_model)
        if selected:
            console.print(f"\n[bold green]✅ Model changed to: {selected}[/bold green]")
        return True, selected, active_workspace

    elif command in ("tree", "t", "files", "browse", "ls"):
        if arg:
            # Handle root/all view
            if arg.lower() in ("all", "root", ".", "--all"):
                _display_file_browser(project_root, title=f"Agent Root ({project_root.name})")
                return True, None, active_workspace

            # User specified project name: e.g. /tree 20261009-appcash
            clean_name = arg.strip().strip("/").replace("workspace/", "")
            target = workspace_dir / clean_name
            is_new = not target.exists()
            target.mkdir(parents=True, exist_ok=True)

            if is_new:
                console.print(f"[bold bright_green]✨ Proyek workspace baru dibuat: workspace/{target.name}[/bold bright_green]")
                console.print(f"[dim]📁 Path: {target}[/dim]")
                console.print("[dim]🤖 AI sekarang memiliki akses penuh untuk baca, buat, edit, dan replace file di sini.[/dim]")
            else:
                console.print(f"[bold bright_cyan]📁 Beralih ke workspace: workspace/{target.name}[/bold bright_cyan]")

            _display_file_browser(target, title=f"Workspace: {target.name}")
            return True, None, target
        else:
            # No argument passed:
            if active_workspace and active_workspace.exists():
                _display_file_browser(active_workspace, title=f"Workspace: {active_workspace.name}")
                console.print(
                    f"[dim]💡 Workspace aktif: [bold cyan]{active_workspace.name}[/bold cyan] | "
                    f"Gunakan [cyan]/tree <nama-proyek>[/cyan] untuk beralih, atau [cyan]/tree all[/cyan] untuk lihat root.[/dim]\n"
                )
                return True, None, active_workspace
            else:
                # Show list of existing projects in workspace/
                projects = sorted([p for p in workspace_dir.iterdir() if p.is_dir()]) if workspace_dir.exists() else []
                if projects:
                    _display_workspace_list(workspace_dir, active_workspace)
                else:
                    console.print("[dim]Belum ada proyek di workspace. Menampilkan root agent:[/dim]")
                    _display_file_browser(project_root, title=f"Agent Root ({project_root.name})")
                    console.print(
                        "[dim]💡 Buat proyek baru dengan: [bold cyan]/tree YYYYMMDD-namaproyek[/bold cyan] "
                        "(contoh: [cyan]/tree 20261009-appcash[/cyan])[/dim]\n"
                    )
                return True, None, active_workspace

    elif command in ("ws", "workspace", "project"):
        if not arg or arg.lower() in ("list", "ls"):
            _display_workspace_list(workspace_dir, active_workspace)
            return True, None, active_workspace
        elif arg.lower() in ("none", "exit", "close", "clear"):
            console.print("[yellow]Workspace dinonaktifkan. Mode general agent.[/yellow]")
            return True, None, None
        else:
            # Switch / create workspace
            clean_name = arg.strip().strip("/").replace("workspace/", "")
            target = workspace_dir / clean_name
            target.mkdir(parents=True, exist_ok=True)
            console.print(f"[bold bright_cyan]📁 Workspace aktif: workspace/{target.name}[/bold bright_cyan]")
            _display_file_browser(target, title=f"Workspace: {target.name}")
            return True, None, target

    elif command in ("view", "cat", "open", "v"):
        if not arg:
            console.print("[yellow]⚠️  Usage: /view <file_path>[/yellow]")
            return True, None, active_workspace
        file_path = Path(arg)
        if not file_path.is_absolute():
            # Check active workspace first
            if active_workspace and (active_workspace / file_path).exists():
                file_path = active_workspace / file_path
            else:
                file_path = project_root / file_path
        _preview_file(file_path)
        return True, None, active_workspace

    elif command in ("create", "touch", "new"):
        if not arg:
            console.print("[yellow]⚠️  Usage: /create <filename>[/yellow]")
            return True, None, active_workspace
        base_dir = active_workspace if active_workspace else workspace_dir / "default"
        base_dir.mkdir(parents=True, exist_ok=True)
        target_file = base_dir / arg.strip()
        target_file.parent.mkdir(parents=True, exist_ok=True)
        if not target_file.exists():
            target_file.write_text("", encoding="utf-8")
            console.print(f"[bold green]✅ File dibuat: {target_file.relative_to(project_root)}[/bold green]")
        else:
            console.print(f"[yellow]⚠️  File sudah ada: {target_file.relative_to(project_root)}[/yellow]")
        return True, None, active_workspace

    elif command in ("help", "h", "?"):
        _display_help()
        return True, None, active_workspace

    elif command in ("status", "info", "i"):
        _display_status(registry, current_model, project_root, active_workspace)
        return True, None, active_workspace

    elif command in ("search", "find", "grep", "s"):
        if not arg:
            console.print("[yellow]⚠️  Usage: /search <pattern>[/yellow]")
            return True, None, active_workspace
        search_root = active_workspace if active_workspace else project_root
        _search_files(search_root, arg)
        return True, None, active_workspace

    elif command in ("code", "c"):
        cfg = terminal_config or TerminalFormatConfig()
        cfg_path = terminal_cfg_path or (project_root / "config" / "terminal_format.json")
        handle_code_command(arg, console, cfg, SESSION_CODE_TRACKER, cfg_path)
        return True, None, active_workspace

    elif command in ("format", "fmt"):
        cfg = terminal_config or TerminalFormatConfig()
        cfg_path = terminal_cfg_path or (project_root / "config" / "terminal_format.json")
        handle_format_command(arg, console, cfg, cfg_path)
        return True, None, active_workspace

    elif command in ("theme",):
        cfg = terminal_config or TerminalFormatConfig()
        cfg_path = terminal_cfg_path or (project_root / "config" / "terminal_format.json")
        handle_format_command(f"theme {arg}".strip(), console, cfg, cfg_path)
        return True, None, active_workspace

    elif command in ("md", "markdown"):
        cfg = terminal_config or TerminalFormatConfig()
        if not arg or arg.lower() in ("render", "view", "show"):
            if SESSION_CODE_TRACKER.last_full_response:
                console.print("\n[bold cyan]📖 Render Ulang Markdown Respons Terakhir:[/bold cyan]\n")
                render_terminal_markdown(
                    SESSION_CODE_TRACKER.last_full_response,
                    console=console,
                    config=cfg,
                    tracker=SESSION_CODE_TRACKER,
                )
            else:
                console.print("[yellow]⚠️  Belum ada respons AI yang tersimpan di sesi ini.[/yellow]")
        else:
            cfg_path = terminal_cfg_path or (project_root / "config" / "terminal_format.json")
            handle_format_command(f"md {arg}".strip(), console, cfg, cfg_path)
        return True, None, active_workspace

    elif command in ("bansos", "bs", "mode-bansos"):
        # Load latest from .env so user edits take effect immediately
        from dotenv import load_dotenv
        load_dotenv(override=True)
        b_url = os.getenv("BANSOS_API_BASE_URL", "https://api.kagiro.net/v1")
        b_key = os.getenv("BANSOS_API_KEY", "")
        b_model = os.getenv("BANSOS_MODEL", "deepseek-v4.1-flash-1017")
        model_id = f"bansos/{b_model}" if not b_model.startswith("bansos/") else b_model

        subcmd = arg.lower().strip()

        if subcmd in ("off", "disable", "stop", "exit"):
            console.print("\n[bold yellow]🎁 Mode Bansos Dinonaktifkan.[/bold yellow] [dim]Kembali ke model default auto-router.[/dim]\n")
            return True, "ag/gemini-3.8-flash-high", active_workspace

        if subcmd in ("status", "info"):
            masked_key = (b_key[:8] + "..." + b_key[-4:]) if len(b_key) > 12 else (b_key[:4] + "..." if b_key else "[red]Belum diset[/red]")
            info_panel = Panel(
                f"[bold bright_yellow]🎁 Status Mode Bansos[/bold bright_yellow]\n\n"
                f"[bold white]Status:[/bold white]          {'[bright_green]AKTIF[/bright_green]' if current_model == model_id else '[dim]Non-aktif[/dim]'}\n"
                f"[bold white]Model:[/bold white]           [bold bright_cyan]{model_id}[/bold bright_cyan]\n"
                f"[bold white]API Base URL:[/bold white]    [cyan]{b_url}[/cyan]\n"
                f"[bold white]API Key:[/bold white]         [dim]{masked_key}[/dim]\n"
                f"[bold white]Auto-Generate:[/bold white]   [bright_green]Tersedia (Tool pembuat aplikasi canggih)[/bright_green]\n"
                f"[bold white]Fallback:[/bold white]        [bright_green]Non-stop / Tanpa Henti (Unlimited)[/bright_green]\n\n"
                f"[dim]💡 Edit BANSOS_API_BASE_URL & BANSOS_API_KEY di file .env kapan saja.[/dim]",
                title="🎁 Bansos Configuration",
                border_style="bright_yellow",
            )
            console.print()
            console.print(info_panel)
            console.print()
            return True, None, active_workspace

        # Default action: Toggle / activate bansos mode
        masked_key = (b_key[:8] + "..." + b_key[-4:]) if len(b_key) > 12 else (b_key[:4] + "..." if b_key else "[red]Belum diset[/red]")
        banner_content = (
            f"[bold bright_yellow]🎁 Mode Bansos Berhasil Diaktifkan![/bold bright_yellow]\n\n"
            f"[bold white]Model:[/bold white]          [bold bright_cyan]{model_id}[/bold bright_cyan] (DeepSeek V4.1 Flash)\n"
            f"[bold white]Endpoint:[/bold white]       [cyan]{b_url}[/cyan]\n"
            f"[bold white]API Key:[/bold white]        [dim]{masked_key}[/dim]\n"
            f"[bold white]Auto-Generate:[/bold white]  [bold bright_green]AKTIF[/bold bright_green] (Memperkaya prompt rancangan aplikasi)\n"
            f"[bold white]Fallback:[/bold white]       [bold bright_green]NON-STOP / TANPA HENTI[/bold bright_green] (Auto-retry tiada henti)\n\n"
            f"[dim]💡 Tip: API Key dan Base URL dapat diedit langsung di file [bold cyan].env[/bold cyan][/dim]"
        )
        console.print()
        console.print(Panel(banner_content, title="🎁 MODE BANSOS ACTIVE", border_style="bright_yellow"))
        console.print()
        return True, model_id, active_workspace

    return False, None, active_workspace


def _display_help() -> None:
    """Display the help panel with available commands."""
    help_table = Table(
        title="💡 Perintah IDE & Workspace",
        title_style="bold bright_cyan",
        border_style="cyan",
        box=box.ROUNDED,
        expand=True,
    )
    help_table.add_column("Command", style="bold bright_green", min_width=30)
    help_table.add_column("Keterangan", style="white")

    commands = [
        ("/bansos [on|off|status]", "Mode bansos gratis (DeepSeek V4.1 Flash via Kagiro, auto-generate & non-stop fallback)"),
        ("/model, /m", "Buka interaktif selector untuk memilih model AI"),
        ("/tree <tanggal-proyek>", "Buka / buat folder proyek baru (contoh: /tree 20261009-appcash)"),
        ("/tree, /t", "Tampilkan struktur file dari workspace aktif / daftar proyek"),
        ("/tree all", "Tampilkan tree lengkap seluruh repository agent"),
        ("/ws, /workspace", "Lihat daftar semua proyek dalam workspace/"),
        ("/ws <nama>", "Beralih atau buat proyek workspace"),
        ("/create <file>", "Buat file baru di workspace aktif"),
        ("/view <file>, /v <file>", "Preview isi file dengan syntax highlighting"),
        ("/code [1|min|full|copy]", "Kelola tampilan kode: toggle minimize, expand, copy, atau list"),
        ("/format", "Pengaturan format terminal CLI (Markdown, tema, minimizer, thinking)"),
        ("/theme <nama>", "Ganti tema syntax highlighting (monokai, dracula, nord, dll.)"),
        ("/md", "Render ulang balasan terakhir dalam format Markdown lengkap"),
        ("/search <pola>, /s", "Cari file berdasarkan nama di workspace"),
        ("/status, /info, /i", "Lihat status sesi, model aktif, dan workspace aktif"),
        ("/help, /h, /?", "Tampilkan bantuan ini"),
        ("clear", "Hapus riwayat obrolan sesi"),
        ("exit, quit, q", "Keluar dari aplikasi"),
    ]

    for cmd, desc in commands:
        help_table.add_row(cmd, desc)

    console.print()
    console.print(help_table)
    console.print()


def _display_status(
    registry: ModelRegistry,
    current_model: str | None,
    project_root: Path,
    active_workspace: Path | None = None,
) -> None:
    """Display session status info."""
    model_info = registry.get(current_model) if current_model else None
    theme = _get_provider_theme(model_info.provider) if model_info else {"icon": "❓", "color": "white"}

    status_lines: list[str] = []
    status_lines.append(f"[bold]Current Model:[/bold]     {theme.get('icon', '❓')} [bold {theme.get('color', 'white')}]{current_model or 'auto (router)'}[/]")

    if active_workspace:
        status_lines.append(f"[bold]Active Workspace:[/bold]  📁 [bold bright_green]workspace/{active_workspace.name}[/bold bright_green]")
    else:
        status_lines.append("[bold]Active Workspace:[/bold]  [dim]None (gunakan /tree YYYYMMDD-namaproyek)[/dim]")

    if model_info:
        caps: list[str] = []
        if model_info.supports_vision:
            caps.append("👁️  Vision")
        if model_info.supports_audio:
            caps.append("🔊 Audio")
        if model_info.supports_search:
            caps.append("🔍 Search")
        if model_info.supports_tools:
            caps.append("🔧 Tools")
        if model_info.supports_reasoning:
            caps.append("🧠 Reasoning")
        status_lines.append(f"[bold]Capabilities:[/bold]      {', '.join(caps) if caps else 'None'}")
        status_lines.append(f"[bold]Context Window:[/bold]    {format_tokens(model_info.context_window)}")
        status_lines.append(f"[bold]Max Output:[/bold]        {format_tokens(model_info.max_output)}")
        status_lines.append(f"[bold]Thinking:[/bold]          {model_info.thinking_format or 'N/A'}")

    status_lines.append(f"[bold]Project Root:[/bold]      {project_root}")
    status_lines.append(f"[bold]Total Models:[/bold]      {registry.count}")

    # Count by provider
    providers: dict[str, int] = {}
    for m in registry.get_all():
        p = m.provider or "unknown"
        providers[p] = providers.get(p, 0) + 1
    provider_str = " · ".join(f"{p}: {c}" for p, c in sorted(providers.items()))
    status_lines.append(f"[bold]Providers:[/bold]         {provider_str}")

    console.print()
    console.print(Panel(
        "\n".join(status_lines),
        title="[bold bright_cyan]📊 Session Status[/bold bright_cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()


def _search_files(root: Path, pattern: str, max_results: int = 30) -> None:
    """Search for files by name pattern.

    Args:
        root: Root directory to search.
        pattern: Search pattern (substring match).
        max_results: Maximum results to show.
    """
    results: list[Path] = []
    pattern_lower = pattern.lower()

    for path in root.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.is_file() and pattern_lower in path.name.lower():
            results.append(path)
        if len(results) >= max_results:
            break

    if not results:
        console.print(f"[yellow]No files matching '{pattern}' found in {root.name}.[/yellow]")
        return

    table = Table(
        title=f"🔍 Search Results for '{pattern}' in {root.name}",
        title_style="bold bright_cyan",
        border_style="cyan",
        box=box.ROUNDED,
    )
    table.add_column("#", style="bold", width=4, justify="right")
    table.add_column("File", style="bright_green")
    table.add_column("Path", style="dim")
    table.add_column("Size", style="dim", justify="right")

    for i, f in enumerate(results, 1):
        rel = f.relative_to(root)
        table.add_row(
            str(i),
            f.name,
            str(rel.parent),
            _format_file_size(f.stat().st_size),
        )

    console.print()
    console.print(table)
    console.print(f"[dim]Found {len(results)} file(s). Use /view <path> to preview.[/dim]")
    console.print()


def _record_file_changes_to_memory(
    persistent_memory: PersistentMemory,
    session_id: str,
    workspace_path: Path,
    response_text: str,
) -> None:
    """Parse AI response for file changes and record them in memory."""
    import re

    # Detect file blocks: ### File: <path>
    file_pattern = re.compile(
        r"(?:###\s*File:|\*\*File:\*\*|```file:)\s*([^\n\*\#`]+)",
    )
    for match in file_pattern.finditer(response_text):
        rel_path = match.group(1).strip().strip("`").strip("*")
        if rel_path:
            persistent_memory.record_file_touch(
                session_id,
                file_path=str(workspace_path / rel_path),
                action="created",
                summary=f"AI created/updated file: {rel_path}",
            )

    # Detect replace blocks: ```replace:<path>
    replace_pattern = re.compile(r"```replace:([^\n]+)")
    for match in replace_pattern.finditer(response_text):
        rel_path = match.group(1).strip()
        if rel_path:
            persistent_memory.record_file_touch(
                session_id,
                file_path=str(workspace_path / rel_path),
                action="edited",
                summary=f"AI edited file: {rel_path}",
            )


def _append_to_system_message(
    msg: dict[str, Any], extra_text: str, separator: str = "\n"
) -> None:
    """Safely append text to message content, handling both str and block lists (e.g. prompt caching)."""
    if not extra_text:
        return
    content = msg.get("content")
    if isinstance(content, list):
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                block["text"] = (block["text"] or "") + separator + extra_text
                return
        content.append({"type": "text", "text": extra_text})
    elif isinstance(content, str):
        msg["content"] = content + separator + extra_text
    else:
        msg["content"] = str(content or "") + separator + extra_text


def _append_volatile_to_system_message(msg: dict[str, Any], extra_text: str) -> None:
    """Append per-turn content without breaking the prompt-cache prefix.

    ``apply_cache_control`` puts a cache breakpoint at the end of the system
    block. Text appended *inside* that block changes the cached prefix, so
    any volatile content (vault memory, which is rewritten every few turns by
    session distill) would invalidate the whole cache on each edit. Appending
    it as a separate, uncached block keeps the stable prefix cacheable and
    still lands in the system message, after the cached part.
    """
    if not extra_text:
        return
    content = msg.get("content")
    if isinstance(content, list) and any(
        isinstance(block, dict) and block.get("cache_control") for block in content
    ):
        content.append({"type": "text", "text": extra_text})
        return
    _append_to_system_message(msg, extra_text, separator="")


async def _distill_cli_session(
    conversation_store: ConversationStore,
    session_id: str,
    active_workspace: Path | None,
) -> None:
    """Refresh the vault session note every few turns. Never raises."""
    _CLI_TURNS[session_id] = _CLI_TURNS.get(session_id, 0) + 1
    if _CLI_TURNS[session_id] % 3 != 0:
        return
    try:
        await distill_session(
            conversation_store,
            session_id,
            workspace_path=str(active_workspace) if active_workspace else None,
        )
    except Exception as exc:  # noqa: BLE001 - memory must not break the turn
        logger.debug(f"[process_input] distill skipped: {exc}")


async def process_input(
    user_input: str,
    session_id: str,
    api_client: APIClient,
    classifier: IntentClassifier,
    router: RoutingType,
    context_assembler: ContextAssembler,
    conversation_store: ConversationStore,
    health_checker: HealthChecker,
    fallback: FallbackStrategy,
    stream: bool = True,
    override_model: str | None = None,
    registry: ModelRegistry | None = None,
    active_workspace: Path | None = None,
    memory_context: str = "",
    tool_registry: Any = None,
    skills_prompt: str = "",
    terminal_config: TerminalFormatConfig | None = None,
    previous_model: str | None = None,
) -> str:
    """Process a single user input through the full pipeline.

    Pipeline: classify → route → assemble context → call API → parse response → apply workspace edits

    Args:
        user_input: The user's message.
        session_id: Session identifier.
        api_client: API client for model calls.
        classifier: Intent classifier.
        router: Model router.
        context_assembler: Context assembler.
        conversation_store: Conversation store.
        health_checker: Health checker.
        fallback: Fallback strategy.
        stream: Whether to stream the response.
        override_model: If set, skip routing and use this model directly.
        registry: Model registry (needed when override_model is set).
        active_workspace: Active workspace path for AI file operations.
        memory_context: Persistent memory summary to inject.
        tool_registry: Tool registry for AI tool-calling support.
        skills_prompt: Prebuilt skills-awareness text for system prompt.
        terminal_config: Terminal formatting configuration.

    Returns:
        str: The model's response text.
    """
    with Timer("total_pipeline"):
        # 1. Classify intent + difficulty.
        # A bare "lanjutkan" carries no intent of its own: the heuristic
        # classifier can only answer "activity/easy, conf≈0.30", which then
        # routes the turn to an unrelated model and throws away the context of
        # the truncated work. Continuation turns therefore inherit the model
        # that produced the previous output.
        resume_model_id = (
            previous_model or conversation_store.get_last_used_model(session_id)
        )
        is_continue_turn = is_continuation_input(user_input)

        classification, difficulty = classifier.classify_with_difficulty(user_input)
        diff_enum = Difficulty(difficulty)

        if is_continue_turn:
            console.print("  [dim]🔁 continuation turn — reusing previous model[/dim]")
        else:
            task_label = classification.task_type.value
            conf_label = f"{classification.confidence:.2f}"
            console.print(
                f"  [dim]📋 {task_label}/{difficulty} "
                f"(conf={conf_label})[/dim]"
            )

        # 1b. Reset fallback chain — setiap request baru mulai bersih
        fallback.reset_session(session_id)

        # 2. Get handler
        handler = HANDLERS.get(classification.task_type, HANDLERS[TaskType.ACTIVITY])

        # 3. Route to model (or use override)
        model = None
        if override_model and registry:
            model = registry.get(override_model)
            if not model:
                console.print(f"[red]❌ Override model '{override_model}' not found[/red]")
                model = router.route(classification.task_type, diff_enum)
        elif is_continue_turn and resume_model_id and registry:
            model = registry.get(resume_model_id)
            if not model:
                logger.warning(
                    "[process_input] continuation model '%s' not in registry; "
                    "falling back to normal routing",
                    resume_model_id,
                )
                model = router.route(classification.task_type, diff_enum)
        else:
            model = router.route(classification.task_type, diff_enum)

        if not model:
            console.print("[red]❌ No model available[/red]")
            return "Error: No model available for this request."

        # Display model info with provider theme
        theme = _get_provider_theme(model.provider)
        console.print(
            f"  [dim]{theme['icon']} [{theme['color']}]{model.display_name}[/] ({model.id})[/dim]"
        )

        # Build augmented input with workspace context if active
        augmented_input = user_input
        _ws_memory: WorkspaceMemory | None = None
        _cont_memory: ContinuationMemory | None = None

        if active_workspace and active_workspace.is_dir():
            _ws_memory = WorkspaceMemory(active_workspace)
            _cont_memory = ContinuationMemory(active_workspace)
            _ws_memory.increment_turn()

            # Detect "continue" — inject truncation buffer instead of full context.
            # The detector lives in brain.classifier.continuation so routing and
            # memory injection can never disagree about what a continuation is.
            is_continue = is_continue_turn

            if is_continue and (_ws_memory.has_truncation() or _cont_memory.has_code_buffer()):
                # Prefer continuation_memory's smart code buffer
                cont_ctx = ""
                if _cont_memory.has_code_buffer():
                    cont_ctx = _cont_memory.build_context(for_continue=True)
                    console.print("  [dim cyan]🔄 Continue mode — loading code buffer...[/dim cyan]")
                if not cont_ctx and _ws_memory.has_truncation():
                    cont_ctx = _ws_memory.get_continue_context()
                    console.print("  [dim cyan]🔄 Continue mode — loading truncation buffer...[/dim cyan]")
                augmented_input = f"{cont_ctx}\n\n[USER REQUEST]\n{user_input}"
            elif is_continue:
                # User says continue, but response wasn't truncated — resume task without dumping files
                continuity_ctx = _ws_memory.get_continuity_context()
                activity_ctx = _cont_memory.build_context(for_continue=False)
                parts = []
                if activity_ctx:
                    parts.append(activity_ctx)
                if continuity_ctx:
                    parts.append(continuity_ctx)
                parts.append(f"[USER REQUEST]\n{user_input} {continuation_hint()}")
                augmented_input = "\n\n".join(parts)
                console.print("  [dim cyan]🔄 Continue mode — melanjutkan langkah berikutnya...[/dim cyan]")
            else:
                # Use SmartMemory for ranked, decay-aware context
                from brain.memory.smart_memory import SmartMemory

                _smart = SmartMemory(active_workspace, workspace_memory=_ws_memory)
                ws_context = _smart.raw.build_smart_context(user_input=user_input)
                # Inject last activity context (super compact, ~50 tokens)
                activity_ctx = _cont_memory.build_context(for_continue=False)
                # Inject continuity context agar AI tidak ngulang
                continuity_ctx = _ws_memory.get_continuity_context()

                context_parts = []
                if ws_context:
                    context_parts.append(ws_context)
                if activity_ctx:
                    context_parts.append(activity_ctx)
                if continuity_ctx:
                    context_parts.append(continuity_ctx)
                context_parts.append(f"[USER REQUEST]\n{user_input}")

                augmented_input = "\n\n".join(context_parts)

        # Continuation turn without an active workspace: there is no buffer to
        # inject, but the message is too thin to stand alone, so state the
        # resume intent explicitly rather than letting the model ask "lanjut apa?".
        if is_continue_turn and augmented_input == user_input:
            augmented_input = f"{user_input}\n\n{continuation_hint()}"

        # Inject memory context (for cross-model continuity)
        if memory_context:
            augmented_input = f"{memory_context}\n\n{augmented_input}"

        # 4. Assemble context (inject skills awareness into system prompt)
        ctx = context_assembler.assemble(
            handler=handler,
            user_input=augmented_input,
            session_id=session_id,
            model_id=model.id,
            context_window=model.context_window,
            model_max_output=model.max_output,
        )

        # Inject skills prompt into system message so AI knows its capabilities
        if skills_prompt and ctx.messages:
            for msg in ctx.messages:
                if msg.get("role") == "system":
                    _append_to_system_message(msg, skills_prompt, separator="\n")
                    break

        # Inject L1 vault memory automatically. The agent must not have to
        # remember to call vault_boot — memory is part of the prompt, always.
        # Built here, not in chat_mode: process_input is where the prompt is
        # assembled, and the builder is cached by vault mtime, so this costs
        # one stat() per turn rather than a rebuild.
        vault_memory = build_vault_context()
        if vault_memory and ctx.messages:
            for msg in ctx.messages:
                if msg.get("role") == "system":
                    _append_volatile_to_system_message(msg, vault_memory)
                    break

        # 5. Prepare request params
        params = handler.prepare_request_params(model)
        temperature = params.get("temperature", 0.5)
        max_tokens = params.get("max_tokens", 8192)

        # Build tools payload for API if model supports tool calling
        tools_payload: list[dict[str, Any]] | None = None
        if tool_registry and model.supports_tools:
            tools_payload = tool_registry.get_all_schemas()
            if tools_payload:
                logger.debug(f"[process] Sending {len(tools_payload)} tool schemas to API")

        cfg = terminal_config or TerminalFormatConfig()

        # 6. Call API
        try:
            active_messages: list[dict[str, Any]] = list(ctx.messages)
            content_parts: list[str] = []
            stream_renderer: TerminalStreamRenderer | None = None
            total_prompt_tokens: int = 0
            total_completion_tokens: int = 0

            async def _execute_single_call(
                messages: list[dict[str, Any]],
                show_stream: bool = True,
                use_tools: bool = True,
            ) -> tuple[ParsedResponse, list[str]]:
                nonlocal stream_renderer, total_prompt_tokens, total_completion_tokens
                c_parts: list[str] = []
                effective_tools = tools_payload if use_tools else None
                if stream:
                    th_parts: list[str] = []
                    stream_renderer = TerminalStreamRenderer(
                        console=console,
                        config=cfg,
                        tracker=SESSION_CODE_TRACKER,
                    )

                    def on_chunk(chunk: str) -> None:
                        c_parts.append(chunk)
                        if show_stream and stream_renderer:
                            stream_renderer.on_content_chunk(chunk)

                    def on_thinking_chunk(chunk: str) -> None:
                        th_parts.append(chunk)
                        if show_stream and stream_renderer:
                            stream_renderer.on_thinking_chunk(chunk)

                    aggregator = StreamAggregator(
                        on_content_chunk=on_chunk,
                        on_thinking_chunk=on_thinking_chunk,
                    )
                    stream_iter = api_client.chat_completion_stream(
                        model=model.id,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=effective_tools,
                    )
                    res_parsed = await aggregator.aggregate(stream_iter)
                    if show_stream:
                        console.print()
                    if res_parsed.cached_tokens > 0 and show_stream:
                        console.print(
                            f"  [dim cyan]⚡ Prompt Cache Hit: {res_parsed.cached_tokens} tokens ({res_parsed.cache_hit_ratio:.1%})[/dim cyan]"
                        )
                    total_prompt_tokens += res_parsed.prompt_tokens
                    total_completion_tokens += res_parsed.completion_tokens
                    return res_parsed, c_parts
                else:
                    raw_res = await api_client.chat_completion(
                        model=model.id,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=effective_tools,
                    )
                    res_parsed = ResponseHandler.handle(raw_res)
                    if res_parsed.cached_tokens > 0 and show_stream:
                        console.print(
                            f"  [dim cyan]⚡ Prompt Cache Hit: {res_parsed.cached_tokens} tokens ({res_parsed.cache_hit_ratio:.1%})[/dim cyan]"
                        )
                    total_prompt_tokens += res_parsed.prompt_tokens
                    total_completion_tokens += res_parsed.completion_tokens
                    return res_parsed, [res_parsed.content or ""]

            parsed, content_parts = await _execute_single_call(active_messages, show_stream=True)

            # 6b. Autonomous Tool Execution Loop (Multi-Turn)
            # If the model requested tool execution (via OpenAI schema tool_calls OR DeepSeek DSML / text blocks),
            # execute them, show execution status, feed results back to the model, and continue until final answer.
            max_tool_rounds = 20
            tool_round = 0

            while tool_round < max_tool_rounds:
                raw_text = "".join(content_parts)
                detected_calls: list[dict[str, Any]] = []

                if parsed.has_tool_calls:
                    detected_calls = parsed.tool_calls
                elif raw_text:
                    detected_calls = ToolCallParser.extract_text_tool_calls(raw_text)

                if not detected_calls or not tool_registry:
                    break

                tool_round += 1
                logger.info(f"[process_input] Tool round {tool_round}: detected {len(detected_calls)} tool call(s)")

                if stream and stream_renderer:
                    stream_renderer.finish(raw_text)

                tool_results_for_round: list[tuple[dict[str, Any], Any]] = []
                for call in detected_calls:
                    t_name = call.get("name") or call.get("function", {}).get("name", "unknown")
                    t_args = call.get("arguments") or {}
                    if isinstance(t_args, str):
                        try:
                            t_args = json.loads(t_args)
                        except Exception:
                            t_args = {"command": t_args} if t_name == "execute_command" else {"raw": t_args}
                    if not isinstance(t_args, dict):
                        t_args = {}

                    # Ensure execute_command defaults cwd to active_workspace if set
                    if (
                        t_name == "execute_command"
                        and active_workspace
                        and (not t_args.get("cwd") or t_args.get("cwd") == ".")
                    ):
                        t_args["cwd"] = str(active_workspace)

                    # Ensure workspace-aware tools receive the active workspace
                    _workspace_tools = {
                        "web_search",
                        "file_search",
                        "file_write",
                        "file_patch",
                        "file_move",
                        "file_delete",
                        "dir_create",
                        "file_stat",
                        "count_lines",
                        "git_status",
                        "git_diff",
                        "json_query",
                        "graphify_ingest",
                        "graphify_query",
                        "memory_recall",
                        "memory_remember",
                        "memory_capture",
                        "memory_rank_files",
                        "vault_boot",
                        "vault_graph_build",
                        "vault_neighbours",
                        "vault_explain",
                        "vault_health",
                        "vault_search",
                        "vault_read",
                        "vault_recall",
                        "vault_write",
                    }
                    if (
                        t_name in _workspace_tools
                        and active_workspace
                        and not t_args.get("workspace_path")
                    ):
                        t_args["workspace_path"] = str(active_workspace)

                    # Ensure relative file paths default to active_workspace if set
                    if active_workspace:
                        for p_key in ("file_path", "path", "dir_path"):
                            if p_key in t_args and isinstance(t_args[p_key], str):
                                raw_p = t_args[p_key].strip()
                                if not Path(raw_p).is_absolute():
                                    cand = active_workspace / raw_p
                                    if cand.exists() or not Path(raw_p).exists():
                                        t_args[p_key] = str(cand)

                    # Display clean tool execution badge
                    summary_arg = ""
                    if t_name == "execute_command":
                        cmd = str(t_args.get("command", ""))
                        summary_arg = f"`{cmd[:60]}...`" if len(cmd) > 60 else f"`{cmd}`"
                    elif "path" in t_args:
                        summary_arg = str(t_args["path"])
                    elif "file_path" in t_args:
                        summary_arg = str(t_args["file_path"])

                    console.print(
                        f"  [bold bright_cyan]⚡ Eksekusi Tool ({tool_round}):[/bold bright_cyan] "
                        f"[bold white]{t_name}[/bold white] [dim]{summary_arg}[/dim]"
                    )

                    t0 = time.time()
                    try:
                        # Handle inline tool patterns like replace:<path> or file:<path>
                        if t_name.startswith("replace:") and active_workspace:
                            rel_file = t_name.split(":", 1)[1].strip()
                            file_p = active_workspace / rel_file
                            s_text = t_args.get("search") or t_args.get("target") or ""
                            r_text = t_args.get("replace") or t_args.get("replacement") or t_args.get("content") or ""
                            raw_diff = str(t_args.get("raw") or t_args.get("diff") or "")
                            if not s_text and "<<<<<<< SEARCH" in raw_diff:
                                parts = raw_diff.split("=======")
                                if len(parts) == 2:
                                    s_text = parts[0].replace("<<<<<<< SEARCH", "").strip()
                                    r_text = parts[1].replace(">>>>>>>", "").strip()
                            editor = FileEditor(backup_before_edit=True)
                            if s_text:
                                res_edit = editor.replace_content(file_p, s_text, r_text)
                                res = {"success": res_edit.success, "message": res_edit.message, "file": rel_file}
                            else:
                                res_edit = editor.create_file(file_p, r_text or raw_diff, overwrite=True)
                                res = {"success": res_edit.success, "message": res_edit.message, "file": rel_file}
                        elif t_name.startswith("file:") and active_workspace:
                            rel_file = t_name.split(":", 1)[1].strip()
                            file_p = active_workspace / rel_file
                            content = str(t_args.get("content") or t_args.get("raw") or "")
                            editor = FileEditor(backup_before_edit=True)
                            res_edit = editor.create_file(file_p, content, overwrite=True)
                            res = {"success": res_edit.success, "message": res_edit.message, "file": rel_file}
                        else:
                            res = await tool_registry.execute(t_name, t_args)

                        elapsed_ms = int((time.time() - t0) * 1000)
                        if isinstance(res, dict) and not res.get("success", True):
                            err_msg = res.get("error", "Tool reported failure")
                            console.print(f"  [dim yellow]⚠️  Tool {t_name}: {err_msg} ({elapsed_ms}ms)[/dim yellow]")
                        else:
                            console.print(f"  [dim green]✔ Tool {t_name} selesai ({elapsed_ms}ms)[/dim green]")
                        tool_results_for_round.append((call, res))
                    except Exception as err:
                        elapsed_ms = int((time.time() - t0) * 1000)
                        console.print(f"  [dim red]✘ Tool {t_name} gagal ({elapsed_ms}ms): {err}[/dim red]")
                        tool_results_for_round.append((call, f"Error: Tool '{t_name}' failed: {err}"))

                # Append assistant call and tool results to active_messages
                if parsed.has_tool_calls:
                    formatted_tool_calls = []
                    for tc in parsed.tool_calls:
                        t_id = tc.get("id") or f"call_{tool_round}"
                        t_fn = tc.get("function")
                        if isinstance(t_fn, dict):
                            fn_name = t_fn.get("name") or tc.get("name", "")
                            fn_args = t_fn.get("arguments", "{}")
                        else:
                            fn_name = tc.get("name", "")
                            fn_args = tc.get("arguments", {})
                        if not isinstance(fn_args, str):
                            fn_args = json.dumps(fn_args, ensure_ascii=False)
                        formatted_tool_calls.append({
                            "id": t_id,
                            "type": "function",
                            "function": {
                                "name": fn_name,
                                "arguments": fn_args,
                            },
                        })

                    active_messages.append({
                        "role": "assistant",
                        "content": parsed.content or None,
                        "tool_calls": formatted_tool_calls,
                    })
                    for call, res in tool_results_for_round:
                        c_id = call.get("id") or f"call_{tool_round}"
                        res_str = json.dumps(res, ensure_ascii=False) if isinstance(res, (dict, list)) else str(res)
                        active_messages.append({
                            "role": "tool",
                            "tool_call_id": c_id,
                            "content": res_str,
                        })
                else:
                    active_messages.append({
                        "role": "assistant",
                        "content": raw_text,
                    })
                    res_blocks = []
                    for call, res in tool_results_for_round:
                        c_name = call.get("name", "tool")
                        res_str = json.dumps(res, ensure_ascii=False) if isinstance(res, (dict, list)) else str(res)
                        res_blocks.append(f"[TOOL RESULT for {c_name}]:\n{res_str}")
                    active_messages.append({
                        "role": "user",
                        "content": "\n\n".join(res_blocks) + "\n\nLanjutkan analisis dan respons Anda berdasarkan hasil eksekusi tool di atas.",
                    })

                # Re-invoke model with updated messages
                parsed, content_parts = await _execute_single_call(active_messages, show_stream=True)

            # If tool execution reached max_tool_rounds and content is still empty (model was mid-execution),
            # give it one clean final round to summarize what it has done.
            if tool_round >= max_tool_rounds and not "".join(content_parts).strip():
                logger.info(f"[process_input] Reached max_tool_rounds ({max_tool_rounds}), asking model for final summary")
                console.print(
                    "\n  [dim yellow]⚡ Batas putaran tool tercapai — meminta model memberikan ringkasan akhir...[/dim yellow]"
                )
                active_messages.append({
                    "role": "user",
                    "content": (
                        "Batas putaran eksekusi tool telah tercapai. Tolong berikan ringkasan dan "
                        "kesimpulan dari pekerjaan yang telah Anda selesaikan kepada user."
                    ),
                })
                parsed, round_parts = await _execute_single_call(
                    active_messages, show_stream=True, use_tools=False
                )
                if round_parts:
                    content_parts = list(round_parts)

            # Auto-continue engine for thinking or truncation
            max_continue_rounds = 5
            continue_round = 0
            continue_messages = list(active_messages)
            _seen_content_hashes: set[int] = set()  # detect AI repeating itself

            # Case 1: Content kosong, thinking penuh → DeepSeek pattern.
            # JANGAN paksa jawaban akhir saat model masih bekerja / baru mulai:
            # - finish_reason="length" → thinking TERPUTUS di tengah (bukan selesai)
            #   → kirim thinking kembali + minta lanjut dari titik terputus.
            # - finish_reason="stop" → model mengakhiri turn setelah thinking
            #   → minta ia melanjutkan pekerjaan dan menulis hasilnya.
            if not "".join(content_parts).strip() and parsed.thinking.strip():
                # Thinking dikirim kembali sebagai konteks assistant supaya
                # model MELANJUTKAN dari titik berhenti, bukan berpikir ulang
                # dari awal (hemat token, tidak prematur).
                continue_messages.append({
                    "role": "assistant",
                    "content": f"<thinking>\n{parsed.thinking.strip()}\n</thinking>",
                })
                if parsed.finish_reason == "length":
                    console.print(
                        "\n  [dim yellow]⚡ Thinking terpotong di tengah — "
                        "melanjutkan dari titik terputus...[/dim yellow]"
                    )
                    continue_messages.append({
                        "role": "user",
                        "content": (
                            "Output kamu terpotong di tengah proses di atas. "
                            "Lanjutkan tepat dari titik terputus — selesaikan "
                            "analisisnya lalu tulis respons lengkapnya. "
                            "Jangan ulangi dari awal."
                        ),
                    })
                else:
                    console.print(
                        "\n  [dim yellow]⚡ Thinking selesai tapi belum ada output — "
                        "meminta model melanjutkan pekerjaan...[/dim yellow]"
                    )
                    continue_messages.append({
                        "role": "user",
                        "content": (
                            "Lanjutkan pekerjaan kamu: selesaikan analisis yang "
                            "belum selesai, lalu tulis hasilnya sebagai respons "
                            "lengkap untuk user. Jangan mengarang isi berkas "
                            "yang belum kamu baca."
                        ),
                    })
                parsed, round_parts = await _execute_single_call(
                    continue_messages, show_stream=True, use_tools=False
                )
                continue_round += 1
                if round_parts:
                    content_parts = list(round_parts)

            # Case 2: Normal auto-continue jika finish_reason == "length"
            last_partial = "".join(content_parts)

            while parsed.finish_reason == "length" and continue_round < max_continue_rounds:
                if not last_partial.strip():
                    # Content kosong di tengah auto-continue. Jika thinking ada,
                    # model TERPUTUS di tengah berpikir (bukan selesai) → kirim
                    # thinking kembali + minta lanjut dari titik terputus.
                    # Jangan paksa jawaban prematur, jangan berhenti diam-diam
                    # (fix "stop sendiri" + "paksa selesai padahal baru mulai").
                    if parsed.thinking.strip() and continue_round < max_continue_rounds - 1:
                        console.print(
                            "\n  [dim yellow]⚡ Thinking terpotong — "
                            "melanjutkan dari titik terputus...[/dim yellow]"
                        )
                        continue_messages.append({
                            "role": "assistant",
                            "content": f"<thinking>\n{parsed.thinking.strip()}\n</thinking>",
                        })
                        continue_messages.append({
                            "role": "user",
                            "content": (
                                "Output kamu terpotong di tengah proses di atas. "
                                "Lanjutkan tepat dari titik terputus — selesaikan "
                                "analisisnya lalu tulis respons lengkapnya. "
                                "Jangan ulangi dari awal."
                            ),
                        })
                        parsed, round_parts_c = await _execute_single_call(
                            continue_messages, show_stream=True, use_tools=False
                        )
                        continue_round += 1
                        last_partial = "".join(round_parts_c)
                        if last_partial.strip():
                            content_parts.extend(round_parts_c)
                            continue
                    console.print(
                        "\n  [dim yellow]⚠️  Content kosong — tidak bisa melanjutkan.[/dim yellow]"
                    )
                    break

                continue_round += 1
                console.print(
                    f"\n  [dim yellow]⚡ Response terpotong — melanjutkan otomatis "
                    f"({continue_round}/{max_continue_rounds})...[/dim yellow]"
                )

                # Kirim potongan terakhir (max 2000 chars) agar AI tahu
                # konteks tanpa kirim seluruh output sebelumnya (hemat token)
                tail_for_context = last_partial[-2000:] if len(last_partial) > 2000 else last_partial
                continue_messages.append({"role": "assistant", "content": tail_for_context})
                continue_messages.append({
                    "role": "user",
                    "content": (
                        "Lanjutkan tepat dari bagian terakhir yang terputus. "
                        "Jangan ulangi dari awal, langsung sambungkan. "
                        "DILARANG menulis ulang kode/teks yang sudah ada di atas."
                    ),
                })

                parsed, round_parts_c = await _execute_single_call(continue_messages, show_stream=True)
                last_partial = "".join(round_parts_c)

                # Deteksi AI ngulang hal yang sama → stop boros token
                if last_partial.strip():
                    content_hash = hash(last_partial.strip()[:500])
                    if content_hash in _seen_content_hashes:
                        console.print(
                            "\n  [dim red]⚠️  AI mengulang konten yang sama — "
                            "menghentikan auto-continue untuk hemat token.[/dim red]"
                        )
                        break
                    _seen_content_hashes.add(content_hash)

                if round_parts_c:
                    content_parts.append("\n")
                    content_parts.extend(round_parts_c)

            if parsed.finish_reason == "length" and continue_round >= max_continue_rounds:
                console.print(
                    f"\n  [dim red]⚠️  Respons masih terpotong setelah {max_continue_rounds}x lanjut. "
                    f"Coba ketik 'continue' untuk melanjutkan.[/dim red]"
                )
                if _ws_memory:
                    full_output = "".join(content_parts)
                    _ws_memory.save_truncation(
                        partial_content=full_output,
                        model_id=model.id,
                    )
                    # Simpan progress agar model berikutnya tahu di mana berhenti
                    _ws_memory.record_progress(
                        phase="auto-continue",
                        status="in_progress",
                        detail=f"Terpotong setelah {continue_round}x continue, "
                               f"model={model.id}, output={len(full_output)} chars",
                    )
                    # Save smart code buffer via continuation memory
                    if _cont_memory:
                        _cont_memory.analyze_and_save(
                            response_text=full_output,
                            model_id=model.id,
                            is_truncated=True,
                        )
                    console.print(
                        f"  [dim cyan]💾 Truncation buffer saved ({len(full_output)} chars). "
                        f"Ketik 'continue' untuk melanjutkan.[/dim cyan]"
                    )

            # 7. Post-process
            parsed = handler.post_process(parsed)

            raw_final = "".join(content_parts) if content_parts else (parsed.content or "")
            # Safety fallback: if content is still empty but thinking has substance,
            # use thinking text as fallback so the terminal never returns a blank empty response!
            if not raw_final.strip() and parsed.thinking and parsed.thinking.strip():
                logger.info("[process_input] Content was empty; using parsed.thinking as fallback response")
                raw_final = parsed.thinking.strip()

            final_text = ToolCallParser.clean_text_tool_calls(raw_final)

            # If final_text is STILL empty after all fallbacks within the try block:
            if not final_text.strip():
                if tool_round > 0:
                    final_text = f"*(Selesai mengeksekusi {tool_round} putaran tool pada workspace)*"
                else:
                    raise RuntimeError(
                        f"Model '{model.id}' returned empty content — advancing fallback chain"
                    )

            if stream and stream_renderer:
                stream_renderer.finish(final_text)
                # If content was never streamed to console (e.g. model outputted only thinking/fallback),
                # render the final fallback text now so user sees it!
                if not stream_renderer.response_header_shown and final_text:
                    render_terminal_markdown(
                        final_text,
                        console=console,
                        config=cfg,
                        tracker=SESSION_CODE_TRACKER,
                    )
            elif not stream:
                render_terminal_markdown(
                    final_text,
                    console=console,
                    config=cfg,
                    tracker=SESSION_CODE_TRACKER,
                )

            # 8. Store in memory (store clean user_input)
            # Save last response for cross-model continuity
            if _ws_memory:
                _ws_memory.save_last_response(final_text, model_id=model.id)

            # Auto-analyze response & save continuation memory
            if _cont_memory:
                _cont_memory.analyze_and_save(
                    response_text=final_text,
                    model_id=model.id,
                    is_truncated=(parsed.finish_reason == "length"),
                )

            conversation_store.add_message(
                session_id, "user", user_input
            )
            conversation_store.add_message(
                session_id, "assistant", final_text,
                model_used=model.id,
                token_count=parsed.completion_tokens,
            )
            await _distill_cli_session(conversation_store, session_id, active_workspace)

            # 9. Record health
            health_checker.record_success(model.id, 0)

            # 10. Display stats with themed output
            console.print(
                f"\n  [dim]📊 tokens: in={parsed.prompt_tokens} "
                f"out={parsed.completion_tokens} | "
                f"model={model.id}[/dim]"
            )

            # 11. Automatically apply file changes to workspace if active
            if active_workspace and active_workspace.is_dir():
                actions = _apply_workspace_changes(active_workspace, final_text)
                if actions:
                    console.print(f"  [dim]✨ Workspace synchronized: {len(actions)} file action(s) applied[/dim]")

                # Record to workspace memory
                if _ws_memory:
                    _ws_memory.record_model(model.id)
                    for act in actions:
                        # Extract filename from action string
                        parts = act.split(": ", 1)
                        if len(parts) > 1:
                            _ws_memory.record_file_edit(
                                parts[1].strip(),
                                action="created" if "💾" in act else "edited",
                                summary=act,
                            )
                    # Clear truncation buffer on success (output not truncated)
                    if parsed.finish_reason != "length":
                        _ws_memory.clear_truncation()
                        if _cont_memory:
                            _cont_memory.clear_code_buffer()
                        # Auto-capture decisions/facts from this turn
                        try:
                            from brain.memory.smart_memory import SmartMemory

                            _smart_auto = SmartMemory(
                                active_workspace, workspace_memory=_ws_memory
                            )
                            _smart_auto.capture_from_turn(
                                user_input=user_input,
                                assistant_output=final_text or "",
                                model_id=getattr(model, "id", "") or "",
                            )
                        except Exception as exc:  # noqa: BLE001
                            # Was `pass`, which hid a NameError for every turn:
                            # `response_text` and `model_config` are not in scope
                            # inside process_input. Log so it can never go silent
                            # again.
                            logger.debug("[smart_memory] auto-capture skipped: %s", exc)

                        _ws_memory.record_progress(
                            phase="response",
                            status="done",
                            detail=f"Selesai, model={model.id}, "
                                   f"output={len(final_text)} chars",
                        )

            return final_text

        except Exception as e:
            health_checker.record_failure(model.id)
            console.print(f"\n[red]❌ Error: {escape(str(e))}[/red]")

            if _ws_memory:
                _ws_memory.record_error(f"{model.id}: {str(e)[:150]}")

            # Walk the fallback chain and previous working model
            current_failed = model.id
            fallback_attempt = 0
            candidate_models: list[ModelInfo] = []

            # 1. Collect chain from FallbackStrategy
            while True:
                next_fb = fallback.get_next(
                    classification.task_type, current_failed, session_id
                )
                if not next_fb:
                    break
                if next_fb not in candidate_models:
                    candidate_models.append(next_fb)
                current_failed = next_fb.id

            # 2. Add previous working model if known and not already in candidates
            tried_ids = fallback._tried.get(session_id, set())
            prev_model_id = (
                previous_model
                or conversation_store.get_last_used_model(session_id)
                or (_ws_memory.get_last_model() if _ws_memory else None)
            )
            if prev_model_id and prev_model_id not in tried_ids and prev_model_id != model.id:
                prev_info = registry.get(prev_model_id) if registry else None
                if prev_info and prev_info not in candidate_models:
                    candidate_models.append(prev_info)

            # 3. Add default model as final safety net if not already tried
            default_id = getattr(router, "_default_model", "ag/gemini-3.8-flash-high")
            if default_id and default_id not in tried_ids and default_id != model.id:
                def_info = registry.get(default_id) if registry else None
                if def_info and def_info not in candidate_models:
                    candidate_models.append(def_info)

            # Now try each candidate model
            for fb_model in candidate_models:
                fallback_attempt += 1
                fb_theme = _get_provider_theme(fb_model.provider)
                console.print(
                    f"  [yellow]🔄 Fallback #{fallback_attempt}: "
                    f"{fb_theme['icon']} {fb_model.id}[/yellow]"
                )

                if _ws_memory:
                    _ws_memory.record_model(fb_model.id)

                fb_tools = tools_payload if (fb_model.supports_tools and tools_payload) else None

                try:
                    raw_response = await api_client.chat_completion(
                        model=fb_model.id,
                        messages=ctx.messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=fb_tools,
                    )
                    fb_parsed = ResponseHandler.handle(raw_response)
                    raw_fb_final = fb_parsed.content or ""
                    if not raw_fb_final.strip() and fb_parsed.thinking and fb_parsed.thinking.strip():
                        logger.info("[fallback] Content was empty; using parsed.thinking as fallback response")
                        raw_fb_final = fb_parsed.thinking.strip()
                    final_text = ToolCallParser.clean_text_tool_calls(raw_fb_final)

                    if not final_text.strip():
                        if fb_parsed.tool_calls:
                            final_text = f"*(Fallback model {fb_model.id} requested {len(fb_parsed.tool_calls)} tool calls)*"
                        else:
                            logger.warning(f"[process_input] Fallback {fb_model.id} returned empty; trying next candidate")
                            health_checker.record_failure(fb_model.id)
                            continue

                    # Success! Apply changes to workspace
                    if active_workspace and active_workspace.is_dir():
                        fb_actions = _apply_workspace_changes(active_workspace, final_text)
                        if fb_actions and _ws_memory:
                            for act in fb_actions:
                                parts = act.split(": ", 1)
                                if len(parts) > 1:
                                    _ws_memory.record_file_edit(
                                        parts[1].strip(),
                                        action="created" if "💾" in act else "edited",
                                        summary=act,
                                    )

                    render_terminal_markdown(
                        final_text,
                        console=console,
                        config=cfg,
                        tracker=SESSION_CODE_TRACKER,
                    )

                    # Save memory
                    if _ws_memory:
                        _ws_memory.save_last_response(final_text, model_id=fb_model.id)
                        _ws_memory.record_progress(
                            phase="response", status="done",
                            detail=f"Fallback ok, model={fb_model.id}, output={len(final_text)} chars",
                        )
                    if _cont_memory:
                        _cont_memory.analyze_and_save(
                            response_text=final_text,
                            model_id=fb_model.id,
                            is_truncated=(fb_parsed.finish_reason == "length"),
                        )
                        if fb_parsed.finish_reason != "length":
                            _cont_memory.clear_code_buffer()

                    conversation_store.add_message(session_id, "user", user_input)
                    conversation_store.add_message(
                        session_id, "assistant", final_text,
                        model_used=fb_model.id,
                        token_count=fb_parsed.completion_tokens,
                    )
                    await _distill_cli_session(
                        conversation_store, session_id, active_workspace
                    )
                    health_checker.record_success(fb_model.id, 0)
                    console.print(
                        f"\n  [dim]📊 fallback model={fb_model.id} | attempt #{fallback_attempt}[/dim]"
                    )
                    return final_text

                except Exception as fb_err:
                    logger.warning(f"[process_input] Fallback {fb_model.id} failed: {fb_err}")
                    health_checker.record_failure(fb_model.id)
                    if _ws_memory:
                        _ws_memory.record_error(f"{fb_model.id}: {str(fb_err)[:150]}")
                    console.print(
                        f"  [red]❌ Fallback {fb_model.id} error: {escape(str(fb_err))}[/red]"
                    )
                    continue

            # If all fallbacks failed
            console.print("\n[bold red]⚠️ Semua opsi model cadangan gagal merespons.[/bold red]")
            return (
                f"Maaf, model '{model.id}' dan seluruh opsi fallback cadangan mengalami kendala: {e}. "
                "Sesi tetap aktif — Anda dapat mencoba kembali atau memilih model lain dengan perintah /model."
            )


async def chat_mode() -> None:
    """Interactive chat REPL with IDE features and Workspace Project support."""
    settings = get_settings()
    secrets = get_secrets()

    # Initialize components
    ModelRegistry.reset_singleton()
    registry = ModelRegistry()
    registry.load_from_config(settings.models_config_path)

    api_client = APIClient(
        settings.api_base_url, secrets.api_key, int(settings.timeout_seconds)
    )

    # Fetch full models from 9Router and register Gemini, Kagiro, and Claude models
    # Auto-fetch /models from every endpoint (main router + Bansos) and
    # register everything, so new models appear without editing config.
    synced = await api_client.sync_all_models(registry)
    logger.info(f"[main] Auto-synced models: {synced}")
    classifier = IntentClassifier()
    router = RoutingType(config_path=settings.models_config_path, registry=registry)
    conversation_store = ConversationStore()
    think_prompt_mgr = ThinkPromptManager()
    context_assembler = ContextAssembler(conversation_store, think_prompt_mgr)
    health_checker = HealthChecker()
    fallback = FallbackStrategy(config_path=settings.models_config_path, registry=registry)

    # Register AI tools (read_file, execute_command, list_directory, discard_changes)
    tool_registry = register_all_tools()
    logger.info(f"[main] Registered {tool_registry.tool_count} tools: {tool_registry.get_all_names()}")

    # Build skills-awareness prompt (injected into system prompt every turn)
    skills_prompt = build_skills_prompt(tool_registry)
    logger.info(f"[main] Skills prompt built: {len(skills_prompt)} chars")

    # Initialize persistent memory
    persistent_memory = PersistentMemory(storage_dir=settings.brain_dir / "memory" / "store")
    logger.info("[main] Persistent memory initialized")

    # Load cross-session memory: try to carry over context from last session
    _startup_memory = ""
    try:
        prev_sessions = persistent_memory.list_sessions()
        if prev_sessions:
            latest_session = prev_sessions[-1]
            _startup_memory = persistent_memory.get_memory_summary(latest_session)
            if _startup_memory:
                logger.info(f"[main] Loaded cross-session memory from '{latest_session}': {len(_startup_memory)} chars")
    except Exception as e:
        logger.warning(f"[main] Could not load cross-session memory: {e}")

    # Initialize 9Router graphify client (optional, fallback)
    _graphify_client = GraphifyClient(
        router_url=settings.api_base_url,
        api_key=secrets.api_key,
        default_model=settings.default_model,
        timeout=int(settings.timeout_seconds),
    )
    _ = _graphify_client
    logger.info("[main] GraphifyClient initialized")

    session_id = generate_session_id()
    project_root = settings.project_root

    # Terminal CLI Formatting Config (Markdown, Minimizer, Custom Theme)
    terminal_cfg_path = settings.config_dir / "terminal_format.json"
    terminal_config = TerminalFormatConfig.load(terminal_cfg_path)

    # Workspace directory
    workspace_dir = project_root / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    active_workspace: Path | None = None

    # Track user-selected model override and previously working model
    override_model: str | None = None
    last_successful_model: str | None = None

    # Count models by provider for banner
    providers: dict[str, int] = {}
    for m in registry.get_all():
        p = m.provider or "unknown"
        providers[p] = providers.get(p, 0) + 1
    provider_summary = ", ".join(f"{p}: {c}" for p, c in sorted(providers.items()))

    # Welcome banner
    banner_lines = [
        "[bold bright_cyan]🤖 Agent Router — Multi-Model AI IDE & Workspace[/bold bright_cyan]",
        "",
        f"[dim]Session:[/dim]    {session_id}",
        f"[dim]Models:[/dim]     {registry.count} total ({provider_summary})",
        f"[dim]Workspace:[/dim]  {workspace_dir}",
        "",
        "[bold bright_green]Perintah Workspace & IDE:[/bold bright_green]",
        "  [bright_green]/tree <tanggal-proyek>[/bright_green] [dim]—[/dim] Buka / buat proyek (contoh: [cyan]/tree 20261009-appcash[/cyan])",
        "  [bright_green]/tree[/bright_green]                 [dim]—[/dim] Lihat file proyek aktif / daftar proyek",
        "  [bright_green]/bansos[/bright_green]               [dim]—[/dim] Mode bansos gratis (DeepSeek V4.1 Flash via Kagiro)",
        "  [bright_green]/model[/bright_green]                [dim]—[/dim] Ganti model AI interaktif (27+ model)",
        "  [bright_green]/view <file>[/bright_green]          [dim]—[/dim] Preview file dengan syntax highlighting",
        "  [bright_green]/code[/bright_green]                 [dim]—[/dim] Toggle minimize / expand blok kode AI (/code list, /code 1)",
        "  [bright_green]/format[/bright_green]               [dim]—[/dim] Kustomisasi format terminal (Markdown, tema, minimizer)",
        "  [bright_green]/search <pola>[/bright_green]        [dim]—[/dim] Cari file di proyek",
        "  [bright_green]/paste[/bright_green]                [dim]—[/dim] Mode multi-line input / paste manual",
        "  [bright_green]/help[/bright_green]                 [dim]—[/dim] Daftar semua perintah",
        "",
        "[dim]AI memiliki otoritas penuh untuk membuat, membaca, dan mengedit file di workspace proyek aktif.[/dim]",
        "[dim]Ketik pesan untuk chat, 'clear' untuk reset histori, 'exit' untuk keluar.[/dim]",
    ]

    console.print(Panel(
        "\n".join(banner_lines),
        border_style="bright_cyan",
        padding=(1, 3),
    ))

    try:
        while True:
            console.print()

            # Build prompt with model & workspace indicators
            ws_tag = f" | 📁 {active_workspace.name}" if active_workspace else ""
            if override_model:
                model_info = registry.get(override_model)
                theme = _get_provider_theme(model_info.provider if model_info else "")
                prompt = (
                    f"[bold green]You[/bold green] "
                    f"[dim]({theme['icon']} {override_model}{ws_tag})[/dim] [bold green]>[/bold green] "
                )
            else:
                prompt = f"[bold green]You[/bold green] [dim](auto{ws_tag})[/dim] [bold green]>[/bold green] "

            try:
                user_input = _read_user_input(prompt)
            except (EOFError, KeyboardInterrupt):
                break

            user_input = user_input.strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                break
            if user_input.lower() == "clear":
                conversation_store.clear_session(session_id)
                console.print("[dim]Histori obrolan berhasil dibersihkan. ✨[/dim]")
                continue

            # Dedicated multi-line paste command
            if user_input.lower() in ("/paste", "/multi", "/multiline"):
                console.print("\n  [bold cyan]📋 Mode Multi-line / Paste Aktif[/bold cyan]")
                console.print("  [dim]Paste teks Anda di bawah ini, lalu ketik [bold white]END[/bold white] di baris baru untuk kirim (atau Ctrl+D):[/dim]\n")
                paste_lines = []
                try:
                    while True:
                        line = input()
                        if line.strip() == "END":
                            break
                        paste_lines.append(line)
                except (EOFError, KeyboardInterrupt):
                    pass
                user_input = "\n".join(paste_lines).strip()
                if not user_input:
                    console.print("  [yellow]Paste dibatalkan (teks kosong).[/yellow]")
                    continue
                console.print(f"  [dim green]✓ {len(paste_lines)} baris berhasil dimuat. Mengirim ke AI...[/dim green]")

            # Handle IDE slash commands
            elif user_input.startswith("/"):
                handled, new_model, new_ws = _handle_ide_command(
                    user_input[1:],
                    registry,
                    project_root,
                    workspace_dir,
                    override_model,
                    active_workspace,
                    terminal_config,
                    terminal_cfg_path,
                )
                if handled:
                    if new_model is not None:
                        # Record model switch in memory
                        if override_model and new_model != override_model:
                            last_successful_model = override_model
                            persistent_memory.record_model_switch(
                                session_id,
                                from_model=override_model or "auto",
                                to_model=new_model,
                                reason="User switched model via /model",
                            )
                            # Show memory summary for new model
                            mem_summary = persistent_memory.get_memory_summary(session_id)
                            if mem_summary:
                                console.print(f"\n  [dim cyan]🧠 Memory loaded: {len(mem_summary)} chars context from previous model[/dim cyan]")
                        override_model = new_model
                    if new_ws != active_workspace:
                        active_workspace = new_ws
                    continue

            # Inject memory summary: current session first, then cross-session
            memory_context = ""
            mem_summary = persistent_memory.get_memory_summary(session_id)
            if mem_summary:
                memory_context = mem_summary
            elif _startup_memory:
                # No current session memory yet — use last session's memory
                memory_context = _startup_memory

            # Process the message
            console.print()
            console.print("[bold bright_cyan]Agent >[/bold bright_cyan]", end=" ")
            try:
                response_text = await process_input(
                    user_input=user_input,
                    session_id=session_id,
                    api_client=api_client,
                    classifier=classifier,
                    router=router,
                    context_assembler=context_assembler,
                    conversation_store=conversation_store,
                    health_checker=health_checker,
                    fallback=fallback,
                    stream=settings.stream_enabled,
                    override_model=override_model,
                    registry=registry,
                    active_workspace=active_workspace,
                    memory_context=memory_context,
                    tool_registry=tool_registry,
                    skills_prompt=skills_prompt,
                    terminal_config=terminal_config,
                    previous_model=last_successful_model,
                )
                if response_text and not response_text.startswith("Maaf, model"):
                    last_successful_model = override_model or conversation_store.get_last_used_model(session_id)
            except (KeyboardInterrupt, asyncio.CancelledError):
                # Ctrl+C must not print a traceback or kill the session. Note
                # that CancelledError and KeyboardInterrupt are BaseException
                # subclasses, so a plain ``except Exception`` would miss them.
                logger.info("[chat_mode] turn interrupted by user")
                console.print()
                console.print("[dim]⏹ Dihentikan (Ctrl+C). Sesi tetap aktif.[/dim]")
                response_text = ""
            except Exception as proc_err:
                logger.exception(f"[chat_mode] Unhandled error during process_input: {proc_err}")
                console.print(f"\n[red]❌ Terjadi kendala: {escape(str(proc_err))}[/red]")
                console.print("[dim]Sesi tetap aktif. Silakan lanjutkan pesan Anda atau ganti model dengan /model.[/dim]")
                response_text = ""

            # Record memory: file edits from AI response
            if active_workspace and response_text:
                _record_file_changes_to_memory(
                    persistent_memory, session_id, active_workspace, response_text
                )
            # ──── AI SELESAI ────────────────────────────────────────────────
            # Flush stdin buffer: buang semua keystroke yang tidak sengaja
            # diketik user selama AI sedang streaming (jeda panjang).
            # Tanpa ini, Enter/karakter yang terketik saat streaming akan
            # langsung dikonsumsi console.input() berikutnya → re-trigger.
            _flush_stdin_buffer()
            console.print(
                "[dim]─────────────────────── ✅ Selesai — ketik balasan Anda ───────────────────────[/dim]"
            )
    finally:
        # The client may already be mid-teardown when the user interrupts the
        # loop; a second interrupt here would replace a clean exit with a
        # traceback.
        try:
            await api_client.close()
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("[chat_mode] client close interrupted")
        except Exception as exc:  # noqa: BLE001 - shutdown must not raise
            logger.warning("[chat_mode] client close failed: %s", exc)
        console.print("\n[dim]Goodbye! 👋[/dim]")


async def list_models_cmd() -> None:
    """List all available models with rich display."""
    settings = get_settings()

    # Load from config
    ModelRegistry.reset_singleton()
    registry = ModelRegistry()
    registry.load_from_config(settings.models_config_path)

    # Also try to fetch from API
    try:
        secrets = get_secrets()
        async with APIClient(settings.api_base_url, secrets.api_key) as client:
            raw_models = await client.list_models()
        api_model_ids = {m["id"] for m in raw_models}
        if raw_models:
            registry.register_from_api_models(raw_models)
    except Exception:
        raw_models = []
        api_model_ids = set()

    models = registry.get_all()

    # Group by provider
    by_provider: dict[str, list[ModelInfo]] = {}
    for m in models:
        by_provider.setdefault(m.provider or "unknown", []).append(m)

    console.print()
    console.print(Rule("[bold bright_cyan]🤖 Available Models[/bold bright_cyan]", style="cyan"))
    console.print()

    for provider, provider_models in sorted(by_provider.items()):
        theme = _get_provider_theme(provider)

        table = Table(
            title=f"{theme['icon']} {theme['label']} Provider ({provider}/)",
            title_style=f"bold {theme['color']}",
            border_style=theme["color"],
            box=box.ROUNDED,
            show_lines=False,
            expand=True,
        )

        table.add_column("Model ID", style="bold")
        table.add_column("Display Name")
        table.add_column("Vision", width=6, justify="center")
        table.add_column("Audio", width=6, justify="center")
        table.add_column("Video", width=6, justify="center")
        table.add_column("Search", width=6, justify="center")
        table.add_column("Tools", width=6, justify="center")
        table.add_column("Thinking", width=16)
        table.add_column("Context", justify="right")
        table.add_column("Max Out", justify="right")

        for m in sorted(provider_models, key=lambda x: x.id):
            api_status = "🟢" if m.id in api_model_ids else "⚪"

            table.add_row(
                f"{api_status} {m.id}",
                m.display_name,
                "✅" if m.supports_vision else "❌",
                "✅" if m.supports_audio else "❌",
                "✅" if m.supports_video else "❌",
                "✅" if m.supports_search else "❌",
                "✅" if m.supports_tools else "❌",
                m.thinking_format or "—",
                format_tokens(m.context_window),
                format_tokens(m.max_output),
            )

        console.print(table)
        console.print()

    console.print(f"[dim]Total: {len(models)} models across {len(by_provider)} providers[/dim]")
    console.print("[dim]🟢 = Available via API  ⚪ = Config only[/dim]")
    console.print()


async def health_cmd() -> None:
    """Check API health."""
    settings = get_settings()
    secrets = get_secrets()

    async with APIClient(settings.api_base_url, secrets.api_key) as client:
        healthy = await client.health_check()

    if healthy:
        console.print("[green]✅ API is healthy[/green]")
    else:
        console.print("[red]❌ API is not responding[/red]")


async def fetch_models_cmd() -> None:
    """Fetch and sync models from API, highlighting Kagiro Marketplace models."""
    settings = get_settings()
    secrets = get_secrets()

    console.print()
    console.print(Rule("[bold bright_magenta]🟣 Kagiro & API Model Fetcher[/bold bright_magenta]", style="bright_magenta"))
    console.print()

    async with APIClient(settings.api_base_url, secrets.api_key) as client:
        try:
            raw_models = await client.list_models()
            kg_models = await client.list_kg_models()
            claude_models = await client.list_claude_models()
        except Exception as e:
            console.print(f"[bold red]❌ Failed to connect to API:[/bold red] {e}")
            return

    table = Table(
        title=f"🟣 Kagiro Marketplace Models ({len(kg_models)} Available from API)",
        title_style="bold bright_magenta",
        border_style="bright_magenta",
        box=box.ROUNDED,
    )
    table.add_column("Model ID", style="bold")
    table.add_column("Owned By")
    table.add_column("Vision", width=6, justify="center")
    table.add_column("Tools", width=6, justify="center")
    table.add_column("Thinking", width=16)
    table.add_column("Context", justify="right")
    table.add_column("Max Out", justify="right")

    for m in sorted(kg_models, key=lambda x: x.get("id", "")):
        caps = m.get("capabilities", {})
        ctx = m.get("context_length") or caps.get("contextWindow", 0)
        max_out = m.get("max_completion_tokens") or caps.get("maxOutput", 0)
        th_fmt = caps.get("thinkingFormat") or "—"
        table.add_row(
            f"[bright_cyan]{m.get('id')}[/bright_cyan]",
            str(m.get("owned_by", "")),
            "✅" if caps.get("vision") else "❌",
            "✅" if caps.get("tools") else "❌",
            str(th_fmt),
            format_tokens(ctx),
            format_tokens(max_out),
        )

    console.print(table)

    claude_table = Table(
        title=f"🔷 Anthropic Claude Models ({len(claude_models)} Available from API)",
        title_style="bold bright_cyan",
        border_style="bright_cyan",
        box=box.ROUNDED,
    )
    claude_table.add_column("Model ID", style="bold")
    claude_table.add_column("Owned By")
    claude_table.add_column("Vision", width=6, justify="center")
    claude_table.add_column("Tools", width=6, justify="center")
    claude_table.add_column("Thinking", width=16)
    claude_table.add_column("Context", justify="right")
    claude_table.add_column("Max Out", justify="right")

    for m in sorted(claude_models, key=lambda x: x.get("id", "")):
        caps = m.get("capabilities", {})
        ctx = m.get("context_length") or caps.get("contextWindow", 0)
        max_out = m.get("max_completion_tokens") or caps.get("maxOutput", 0)
        th_fmt = caps.get("thinkingFormat") or "—"
        claude_table.add_row(
            f"[bright_yellow]{m.get('id')}[/bright_yellow]",
            str(m.get("owned_by", "")),
            "✅" if caps.get("vision") else "❌",
            "✅" if caps.get("tools") else "❌",
            str(th_fmt),
            format_tokens(ctx),
            format_tokens(max_out),
        )

    console.print()
    console.print(claude_table)
    console.print(
        f"\n[bold green]✔ Successfully fetched {len(kg_models)} Kagiro models and "
        f"{len(claude_models)} Claude models ({len(raw_models)} total models from API router).[/bold green]\n"
    )


async def ask_cmd(question: str, model: str | None = None) -> None:
    """One-shot question mode.

    Args:
        question: The question to ask.
        model: Optional model override.
    """
    settings = get_settings()
    secrets = get_secrets()

    ModelRegistry.reset_singleton()
    registry = ModelRegistry()
    registry.load_from_config(settings.models_config_path)

    api_client = APIClient(
        settings.api_base_url, secrets.api_key, int(settings.timeout_seconds)
    )
    classifier = IntentClassifier()
    router = RoutingType(config_path=settings.models_config_path, registry=registry)
    conversation_store = ConversationStore()
    think_prompt_mgr = ThinkPromptManager()
    context_assembler = ContextAssembler(conversation_store, think_prompt_mgr)
    health_checker = HealthChecker()
    fallback = FallbackStrategy(config_path=settings.models_config_path, registry=registry)

    terminal_cfg_path = settings.config_dir / "terminal_format.json"
    terminal_config = TerminalFormatConfig.load(terminal_cfg_path)

    try:
        result = await process_input(
            user_input=question,
            session_id="oneshot",
            api_client=api_client,
            classifier=classifier,
            router=router,
            context_assembler=context_assembler,
            conversation_store=conversation_store,
            health_checker=health_checker,
            fallback=fallback,
            stream=False,
            override_model=model,
            registry=registry,
            terminal_config=terminal_config,
        )
        if result and not terminal_config.markdown_enabled:
            console.print(result)
    finally:
        await api_client.close()


def _run_async(coro_fn, *fn_args, **fn_kwargs) -> None:
    """Run a coroutine function and exit quietly when the user hits Ctrl+C.

    Without this, ``asyncio.run`` lets ``KeyboardInterrupt`` bubble out of
    ``main()`` and Python prints a full traceback for what is a normal way to
    quit a CLI.
    """
    try:
        asyncio.run(coro_fn(*fn_args, **fn_kwargs))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("[main] interrupted by user")
        console.print("\n[dim]⏹ Dihentikan.[/dim]")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Agent Router — Multi-Model AI Assistant with Workspace IDE",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py chat                     Interactive chat mode with Workspace IDE
  python main.py ask "What is Python?"    One-shot question
  python main.py ask "Explain OOP" --model kg/deepseek-4-1-flash
  python main.py models                   List all models
  python main.py health                   Check API health
  python main.py web                      Local Web GUI at http://127.0.0.1:8080
  python main.py web --tunnel             Web GUI with optional public tunnel
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # chat command
    subparsers.add_parser("chat", help="Interactive chat mode with IDE & Workspace")

    # ask command
    ask_parser = subparsers.add_parser("ask", help="One-shot question")
    ask_parser.add_argument("question", help="Question to ask")
    ask_parser.add_argument(
        "--model", "-m", default=None,
        help="Model to use (e.g., kg/deepseek-4-1-flash, ag/claude-sonnet-4-6)"
    )

    # models command
    subparsers.add_parser("models", help="List available models")

    # fetch command
    subparsers.add_parser("fetch", help="Fetch and inspect models from API router")
    subparsers.add_parser("fetch-models", help="Fetch and inspect models from API router")

    # health command
    subparsers.add_parser("health", help="Check API health")

    # web command
    web_parser = subparsers.add_parser("web", help="Start the Web GUI server")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    web_parser.add_argument("--port", "-p", type=int, default=8080, help="Port (default: 8080)")
    web_parser.add_argument(
        "--tunnel",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Expose the Web GUI publicly through Cloudflare Tunnel (default: disabled)",
    )

    args = parser.parse_args()

    if args.command == "chat" or args.command is None:
        _run_async(chat_mode)
    elif args.command == "ask":
        _run_async(ask_cmd, args.question, getattr(args, "model", None))
    elif args.command == "models":
        _run_async(list_models_cmd)
    elif args.command in ("fetch", "fetch-models"):
        _run_async(fetch_models_cmd)
    elif args.command == "health":
        _run_async(health_cmd)
    elif args.command == "web":
        from rich.panel import Panel

        from utils.tunnel import CloudflareTunnel
        from web.server import run_server

        tunnel = None
        public_url = None
        enable_tunnel = getattr(args, "tunnel", True)

        if enable_tunnel:
            console.print("[bold yellow]🌐 Starting Cloudflare Tunnel...[/bold yellow]")
            tunnel = CloudflareTunnel(port=args.port)
            public_url = tunnel.start()

        local_url = f"http://{args.host}:{args.port}"
        panel_lines = [
            f"[bold green]✔ Local Web GUI:[/bold green]   [underline]{local_url}[/underline]"
        ]
        if public_url:
            panel_lines.append(
                f"[bold cyan]✔ Cloudflare Tunnel:[/bold cyan] [bold underline white]{public_url}[/bold underline white]"
            )
            panel_lines.append("[dim]🔗 Bisa diakses dari luar jaringan / HP tanpa port forwarding.[/dim]")
        elif enable_tunnel:
            panel_lines.append("[yellow]⚠ Cloudflare Tunnel aktif, periksa tunnel.log untuk URL publik.[/yellow]")

        console.print(Panel("\n".join(panel_lines), title="[bold cyan]Agent Router Web Server[/bold cyan]", border_style="cyan"))

        try:
            run_server(host=args.host, port=args.port, enable_tunnel=False)
        finally:
            if tunnel:
                tunnel.stop()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
