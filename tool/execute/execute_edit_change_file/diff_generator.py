"""
tool.execute.execute_edit_change_file.diff_generator — Generate diffs.

Uses Python's difflib to generate unified and color-coded diffs
between original and modified file content.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DiffResult:
    """Result of a diff generation.

    Attributes:
        unified: Unified diff string.
        added_lines: Number of added lines.
        removed_lines: Number of removed lines.
        changed_lines: Number of changed lines (approx).
        has_changes: Whether any changes were detected.
    """
    unified: str = ""
    added_lines: int = 0
    removed_lines: int = 0
    changed_lines: int = 0
    has_changes: bool = False


class DiffGenerator:
    """Generates diffs between text content.

    Supports unified diff format and provides statistics
    about the changes.
    """

    @staticmethod
    def generate(
        original: str,
        modified: str,
        filename: str = "file",
        context_lines: int = 3,
    ) -> DiffResult:
        """Generate a unified diff between two strings.

        Args:
            original: Original content.
            modified: Modified content.
            filename: Filename for diff header.
            context_lines: Number of context lines around changes.

        Returns:
            DiffResult with unified diff and stats.
        """
        orig_lines = original.splitlines(keepends=True)
        mod_lines = modified.splitlines(keepends=True)

        diff_lines = list(difflib.unified_diff(
            orig_lines,
            mod_lines,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            n=context_lines,
        ))

        unified = "".join(diff_lines)

        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

        return DiffResult(
            unified=unified,
            added_lines=added,
            removed_lines=removed,
            changed_lines=min(added, removed),
            has_changes=bool(diff_lines),
        )

    @staticmethod
    def colorize(diff_text: str) -> str:
        """Add ANSI color codes to a unified diff.

        Args:
            diff_text: Unified diff string.

        Returns:
            Color-coded diff string for terminal display.
        """
        colored_lines: list[str] = []
        red = "\033[31m"
        green = "\033[32m"
        cyan = "\033[36m"
        reset = "\033[0m"

        for line in diff_text.splitlines(keepends=True):
            if line.startswith("+++") or line.startswith("---"):
                colored_lines.append(f"{cyan}{line}{reset}")
            elif line.startswith("+"):
                colored_lines.append(f"{green}{line}{reset}")
            elif line.startswith("-"):
                colored_lines.append(f"{red}{line}{reset}")
            elif line.startswith("@@"):
                colored_lines.append(f"{cyan}{line}{reset}")
            else:
                colored_lines.append(line)

        return "".join(colored_lines)

    @staticmethod
    def similarity_ratio(original: str, modified: str) -> float:
        """Calculate similarity ratio between two texts.

        Args:
            original: Original content.
            modified: Modified content.

        Returns:
            float: Similarity ratio (0.0 to 1.0).
        """
        return difflib.SequenceMatcher(None, original, modified).ratio()
