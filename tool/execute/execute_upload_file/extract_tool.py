"""extract_files: turn every document in the workspace into text and images the agent can use."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from config.settings import get_settings
from tool.execute.execute_local_ops.path_guard import (
    PathGuardError,
    resolve_inside,
    resolve_workspace,
)
from utils.extract import DOCUMENT_SUFFIXES, extract, save_images
from utils.logger import get_logger
from utils.markdown_compact import compact_markdown

logger = get_logger(__name__)

OUTPUT_DIR = "extracted"
MAX_FILES = 100
MAX_FILE_BYTES = 50 * 1024 * 1024
_SKIP_DIRS = {OUTPUT_DIR, "node_modules", "__pycache__", "graphify-out", "venv"}


def _documents(root: Path, start: Path) -> list[Path]:
    if start.is_file():
        return [start]
    found = []
    for path in sorted(start.rglob("*")):
        parts = path.relative_to(root).parts
        hidden = any(part.startswith(".") or part in _SKIP_DIRS for part in parts)
        if not hidden and path.is_file() and path.suffix.lower() in DOCUMENT_SUFFIXES:
            found.append(path)
    return found


def _extract_one(root: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(root)
    target = root / OUTPUT_DIR / relative.with_suffix(relative.suffix + ".md")
    image_dir = target.with_name(f"{relative.name}_img")
    entry: dict[str, Any] = {"file": str(relative), "text": str(target.relative_to(root))}
    if path.stat().st_size > MAX_FILE_BYTES:
        return {**entry, "error": "file is larger than 50 MB"}
    if target.exists() and target.stat().st_mtime >= path.stat().st_mtime:
        pictures = sorted(image_dir.iterdir()) if image_dir.is_dir() else []
        return {**entry, "images": [str(p.relative_to(root)) for p in pictures], "cached": True}
    prefix = f"{relative.name}_img/"
    try:
        result = extract(path.read_bytes(), path.name, prefix, ocr=True)
    except Exception as exc:  # noqa: BLE001 - one broken file must not stop the others
        logger.warning(f"[extract_files] {relative}: {exc}")
        return {**entry, "error": str(exc)[:200]}
    target.parent.mkdir(parents=True, exist_ok=True)
    text = compact_markdown(result.markdown)
    target.write_text(text, encoding="utf-8")
    saved = save_images(result, image_dir)
    return {**entry, "chars": len(text), "pages": result.pages, "scanned": result.scanned,
            "images": [str(p.relative_to(root)) for p in saved]}


def _extract_files_sync(root: Path, start: Path) -> dict[str, Any]:
    documents = _documents(root, start)
    files = [_extract_one(root, path) for path in documents[:MAX_FILES]]
    return {"success": True, "files": files, "more": max(0, len(documents) - MAX_FILES),
            "note": "Read a text with read_file. Show an image with ![name](path)."}


async def extract_files(path: str = ".", workspace_path: str | None = None) -> dict[str, Any]:
    """Extract text and images from one document, or from every document under a folder."""
    root = resolve_workspace(workspace_path, get_settings().project_root).resolve()
    try:
        start = resolve_inside(root, path, must_exist=True)
    except PathGuardError as exc:
        return {"success": False, "error": str(exc)}
    return await asyncio.to_thread(_extract_files_sync, root, start)


__all__ = ["extract_files"]
