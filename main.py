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
import os
import re
import select
import sys
import termios
import time
from collections.abc import Callable
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

from agent import session_stack
from agent.orchestrator import Orchestrator
from brain.memory.project_memory import ProjectMemory
from config.constants import ResponseStatus
from config.secrets import get_secrets
from config.settings import get_settings
from model.connection.api_client import APIClient
from model.registry.model_info import ModelInfo
from model.registry.model_registry import ModelRegistry
from security import sandbox
from security.web_access import is_loopback_host
from tool.execute.execute_edit_change_file.file_editor import FileEditor
from tool.execute.execute_local_memory import user_profile
from utils import cli_ui
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
from utils.session_settings import PONYTAIL_MODES
from utils.workspace_context import set_active_ponytail

logger = get_logger(__name__)
console = Console()

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
    # input() rather than console.input(): readline must draw the prompt itself,
    # or arrow keys and history redraw over it.
    first_line = input(prompt)

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
    banned_chars = set('|<>*\\`#\n\r\t"\'')
    if banned_chars & set(rel_path):
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
            valid_ext = (
                ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json",
                ".md", ".txt", ".yaml", ".yml", ".sh", ".toml", ".cfg", ".ini",
                ".xml", ".csv", ".sql", ".env", ".php", ".rb", ".go", ".rs",
                ".java", ".kt", ".swift", ".c", ".cpp", ".h", ".hpp",
                ".vue", ".svelte", ".scss", ".less", ".lock", ".blade.php",
            )
            if not any(part.endswith(ext) for ext in valid_ext):
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

    elif command in ("stack", "sesi", "session"):
        _display_stack("")
        return True, None, active_workspace

    elif command == "ponytail":
        _display_stack(arg)
        return True, None, active_workspace

    elif command in ("memory", "mem", "memori"):
        _display_memory(arg, active_workspace)
        return True, None, active_workspace

    elif command in ("sandbox", "doctor"):
        _display_doctor()
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
                f"[bold white]Fallback:[/bold white]        [bright_green]Retry otomatis, lalu pindah ke model berikutnya[/bright_green]\n\n"
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
    """Display the available commands, grouped by what they are for."""
    sections = [
        ("Proyek", [
            ("/tree <nama>", "Buka atau buat proyek, misalnya /tree 20261009-appcash"),
            ("/tree, /t", "Struktur file proyek aktif, atau daftar proyek"),
            ("/tree all", "Struktur seluruh repository agent"),
            ("/ws [nama], /workspace", "Daftar proyek, atau pindah proyek"),
            ("/create <file>", "Buat file baru di proyek aktif"),
            ("/view <file>, /v", "Lihat isi file"),
            ("/search <pola>, /s", "Cari file berdasarkan nama"),
        ]),
        ("Model", [
            ("/model, /m", "Pilih model"),
            ("/bansos [on|off|status]", "Mode bansos (model gratis lewat Kagiro)"),
            ("/status, /info, /i", "Model, proyek, sandbox, dan provider aktif"),
        ]),
        ("Memori", [
            ("/memory", "Lihat apa yang diingat tentang kamu dan proyek ini"),
            ("/memory add <teks>", "Ingat sesuatu di semua proyek"),
            ("/memory forget <teks>", "Hapus yang salah atau sudah usang"),
        ]),
        ("Sesi ini", [
            ("/stack", "Folder, vault, graph, skor aislop, dan memori milik chat ini"),
            ("/ponytail <off|lite|full|ultra>", "Ganti mode Ponytail untuk chat ini"),
        ]),
        ("Keamanan", [
            ("/sandbox", "Uji isolasi perintah agent (sama dengan: python main.py doctor)"),
        ]),
        ("Tampilan", [
            ("/code [1|min|full|copy]", "Lipat, buka, atau salin blok kode"),
            ("/format", "Atur Markdown, tema, dan tampilan thinking"),
            ("/theme <nama>", "Tema syntax highlighting (monokai, dracula, nord, ...)"),
            ("/md", "Tampilkan ulang jawaban terakhir sebagai Markdown"),
            ("/paste", "Tempel teks beberapa baris"),
        ]),
        ("Sesi", [
            ("/help, /h, /?", "Bantuan ini"),
            ("clear", "Hapus riwayat obrolan sesi ini"),
            ("exit, quit, q", "Keluar"),
        ]),
    ]
    table = Table(box=box.SIMPLE, expand=True, show_header=False, pad_edge=False)
    table.add_column(style="bold bright_green", min_width=26, no_wrap=True)
    table.add_column(style="white")
    for title, commands in sections:
        table.add_row(f"[bold bright_cyan]{title}[/bold bright_cyan]", "")
        for command, description in commands:
            table.add_row(f"  {command}", description)
        table.add_section()
    console.print()
    console.print(table)


