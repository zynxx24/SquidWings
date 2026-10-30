"""The look of the interactive terminal: banner, prompt, activity, and result lines."""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from security import sandbox

# \001 and \002 tell readline these bytes take no space on screen. Without them,
# arrow keys and long lines redraw in the wrong place.
PROMPT = "\001\033[1;36m\002❯\001\033[0m\002 "
_HISTORY_FILE = Path.home() / ".squidwings_history"
_HISTORY_LENGTH = 2_000
_TICK_SECONDS = 0.25


def enable_line_editing() -> None:
    """Arrow keys, Ctrl+R, and history that survives restarts, where available."""
    try:
        import readline
    except ImportError:
        return
    with contextlib.suppress(OSError):
        readline.read_history_file(_HISTORY_FILE)
    readline.set_history_length(_HISTORY_LENGTH)
    atexit.register(_write_history, readline)


def _write_history(readline: Any) -> None:
    with contextlib.suppress(OSError):
        readline.write_history_file(_HISTORY_FILE)


def sandbox_label() -> str:
    """One line on how commands are isolated. Plain about the unsafe states."""
    policy = sandbox.load_policy()
    try:
        backend = sandbox.active_backend(policy)
    except sandbox.SandboxError:
        return "[bold red]tidak tersedia, perintah ditolak[/bold red] [dim](jalankan: python main.py doctor)[/dim]"
    if backend == sandbox.BACKEND_NONE:
        return "[bold yellow]mati, perintah berjalan langsung di mesin ini[/bold yellow]"
    network = "internet nyala" if policy.network else "internet mati"
    return f"[green]{backend}[/green] [dim]· {network}[/dim]"


def banner(console: Console, *, models: int, providers: list[str], session_id: str) -> None:
    title = Text.assemble(("SquidWings", "bold bright_cyan"), ("  agent untuk semua model", "dim"))
    rows = [
        f"[dim]model    [/dim]{models} model [dim]· {escape(', '.join(providers) or 'tidak ada provider')}[/dim]",
        f"[dim]sandbox  [/dim]{sandbox_label()}",
        f"[dim]sesi     [/dim][dim]{escape(session_id)}[/dim]",
        "",
        "[dim]Ketik tugasmu, atau[/dim] [cyan]/tree nama-proyek[/cyan] [dim]untuk membuka proyek ·[/dim] "
        "[cyan]/model[/cyan] [dim]·[/dim] [cyan]/memory[/cyan] [dim]·[/dim] [cyan]/help[/cyan] [dim]·[/dim] [cyan]exit[/cyan]",
    ]
    console.print(Panel("\n".join(rows), title=title, title_align="left", border_style="bright_cyan", padding=(0, 2)))


def context_line(model: str | None, workspace: Path | None) -> str:
    """What applies to the next message, printed above the prompt."""
    short_model = escape((model or "auto").split("/")[-1])
    place = f"[bright_green]{escape(workspace.name)}[/bright_green]" if workspace else "[dim]tanpa proyek[/dim]"
    return f"[dim]{short_model} ·[/dim] {place}"


def route_line(task_type: str, difficulty: str, model: str) -> str:
    return f"[dim]{escape(task_type)} · {escape(difficulty)} · {escape(model.split('/')[-1])}[/dim]"


def tool_line(name: str, detail: str, ok: bool, ms: int, error: str = "") -> str:
    mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
    shown = f" [dim]{escape(detail[:70])}[/dim]" if detail else ""
    reason = f" [red]{escape(error[:90])}[/red]" if error and not ok else ""
    return f"  {mark} {escape(name)}{shown} [dim]{ms}ms[/dim]{reason}"


def _format_tokens(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


def result_line(response: Any, seconds: float) -> str:
    """The summary under every answer."""
    stats = response.harness or {}
    parts = [f"{seconds:.0f}d", f"{_format_tokens(response.total_tokens)} token"]
    if response.tool_calls:
        parts.append(f"{len(response.tool_calls)} tool")
    parts.append(escape(str(response.model_used).split("/")[-1]))
    if stats.get("escalated_to"):
        parts.append(f"naik ke {escape(stats['escalated_to'].split('/')[-1])}")
    if stats.get("skills_used"):
        parts.append(f"skill {escape(', '.join(stats['skills_used']))}")
    text = "[dim]" + " · ".join(parts) + "[/dim]"
    if stats.get("unresolved"):
        return f"[yellow]! belum terverifikasi[/yellow] {text}"
    if stats.get("verified"):
        return f"[green]✓ terverifikasi[/green] {text}"
    return text


def error_panel(console: Console, message: str) -> None:
    console.print(Panel(escape(message), title="Gagal", title_align="left", border_style="red", padding=(0, 2)))
    console.print("[dim]Sesi tetap aktif. Coba lagi, atau ganti model dengan /model.[/dim]")


class Activity:
    """A spinner with elapsed time, so a slow model does not look like a hang.

    It must be stopped before anything else prints: streamed text and a live
    spinner would overwrite each other.
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._status: Any = None
        self._ticker: asyncio.Task[None] | None = None
        self._text = ""
        self._started = 0.0

    def _render(self) -> str:
        elapsed = time.monotonic() - self._started
        return f"[cyan]{escape(self._text)}[/cyan] [dim]{elapsed:.0f}d · Ctrl+C untuk berhenti[/dim]"

    async def _tick(self) -> None:
        while self._status is not None:
            self._status.update(self._render())
            await asyncio.sleep(_TICK_SECONDS)

    def start(self, text: str) -> None:
        self._text = text
        if not self._console.is_terminal:
            return
        if self._status is None:
            self._started = time.monotonic()
            self._status = self._console.status(self._render(), spinner="dots")
            self._status.start()
            self._ticker = asyncio.get_running_loop().create_task(self._tick())

    def update(self, text: str) -> None:
        """Change the message, keeping the timer."""
        self._text = text

    def stop(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None
        if self._ticker is not None:
            self._ticker.cancel()
            self._ticker = None
