"""
tool.sandbox.resource_limiter — Enforce resource limits on execution.

Limits execution time, memory usage, and file size to prevent
runaway processes.
"""

from __future__ import annotations

import resource as sys_resource
import signal
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ResourceLimits:
    """Resource limits for a sandboxed execution.

    Attributes:
        max_execution_time: Maximum execution time in seconds.
        max_memory_mb: Maximum memory usage in MB.
        max_file_size_mb: Maximum output file size in MB.
        max_processes: Maximum number of child processes.
    """
    max_execution_time: float = 30.0
    max_memory_mb: int = 512
    max_file_size_mb: int = 100
    max_processes: int = 10


class ResourceLimiter:
    """Enforces resource limits on command execution.

    Uses POSIX resource limits (via the `resource` module)
    and signal-based timeouts.
    """

    DEFAULT_LIMITS = ResourceLimits()

    def __init__(self, limits: ResourceLimits | None = None) -> None:
        """Initialize with resource limits.

        Args:
            limits: ResourceLimits to enforce. Uses defaults if None.
        """
        self._limits = limits or self.DEFAULT_LIMITS

    @property
    def limits(self) -> ResourceLimits:
        """Current resource limits."""
        return self._limits

    def enforce(self, limits: ResourceLimits | None = None) -> None:
        """Apply resource limits to the current process.

        NOTE: This affects the current process. Typically called
        in a child process (subprocess preexec_fn).

        Args:
            limits: Limits to apply. Uses instance limits if None.
        """
        lim = limits or self._limits

        # Memory limit (soft, hard) in bytes
        mem_bytes = lim.max_memory_mb * 1024 * 1024
        try:
            sys_resource.setrlimit(
                sys_resource.RLIMIT_AS,
                (mem_bytes, mem_bytes),
            )
        except (ValueError, OSError) as e:
            logger.debug(f"[limiter] Cannot set memory limit: {e}")

        # File size limit
        file_bytes = lim.max_file_size_mb * 1024 * 1024
        try:
            sys_resource.setrlimit(
                sys_resource.RLIMIT_FSIZE,
                (file_bytes, file_bytes),
            )
        except (ValueError, OSError) as e:
            logger.debug(f"[limiter] Cannot set file size limit: {e}")

        # Process limit
        try:
            sys_resource.setrlimit(
                sys_resource.RLIMIT_NPROC,
                (lim.max_processes, lim.max_processes),
            )
        except (ValueError, OSError) as e:
            logger.debug(f"[limiter] Cannot set process limit: {e}")

        logger.debug(
            f"[limiter] Enforced: mem={lim.max_memory_mb}MB, "
            f"fsize={lim.max_file_size_mb}MB, "
            f"procs={lim.max_processes}"
        )

    @contextmanager
    def timeout_context(
        self,
        seconds: float | None = None,
    ) -> Generator[None, None, None]:
        """Context manager that raises TimeoutError after N seconds.

        Uses SIGALRM on Unix systems.

        Args:
            seconds: Timeout in seconds. Uses limit default if None.

        Yields:
            None

        Raises:
            TimeoutError: If execution exceeds the timeout.
        """
        timeout = seconds or self._limits.max_execution_time

        def handler(signum: int, frame: Any) -> None:
            raise TimeoutError(
                f"Execution timed out after {timeout}s"
            )

        old_handler = signal.signal(signal.SIGALRM, handler)
        signal.setitimer(signal.ITIMER_REAL, timeout)

        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)

    def get_preexec_fn(self) -> Any:
        """Get a preexec_fn for subprocess that applies limits.

        Returns:
            Callable to pass as preexec_fn to subprocess.Popen.
        """
        limits = self._limits

        def _apply_limits() -> None:
            self.enforce(limits)

        return _apply_limits

    def check_current_usage(self) -> dict[str, Any]:
        """Get current resource usage stats.

        Returns:
            dict with memory and CPU usage info.
        """
        usage = sys_resource.getrusage(sys_resource.RUSAGE_SELF)
        return {
            "max_rss_mb": round(usage.ru_maxrss / 1024, 1),  # KB → MB
            "user_time": round(usage.ru_utime, 3),
            "system_time": round(usage.ru_stime, 3),
        }