def _display_memory(arg: str, active_workspace: Path | None) -> None:
    """Show or change what the agent remembers."""
    project = ProjectMemory.for_path(active_workspace)
    action, _, text = arg.partition(" ")
    action = action.lower()
    text = text.strip()

    if action == "add" and text:
        if user_profile.remember(text):
            console.print(f"[green]✓ diingat[/green] {escape(text)}")
        else:
            console.print("[yellow]Tidak disimpan: sudah ada, atau terlihat seperti kredensial.[/yellow]")
        return
    if action == "forget" and text:
        removed = user_profile.forget(text) + (project.forget(text) if project else 0)
        console.print(f"[green]✓ {removed} item dihapus[/green]" if removed else "[dim]Tidak ada yang cocok.[/dim]")
        return

    profile = user_profile.entries()
    console.print()
    console.print("[bold bright_cyan]Tentang kamu[/bold bright_cyan] [dim](semua proyek)[/dim]")
    if profile:
        for line in profile:
            console.print(f"  • {escape(line)}")
    else:
        console.print("  [dim]Belum ada. Coba: ingat: saya memakai pnpm[/dim]")
    console.print()
    label = active_workspace.name if active_workspace else "tidak ada proyek aktif"
    console.print(f"[bold bright_cyan]Proyek[/bold bright_cyan] [dim]({escape(label)})[/dim]")
    summary = project.summary() if project else ""
    if summary:
        console.print(escape(summary.replace("[MEMORY]\n", "").replace("\n[/MEMORY]", "")))
    else:
        console.print("  [dim]Belum ada.[/dim]")
    console.print()


def _display_stack(arg: str) -> None:
    """Show what this chat owns, or change its Ponytail mode."""
    stack = session_stack.current()
    if stack is None:
        console.print("[dim]Belum ada sesi aktif.[/dim]")
        return
    if arg:
        if arg not in PONYTAIL_MODES:
            console.print(f"[yellow]Mode tidak dikenal. Pilih: {', '.join(PONYTAIL_MODES)}[/yellow]")
            return
        stack.update(ponytail=arg)
        set_active_ponytail(arg)
        console.print(f"[green]✓ Ponytail sesi ini: {arg}[/green]")
        return
    info = stack.status()
    graph = info["graphify"]
    slop = info["aislop"]
    last = slop.get("last") or {}
    memory = info["memory"]
    graph_text = f"{graph.get('nodes', 0)} node" if graph.get("built") else "belum dibangun"
    rows = [
        ("Folder", info["folder"]),
        ("Ponytail", info["ponytail"]["mode"]),
        ("Graphify", graph_text if graph["installed"] else "belum terpasang"),
        ("aislop", (f"skor {last.get('score')}" if last else "belum dipindai") if slop["installed"] else "belum terpasang"),
        ("Vault", f"{info['vault']['notes']} catatan"),
        ("Memori", f"{memory['decisions']} keputusan, {memory['facts']} fakta, {memory['files']} file"),
    ]
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style="dim")
    table.add_column()
    for label, value in rows:
        table.add_row(label, escape(str(value)))
    console.print()
    console.print("[bold bright_cyan]Sesi ini[/bold bright_cyan]")
    console.print(table)


def _display_doctor() -> bool:
    """Try to break out of the sandbox and show what happened."""
    console.print()
    console.print("[bold bright_cyan]Uji sandbox[/bold bright_cyan]")
    results = sandbox.self_check()
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(width=2)
    table.add_column()
    table.add_column(style="dim")
    for check, passed, detail in results:
        table.add_row("[green]✓[/green]" if passed else "[red]✗[/red]", escape(check), escape(detail))
    console.print(table)
    healthy = all(passed for _, passed, _ in results)
    if healthy:
        console.print("[green]Sandbox bekerja.[/green]")
    else:
        console.print("[red]Ada yang gagal. Kirim output ini untuk diperiksa.[/red]")
    for backend in (sandbox.BACKEND_BWRAP, sandbox.BACKEND_DOCKER):
        works, detail = sandbox.probe(backend)
        console.print(f"[dim]{backend}: {'tersedia' if works else 'tidak tersedia'} ({escape(detail)})[/dim]")
    console.print()
    return healthy


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

    status_lines.append(f"[bold]Sandbox:[/bold]           {cli_ui.sandbox_label()}")
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


