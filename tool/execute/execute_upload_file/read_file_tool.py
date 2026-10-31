"""
tool.execute.execute_upload_file.read_file_tool — Read file tool for AI.

AI tidak bisa langsung baca file dari filesystem.
Tapi AI punya tool ini untuk membaca isi file —
validasi dulu, lalu baca content, lalu return ke AI.

Mendukung:
- Text files (UTF-8, fallback errors='replace')
- Binary files → return metadata only (size, extension)
- Size limit 512KB untuk text preview
- Line range selection (start_line, end_line)
"""

from __future__ import annotations

import functools
import mimetypes
from pathlib import Path
from typing import Any

from utils.logger import get_logger
from utils.markdown_compact import compact_markdown, fit_markdown

logger = get_logger(__name__)

# Max text preview size
MAX_PREVIEW_BYTES = 512_000  # 512KB
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".epub"}
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_DOCUMENT_CHARS = 20_000
MARKDOWN_EXTENSIONS = {".md", ".markdown"}
COMPACT_MARKDOWN_ABOVE_CHARS = 6_000

# Text-friendly extensions
TEXT_EXTENSIONS: set[str] = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml", ".yml",
    ".toml", ".md", ".txt", ".csv", ".html", ".css", ".xml", ".sql",
    ".sh", ".bat", ".env", ".gitignore", ".ini", ".cfg", ".conf",
    ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp", ".rb",
    ".php", ".swift", ".kt", ".dart", ".lua", ".r", ".R",
    ".dockerfile", ".makefile", ".gradle", ".properties",
}


@functools.lru_cache(maxsize=8)
def _document_markdown(path: str, _modified: float) -> str:
    """Convert a document once. Reading a range would otherwise convert it each time."""
    from markitdown import MarkItDown

    return compact_markdown(str(MarkItDown(enable_plugins=False).convert_local(path).markdown))


def _read_document(
    path: Path, ext: str, size: int, start_line: int | None, end_line: int | None
) -> dict[str, Any] | None:
    """A PDF or office file as compact Markdown, or None if it cannot be converted."""
    if size > MAX_DOCUMENT_BYTES:
        return None
    try:
        markdown = _document_markdown(str(path), path.stat().st_mtime)
    except Exception as exc:  # noqa: BLE001 - the converter raises many types
        logger.warning(f"[read_file] Could not convert {path.name}: {exc}")
        return None
    lines = markdown.split("\n")
    result: dict[str, Any] = {
        "success": True,
        "file_path": str(path),
        "file_name": path.name,
        "extension": ext,
        "file_size": size,
        "file_size_human": _format_size(size),
        "is_text": True,
        "converted_from": ext.lstrip("."),
        "total_lines": len(lines),
    }
    if start_line is not None or end_line is not None:
        first = max(1, start_line or 1) - 1
        last = min(len(lines), end_line or len(lines))
        result.update(content="\n".join(lines[first:last]), shown_start=first + 1, shown_end=last)
    else:
        result["content"] = fit_markdown(markdown, MAX_DOCUMENT_CHARS)
        if len(markdown) > MAX_DOCUMENT_CHARS:
            result["note"] = "Long document, so some sections are skipped. Use start_line and end_line for more."
    return result


