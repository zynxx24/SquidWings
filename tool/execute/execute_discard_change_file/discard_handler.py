"""
tool.execute.execute_discard_change_file.discard_handler — Revert file changes.

Reverts file changes using backups or git checkout.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DiscardResult:
    """Result of a discard/revert operation.

    Attributes:
        file_path: Path of the reverted file.
        success: Whether the revert succeeded.
        method: Method used (backup, git, manual).
        message: Description of what happened.
    """
    file_path: str
    success: bool = True
    method: str = ""
    message: str = ""


class DiscardHandler:
    """Handles discarding/reverting file changes.

    Strategies (tried in order):
    1. Restore from agent backup
    2. Git checkout (if in a git repo)
    """

    def __init__(
        self,
        backup_manager: Any = None,
        git_integration: Any = None,
    ) -> None:
        """Initialize the handler.

        Args:
            backup_manager: BackupManager instance.
            git_integration: GitIntegration instance.
        """
        self._backup = backup_manager
        self._git = git_integration

    def discard(self, file_path: str | Path) -> DiscardResult:
        """Discard changes to a file.

        Tries backup restore first, then git checkout.

        Args:
            file_path: Path to the file to revert.

        Returns:
            DiscardResult with the outcome.
        """
        path = Path(file_path)

        # Strategy 1: Restore from backup
        if self._backup:
            latest = self._backup.get_latest_backup(str(path))
            if latest:
                try:
                    self._backup.restore(latest)
                    logger.info(f"[discard] Restored from backup: {path}")
                    return DiscardResult(
                        file_path=str(path),
                        success=True,
                        method="backup",
                        message=f"Restored from backup: {latest.name}",
                    )
                except Exception as e:
                    logger.warning(f"[discard] Backup restore failed: {e}")

        # Strategy 2: Git checkout
        if self._git:
            try:
                result = self._git.checkout_file(str(path))
                if result:
                    logger.info(f"[discard] Git checkout: {path}")
                    return DiscardResult(
                        file_path=str(path),
                        success=True,
                        method="git",
                        message="Reverted to git HEAD",
                    )
            except Exception as e:
                logger.warning(f"[discard] Git checkout failed: {e}")

        return DiscardResult(
            file_path=str(path),
            success=False,
            message="No backup or git history available for this file",
        )
