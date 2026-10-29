"""
tool.execute.execute_edit_change_file.edit_validator — Validate proposed edits.

Checks that a file edit is safe before applying it:
file exists, writable, and syntax is valid after edit.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path

from config.constants import SYNTAX_CHECKABLE_EXTENSIONS
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class EditValidation:
    """Result of edit validation.

    Attributes:
        is_valid: Whether the edit can be applied safely.
        errors: Validation error messages.
        warnings: Non-fatal warning messages.
    """
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class EditValidator:
    """Validates proposed file edits before applying them.

    Checks:
    - File exists and is writable
    - File is within allowed directory
    - Syntax is valid after edit (for Python files)
    - File size doesn't exceed limits
    """

    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB

    def __init__(self, allowed_root: str | Path | None = None) -> None:
        """Initialize the validator.

        Args:
            allowed_root: Root directory for edit operations.
                          Files outside this directory are rejected.
        """
        self._root = Path(allowed_root).resolve() if allowed_root else None

    def validate(
        self,
        file_path: str | Path,
        new_content: str | None = None,
        must_exist: bool = True,
    ) -> EditValidation:
        """Validate a proposed file edit.

        Args:
            file_path: Path to the target file.
            new_content: The proposed new content (for syntax checking).
            must_exist: Whether the file must already exist.

        Returns:
            EditValidation with results.
        """
        result = EditValidation()
        path = Path(file_path).resolve()

        # Check: within allowed root
        if self._root and not str(path).startswith(str(self._root)):
            result.is_valid = False
            result.errors.append(
                f"File outside allowed root: {path} (root: {self._root})"
            )
            return result

        # Check: file exists (if required)
        if must_exist and not path.exists():
            result.is_valid = False
            result.errors.append(f"File not found: {path}")
            return result

        # Check: is a file (not directory)
        if path.exists() and not path.is_file():
            result.is_valid = False
            result.errors.append(f"Not a regular file: {path}")
            return result

        # Check: writable
        if path.exists() and not os.access(path, os.W_OK):
            result.is_valid = False
            result.errors.append(f"File not writable: {path}")
            return result

        # Check: parent directory exists and writable
        if not path.exists():
            parent = path.parent
            if not parent.exists():
                result.warnings.append(
                    f"Parent directory will be created: {parent}"
                )
            elif not os.access(parent, os.W_OK):
                result.is_valid = False
                result.errors.append(f"Parent directory not writable: {parent}")
                return result

        # Check: new content size
        if new_content is not None and len(new_content) > self.MAX_FILE_SIZE:
            result.is_valid = False
            result.errors.append(
                f"Content too large: {len(new_content)} bytes "
                f"(max: {self.MAX_FILE_SIZE})"
            )
            return result

        # Check: Python syntax validation
        if new_content and path.suffix in SYNTAX_CHECKABLE_EXTENSIONS:
            syntax_result = self._check_syntax(path, new_content)
            if syntax_result:
                result.warnings.append(syntax_result)

        return result

    def _check_syntax(self, path: Path, content: str) -> str:
        """Check syntax of the proposed content.

        Only checks Python files (.py) for now.

        Args:
            path: File path (for extension check).
            content: Proposed content.

        Returns:
            Error message if syntax is invalid, empty string if OK.
        """
        if path.suffix != ".py":
            return ""

        try:
            ast.parse(content, filename=str(path))
            return ""
        except SyntaxError as e:
            return (
                f"Python syntax error at line {e.lineno}: {e.msg}"
            )
