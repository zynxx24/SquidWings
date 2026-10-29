"""
tool.execute.execute_command_prompt.command_executor — Execute shell commands.

Runs shell commands in a subprocess with timeout handling,
output capture, and safety checks.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from utils.exceptions import ToolError
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CommandResult:
    """Result of a command execution.

    Attributes:
        command: The command that was executed.
        stdout: Standard output.
        stderr: Standard error.
        return_code: Exit code.
        duration: Execution time in seconds.
        timed_out: Whether the command timed out.
        cwd: Working directory used.
    """
    command: str
    stdout: str = ""
    stderr: str = ""
    return_code: int = -1
    duration: float = 0.0
    timed_out: bool = False
    cwd: str = "."

    @property
    def success(self) -> bool:
        """Whether the command completed successfully."""
        return self.return_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        """Combined stdout + stderr output."""
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append(f"[stderr] {self.stderr.strip()}")
        return "\n".join(parts) if parts else "(no output)"

    def to_dict(self) -> dict[str, Any]:
        """Serialize for tool result."""
        return {
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_code": self.return_code,
            "success": self.success,
            "duration": round(self.duration, 3),
            "timed_out": self.timed_out,
        }


class CommandExecutor:
    """Executes shell commands in a subprocess.

    Features:
    - Async execution with asyncio.create_subprocess_shell
    - Configurable timeout
    - Full stdout/stderr capture
    - Duration tracking
    - Safety integration with CommandSanitizer
    """

    DEFAULT_TIMEOUT: float = 30.0
    MAX_OUTPUT_SIZE: int = 100_000  # 100KB max output

    def __init__(
        self,
        default_timeout: float | None = None,
        default_cwd: str = ".",
    ) -> None:
        """Initialize the executor.

        Args:
            default_timeout: Default timeout in seconds.
            default_cwd: Default working directory.
        """
        self._timeout = default_timeout or self.DEFAULT_TIMEOUT
        self._default_cwd = default_cwd

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Execute a shell command.

        Args:
            command: The command string to execute.
            cwd: Working directory. Defaults to default_cwd.
            timeout: Timeout in seconds. Defaults to default_timeout.
            env: Optional environment variables.

        Returns:
            CommandResult with output and status.

        Raises:
            ToolError: If command execution fails unexpectedly.
        """
        effective_cwd = cwd or self._default_cwd
        effective_timeout = timeout or self._timeout

        logger.info(
            f"[cmd_exec] Running: {command[:100]} "
            f"(cwd={effective_cwd}, timeout={effective_timeout}s)"
        )

        start = time.monotonic()
        timed_out = False

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=effective_cwd,
                env=env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=effective_timeout,
                )
            except TimeoutError:
                timed_out = True
                process.kill()
                await process.wait()
                stdout_bytes = b""
                stderr_bytes = f"Command timed out after {effective_timeout}s".encode()

            duration = time.monotonic() - start

            stdout = self._decode_output(stdout_bytes)
            stderr = self._decode_output(stderr_bytes)

            result = CommandResult(
                command=command,
                stdout=stdout,
                stderr=stderr,
                return_code=process.returncode if process.returncode is not None else -1,
                duration=duration,
                timed_out=timed_out,
                cwd=effective_cwd,
            )

            level = "info" if result.success else "warning"
            getattr(logger, level)(
                f"[cmd_exec] Done: rc={result.return_code}, "
                f"time={result.duration:.2f}s, "
                f"stdout={len(stdout)} chars"
            )

            return result

        except Exception as e:
            duration = time.monotonic() - start
            logger.error(f"[cmd_exec] Failed: {e}")
            raise ToolError(
                message=f"Command execution failed: {e}",
                tool_name="execute_command",
                original_error=e,
            ) from e

    def _decode_output(self, data: bytes) -> str:
        """Decode command output bytes, handling encoding issues.

        Args:
            data: Raw bytes from subprocess.

        Returns:
            Decoded string (truncated if too large).
        """
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = str(data)

        if len(text) > self.MAX_OUTPUT_SIZE:
            text = text[:self.MAX_OUTPUT_SIZE] + "\n\n[... output truncated ...]"

        return text
