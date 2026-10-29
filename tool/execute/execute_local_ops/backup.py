"""Local backup manager for destructive file operations.

Backups live under ``<workspace>/.agent_memory/backups/<timestamp>-<name>``.
Each backup is a plain copy; no compression, no external deps.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

_BACKUP_DIRNAME = ".agent_memory"
_BACKUP_SUBDIR = "backups"


def _backup_root(workspace: Path) -> Path:
    root = workspace / _BACKUP_DIRNAME / _BACKUP_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def backup_file(workspace: Path, target: Path) -> Path | None:
    """Copy ``target`` into the backup directory. Returns the backup path."""
    if not target.exists() or not target.is_file():
        return None
    root = _backup_root(workspace)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_name = target.name.replace("/", "_").replace("\\", "_")
    backup_path = root / f"{stamp}-{safe_name}"
    counter = 1
    while backup_path.exists():
        backup_path = root / f"{stamp}-{counter}-{safe_name}"
        counter += 1
    try:
        shutil.copy2(target, backup_path)
        return backup_path
    except OSError as exc:
        logger.warning("local_ops: backup failed for %s: %s", target, exc)
        return None


__all__ = ["backup_file"]
