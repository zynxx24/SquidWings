"""Shrink Markdown before it reaches a model, without changing what it says.

This uses the parser from markdown-it (through markdown-it-py), not its HTML
output: HTML costs more tokens than the Markdown it came from. Parsing means code
blocks stay byte for byte, while the padding and noise around them can go.

Removed: comments, remote and embedded images, badges, horizontal rules, table
padding, tracking parameters in links, repeated blank lines.
Kept: all text, headings, lists, tables, code, emphasis, and link targets.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from markdown_it import MarkdownIt

from utils.logger import get_logger

logger = get_logger(__name__)

_MAX_INPUT_CHARS = 2_000_000
_FRONT_MATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
_ALT_RE = re.compile(r"""\balt=["']([^"']*)["']""", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_BADGE_RE = re.compile(r"shields\.io|badge|travis-ci|codecov\.io|circleci\.com", re.I)
_TRACKING_RE = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|ref_src)$", re.I)
_HEADING_LINE_RE = re.compile(r"^#{1,6} ")
_WORD_RE = re.compile(r"\w[\w.]{2,}")

_parser = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])


def _clean_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_RE.match(k)]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def _image(src: str, alt: str) -> str:
    if src.startswith(("http://", "https://", "data:")):
        return "" if _BADGE_RE.search(src) else alt
    # A local image is part of the project, so its path stays.
    return f"![{alt}]({src})"


def _html(markup: str) -> str:
    """Only comments and images go. Other tags may be text, as in List<String>."""
    without_comments = _COMMENT_RE.sub("", markup)

    def image_alt(match: re.Match[str]) -> str:
        alt = _ALT_RE.search(match.group(0))
        return alt.group(1) if alt else ""

    return _IMG_TAG_RE.sub(image_alt, without_comments)


def _inline(token: Any) -> str:
    out: list[str] = []
    links: list[tuple[str, int]] = []
    for child in token.children or []:
        kind = child.type
        if kind == "text":
            out.append(child.content)
        elif kind == "code_inline":
            out.append(f"{child.markup}{child.content}{child.markup}")
        elif kind == "softbreak":
            out.append(" ")
        elif kind == "hardbreak":
            out.append("\n")
        elif kind == "image":
            out.append(_image(str(child.attrGet("src") or ""), child.content))
        elif kind == "html_inline":
            out.append(_html(child.content))
        elif kind == "link_open":
            links.append((str(child.attrGet("href") or ""), len(out)))
        elif kind == "link_close" and links:
            href, start = links.pop()
            label = "".join(out[start:])
            del out[start:]
            if label and href and href != label:
                out.append(f"[{label}]({_clean_url(href)})")
            else:
                out.append(label or "")
        elif child.markup:
            # Emphasis markers stay: dropping them would turn a*b*c into abc.
            out.append(child.markup)
    return "".join(out).strip()


def _render(tokens: list[Any]) -> str:
    lines: list[str] = []
    prefixes: list[str] = []
    counters: list[int | None] = []
    pending: list[str] = []
    cells: list[str] = []

    def emit(text: str) -> None:
        prefix = "".join(prefixes[:-1]) + pending.pop() if pending else "".join(prefixes)
        lines.append(prefix + text)

    def blank() -> None:
        # Blank lines inside code are appended directly, so they are never merged.
        if lines and lines[-1] != "":
            lines.append("")

    def emit_block(text: str) -> None:
        for line in text.split("\n"):
            emit(line)

    for index, token in enumerate(tokens):
        kind = token.type
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if kind == "heading_open" and following is not None:
            blank()
            emit(f"{'#' * int(token.tag[1])} {_inline(following)}")
        elif kind == "paragraph_open" and following is not None:
            text = _inline(following)
            if text:
                emit_block(text)
        elif kind == "paragraph_close" and not token.hidden:
            blank()
        elif kind in {"fence", "code_block"}:
            fence = token.markup or "```"
            language = token.info.strip().split(" ")[0] if token.info else ""
            emit(f"{fence}{language}")
            for line in token.content.rstrip("\n").split("\n"):
                lines.append("".join(prefixes) + line)
            lines.append("".join(prefixes) + fence)
            blank()
        elif kind == "html_block":
            markup = _html(token.content).strip()
            if _TAG_RE.sub("", markup).strip():
                emit_block(markup)
                blank()
        elif kind == "blockquote_open":
            prefixes.append("> ")
        elif kind == "blockquote_close":
            prefixes.pop()
        elif kind == "bullet_list_open":
            counters.append(None)
        elif kind == "ordered_list_open":
            counters.append(int(token.attrGet("start") or 1))
        elif kind in {"bullet_list_close", "ordered_list_close"}:
            counters.pop()
            if not counters:
                blank()
        elif kind == "list_item_open":
            number = counters[-1]
            marker = "- " if number is None else f"{number}. "
            if number is not None:
                counters[-1] = number + 1
            pending.append(marker)
            prefixes.append(" " * len(marker))
        elif kind == "list_item_close":
            prefixes.pop()
            if pending:
                pending.pop()
        elif kind in {"th_open", "td_open"} and following is not None:
            cells.append(_inline(following))
        elif kind == "tr_close":
            emit("|" + "|".join(cells) + "|")
            cells_in_row = len(cells)
            cells.clear()
            if following is not None and following.type == "thead_close":
                emit("|" + "-|" * cells_in_row)
        elif kind == "table_close":
            blank()
    return "\n".join(lines).strip()


def compact_markdown(text: str) -> str:
    """Return the same content in fewer tokens. Never returns something longer."""
    if not text.strip() or len(text) > _MAX_INPUT_CHARS:
        return text
    match = _FRONT_MATTER_RE.match(text)
    front = match.group(0) if match else ""
    try:
        compacted = front + _render(_parser.parse(text[len(front):]))
    except Exception as exc:  # noqa: BLE001 - a parser bug must not lose the content
        logger.warning(f"[markdown] Could not compact: {exc}")
        return text
    return compacted if len(compacted) < len(text) else text


def fit_markdown(text: str, limit: int, query: str = "") -> str:
    """Compact, then keep every heading and the sections that matter most.

    A plain cut loses the end of a document without saying what was there. Keeping
    the headings tells the model what else exists, so it can ask for that part.
    """
    compacted = compact_markdown(text)
    if len(compacted) <= limit:
        return compacted

    sections: list[list[str]] = [[]]
    in_fence = False
    for line in compacted.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        if not in_fence and _HEADING_LINE_RE.match(line):
            sections.append([])
        sections[-1].append(line)
    if len(sections) < 3:
        omitted = len(compacted) - limit
        return f"{compacted[:limit]}\n[... {omitted} karakter dilewati]"

    wanted = set(_WORD_RE.findall(query.lower()))
    bodies = ["\n".join(section) for section in sections]
    headings = sum(len(section[0]) + 1 for section in sections if section)
    remaining = max(0, limit - headings)
    # Earlier sections win ties, because documents usually open with the summary.
    order = sorted(range(len(bodies)), key=lambda i: (-len(wanted & set(_WORD_RE.findall(bodies[i].lower()))), i))
    kept: set[int] = set()
    for index in order:
        size = len(bodies[index])
        if size <= remaining:
            kept.add(index)
            remaining -= size

    parts: list[str] = []
    for index, section in enumerate(sections):
        if index in kept:
            parts.append(bodies[index])
        elif section:
            skipped = len(bodies[index]) - len(section[0])
            parts.append(f"{section[0]}\n[... {skipped} karakter dilewati]" if skipped > 0 else section[0])
    return "\n".join(parts)


__all__ = ["compact_markdown", "fit_markdown"]
