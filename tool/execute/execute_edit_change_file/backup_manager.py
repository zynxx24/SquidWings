"""
tool.execute.execute_edit_change_file.backup_manager — File backup management.

Creates backups of files before editing and supports restoration.
Backups are stored in a .agent_backups/ directory.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)


class BackupManager:
    """Manages file backups for safe editing.

    Creates timestamped backups in .agent_backups/ before
    any file edit, allowing rollback if needed.
    """

    BACKUP_DIR_NAME: str = ".agent_backups"
    MAX_BACKUPS_PER_FILE: int = 10

    def __init__(self, project_root: str | Path | None = None) -> None:
        """Initialize the backup manager.

        Args:
            project_root: Root directory for the project. Backups are
                          stored in {project_root}/.agent_backups/.
        """
        self._root = Path(project_root) if project_root else Path.cwd()
        self._backup_dir = self._root / self.BACKUP_DIR_NAME

    def backup(self, file_path: str | Path) -> Path | None:
        """Create a backup of a file.

        Args:
            file_path: Path to the file to backup.

        Returns:
            Path to the backup file, or None if file doesn't exist.
        """
        src = Path(file_path)
        if not src.exists():
            logger.debug(f"[backup] Skipped (not exists): {src}")
            return None

        # Create backup directory structure
        relative = self._get_relative_path(src)
        timestamp = int(time.time() * 1000)
        backup_name = f"{relative.name}.{timestamp}.bak"
        backup_path = self._backup_dir / relative.parent / backup_name

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(backup_path))

        logger.info(f"[backup] Created: {backup_path}")

        # Cleanup old backups
        self._cleanup_old_backups(relative)

        return backup_path

    def restore(self, backup_path: str | Path) -> Path:
        """Restore a file from a backup.

        Args:
            backup_path: Path to the backup file.

        Returns:
            Path to the restored file.

        Raises:
            FileNotFoundError: If backup doesn't exist.
        """
        bak = Path(backup_path)
        if not bak.exists():
            raise FileNotFoundError(f"Backup not found: {bak}")

        # Parse original path from backup path
        original_path = self._backup_to_original(bak)
        original_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(bak), str(original_path))

        logger.info(f"[backup] Restored: {bak} → {original_path}")
        return original_path

    def get_latest_backup(self, file_path: str | Path) -> Path | None:
        """Get the most recent backup for a file.

        Args:
            file_path: Original file path.

        Returns:
            Path to the latest backup, or None if no backups exist.
        """
        backups = self.list_backups(file_path)
        return backups[-1] if backups else None

    def list_backups(self, file_path: str | Path) -> list[Path]:
        """List all backups for a file, sorted by timestamp.

        Args:
            file_path: Original file path.

        Returns:
            list: Backup paths sorted oldest to newest.
        """
        relative = self._get_relative_path(Path(file_path))
        backup_dir = self._backup_dir / relative.parent

        if not backup_dir.exists():
            return []

        pattern = f"{relative.name}.*.bak"
        backups = sorted(backup_dir.glob(pattern))
        return backups

    def cleanup_old_backups(self, max_age_hours: int = 24) -> int:
        """Clean up backups older than max_age_hours.

        Args:
            max_age_hours: Maximum backup age in hours.

        Returns:
            int: Number of backups deleted.
        """
        if not self._backup_dir.exists():
            return 0

        cutoff = time.time() - (max_age_hours * 3600)
        deleted = 0

        for bak in self._backup_dir.rglob("*.bak"):
            if bak.stat().st_mtime < cutoff:
                bak.unlink()
                deleted += 1

        if deleted:
            logger.info(f"[backup] Cleaned up {deleted} old backups")

        return deleted

    def _cleanup_old_backups(self, relative: Path) -> None:
        """Keep only the most recent N backups per file."""
        backups = self.list_backups(self._root / relative)
        if len(backups) > self.MAX_BACKUPS_PER_FILE:
            to_remove = backups[:-self.MAX_BACKUPS_PER_FILE]
            for old_bak in to_remove:
                old_bak.unlink()

    def _get_relative_path(self, file_path: Path) -> Path:
        """Get path relative to project root."""
        try:
            return file_path.resolve().relative_to(self._root.resolve())
        except ValueError:
            return Path(file_path.name)

    def _backup_to_original(self, backup_path: Path) -> Path:
        """Derive the original file path from a backup path."""
        relative_to_backup = backup_path.relative_to(self._backup_dir)
        # Remove the timestamp.bak suffix
        name_parts = backup_path.name.rsplit(".", 2)
        original_name = name_parts[0] if len(name_parts) >= 3 else backup_path.name
        return self._root / relative_to_backup.parent / original_name
