"""Tolerant YAML-subset frontmatter parser and serializer.

Obsidian frontmatter is YAML, but pulling in PyYAML for a handful of scalar
and list fields is not worth the dependency. This module handles the subset
the vault actually uses: scalars, quoted strings, inline lists, and block
lists. Anything it cannot parse is preserved as a raw string rather than
raising, because a malformed note must never break the index.
"""

from __future__ import annotations

import re
from typing import Any

from tool.execute.execute_local_memory.vault import constants as C

# Frontmatter must start at the very first byte of the file.
_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_TAG_RE = re.compile(r"(?:^|[\s(\[{])#([A-Za-z0-9_][A-Za-z0-9_/-]*)")
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")
_BLOCK_ITEM_RE = re.compile(r"^[ \t]*[-*][ \t]+(.*)$")

# Fenced code blocks are stripped before summary extraction so sample code
# never becomes a node summary. The fence marker is built from chr(96) and
# chr(126) so this source file never contains a literal fence run.
_FENCE_CHARS = chr(96) * 3 + "|" + chr(126) * 3
_FENCE_RE = re.compile(r"^(" + _FENCE_CHARS + r").*?^\1[ \t]*$", re.DOTALL | re.MULTILINE)

# Preferred key order when writing frontmatter back out.
_KEY_ORDER = (
    "type", "status", "confidence", "updated",
    "superseded_by", "title", "tags", "aliases",
)


def _coerce(value: str) -> Any:
    """Best-effort scalar coercion. Unknown shapes stay as strings."""
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    low = text.lower()
    if low in {"true", "yes"}:
        return True
    if low in {"false", "no"}:
        return False
    if low in {"null", "none", "~", ""}:
        return ""
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except ValueError:
            return text
    return text


def _split_inline_list(value: str) -> list[str]:
    inner = value.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    return [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a note into (frontmatter dict, body). Never raises.

    Supports scalars, quoted strings, inline lists, and block lists.
    """
    if not text:
        return {}, ""
    match = _FM_RE.match(text)
    if not match:
        return {}, text

    raw = match.group(1)
    body = text[match.end():]
    meta: dict[str, Any] = {}
    pending_list_key: str | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        item = _BLOCK_ITEM_RE.match(line)
        if item and pending_list_key is not None:
            current = meta.get(pending_list_key)
            if not isinstance(current, list):
                current = []
                meta[pending_list_key] = current
            current.append(_coerce(item.group(1)))
            continue

        if ":" not in line:
            meta.setdefault("_unparsed", []).append(stripped)
            pending_list_key = None
            continue

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            pending_list_key = None
            continue

        if value == "":
            pending_list_key = key
            meta[key] = []
        elif value.startswith("[") and value.endswith("]"):
            meta[key] = _split_inline_list(value)
            pending_list_key = None
        else:
            meta[key] = _coerce(value)
            pending_list_key = None

    # A bare "key:" with no list items becomes an empty string, not [].
    for key in [k for k, v in meta.items() if v == [] and k != "tags"]:
        meta[key] = ""
    return meta, body


def serialize_frontmatter(meta: dict[str, Any]) -> str:
    """Render a frontmatter block. Empty values are omitted."""
    if not meta:
        return ""

    ordered: list[str] = [k for k in _KEY_ORDER if k in meta]
    ordered += [k for k in meta if k not in _KEY_ORDER and not k.startswith("_")]

    lines = ["---"]
    for key in ordered:
        value = meta.get(key)
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (list, tuple, set)):
            items = [str(v) for v in value if str(v).strip()]
            if not items:
                continue
            lines.append(f"{key}: [{', '.join(items)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def extract_tags(body: str, meta: dict[str, Any] | None = None) -> list[str]:
    """Union of frontmatter tags and inline #tags."""
    tags: set[str] = set()
    fm = (meta or {}).get("tags")
    if isinstance(fm, list):
        tags.update(str(t).lstrip("#") for t in fm if str(t).strip())
    elif isinstance(fm, str) and fm:
        tags.update(t.strip().lstrip("#") for t in fm.split(",") if t.strip())
    for match in _TAG_RE.finditer(body or ""):
        tags.add(match.group(1))
    return sorted(tags)


def extract_links(body: str) -> list[str]:
    """All wikilink targets, deduplicated and order-preserving."""
    seen: dict[str, None] = {}
    for match in _WIKILINK_RE.finditer(body or ""):
        target = match.group(1).strip()
        if target:
            seen.setdefault(target, None)
    return list(seen)


def extract_relations(meta: dict[str, Any]) -> list[tuple[str, str]]:
    """Typed relations declared in frontmatter as (edge_type, target).

    Only explicit, authored relations are returned - nothing is inferred
    here. Wikilinks in the value are unwrapped so both [[x]] and x work.
    """
    out: list[tuple[str, str]] = []
    for key, value in (meta or {}).items():
        edge_type = C.edge_type_for_key(key)
        if not edge_type:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = str(item or "").strip()
            if not text:
                continue
            links = extract_links(text)
            targets = links if links else [text]
            for target in targets:
                clean = target.strip()
                if clean:
                    out.append((edge_type, clean))
    return out


def extract_headings(body: str) -> list[str]:
    return [m.group(1).strip() for m in _HEADING_RE.finditer(body or "")]


def extract_summary(body: str, max_chars: int = C.MAX_SUMMARY_CHARS) -> str:
    """First 1-3 sentences of the body with markdown stripped.

    This is the node summary used by L2 - short enough to load many nodes
    into context, long enough to decide whether to open the full note.
    """
    text = _FENCE_RE.sub(" ", body or "")
    text = _HEADING_RE.sub(" ", text)
    text = re.sub(r"!?\[\[([^\[\]|]+)(?:\|[^\[\]]*)?\]\]", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_" + chr(96) + r">#~]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for sentence in sentences[:3]:
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > max_chars:
            break
        out = candidate
    if out:
        return out
    return text[:max_chars].rstrip()


def first_heading(body: str) -> str:
    """The first H1-H6 heading, used as a fallback title."""
    headings = extract_headings(body)
    return headings[0] if headings else ""


__all__ = [
    "parse_frontmatter",
    "serialize_frontmatter",
    "extract_tags",
    "extract_links",
    "extract_relations",
    "extract_headings",
    "extract_summary",
    "first_heading",
]