def read_file(
    file_path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Read a file and return its contents to the AI.

    AI memanggil tool ini untuk membaca file. File divalidasi dulu,
    lalu content dibaca dan dikembalikan.

    Args:
        file_path: Absolute or relative path to the file.
        start_line: Optional 1-indexed start line.
        end_line: Optional 1-indexed end line (inclusive).
        encoding: Text encoding (default utf-8).

    Returns:
        dict with keys: success, file_path, content, total_lines,
        file_size, extension, is_text, error (if any).
    """
    path = Path(file_path).resolve()

    # Basic checks
    if not path.exists():
        return {
            "success": False,
            "error": f"File not found: {file_path}",
            "file_path": str(path),
        }

    if not path.is_file():
        return {
            "success": False,
            "error": f"Not a file (maybe a directory): {file_path}",
            "file_path": str(path),
        }

    ext = path.suffix.lower()
    size = path.stat().st_size

    if ext in DOCUMENT_EXTENSIONS:
        document = _read_document(path, ext, size, start_line, end_line)
        if document:
            return document

    # Check if it's a text file
    is_text = ext in TEXT_EXTENSIONS or ext == ""
    if not is_text:
        # Try mimetype detection
        mime, _ = mimetypes.guess_type(str(path))
        is_text = mime is not None and mime.startswith("text/")

    if not is_text:
        # Binary file → return metadata only
        return {
            "success": True,
            "file_path": str(path),
            "file_name": path.name,
            "extension": ext,
            "file_size": size,
            "file_size_human": _format_size(size),
            "is_text": False,
            "content": f"[Binary file: {path.name} ({_format_size(size)}). "
                       f"Cannot display content. Extension: {ext}]",
            "total_lines": 0,
        }

    # Too large
    if size > MAX_PREVIEW_BYTES:
        return {
            "success": True,
            "file_path": str(path),
            "file_name": path.name,
            "extension": ext,
            "file_size": size,
            "file_size_human": _format_size(size),
            "is_text": True,
            "content": f"[File too large to read: {_format_size(size)}. "
                       f"Max preview: {_format_size(MAX_PREVIEW_BYTES)}. "
                       f"Use start_line/end_line to read a portion.]",
            "total_lines": 0,
            "truncated": True,
        }

    # Read content
    try:
        content = path.read_text(encoding=encoding, errors="replace")
    except Exception as e:
        return {
            "success": False,
            "error": f"Cannot read file: {e}",
            "file_path": str(path),
        }

    lines = content.split("\n")
    total_lines = len(lines)

    # Apply line range if specified
    shown_start = 1
    shown_end = total_lines
    if start_line is not None or end_line is not None:
        s = max(1, start_line or 1) - 1  # Convert to 0-indexed
        range_end = min(total_lines, end_line or total_lines)
        lines = lines[s:range_end]
        content = "\n".join(lines)
        shown_start = s + 1
        shown_end = range_end

    logger.info(
        f"[read_file] Read {path.name}: {total_lines} lines, {size} bytes, "
        f"showing L{shown_start}-L{shown_end}"
    )

    # An edit needs the exact text, so this only applies when no range was asked for.
    note: dict[str, str] = {}
    if (
        ext in MARKDOWN_EXTENSIONS
        and start_line is None
        and end_line is None
        and len(content) > COMPACT_MARKDOWN_ABOVE_CHARS
    ):
        content = fit_markdown(content, MAX_DOCUMENT_CHARS)
        note = {"note": "Compacted view, not the exact text. Read with start_line and end_line before file_patch."}

    return {
        **note,
        "success": True,
        "file_path": str(path),
        "file_name": path.name,
        "extension": ext,
        "file_size": size,
        "file_size_human": _format_size(size),
        "is_text": True,
        "content": content,
        "total_lines": total_lines,
        "shown_start": shown_start,
        "shown_end": shown_end,
    }


def list_directory(
    dir_path: str,
    max_depth: int = 2,
    show_hidden: bool = False,
) -> dict[str, Any]:
    """List contents of a directory tree.

    AI memanggil ini untuk melihat struktur folder.

    Args:
        dir_path: Path to directory.
        max_depth: How deep to recurse.
        show_hidden: Whether to show hidden files/dirs.

    Returns:
        dict with tree structure.
    """
    path = Path(dir_path).resolve()

    if not path.exists():
        return {"success": False, "error": f"Directory not found: {dir_path}"}
    if not path.is_dir():
        return {"success": False, "error": f"Not a directory: {dir_path}"}

    ignore_dirs = {
        "__pycache__", ".git", ".venv", "venv", "node_modules",
        ".pytest_cache", ".mypy_cache", ".tox", ".eggs",
        "dist", "build", ".egg-info", ".idea", ".vscode",
        "vendor", "storage", ".gradle",
    }

    tree_lines: list[str] = [f"📁 {path.name}/"]
    file_count = 0
    dir_count = 0

    def _walk(p: Path, depth: int, prefix: str) -> None:
        nonlocal file_count, dir_count
        if depth > max_depth:
            return

        try:
            entries = sorted(
                p.iterdir(),
                key=lambda x: (not x.is_dir(), x.name.lower()),
            )
        except PermissionError:
            tree_lines.append(f"{prefix}⛔ Permission denied")
            return

        for i, entry in enumerate(entries):
            if not show_hidden and entry.name.startswith("."):
                continue
            if entry.is_dir() and entry.name in ignore_dirs:
                continue

            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            extension = "    " if is_last else "│   "

            if entry.is_dir():
                dir_count += 1
                tree_lines.append(f"{prefix}{connector}📂 {entry.name}/")
                _walk(entry, depth + 1, prefix + extension)
            else:
                file_count += 1
                size = entry.stat().st_size
                tree_lines.append(
                    f"{prefix}{connector}📄 {entry.name} ({_format_size(size)})"
                )

    _walk(path, 0, "")

    return {
        "success": True,
        "dir_path": str(path),
        "tree": "\n".join(tree_lines),
        "file_count": file_count,
        "dir_count": dir_count,
    }


def _format_size(size: int) -> str:
    """Format bytes to human-readable."""
    if size >= 1_048_576:
        return f"{size / 1_048_576:.1f}MB"
    if size >= 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size}B"
