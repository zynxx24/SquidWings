"""
utils.helpers — Miscellaneous helper functions.

Small, stateless utility functions used across the codebase.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any


def truncate_string(text: str, max_length: int = 200, suffix: str = "...") -> str:
    """Truncate a string to max_length, adding suffix if truncated.

    Args:
        text: String to truncate.
        max_length: Maximum length (including suffix).
        suffix: String to append when truncated.

    Returns:
        Truncated string.
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def format_tokens(count: int) -> str:
    """Format a token count for human-readable display.

    Args:
        count: Number of tokens.

    Returns:
        Formatted string (e.g., "1.2K", "384K", "1.05M").
    """
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def safe_json_loads(text: str, default: Any = None) -> Any:
    """Safely parse JSON, returning default on failure.

    Args:
        text: JSON string to parse.
        default: Value to return if parsing fails.

    Returns:
        Parsed JSON value, or default.
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def merge_dicts(*dicts: dict[str, Any]) -> dict[str, Any]:
    """Deep merge multiple dicts (later dicts override earlier ones).

    Nested dicts are merged recursively. Non-dict values are overridden.

    Args:
        *dicts: Dicts to merge.

    Returns:
        Merged dict.
    """
    result: dict[str, Any] = {}

    for d in dicts:
        for key, value in d.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = merge_dicts(result[key], value)
            else:
                result[key] = value

    return result


def generate_request_id() -> str:
    """Generate a unique request ID for tracking.

    Returns:
        UUID-based request ID string (e.g., "req-a1b2c3d4").
    """
    return f"req-{uuid.uuid4().hex[:8]}"


_SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def is_valid_session_id(session_id: object) -> bool:
    """Whether an id is safe to use as a file name (no separators, no leading dot)."""
    return isinstance(session_id, str) and _SESSION_ID_RE.fullmatch(session_id) is not None


def generate_session_id() -> str:
    """Generate a unique session ID.

    Returns:
        UUID-based session ID string (e.g., "sess-a1b2c3d4e5f6").
    """
    return f"sess-{uuid.uuid4().hex[:12]}"


def extract_code_blocks(text: str) -> list[dict[str, str]]:
    """Extract fenced code blocks from markdown text.

    Args:
        text: Markdown text containing code blocks.

    Returns:
        List of dicts with 'language' and 'code' keys.
    """
    import re

    blocks: list[dict[str, str]] = []
    pattern = r"```(\w*)\n(.*?)```"

    for match in re.finditer(pattern, text, re.DOTALL):
        blocks.append({
            "language": match.group(1) or "text",
            "code": match.group(2).strip(),
        })

    return blocks


def chunk_list(lst: list[Any], chunk_size: int) -> list[list[Any]]:
    """Split a list into chunks of a given size.

    Args:
        lst: List to split.
        chunk_size: Maximum size of each chunk.

    Returns:
        List of chunks.
    """
    return [lst[i: i + chunk_size] for i in range(0, len(lst), chunk_size)]


def safe_get(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safely get a nested value from a dict.

    Args:
        data: Dict to traverse.
        *keys: Sequence of keys for nested access.
        default: Value to return if any key is missing.

    Returns:
        The value at the nested key path, or default.

    Example:
        safe_get({"a": {"b": {"c": 1}}}, "a", "b", "c")  # → 1
        safe_get({"a": {}}, "a", "b", "c", default=0)     # → 0
    """
    current: Any = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
            if current is None:
                return default
        else:
            return default
    return current
