"""
tool.sandbox.sandbox_manager — Manage sandboxed execution environments.

Creates temporary, isolated directories for safe command and
code execution.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class SandboxManager:
    """Manages sandboxed execution environments.

    Creates temporary directories for isolated command execution.
    Supports copying project files into the sandbox and cleaning up.
    """

    def __init__(
        self,
        base_dir: str | Path | None = None,
        auto_cleanup: bool = True,
    ) -> None:
        """Initialize the manager.

        Args:
            base_dir: Base directory for sandboxes. Uses system temp if None.
            auto_cleanup: Whether to auto-delete sandboxes on destroy.
        """
        self._base_dir = Path(base_dir) if base_dir else None
        self._auto_cleanup = auto_cleanup
        self._active_sandboxes: dict[str, Path] = {}

    def create_sandbox(
        self,
        name: str = "sandbox",
        copy_from: str | Path | None = None,
        copy_patterns: list[str] | None = None,
    ) -> Path:
        """Create a new sandbox directory.

        Args:
            name: Sandbox name (used as prefix).
            copy_from: Optional source directory to copy files from.
            copy_patterns: Optional glob patterns for files to copy.

        Returns:
            Path to the created sandbox directory.
        """
        prefix = f"agent_{name}_"
        sandbox_dir = Path(tempfile.mkdtemp(
            prefix=prefix,
            dir=str(self._base_dir) if self._base_dir else None,
        ))

        self._active_sandboxes[name] = sandbox_dir

        # Copy files if requested
        if copy_from:
            src = Path(copy_from)
            if src.exists():
                if copy_patterns:
                    for pattern in copy_patterns:
                        for f in src.glob(pattern):
                            if f.is_file():
                                dest = sandbox_dir / f.relative_to(src)
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(str(f), str(dest))
                else:
                    # Copy entire directory
                    shutil.copytree(str(src), str(sandbox_dir), dirs_exist_ok=True)

        logger.info(f"[sandbox] Created: {sandbox_dir}")
        return sandbox_dir

    def execute_in_sandbox(
        self,
        name: str,
        command: str,
        executor: Any = None,
    ) -> Any:
        """Execute a command in a sandbox.

        Args:
            name: Sandbox name.
            command: Command to execute.
            executor: CommandExecutor instance.

        Returns:
            Command result from the executor.

        Raises:
            ValueError: If sandbox not found.
        """
        sandbox_dir = self._active_sandboxes.get(name)
        if not sandbox_dir or not sandbox_dir.exists():
            raise ValueError(f"Sandbox not found: {name}")

        if executor:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're already in an async context, create a new task
                return executor.execute(command, cwd=str(sandbox_dir))
            else:
                return loop.run_until_complete(
                    executor.execute(command, cwd=str(sandbox_dir))
                )

        logger.debug(f"[sandbox] No executor provided for: {command}")
        return None

    def destroy_sandbox(self, name: str) -> bool:
        """Destroy a sandbox and clean up.

        Args:
            name: Sandbox name.

        Returns:
            True if successfully destroyed.
        """
        sandbox_dir = self._active_sandboxes.pop(name, None)
        if not sandbox_dir:
            return False

        if self._auto_cleanup and sandbox_dir.exists():
            shutil.rmtree(str(sandbox_dir), ignore_errors=True)
            logger.info(f"[sandbox] Destroyed: {sandbox_dir}")

        return True

    def get_sandbox_path(self, name: str) -> Path | None:
        """Get the path to an active sandbox."""
        return self._active_sandboxes.get(name)

    def list_sandboxes(self) -> list[str]:
        """List all active sandbox names."""
        return list(self._active_sandboxes.keys())

    def cleanup_all(self) -> int:
        """Destroy all active sandboxes.

        Returns:
            Number of sandboxes destroyed.
        """
        names = list(self._active_sandboxes.keys())
        for name in names:
            self.destroy_sandbox(name)
        return len(names)
