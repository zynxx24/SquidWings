"""
tool.execute.execute_upload_file.upload_handler — Handle file uploads.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class UploadResult:
    """Result of a file upload operation."""
    source_path: str
    destination_path: str
    success: bool = True
    file_size: int = 0
    message: str = ""


class UploadHandler:
    """Handles file uploads (local placement)."""

    def __init__(self, storage_root: str | Path | None = None) -> None:
        self._root = Path(storage_root) if storage_root else Path.cwd() / ".agent_uploads"

    def upload(self, source: str | Path, destination: str | Path | None = None) -> UploadResult:
        """Upload (copy) a file to the storage location.

        Args:
            source: Source file path.
            destination: Optional destination path. Defaults to storage_root/filename.

        Returns:
            UploadResult with outcome.
        """
        src = Path(source)
        if not src.exists():
            return UploadResult(str(src), "", success=False, message=f"Source not found: {src}")

        dst = Path(destination) if destination else self._root / src.name

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))

        size = dst.stat().st_size
        logger.info(f"[upload] {src} → {dst} ({size} bytes)")

        return UploadResult(
            source_path=str(src),
            destination_path=str(dst),
            success=True,
            file_size=size,
            message=f"Uploaded to {dst}",
        )
