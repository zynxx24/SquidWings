"""
tool.execute.execute_edit_change_file.file_editor — Apply file edits.

Creates, modifies, and manages file content with full diff tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.exceptions import ToolError
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class EditResult:
    """Result of a file edit operation.

    Attributes:
        file_path: Path to the edited file.
        operation: Type of operation (create, edit, replace, insert, delete).
        success: Whether the edit succeeded.
        diff: Unified diff of the changes.
        original_content: Content before the edit.
        new_content: Content after the edit.
        lines_changed: Number of lines affected.
        message: Description of what was done.
    """
    file_path: str
    operation: str = ""
    success: bool = True
    diff: str = ""
    original_content: str = ""
    new_content: str = ""
    lines_changed: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for tool result."""
        return {
            "file_path": self.file_path,
            "operation": self.operation,
            "success": self.success,
            "lines_changed": self.lines_changed,
            "message": self.message,
            "diff": self.diff[:2000] if self.diff else "",
        }


class FileEditor:
    """Applies file edits with diff tracking.

    All operations return an EditResult that includes the
    unified diff of the changes made.
    """

    def __init__(self, backup_before_edit: bool = True) -> None:
        """Initialize the editor.

        Args:
            backup_before_edit: Whether to create backups before editing.
        """
        self._backup_enabled = backup_before_edit

    def create_file(
        self,
        file_path: str | Path,
        content: str,
        overwrite: bool = False,
    ) -> EditResult:
        """Create a new file with the given content.

        Args:
            file_path: Path to create the file at.
            content: File content to write.
            overwrite: Whether to overwrite if file exists.

        Returns:
            EditResult with the operation details.

        Raises:
            ToolError: If file exists and overwrite=False.
        """
        path = Path(file_path)

        if path.exists() and not overwrite:
            raise ToolError(
                message=f"File already exists: {path}",
                tool_name="edit_file",
            )

        original = path.read_text(encoding="utf-8") if path.exists() else ""

        # Create parent directories
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        diff = self._make_diff(original, content, str(path))

        result = EditResult(
            file_path=str(path),
            operation="create",
            success=True,
            diff=diff,
            original_content=original,
            new_content=content,
            lines_changed=len(content.splitlines()),
            message=f"Created file: {path}",
        )

        logger.info(f"[editor] Created: {path} ({result.lines_changed} lines)")
        return result

    def edit_file(
        self,
        file_path: str | Path,
        new_content: str,
    ) -> EditResult:
        """Replace the entire content of a file.

        Args:
            file_path: Path to the file.
            new_content: New content to write.

        Returns:
            EditResult with diff.

        Raises:
            ToolError: If file doesn't exist.
        """
        path = Path(file_path)
        if not path.exists():
            raise ToolError(
                message=f"File not found: {path}",
                tool_name="edit_file",
            )

        original = path.read_text(encoding="utf-8")
        path.write_text(new_content, encoding="utf-8")

        diff = self._make_diff(original, new_content, str(path))
        original_lines = set(original.splitlines())
        new_lines = set(new_content.splitlines())
        changed = len(original_lines.symmetric_difference(new_lines))

        result = EditResult(
            file_path=str(path),
            operation="edit",
            success=True,
            diff=diff,
            original_content=original,
            new_content=new_content,
            lines_changed=changed,
            message=f"Edited file: {path} ({changed} lines changed)",
        )

        logger.info(f"[editor] Edited: {path} ({changed} lines changed)")
        return result

    def replace_content(
        self,
        file_path: str | Path,
        target: str,
        replacement: str,
        count: int = 1,
    ) -> EditResult:
        """Replace a specific string in a file.

        Args:
            file_path: Path to the file.
            target: Text to find and replace.
            replacement: Replacement text.
            count: Maximum replacements (-1 for all).

        Returns:
            EditResult with diff.
        """
        path = Path(file_path)
        if not path.exists():
            raise ToolError(message=f"File not found: {path}", tool_name="edit_file")

        original = path.read_text(encoding="utf-8")

        if target not in original:
            return EditResult(
                file_path=str(path),
                operation="replace",
                success=False,
                message=f"Target text not found in {path}",
            )

        if count == -1:
            new_content = original.replace(target, replacement)
            replacements = original.count(target)
        else:
            new_content = original.replace(target, replacement, count)
            replacements = min(count, original.count(target))

        path.write_text(new_content, encoding="utf-8")
        diff = self._make_diff(original, new_content, str(path))

        result = EditResult(
            file_path=str(path),
            operation="replace",
            success=True,
            diff=diff,
            original_content=original,
            new_content=new_content,
            lines_changed=replacements,
            message=f"Replaced {replacements} occurrence(s) in {path}",
        )

        logger.info(f"[editor] Replace: {path} ({replacements} occurrences)")
        return result

    def insert_at_line(
        self,
        file_path: str | Path,
        line_number: int,
        content: str,
    ) -> EditResult:
        """Insert content at a specific line number.

        Args:
            file_path: Path to the file.
            line_number: 1-indexed line to insert before.
            content: Content to insert.

        Returns:
            EditResult with diff.
        """
        path = Path(file_path)
        if not path.exists():
            raise ToolError(message=f"File not found: {path}", tool_name="edit_file")

        original = path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)

        idx = max(0, min(line_number - 1, len(lines)))
        insert_lines = content.splitlines(keepends=True)
        if insert_lines and not insert_lines[-1].endswith("\n"):
            insert_lines[-1] += "\n"

        new_lines = lines[:idx] + insert_lines + lines[idx:]
        new_content = "".join(new_lines)

        path.write_text(new_content, encoding="utf-8")
        diff = self._make_diff(original, new_content, str(path))

        result = EditResult(
            file_path=str(path),
            operation="insert",
            success=True,
            diff=diff,
            original_content=original,
            new_content=new_content,
            lines_changed=len(insert_lines),
            message=f"Inserted {len(insert_lines)} lines at line {line_number}",
        )

        logger.info(f"[editor] Insert: {path} line {line_number} (+{len(insert_lines)})")
        return result

    def delete_lines(
        self,
        file_path: str | Path,
        start_line: int,
        end_line: int,
    ) -> EditResult:
        """Delete a range of lines from a file.

        Args:
            file_path: Path to the file.
            start_line: First line to delete (1-indexed, inclusive).
            end_line: Last line to delete (1-indexed, inclusive).

        Returns:
            EditResult with diff.
        """
        path = Path(file_path)
        if not path.exists():
            raise ToolError(message=f"File not found: {path}", tool_name="edit_file")

        original = path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)

        si = max(0, start_line - 1)
        ei = min(len(lines), end_line)
        deleted = ei - si

        new_lines = lines[:si] + lines[ei:]
        new_content = "".join(new_lines)

        path.write_text(new_content, encoding="utf-8")
        diff = self._make_diff(original, new_content, str(path))

        result = EditResult(
            file_path=str(path),
            operation="delete",
            success=True,
            diff=diff,
            original_content=original,
            new_content=new_content,
            lines_changed=deleted,
            message=f"Deleted lines {start_line}-{end_line} ({deleted} lines)",
        )

        logger.info(f"[editor] Delete: {path} lines {start_line}-{end_line}")
        return result

    @staticmethod
    def _make_diff(original: str, modified: str, filename: str) -> str:
        """Generate a unified diff between two strings."""
        import difflib

        orig_lines = original.splitlines(keepends=True)
        mod_lines = modified.splitlines(keepends=True)

        diff = difflib.unified_diff(
            orig_lines,
            mod_lines,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
        return "".join(diff)
