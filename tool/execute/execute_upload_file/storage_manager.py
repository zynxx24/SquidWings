"""
tool.execute.execute_upload_file.storage_manager — Manage uploaded file storage.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class StorageManager:
    """Manages the storage location for uploaded files."""

    def __init__(self, storage_root: str | Path | None = None) -> None:
        self._root = Path(storage_root) if storage_root else Path.cwd() / ".agent_uploads"
        self._root.mkdir(parents=True, exist_ok=True)

    def store(self, source: str | Path, relative_path: str | None = None) -> Path:
        """Store a file in managed storage.

        Args:
            source: Source file path.
            relative_path: Optional relative path within storage.

        Returns:
            Path to the stored file.
        """
        src = Path(source)
        dst = self._root / relative_path if relative_path else self._root / src.name

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        logger.info(f"[storage] Stored: {dst}")
        return dst

    def retrieve(self, filename: str) -> Path | None:
        """Get the path to a stored file.

        Args:
            filename: Name or relative path of the file.

        Returns:
            Path if found, None otherwise.
        """
        path = self._root / filename
        return path if path.exists() else None

    def delete(self, filename: str) -> bool:
        """Delete a stored file.

        Args:
            filename: Name or relative path.

        Returns:
            True if deleted.
        """
        path = self._root / filename
        if path.exists():
            path.unlink()
            logger.info(f"[storage] Deleted: {path}")
            return True
        return False

    def list_files(self) -> list[dict[str, Any]]:
        """List all stored files.

        Returns:
            List of dicts with name and size.
        """
        files = []
        for f in self._root.rglob("*"):
            if f.is_file():
                files.append({
                    "name": str(f.relative_to(self._root)),
                    "size": f.stat().st_size,
                })
        return files

    @property
    def storage_root(self) -> Path:
        return self._root