async def process_input(
    user_input: str,
    session_id: str,
    orchestrator: Orchestrator,
    stream: bool = True,
    override_model: str | None = None,
    active_workspace: Path | None = None,
    terminal_config: TerminalFormatConfig | None = None,
    persist: bool = True,
) -> str:
    """Run one turn through the orchestrator and show it in the terminal.

    All agent behaviour lives in the orchestrator, so the CLI, the web chat, and
    team runs behave the same. This function only draws what happens, and writes
    files from fenced blocks in the reply when a workspace is open.

    Returns:
        The final response text.
    """
    cfg = terminal_config or TerminalFormatConfig()
    stack = session_stack.enter(session_id, active_workspace)

    def new_renderer() -> TerminalStreamRenderer:
        return TerminalStreamRenderer(console=console, config=cfg, tracker=SESSION_CODE_TRACKER)

    renderer = new_renderer()
    activity = cli_ui.Activity(console)
    showed_text = False
    started = time.monotonic()

    def on_content(chunk: str) -> None:
        nonlocal showed_text
        activity.stop()
        showed_text = True
        renderer.on_content_chunk(chunk)

    def on_thinking(chunk: str) -> None:
        # The spinner shows progress, so hidden thinking prints nothing of its own.
        if cfg.thinking_display in ("collapsed", "hide"):
            renderer.thinking_buffer.append(chunk)
            activity.update(f"Berpikir ({sum(len(c) for c in renderer.thinking_buffer)} karakter)")
            return
        activity.stop()
        renderer.on_thinking_chunk(chunk)

    def on_reset() -> None:
        # A terminal cannot unprint, so mark where the corrected answer starts.
        nonlocal renderer, showed_text
        activity.stop()
        renderer.finish("")
        renderer = new_renderer()
        if showed_text:
            console.print("\n  [yellow]↻ Jawaban diperbarui setelah pemeriksaan:[/yellow]\n")
        showed_text = False
        activity.start("Memperbaiki")

    def on_event(event: dict[str, Any]) -> None:
        nonlocal renderer
        kind = event.get("type")
        if kind == "route":
            console.print(cli_ui.route_line(
                str(event.get("task_type")), str(event.get("difficulty")), str(event.get("model"))
            ))
            activity.start("Menunggu model")
        elif kind == "remembered":
            for item in event.get("items") or []:
                console.print(f"[green]✓ diingat[/green] [dim]{escape(str(item))}[/dim]")
        elif kind == "tool" and event.get("phase") == "start":
            activity.stop()
            renderer.finish("")
            renderer = new_renderer()
            activity.start(f"Menjalankan {event.get('name')}")
        elif kind == "tool":
            activity.stop()
            console.print(cli_ui.tool_line(
                str(event.get("name")), str(event.get("detail") or ""), bool(event.get("ok")),
                int(event.get("ms") or 0), str(event.get("error") or ""),
            ))
            activity.start("Menunggu model")
        elif kind == "status":
            activity.update(str(event.get("text") or ""))

    try:
        response = await orchestrator.process(
            user_input=user_input,
            session_id=session_id,
            override_model=override_model,
            stream=stream,
            on_content_chunk=on_content if stream else None,
            on_thinking_chunk=on_thinking if stream else None,
            on_reset=on_reset,
            on_event=on_event,
            persist=persist,
        )
    finally:
        activity.stop()

    if response.status == ResponseStatus.ERROR:
        renderer.finish("")
        cli_ui.error_panel(console, response.content)
        return response.content

    renderer.finish(response.content)
    if not showed_text:
        render_terminal_markdown(response.content, console=console, config=cfg, tracker=SESSION_CODE_TRACKER)

    console.print()
    console.print(cli_ui.result_line(response, time.monotonic() - started))

    if stack.workspace.is_dir():
        actions = _apply_workspace_changes(stack.workspace, response.content)
        if actions:
            console.print(f"[dim]{len(actions)} file dari jawaban diterapkan ke proyek[/dim]")
            project = ProjectMemory.for_path(stack.workspace)
            if project:
                project.record_file_actions(actions)

    return response.content


