"""
utils.cli_formatter — Terminal CLI Markdown & Code Minimizer Formatting Engine.

Provides:
- Rich Markdown rendering in terminal CLI.
- Code block minimization with preview, line counts, and language badges.
- Expandable / collapsible code management with `/code` and `/format` commands.
- Smart real-time streaming formatter that avoids flooding terminal with hundreds of lines of code.
- Customizable syntax highlighting themes (Monokai, Dracula, Nord, One-Dark, etc.).
- Customizable thinking block formats (stream, collapsed, panel, hide).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.markdown import CodeBlock, Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

# Common file extensions to programming language mapping
LANG_EXTENSIONS: dict[str, str] = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "jsx": "jsx",
    "tsx": "tsx",
    "html": "html",
    "css": "css",
    "scss": "scss",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "toml": "toml",
    "sh": "bash",
    "bash": "bash",
    "zsh": "bash",
    "sql": "sql",
    "md": "markdown",
    "rs": "rust",
    "go": "go",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "h": "c",
    "hpp": "cpp",
    "php": "php",
    "rb": "ruby",
    "xml": "xml",
}

AVAILABLE_THEMES = [
    "monokai",
    "dracula",
    "nord",
    "one-dark",
    "material",
    "github-dark",
    "solarized-dark",
    "fruity",
    "native",
]


@dataclass
class TerminalFormatConfig:
    """User preferences for terminal output formatting."""

    markdown_enabled: bool = True
    code_display: str = "minimized"  # "minimized" | "collapsed" | "full"
    min_lines_threshold: int = 5  # Snippets <= 5 lines stay full; > 5 lines get minimized
    preview_lines: int = 3  # Lines shown in preview when minimized
    syntax_theme: str = "monokai"  # Pygments syntax theme
    line_numbers: bool = True  # Show line numbers in code
    thinking_display: str = "collapsed"  # "collapsed" | "stream" | "panel" | "hide"
    stream_mode: str = "smart"  # "smart" (minimizes code while streaming) | "raw"
    border_style: str = "bright_cyan"

    @classmethod
    def load(cls, config_path: Path) -> TerminalFormatConfig:
        """Load config from JSON file or return defaults."""
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except Exception:
                pass
        return cls()

    def save(self, config_path: Path) -> None:
        """Save config to JSON file."""
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except Exception:
            pass

    def update(self, **kwargs: Any) -> None:
        """Update fields safely."""
        for k, v in kwargs.items():
            if hasattr(self, k):
                # Type cast if necessary
                field_type = type(getattr(self, k))
                if field_type is bool and isinstance(v, str):
                    setattr(self, k, v.lower() in ("true", "1", "yes", "on", "aktif"))
                elif field_type is int and isinstance(v, (str, float)):
                    setattr(self, k, int(v))
                else:
                    setattr(self, k, v)


@dataclass
class CodeBlockInfo:
    """Metadata and content for an extracted code block."""

    index: int  # 1-based index (e.g. 1 for block #1)
    language: str
    code: str
    target_path: str | None = None
    block_type: str = "snippet"  # "snippet" | "file" | "replace"
    line_count: int = 0
    is_minimized: bool = True

    def __post_init__(self) -> None:
        if not self.line_count:
            self.line_count = len(self.code.splitlines())


class CodeSessionTracker:
    """Tracks code blocks from the most recent AI response."""

    def __init__(self) -> None:
        self.blocks: list[CodeBlockInfo] = []
        self.last_full_response: str = ""

    def clear(self) -> None:
        self.blocks.clear()

    def add_block(self, block: CodeBlockInfo) -> None:
        self.blocks.append(block)

    def get_block(self, index: int) -> CodeBlockInfo | None:
        for b in self.blocks:
            if b.index == index:
                return b
        return None

    def get_all(self) -> list[CodeBlockInfo]:
        return list(self.blocks)

    def count(self) -> int:
        return len(self.blocks)


# Global tracker instance for current chat session
SESSION_CODE_TRACKER = CodeSessionTracker()


def parse_tag(tag: str) -> tuple[str, str, str | None]:
    """Parse tag from code fence (e.g. 'python', 'file:src/app.py', 'replace:main.py').

    Returns:
        (language, block_type, target_path)
    """
    tag = tag.strip()
    if not tag:
        return "text", "snippet", None

    # Case: ```file:<path>
    if tag.startswith("file:"):
        path = tag[5:].strip()
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        lang = LANG_EXTENSIONS.get(ext, "text")
        return lang, "file", path

    # Case: ```replace:<path>
    if tag.startswith("replace:"):
        path = tag[8:].strip()
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        lang = LANG_EXTENSIONS.get(ext, "text")
        return lang, "replace", path

    # Case: ```lang
    # Check if tag has extra arguments, e.g. "python title='main.py'"
    parts = tag.split()
    lang = parts[0].lower() if parts else "text"
    return lang, "snippet", None


def render_code_panel(
    block: CodeBlockInfo,
    config: TerminalFormatConfig,
    force_full: bool = False,
) -> Panel:
    """Render a styled Rich Panel for a code block (minimized, collapsed, or full)."""
    lang = block.language or "text"
    total_lines = block.line_count or len(block.code.splitlines())
    theme = config.syntax_theme

    # Choose icons & colors based on block type
    if block.block_type == "file":
        type_icon = "💾"
        border_style = "bright_green"
        type_desc = f"file: {block.target_path}" if block.target_path else "file create"
    elif block.block_type == "replace":
        type_icon = "✏️"
        border_style = "bright_yellow"
        type_desc = f"replace: {block.target_path}" if block.target_path else "replace edit"
    else:
        type_icon = "💻"
        border_style = config.border_style
        type_desc = lang

    title = (
        f"[bold {border_style}]{type_icon} {type_desc}[/bold {border_style}] "
        f"[dim]({total_lines} baris)[/dim]"
    )

    should_minimize = (
        not force_full
        and config.code_display in ("minimized", "collapsed")
        and total_lines > config.min_lines_threshold
    )

    if not should_minimize or config.code_display == "full":
        # FULL EXPANDED VIEW
        syntax = Syntax(
            block.code,
            lang,
            theme=theme,
            line_numbers=config.line_numbers,
            word_wrap=True,
            padding=0,
        )
        subtitle = (
            f"[dim]💡 Blok #{block.index} [bold]/code min[/bold] untuk minimize "
            f"| [bold]/code copy {block.index}[/bold] untuk salin[/dim]"
        )
        return Panel(
            syntax,
            title=f"{title} [bold green][full #{block.index}][/bold green]",
            subtitle=subtitle,
            border_style=border_style,
            box=box.ROUNDED,
            padding=(0, 1),
        )

    elif config.code_display == "collapsed":
        # ULTRA-COMPACT COLLAPSED VIEW (Header only, 0 preview lines)
        msg = Text.from_markup(
            f"  [dim italic]Kode disembunyikan.[/dim italic] "
            f"[bold cyan]Ketik [/bold cyan][bold white]/code {block.index}[/bold white]"
            f"[bold cyan] atau [/bold cyan][bold white]/code[/bold white]"
            f"[bold cyan] untuk expand full kode.[/bold cyan]"
        )
        return Panel(
            msg,
            title=f"{title} [bold yellow][collapsed #{block.index}][/bold yellow]",
            border_style=border_style,
            box=box.ROUNDED,
            padding=(0, 1),
        )

    else:
        # MINIMIZED VIEW WITH PREVIEW (Default)
        preview_count = min(config.preview_lines, total_lines)
        lines = block.code.splitlines()
        preview_text = "\n".join(lines[:preview_count])
        hidden_count = total_lines - preview_count

        syntax = Syntax(
            preview_text,
            lang,
            theme=theme,
            line_numbers=config.line_numbers,
            word_wrap=True,
            padding=0,
        )

        hint_parts = [
            f"\n[dim italic]... ({hidden_count} baris lainnya disembunyikan)[/dim italic]",
            f"[dim]💡 Ketik [/dim][bold cyan]/code {block.index}[/bold cyan]"
            f"[dim] untuk lihat full, atau [/dim][bold cyan]/code[/bold cyan]"
            f"[dim] untuk expand semua.[/dim]",
        ]
        footer_text = Text.from_markup("\n".join(hint_parts))

        group = Group(syntax, footer_text)
        return Panel(
            group,
            title=f"{title} [bold yellow][minimized #{block.index}][/bold yellow]",
            border_style=border_style,
            box=box.ROUNDED,
            padding=(0, 1),
        )


class MinimizableCodeBlock(CodeBlock):
    """Rich Markdown CodeBlock override supporting minimization and indexing."""

    config: TerminalFormatConfig = TerminalFormatConfig()
    tracker: CodeSessionTracker = SESSION_CODE_TRACKER

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        code = str(self.text).rstrip()
        lang, block_type, target_path = parse_tag(self.lexer_name or "text")

        # Create CodeBlockInfo and register in tracker
        index = self.tracker.count() + 1
        block_info = CodeBlockInfo(
            index=index,
            language=lang,
            code=code,
            target_path=target_path,
            block_type=block_type,
            line_count=len(code.splitlines()),
        )
        self.tracker.add_block(block_info)

        panel = render_code_panel(block_info, self.config)
        yield panel


class CustomTerminalMarkdown(Markdown):
    """Custom Markdown renderer that formats code blocks with minimization."""

    elements = {
        **Markdown.elements,
        "fence": MinimizableCodeBlock,
        "code_block": MinimizableCodeBlock,
    }


def render_terminal_markdown(
    text: str,
    console: Console,
    config: TerminalFormatConfig,
    tracker: CodeSessionTracker | None = None,
    force_full_code: bool = False,
) -> None:
    """Render full markdown response in terminal CLI.

    Args:
        text: Raw AI response markdown string.
        console: Rich console instance.
        config: TerminalFormatConfig settings.
        tracker: CodeSessionTracker (uses global if None).
        force_full_code: If True, renders all code blocks in full.
    """
    if tracker is None:
        tracker = SESSION_CODE_TRACKER

    tracker.last_full_response = text

    if not config.markdown_enabled:
        console.print(text, markup=False, highlight=False)
        return

    # Update class-level config for custom Markdown element
    MinimizableCodeBlock.config = config
    MinimizableCodeBlock.tracker = tracker

    if force_full_code:
        # Temporarily set full display
        temp_cfg = TerminalFormatConfig(**asdict(config))
        temp_cfg.code_display = "full"
        MinimizableCodeBlock.config = temp_cfg

    # Clear tracker for fresh rendering
    tracker.clear()

    # Pre-clean markdown for Rich Markdown compatibility if needed
    cleaned_text = _normalize_markdown(text)
    md = CustomTerminalMarkdown(cleaned_text, code_theme=config.syntax_theme)
    console.print(md)

    # Restore config
    MinimizableCodeBlock.config = config


def _normalize_markdown(text: str) -> str:
    """Normalize file headers and replace tags for standard markdown parser."""
    # Convert ### File: <path>\n```lang -> ```file:<path>\n
    text = re.sub(
        r"(?:###\s*File:|\*\*File:\*\*)\s*([^\n\*\#]+?)\s*\n```([a-zA-Z0-9_\-\.]*)",
        r"```file:\1",
        text,
    )
    return text


class TerminalStreamRenderer:
    """Real-time streaming renderer for terminal CLI.

    Intercepts code fences during live streaming to prevent 100+ lines of code
    from flooding the terminal, displaying an animated line counter instead
    and rendering the minimized code card upon fence closure.
    """

    def __init__(
        self,
        console: Console,
        config: TerminalFormatConfig,
        tracker: CodeSessionTracker | None = None,
    ) -> None:
        self.console = console
        self.config = config
        self.tracker = tracker or SESSION_CODE_TRACKER

        # Stream state
        self.state: str = "TEXT"  # "TEXT" | "IN_CODE" | "IN_THINKING"
        self.code_buffer: list[str] = []
        self.current_tag: str = ""
        self.fence_pending: str = ""
        self.current_line_count: int = 0
        self.thinking_buffer: list[str] = []
        self.thinking_shown: bool = False
        self.response_header_shown: bool = False

        # Reset tracker for the new turn
        self.tracker.clear()

    def on_thinking_chunk(self, chunk: str) -> None:
        """Handle incoming thinking chunk."""
        self.thinking_buffer.append(chunk)

        if self.config.thinking_display == "hide":
            return

        if self.config.thinking_display == "stream":
            if not self.thinking_shown:
                self.console.print("\n  [dim italic]💭 Thinking...[/dim italic]\n", end="")
                self.thinking_shown = True
            self.console.print(chunk, end="", style="dim", markup=False, highlight=False)

        elif self.config.thinking_display == "collapsed":
            char_count = sum(len(c) for c in self.thinking_buffer)
            # Update counter in-place
            sys.stdout.write(f"\r\033[K  [dim italic]💭 Thinking... ({char_count} karakter)[/dim italic]")
            sys.stdout.flush()

        elif self.config.thinking_display == "panel":
            # Just accumulate; will display panel upon first response chunk
            pass

    def on_content_chunk(self, chunk: str) -> None:
        """Handle incoming content chunk."""
        # Check if thinking just finished
        if self.thinking_buffer and not self.response_header_shown:
            if self.config.thinking_display == "collapsed":
                words = sum(len(c.split()) for c in self.thinking_buffer)
                sys.stdout.write(f"\r\033[K  [dim]💭 Selesai berpikir ({words} kata)[/dim]\n\n")
                sys.stdout.flush()
            elif self.config.thinking_display == "panel":
                full_think = "".join(self.thinking_buffer).strip()
                self.console.print(
                    Panel(
                        Text(full_think, style="dim italic"),
                        title="[dim]💭 Pemikiran AI (Thinking)[/dim]",
                        border_style="dim",
                        padding=(0, 1),
                    )
                )
            elif self.thinking_shown and self.config.thinking_display == "stream":
                self.console.print("\n\n  [dim italic]💬 Response:[/dim italic]\n", end="")

            self.response_header_shown = True

        if self.config.stream_mode == "raw" or not self.config.markdown_enabled:
            # Fallback to direct stream
            self.console.print(chunk, end="", markup=False, highlight=False)
            return

        # Smart stream parsing: split chunk into characters/tokens
        self._process_stream_text(chunk)

    def _process_stream_text(self, text: str) -> None:
        """Process incoming text with state machine."""
        i = 0
        n = len(text)

        while i < n:
            if self.state == "TEXT":
                # Check for DSML or tool call tag (e.g. DeepSeek DSML)
                dsml_match = re.search(r"[<｜\s]*DSML[｜\s]*(?:calls|invoke)|<[｜|]tool calls? begin[｜|]>", text[i:])
                fence_idx = text.find("```", i)

                if dsml_match and (fence_idx == -1 or dsml_match.start() < (fence_idx - i)):
                    # Output text before DSML
                    pre_text = text[i : i + dsml_match.start()]
                    if pre_text:
                        self.console.print(pre_text, end="", markup=False, highlight=False)
                    self.state = "IN_TOOL_CALL"
                    i += dsml_match.start()
                    sys.stdout.write("  [dim cyan]⚡ AI memanggil tool...[/dim cyan]\r")
                    sys.stdout.flush()
                    continue

                # Look for code fence opening ```
                if fence_idx != -1:
                    # Output text before fence
                    pre_text = text[i:fence_idx]
                    if pre_text:
                        self.console.print(pre_text, end="", markup=False, highlight=False)

                    # Look for end of fence tag line (newline)
                    newline_idx = text.find("\n", fence_idx + 3)
                    if newline_idx != -1:
                        self.current_tag = text[fence_idx + 3 : newline_idx].strip()
                        self.state = "IN_CODE"
                        self.code_buffer = []
                        self.current_line_count = 0
                        i = newline_idx + 1

                        # Initial live indicator if minimizing
                        if self._should_minimize_current():
                            self._show_code_progress()
                        else:
                            # Stream fence header
                            lang, b_type, t_path = parse_tag(self.current_tag)
                            title = t_path if t_path else lang
                            self.console.print(f"\n[dim cyan]── 💻 {title} ──[/dim cyan]\n", end="")
                    else:
                        # Fence line splits across chunk, hold in buffer
                        self.fence_pending = text[fence_idx:]
                        break
                else:
                    # Check for partial fence at the very end of chunk (e.g. ` or ``)
                    if text.endswith("``"):
                        self.console.print(text[i:-2], end="", markup=False, highlight=False)
                        self.fence_pending = "``"
                        break
                    elif text.endswith("`"):
                        self.console.print(text[i:-1], end="", markup=False, highlight=False)
                        self.fence_pending = "`"
                        break
                    else:
                        self.console.print(text[i:], end="", markup=False, highlight=False)
                        break

            elif self.state == "IN_TOOL_CALL":
                # Look for closing DSML / tool tag
                dsml_end = re.search(r"</[<｜\s]*DSML[｜\s]*(?:calls|invoke)>|<[｜|]tool calls? end[｜|]>", text[i:])
                if dsml_end:
                    i += dsml_end.end()
                    sys.stdout.write("\r\033[K")
                    sys.stdout.flush()
                    self.state = "TEXT"
                else:
                    # Suppress raw XML streaming to terminal
                    break

            elif self.state == "IN_CODE":
                # Inside code block: look for closing ```
                fence_idx = text.find("```", i)
                if fence_idx != -1:
                    # Append code before closing fence
                    code_piece = text[i:fence_idx]
                    self.code_buffer.append(code_piece)
                    full_code = "".join(self.code_buffer)

                    # Close code block
                    self._finalize_code_block(full_code)
                    self.state = "TEXT"
                    self.code_buffer = []
                    i = fence_idx + 3

                    # Skip optional trailing newline after ```
                    if i < n and text[i] == "\n":
                        i += 1
                else:
                    # All text in this chunk is code
                    code_piece = text[i:]
                    self.code_buffer.append(code_piece)
                    new_lines = code_piece.count("\n")
                    self.current_line_count += new_lines

                    if self._should_minimize_current():
                        self._show_code_progress()
                    else:
                        # Full streaming: print code as it comes
                        self.console.print(code_piece, end="", markup=False, highlight=False)
                    break

    def _should_minimize_current(self) -> bool:
        """Check if current code should be minimized during streaming."""
        return self.config.code_display in ("minimized", "collapsed")

    def _show_code_progress(self) -> None:
        """Display live animated counter for writing code."""
        lang, b_type, t_path = parse_tag(self.current_tag)
        target = f"{t_path}" if t_path else lang
        icon = "💾" if b_type == "file" else ("✏️" if b_type == "replace" else "💻")
        # Overwrite line
        msg = f"\r\033[K  [dim cyan]{icon} Menulis kode {target}... ({self.current_line_count} baris)[/dim cyan]"
        sys.stdout.write(msg)
        sys.stdout.flush()

    def _finalize_code_block(self, full_code: str) -> None:
        """Render completed code block and register in tracker."""
        # Clear the live progress line
        if self._should_minimize_current():
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

        lang, block_type, target_path = parse_tag(self.current_tag)
        code_clean = full_code.strip("\n")
        lines = code_clean.splitlines()

        index = self.tracker.count() + 1
        block_info = CodeBlockInfo(
            index=index,
            language=lang,
            code=code_clean,
            target_path=target_path,
            block_type=block_type,
            line_count=len(lines),
        )
        self.tracker.add_block(block_info)

        # Render panel
        panel = render_code_panel(block_info, self.config)
        self.console.print()
        self.console.print(panel)
        self.console.print()

    def finish(self, final_text: str = "") -> None:
        """Call when streaming finishes to clean up remaining buffers."""
        if self.thinking_buffer and not self.response_header_shown:
            words = sum(len(c.split()) for c in self.thinking_buffer)
            if self.config.thinking_display == "collapsed":
                sys.stdout.write(f"\r\033[K  [dim]💭 Selesai berpikir ({words} kata)[/dim]\n\n")
                sys.stdout.flush()
            self.response_header_shown = True

        if self.state == "IN_TOOL_CALL":
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
            self.state = "TEXT"

        if self.state == "IN_CODE" and self.code_buffer:
            # Unclosed code fence (e.g. truncated response)
            full_code = "".join(self.code_buffer)
            self._finalize_code_block(full_code)
            self.state = "TEXT"

        # Record full response in tracker
        if final_text:
            self.tracker.last_full_response = final_text

        # Show subtle hint if blocks were minimized
        minimized_count = sum(
            1 for b in self.tracker.get_all()
            if self.config.code_display in ("minimized", "collapsed")
            and b.line_count > self.config.min_lines_threshold
        )
        if minimized_count > 0:
            self.console.print(
                f"  [dim]💡 [bold cyan]{minimized_count}[/bold cyan] blok kode diminimalkan. "
                f"Ketik [bold white]/code[/bold white] untuk expand semua, "
                f"atau [bold white]/code 1[/bold white] untuk blok pertama.[/dim]"
            )


# ─────────────────────────────────────────────────────────────────────────────
# CLI Helper Commands for Terminal Chat Mode
# ─────────────────────────────────────────────────────────────────────────────

def handle_code_command(
    arg: str,
    console: Console,
    config: TerminalFormatConfig,
    tracker: CodeSessionTracker,
    config_path: Path,
) -> bool:
    """Handle `/code` slash commands.

    Usage:
        /code              -> Toggle minimize <-> full for all blocks
        /code toggle       -> Toggle default code_display setting
        /code full, expand -> Expand all blocks from last response in full
        /code min, fold    -> Minimize all blocks
        /code <N>          -> Show block #N in full syntax highlighting
        /code copy <N>     -> Copy block #N to clipboard
        /code save <N> <p> -> Save block #N to file path
        /code list         -> List all code blocks from last response
    """
    parts = arg.strip().split()
    subcmd = parts[0].lower() if parts else "toggle"

    # Case: `/code` or `/code toggle`
    if subcmd in ("toggle", "t"):
        if config.code_display in ("minimized", "collapsed"):
            config.code_display = "full"
            console.print("\n[bold green]🔓 Mode Kode: FULL (Kode lengkap ditampilkan)[/bold green]")
        else:
            config.code_display = "minimized"
            console.print("\n[bold yellow]🔒 Mode Kode: MINIMIZED (Kode diminimalkan dengan preview)[/bold yellow]")
        config.save(config_path)

        # Re-render blocks from last response if available
        if tracker.count() > 0:
            console.print(f"[dim]Menampilkan {tracker.count()} blok kode:[/dim]\n")
            for block in tracker.get_all():
                console.print(render_code_panel(block, config))
        return True

    # Case: `/code full` or `/code expand`
    if subcmd in ("full", "expand", "open", "all"):
        if tracker.count() == 0:
            console.print("[yellow]⚠️  Tidak ada blok kode dari respons AI terakhir.[/yellow]")
            return True
        console.print(f"\n[bold green]📖 Menampilkan semua {tracker.count()} blok kode secara lengkap:[/bold green]\n")
        for block in tracker.get_all():
            console.print(render_code_panel(block, config, force_full=True))
        return True

    # Case: `/code min` or `/code fold` or `/code collapse`
    if subcmd in ("min", "minimize", "fold", "collapse"):
        config.code_display = "minimized" if subcmd != "collapse" else "collapsed"
        config.save(config_path)
        console.print(f"\n[bold yellow]🔒 Mode Kode diatur ke: {config.code_display.upper()}[/bold yellow]")
        if tracker.count() > 0:
            for block in tracker.get_all():
                console.print(render_code_panel(block, config))
        return True

    # Case: `/code list` or `/code ls`
    if subcmd in ("list", "ls"):
        if tracker.count() == 0:
            console.print("[yellow]⚠️  Belum ada blok kode di sesi ini.[/yellow]")
            return True

        table = Table(
            title="📋 Daftar Blok Kode Respons Terakhir",
            title_style="bold bright_cyan",
            border_style="cyan",
            box=box.ROUNDED,
            expand=True,
        )
        table.add_column("#", justify="center", style="bold yellow", width=4)
        table.add_column("Tipe", style="white", width=10)
        table.add_column("Bahasa", style="cyan", width=12)
        table.add_column("Target File", style="green")
        table.add_column("Baris", justify="right", style="magenta", width=8)
        table.add_column("Aksi Cepat", style="dim")

        for block in tracker.get_all():
            target = block.target_path or "—"
            table.add_row(
                str(block.index),
                block.block_type,
                block.language,
                target,
                str(block.line_count),
                f"/code {block.index} (lihat) | /code copy {block.index} (salin)",
            )
        console.print()
        console.print(table)
        console.print()
        return True

    # Case: `/code copy <N>`
    if subcmd == "copy":
        idx_str = parts[1] if len(parts) > 1 else "1"
        try:
            target_idx = int(idx_str)
        except ValueError:
            target_idx = 1
        selected_block = tracker.get_block(target_idx)
        if not selected_block:
            console.print(f"[red]❌ Blok kode #{target_idx} tidak ditemukan.[/red]")
            return True

        # Copy box display
        console.print(f"\n[bold bright_green]📋 Salin Blok #{selected_block.index} ({selected_block.language}):[/bold bright_green]")
        console.print(Panel(Text(selected_block.code), border_style="green", title="Content to Copy", padding=(1, 2)))
        console.print("[dim]💡 Blok kode siap di-copy di atas.[/dim]\n")
        return True

    # Case: `/code save <N> <path>`
    if subcmd == "save":
        if len(parts) < 3:
            console.print("[yellow]⚠️  Usage: /code save <nomor_blok> <file_path>[/yellow]")
            return True
        try:
            target_idx = int(parts[1])
        except ValueError:
            console.print("[red]❌ Nomor blok tidak valid.[/red]")
            return True
        target_file = Path(parts[2])
        selected_block = tracker.get_block(target_idx)
        if not selected_block:
            console.print(f"[red]❌ Blok kode #{target_idx} tidak ditemukan.[/red]")
            return True

        try:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(selected_block.code, encoding="utf-8")
            console.print(f"[bold green]✅ Blok #{selected_block.index} berhasil disimpan ke {target_file} ({selected_block.line_count} baris)![/bold green]")
        except Exception as e:
            console.print(f"[red]❌ Gagal menyimpan file: {e}[/red]")
        return True

    # Case: `/code <N>` (e.g. `/code 1`)
    try:
        target_idx = int(subcmd)
        selected_block = tracker.get_block(target_idx)
        if not selected_block:
            console.print(f"[red]❌ Blok #{target_idx} tidak ditemukan. Ketik '/code list' untuk melihat daftar.[/red]")
            return True
        console.print(f"\n[bold green]📖 Menampilkan Blok #{selected_block.index}:[/bold green]\n")
        console.print(render_code_panel(selected_block, config, force_full=True))
        return True
    except ValueError:
        pass

    console.print(
        "[yellow]⚠️  Perintah tidak dikenal. Gunakan: "
        "/code (toggle), /code full, /code min, /code 1, /code list, /code copy 1[/yellow]"
    )
    return True


def handle_format_command(
    arg: str,
    console: Console,
    config: TerminalFormatConfig,
    config_path: Path,
) -> bool:
    """Handle `/format` slash commands to configure terminal formatting.

    Usage:
        /format                 -> Display current format settings
        /format theme <name>    -> Change syntax highlighting theme
        /format lines <N>       -> Set minimum lines threshold to minimize
        /format preview <N>     -> Set preview lines for minimized code
        /format code <min|full|collapsed> -> Set default code display
        /format thinking <collapsed|stream|panel|hide> -> Set thinking format
        /format md <on|off>     -> Toggle markdown rendering
        /format reset           -> Reset settings to default
    """
    parts = arg.strip().split()
    if not parts:
        # Display settings table
        table = Table(
            title="⚙️ Pengaturan Tampilan Terminal (Terminal CLI Formatter)",
            title_style="bold bright_cyan",
            border_style="cyan",
            box=box.ROUNDED,
            expand=True,
        )
        table.add_column("Setting", style="bold bright_green", width=22)
        table.add_column("Nilai Saat Ini", style="bold white", width=18)
        table.add_column("Pilihan / Contoh Perintah", style="dim")

        table.add_row(
            "Markdown Rendering",
            "✅ ON" if config.markdown_enabled else "❌ OFF",
            "/format md on | /format md off",
        )
        table.add_row(
            "Code Display",
            config.code_display.upper(),
            "/format code min | full | collapsed",
        )
        table.add_row(
            "Min Lines Threshold",
            f"{config.min_lines_threshold} baris",
            "/format lines 5 (hanya kode > N baris diminimalkan)",
        )
        table.add_row(
            "Preview Lines",
            f"{config.preview_lines} baris",
            "/format preview 3 (baris pratinjau dalam kotak)",
        )
        table.add_row(
            "Syntax Theme",
            config.syntax_theme,
            f"/theme <nama> ({', '.join(AVAILABLE_THEMES[:5])}...)",
        )
        table.add_row(
            "Thinking Format",
            config.thinking_display.upper(),
            "/format thinking collapsed | stream | panel | hide",
        )
        table.add_row(
            "Line Numbers",
            "✅ ON" if config.line_numbers else "❌ OFF",
            "/format numbers on | off",
        )
        table.add_row(
            "Stream Mode",
            config.stream_mode.upper(),
            "/format stream smart | raw",
        )

        console.print()
        console.print(table)
        console.print("[dim]💡 Perubahan otomatis disimpan ke config/terminal_format.json[/dim]\n")
        return True

    subcmd = parts[0].lower()
    val = parts[1].lower() if len(parts) > 1 else ""

    if subcmd in ("theme", "t"):
        if not val:
            console.print(f"[yellow]⚠️  Tema tersedia: {', '.join(AVAILABLE_THEMES)}[/yellow]")
            return True
        if val in AVAILABLE_THEMES:
            config.syntax_theme = val
            config.save(config_path)
            console.print(f"[bold green]✅ Syntax theme diubah ke: {val}[/bold green]")
        else:
            console.print(f"[red]❌ Tema '{val}' tidak dikenal. Pilihan: {', '.join(AVAILABLE_THEMES)}[/red]")
        return True

    if subcmd in ("code", "display", "c"):
        if val in ("min", "minimized", "minimize"):
            config.code_display = "minimized"
        elif val in ("full", "expand", "max"):
            config.code_display = "full"
        elif val in ("collapsed", "hide", "fold"):
            config.code_display = "collapsed"
        else:
            console.print("[yellow]⚠️  Pilihan: min (preview), full (penuh), collapsed (tertutup)[/yellow]")
            return True
        config.save(config_path)
        console.print(f"[bold green]✅ Default tampilan kode diubah ke: {config.code_display.upper()}[/bold green]")
        return True

    if subcmd in ("lines", "threshold", "min_lines"):
        try:
            n = int(val)
            config.min_lines_threshold = max(1, n)
            config.save(config_path)
            console.print(f"[bold green]✅ Threshold baris kode diubah ke: {config.min_lines_threshold} baris[/bold green]")
        except ValueError:
            console.print("[red]❌ Masukkan angka valid. Contoh: /format lines 5[/red]")
        return True

    if subcmd in ("preview", "prev"):
        try:
            n = int(val)
            config.preview_lines = max(1, n)
            config.save(config_path)
            console.print(f"[bold green]✅ Pratinjau baris kode diubah ke: {config.preview_lines} baris[/bold green]")
        except ValueError:
            console.print("[red]❌ Masukkan angka valid. Contoh: /format preview 3[/red]")
        return True

    if subcmd in ("thinking", "think"):
        if val in ("collapsed", "stream", "panel", "hide"):
            config.thinking_display = val
            config.save(config_path)
            console.print(f"[bold green]✅ Format thinking diubah ke: {val.upper()}[/bold green]")
        else:
            console.print("[yellow]⚠️  Pilihan: collapsed (ringkas), stream (live dim), panel (kotak), hide (sembunyi)[/yellow]")
        return True

    if subcmd in ("md", "markdown"):
        config.markdown_enabled = val in ("on", "1", "true", "yes", "enable")
        config.save(config_path)
        console.print(f"[bold green]✅ Markdown rendering: {'ON' if config.markdown_enabled else 'OFF'}[/bold green]")
        return True

    if subcmd in ("numbers", "linenumbers"):
        config.line_numbers = val in ("on", "1", "true", "yes")
        config.save(config_path)
        console.print(f"[bold green]✅ Line numbers: {'ON' if config.line_numbers else 'OFF'}[/bold green]")
        return True

    if subcmd in ("stream", "streaming"):
        if val in ("smart", "raw"):
            config.stream_mode = val
            config.save(config_path)
            console.print(f"[bold green]✅ Stream mode: {val.upper()}[/bold green]")
        else:
            console.print("[yellow]⚠️  Pilihan: smart (minimalkan kode live), raw (output apa adanya)[/yellow]")
        return True

    if subcmd in ("reset", "default"):
        default_cfg = TerminalFormatConfig()
        for k in default_cfg.__dataclass_fields__:
            setattr(config, k, getattr(default_cfg, k))
        config.save(config_path)
        console.print("[bold green]✅ Pengaturan terminal berhasil direset ke default![/bold green]")
        return True

    console.print(f"[yellow]⚠️  Setting '{subcmd}' tidak dikenal. Ketik '/format' untuk bantuan.[/yellow]")
    return True
