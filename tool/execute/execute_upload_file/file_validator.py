"""
tool.execute.execute_upload_file.file_validator — Validate files for upload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

ALLOWED_EXTENSIONS: set[str] = {
    ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".md",
    ".txt", ".csv", ".html", ".css", ".xml", ".sql", ".sh", ".bat",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".pdf", ".zip", ".tar", ".gz",
}

MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50MB


@dataclass
class FileValidation:
    """Result of file validation."""
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    file_size: int = 0
    file_extension: str = ""


class FileValidator:
    """Validates files before upload."""

    def __init__(
        self,
        allowed_extensions: set[str] | None = None,
        max_file_size: int = MAX_FILE_SIZE,
    ) -> None:
        self._allowed = allowed_extensions or ALLOWED_EXTENSIONS
        self._max_size = max_file_size

    def validate(self, file_path: str | Path) -> FileValidation:
        """Validate a file for upload.

        Checks: exists, readable, size within limit, extension allowed.

        Args:
            file_path: Path to validate.

        Returns:
            FileValidation result.
        """
        path = Path(file_path)
        result = FileValidation(file_extension=path.suffix)

        if not path.exists():
            result.is_valid = False
            result.errors.append(f"File not found: {path}")
            return result

        if not path.is_file():
            result.is_valid = False
            result.errors.append(f"Not a regular file: {path}")
            return result

        result.file_size = path.stat().st_size

        if result.file_size > self._max_size:
            result.is_valid = False
            result.errors.append(
                f"File too large: {result.file_size} bytes (max: {self._max_size})"
            )

        if self._allowed and path.suffix.lower() not in self._allowed:
            result.is_valid = False
            result.errors.append(
                f"Extension not allowed: {path.suffix}"
            )

        return result