async def chat_mode() -> None:
    """Interactive chat REPL with IDE features and Workspace Project support."""
    settings = get_settings()
    secrets = get_secrets()

    # One orchestrator: the same agent the web chat and team runs use.
    api_client = APIClient(
        settings.api_base_url, secrets.api_key, int(settings.timeout_seconds)
    )
    orchestrator = Orchestrator.from_settings(settings, api_client=api_client)
    registry = orchestrator.registry
    conversation_store = orchestrator.conversation_store

    # Auto-fetch /models from every provider and register everything, so new
    # models appear without editing config.
    synced = await api_client.sync_all_models(registry)
    logger.info(f"[main] Auto-synced models: {synced}")

    session_id = generate_session_id()
    project_root = settings.project_root

    # Terminal CLI Formatting Config (Markdown, Minimizer, Custom Theme)
    terminal_cfg_path = settings.config_dir / "terminal_format.json"
    terminal_config = TerminalFormatConfig.load(terminal_cfg_path)

    # Workspace directory
    workspace_dir = project_root / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    active_workspace: Path | None = None

    # Track user-selected model override
    override_model: str | None = None

    cli_ui.enable_line_editing()
    cli_ui.banner(
        console,
        models=registry.count,
        providers=sorted({m.provider or "unknown" for m in registry.get_all()}),
        session_id=session_id,
    )

    try:
        while True:
            console.print()

            session_stack.enter(session_id, active_workspace)
            console.print(cli_ui.context_line(override_model, active_workspace))
            try:
                user_input = _read_user_input(cli_ui.PROMPT)
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
                        override_model = new_model
                    if new_ws != active_workspace:
                        active_workspace = new_ws
                    continue

            # Process the message
            console.print()
            try:
                await process_input(
                    user_input=user_input,
                    session_id=session_id,
                    orchestrator=orchestrator,
                    stream=settings.stream_enabled,
                    override_model=override_model,
                    active_workspace=active_workspace,
                    terminal_config=terminal_config,
                )
            except (KeyboardInterrupt, asyncio.CancelledError):
                # Ctrl+C must not print a traceback or kill the session. Note
                # that CancelledError and KeyboardInterrupt are BaseException
                # subclasses, so a plain ``except Exception`` would miss them.
                logger.info("[chat_mode] turn interrupted by user")
                console.print()
                console.print("[dim]⏹ Dihentikan (Ctrl+C). Sesi tetap aktif.[/dim]")
            except Exception as proc_err:
                logger.exception(f"[chat_mode] Unhandled error during process_input: {proc_err}")
                cli_ui.error_panel(console, str(proc_err))

            # ──── AI SELESAI ────────────────────────────────────────────────
            # Flush stdin buffer: buang semua keystroke yang tidak sengaja
            # diketik user selama AI sedang streaming (jeda panjang).
            # Tanpa ini, Enter/karakter yang terketik saat streaming akan
            # langsung dikonsumsi console.input() berikutnya → re-trigger.
            _flush_stdin_buffer()
    finally:
        # The client may already be mid-teardown when the user interrupts the
        # loop; a second interrupt here would replace a clean exit with a
        # traceback.
        try:
            await orchestrator.close()
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

    api_client = APIClient(
        settings.api_base_url, secrets.api_key, int(settings.timeout_seconds)
    )
    orchestrator = Orchestrator.from_settings(settings, api_client=api_client)
    await api_client.sync_all_models(orchestrator.registry)

    terminal_config = TerminalFormatConfig.load(settings.config_dir / "terminal_format.json")
    workspace = os.getenv("SQUIDWINGS_WORKSPACE", "").strip()

    try:
        # One-shot: nothing to carry over, so it stays out of chat history.
        await process_input(
            user_input=question,
            session_id=generate_session_id(),
            orchestrator=orchestrator,
            stream=False,
            override_model=model,
            active_workspace=Path(workspace) if workspace else None,
            terminal_config=terminal_config,
            persist=False,
        )
    finally:
        await orchestrator.close()


def _run_async(coro_fn: Callable[..., Any], *fn_args: Any, **fn_kwargs: Any) -> None:
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

    # Doctor
    subparsers.add_parser("doctor", help="Test that agent commands are really isolated")

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
    elif args.command == "doctor":
        sys.exit(0 if _display_doctor() else 1)
    elif args.command == "web":
        from rich.panel import Panel

        from utils.tunnel import CloudflareTunnel
        from web.server import open_to_public, run_server

        tunnel = None
        public_url = None
        enable_tunnel = getattr(args, "tunnel", False)

        is_public = enable_tunnel or not is_loopback_host(args.host)
        if is_public:
            open_to_public()

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

        if is_public:
            panel_lines.append(
                "[bold yellow]Tanpa login: siapa pun yang punya URL ini bisa memakai agent dan "
                "membaca semua chat.[/bold yellow]"
            )

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
