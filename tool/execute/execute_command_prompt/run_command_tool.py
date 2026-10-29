"""
tool.execute.execute_command_prompt.run_command_tool — Execute command tool for AI.

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

from typing import Any

from tool.execute.execute_command_prompt.command_executor import CommandExecutor
from tool.execute.execute_command_prompt.command_sanitizer import CommandSanitizer
from utils.logger import get_logger

logger = get_logger(__name__)


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

    try:
        result = await executor.execute(
            command=check.sanitized_command,
            cwd=cwd,
            timeout=timeout,
        )

        logger.info(
            f"[run_cmd] Done: rc={result.return_code}, "
            f"time={result.duration:.2f}s, "
            f"stdout={len(result.stdout)} chars"
        )

        return {
            "success": result.success,
            "command": command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.return_code,
            "duration": round(result.duration, 3),
            "timed_out": result.timed_out,
            "cwd": cwd,
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
