"""
tools.command_prompt.run_command_tool — Execute command tool for AI.

AI tidak bisa langsung jalankan terminal command.
Tapi AI punya tool ini yang:
1. Sanitize command (safety check)
2. Execute di subprocess
3. Capture stdout/stderr
4. Return hasilnya ke AI

Mendukung:
- Command sanitization (block dangerous commands)
- Timeout protection
- Output size limit
- Working directory specification
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from brain.utils import web_terminal
from brain.utils.logger import get_logger
from tools.command_prompt.command_executor import CommandExecutor
from tools.command_prompt.command_sanitizer import CommandSanitizer
from tools.command_prompt.terminal_tool import terminal_run

logger = get_logger(__name__)
# Long enough to render a minute of video. A command that hangs still ends.
MAX_COMMAND_SECONDS = 900
_SUDO_RE = re.compile(r"(^|[;&|(]\s*|\s)sudo\s")


async def execute_command(
    command: str,
    cwd: str = ".",
    timeout: int = 30,
) -> dict[str, Any]:
    """Execute a shell command and return the result.

    AI memanggil tool ini untuk menjalankan command.
    Command disanitize dulu, lalu dieksekusi di subprocess.

    Args:
        command: Shell command to execute.
        cwd: Working directory for the command.
        timeout: Timeout in seconds.

    Returns:
        dict with: success, command, stdout, stderr,
        return_code, duration, timed_out, error (if any).
    """
    timeout = max(1, min(int(timeout or 30), MAX_COMMAND_SECONDS))

    # sudo cannot run in the sandbox. With the sudo password saved (Kredensial → Sistem) or a NOPASSWD rule,
    # it runs on this machine at once (brain/utils/host_sudo.py); otherwise it goes to the user's Terminal panel.
    if _SUDO_RE.search(command):
        from brain.utils import host_sudo

        if host_sudo.ready():
            return await host_sudo.run(command, cwd=cwd, timeout=max(timeout, 300))
        if web_terminal.enabled():
            handed = await terminal_run(command, timeout=max(timeout, 120))
            return {**handed, "note": str(handed.get("note") or "") + " Ran in the user's Terminal panel because it needs sudo."}
        return {
            "success": False, "command": command, "blocked": True, "return_code": -1, "stdout": "",
            "stderr": "sudo needs the user's password.",
            "error": "sudo needs the user's password. Ask the user to run this command themselves "
                     "(in the CLI: type it after '!'), then continue with the result.",
        }

    # 1. Sanitize command
    sanitizer = CommandSanitizer()
    check = sanitizer.sanitize(command)

    if not check.is_safe:
        logger.warning(f"[run_cmd] BLOCKED: {check.blocked_reason} — {command[:80]}")
        return {
            "success": False,
            "command": command,
            "error": f"Command blocked: {check.blocked_reason}",
            "blocked": True,
            "stdout": "",
            "stderr": f"⛔ Command blocked by safety check: {check.blocked_reason}",
            "return_code": -1,
        }

    if check.warnings:
        logger.info(f"[run_cmd] Warnings: {check.warnings}")

    # 2. Execute
    executor = CommandExecutor(default_timeout=timeout)

    from brain.utils import render_hub

    # A render or encode (ffmpeg, hyperframes/remotion render, the animate export) waits for the machine's render
    # slot and runs at low priority, so it never stacks on another render or makes the laptop stutter.
    render = render_hub.is_render_command(command)
    compressed = False
    try:
        if render:
            async with render_hub.render_slot(f"command {command[:40]}"):
                result = await executor.execute(command=render_hub.lower_priority(check.sanitized_command),
                                                cwd=cwd, timeout=timeout)
        else:
            # rtk (brain/utils/rtk.py): the same command, its output compressed for the model (failures kept,
            # exit code unchanged). The safety checks above judged the command as the model wrote it.
            from brain.utils import rtk

            ran = await asyncio.to_thread(rtk.rewrite, check.sanitized_command)
            result = await executor.execute(
                command=ran,
                cwd=cwd,
                timeout=timeout,
            )
            compressed = ran != check.sanitized_command

        logger.info(
            f"[run_cmd] Done: rc={result.return_code}, "
            f"time={result.duration:.2f}s, "
            f"stdout={len(result.stdout)} chars"
        )

        rtk_note = ({"rtk": True, "note": "Output compressed by rtk. For the full output run the same command as "
                                          f"'rtk bypass {command[:200]}'."} if not render and compressed else {})
        return {
            **rtk_note,
            "success": result.success,
            "command": command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.return_code,
            "duration": round(result.duration, 3),
            "timed_out": result.timed_out,
            "cwd": cwd,
            # Explains failures such as "no route to host" or "read-only file system".
            "sandbox": result.sandbox,
        }

    except Exception as e:
        logger.error(f"[run_cmd] Failed: {e}")
        return {
            "success": False,
            "command": command,
            "error": str(e),
            "stdout": "",
            "stderr": str(e),
            "return_code": -1,
        }
