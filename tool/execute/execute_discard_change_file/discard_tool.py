"""
tool.execute.execute_discard_change_file.discard_tool — Discard tool wrapper.

Fungsi discard_changes() yang siap di-register ke ToolRegistry.
Menghubungkan DiscardHandler, BackupManager, dan GitIntegration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tool.execute.execute_discard_change_file.discard_handler import (
    DiscardHandler,
    DiscardResult,
)
from tool.execute.execute_discard_change_file.git_integration import GitIntegration
from tool.execute.execute_edit_change_file.backup_manager import BackupManager
from utils.logger import get_logger

logger = get_logger(__name__)


def discard_changes(
    file_path: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Discard / revert changes made to a file.

    Tries to restore from agent backup first, then falls back
    to git checkout HEAD.

    Args:
        file_path: Path to the file to revert (absolute or relative).
        workspace: Optional workspace root for backup lookup.

    Returns:
        dict with keys: success, method, message, file_path.
    """
    project_root = Path(workspace) if workspace else Path.cwd()
    backup_mgr = BackupManager(project_root)
    git_integ = GitIntegration(str(project_root))

    handler = DiscardHandler(
        backup_manager=backup_mgr,
        git_integration=git_integ,
    )

    # Resolve file_path: if relative, resolve against workspace
    resolved = Path(file_path)
    if not resolved.is_absolute():
        resolved = project_root / resolved

    result: DiscardResult = handler.discard(resolved)

    outcome = {
        "success": result.success,
        "method": result.method,
        "message": result.message,
        "file_path": result.file_path,
    }

    if result.success:
        logger.info(f"[discard_tool] Reverted: {result.file_path} via {result.method}")
    else:
        logger.warning(f"[discard_tool] Failed to revert: {result.file_path} — {result.message}")

    return outcome
