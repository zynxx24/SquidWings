"""
tool.execute.execute_discard_change_file.git_integration — Git operations wrapper.

Wraps common git commands for file-level operations like
checkout, diff, and status.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)


class GitIntegration:
    """Wraps git commands for file operations.

    Provides file-level git operations: checkout, diff, status.
    All operations are synchronous (subprocess.run).
    """

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self._root = str(Path(repo_root)) if repo_root else "."

    def is_git_repo(self) -> bool:
        """Check if the current directory is a git repository."""
        return self._run(["git", "rev-parse", "--git-dir"]).returncode == 0

    def checkout_file(self, file_path: str) -> bool:
        """Revert a file to its state at HEAD.

        Args:
            file_path: Path to the file to checkout.

        Returns:
            True if checkout succeeded.
        """
        result = self._run(["git", "checkout", "HEAD", "--", file_path])
        success = result.returncode == 0
        if success:
            logger.info(f"[git] Checked out: {file_path}")
        else:
            logger.warning(f"[git] Checkout failed: {result.stderr}")
        return success

    def get_diff(self, file_path: str | None = None) -> str:
        """Get the diff for a file or the entire repo.

        Args:
            file_path: Optional specific file. None for all changes.

        Returns:
            Diff output string.
        """
        cmd = ["git", "diff"]
        if file_path:
            cmd.append(file_path)
        result = self._run(cmd)
        return str(result.stdout or "")

    def get_status(self, file_path: str | None = None) -> str:
        """Get git status for a file or the repo.

        Args:
            file_path: Optional specific file.

        Returns:
            Status output string.
        """
        cmd = ["git", "status", "--porcelain"]
        if file_path:
            cmd.append(file_path)
        result = self._run(cmd)
        return str(result.stdout or "")

    def is_modified(self, file_path: str) -> bool:
        """Check if a file has uncommitted changes.

        Args:
            file_path: Path to check.

        Returns:
            True if the file has changes.
        """
        status = self.get_status(file_path)
        return bool(status.strip())

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        """Run a git command.

        Args:
            cmd: Command and arguments.

        Returns:
            CompletedProcess result.
        """
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=self._root,
                timeout=10,
            )
        except Exception as e:
            logger.error(f"[git] Command failed: {cmd}: {e}")
            return subprocess.CompletedProcess(
                cmd, returncode=1, stdout="", stderr=str(e)
            )
