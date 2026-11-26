"""Telegram messages that read well on a phone, and the page a message can open in Telegram.

Telegram shows a small part of HTML (b, i, s, u, code, pre, a, blockquote), so Markdown from an agent is turned
into that, and a JSON result (rows from a Data table, an object from an HTTP call) into a short list a person can
scan. What does not fit a message goes to a web page, opened from a button: ``page_html`` draws it.

Nothing here sends anything; ``brain.agent.team_outputs`` does.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

MESSAGE_LIMIT = 3800          # Telegram allows 4096 characters after the tags are read; leave room for them
SUMMARY_ROWS = 8              # rows of a table that a message shows before "dan N lainnya"
LONG_TEXT = 1800              # beyond this, a message with a web page shows a summary and a button
TABLE_WIDTH = 34              # characters a phone shows in a monospace line
_TITLE_KEYS = ("nama", "name", "judul", "title", "produk", "product", "label", "kode", "code", "id")
_PLAIN_NUMBER_KEYS = re.compile(r"(^|_)(id|kode|code|hp|phone|telp|telepon|chat|nomor|no|nik|tahun|year|zip|pos)($|_)", re.I)

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True}).enable(["table", "strikethrough"])


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _attr(text: str) -> str:
    return html.escape(text, quote=True)


def _safe_link(href: str) -> str:
    return href if re.match(r"(https?|tg)://", href or "") else ""


# ── Markdown to Telegram HTML ────────────────────────────────────────────────


def _inline(tokens: list[Token]) -> str:
    out: list[str] = []
    links: list[bool] = []
    for token in tokens:
        kind = token.type
        if kind == "text":
            out.append(_esc(token.content))
        elif kind == "code_inline":
            out.append(f"<code>{_esc(token.content)}</code>")
        elif kind in ("softbreak", "hardbreak"):
            out.append("\n")
        elif kind == "strong_open":
            out.append("<b>")
        elif kind == "strong_close":
            out.append("</b>")
        elif kind == "em_open":
            out.append("<i>")
        elif kind == "em_close":
            out.append("</i>")
        elif kind == "s_open":
            out.append("<s>")
        elif kind == "s_close":
            out.append("</s>")
        elif kind == "link_open":
            href = _safe_link(str(token.attrGet("href") or ""))
            links.append(bool(href))
            if href:
                out.append(f'<a href="{_attr(href)}">')
        elif kind == "link_close":
            if links and links.pop():
                out.append("</a>")
        elif kind == "image":
            alt = token.content or "gambar"
            src = _safe_link(str(token.attrGet("src") or ""))
            out.append(f'<a href="{_attr(src)}">{_esc(alt)}</a>' if src else _esc(alt))
        else:
            out.append(_esc(token.content))
    return "".join(out)


def _plain(tokens: list[Token]) -> str:
    return "".join(t.content if t.type != "softbreak" else " " for t in tokens)


def _table(rows: list[list[str]]) -> str:
    """A small table as aligned monospace; a wide one as a block per row, which a phone shows without scrolling."""
    if not rows:
        return ""
    head, body = rows[0], rows[1:]
    widths = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(len(head))]
    if sum(widths) + 2 * (len(widths) - 1) <= TABLE_WIDTH:
        lines = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows]
        lines.insert(1, "  ".join("─" * w for w in widths))
        return f"<pre>{_esc(chr(10).join(lines))}</pre>"
    blocks = []
    for row in body:
        title = row[0] if row else ""
        rest = [f"{_esc(head[i])}: {_esc(cell)}" for i, cell in enumerate(row[1:], start=1) if i < len(head) and cell]
        blocks.append(f"<b>{_esc(title)}</b>" + ("\n" + "\n".join(rest) if rest else ""))
    return "\n\n".join(blocks)


def markdown_to_html(text: str) -> str:
    """Markdown as the HTML Telegram accepts. Headings become bold lines, lists get bullets, tables fit a phone."""
    tokens = _md.parse(text or "")
    out: list[str] = []
    lists: list[dict[str, Any]] = []        # {"ordered": bool, "n": int}
    quote = 0
    table: list[list[str]] | None = None
    row: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        kind = token.type
        if kind == "heading_open":
            out.append(f"<b>{_inline(tokens[index + 1].children or [])}</b>\n\n")
            index += 3
            continue
        if kind == "paragraph_open":
            body = _inline(tokens[index + 1].children or [])
            out.append(body + ("\n" if lists else "\n\n"))
            index += 3
            continue
        if kind in ("bullet_list_open", "ordered_list_open"):
            start = token.attrGet("start")
            lists.append({"ordered": kind == "ordered_list_open", "n": int(start) if start else 1})
        elif kind in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
            if not lists:
                out.append("\n")
        elif kind == "list_item_open":
            level = lists[-1]
            marker = f"{level['n']}." if level["ordered"] else ("•" if len(lists) == 1 else "◦")
            level["n"] += 1
            out.append("   " * (len(lists) - 1) + marker + " ")
        elif kind == "blockquote_open":
            quote += 1
            out.append("<blockquote>")
        elif kind == "blockquote_close":
            quote -= 1
            while out and out[-1].endswith("\n"):
                out[-1] = out[-1].rstrip("\n")
                if out[-1]:
                    break
            out.append("</blockquote>\n\n")
        elif kind in ("fence", "code_block"):
            language = (token.info or "").strip().split(" ")[0]
            klass = f' class="language-{_attr(language)}"' if re.fullmatch(r"[\w+#-]{1,20}", language or "") else ""
            out.append(f"<pre><code{klass}>{_esc(token.content.rstrip())}</code></pre>\n\n")
        elif kind == "hr":
            out.append("──────────\n\n")
        elif kind == "table_open":
            table = []
        elif kind == "tr_open":
            row = []
        elif kind == "inline" and table is not None:
            row.append(_plain(token.children or []).strip())
        elif kind == "tr_close" and table is not None:
            table.append(row)
        elif kind == "table_close" and table is not None:
            out.append(_table(table) + "\n\n")
            table = None
        index += 1
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


# ── JSON as something to read ─────────────────────────────────────────────────


def _show(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "ya" if value else "tidak"
    if isinstance(value, int | float) and not _PLAIN_NUMBER_KEYS.search(key):
        if float(value).is_integer() and abs(value) >= 1000:
            return f"{int(value):,}".replace(",", ".")
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"\d{4,}", value) and not _PLAIN_NUMBER_KEYS.search(key) and not value.startswith("0"):
        return f"{int(value):,}".replace(",", ".")
    if isinstance(value, dict):
        title = _title_of(value)
        return title[1] if title else json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return f"{len(value)} item"
    return "" if value is None else str(value)


def _title_of(record: dict[str, Any]) -> tuple[str, str] | None:
    for wanted in _TITLE_KEYS:
        for key, value in record.items():
            if key.lower() == wanted and value not in (None, "") and not isinstance(value, dict | list):
                return key, str(value)
    for key, value in record.items():
        if isinstance(value, str) and value.strip():
            return key, value
    return None


def _record(record: dict[str, Any], bullet: str = "") -> str:
    title = _title_of(record)
    lines = []
    for key, value in record.items():
        if key.startswith("_") or (title and key == title[0]) or value in (None, "", [], {}):
            continue
        lines.append(f"{_esc(key.replace('_', ' '))}: {_esc(_show(key, value))}")
    head = f"{bullet}<b>{_esc(title[1])}</b>" if title else bullet.rstrip()
    pad = "   " if bullet else ""
    return "\n".join([head, *(pad + line for line in lines)]).strip()


def json_to_html(data: Any, rows: int = SUMMARY_ROWS) -> tuple[str, bool]:
    """A JSON value as a short message. Returns the HTML and whether something was left out."""
    if isinstance(data, list):
        if not data:
            return "<i>Tidak ada data.</i>", False
        if all(isinstance(item, dict) for item in data):
            shown = [_record(item, "• ") for item in data[:rows]]
            more = len(data) - len(shown)
            text = "\n\n".join(shown) + (f"\n\n<i>dan {more} lainnya</i>" if more > 0 else "")
            return text, more > 0
        shown_items = [f"• {_esc(_show('', item))}" for item in data[: rows * 2]]
        more = len(data) - len(shown_items)
        return "\n".join(shown_items) + (f"\n<i>dan {more} lainnya</i>" if more > 0 else ""), more > 0
    if isinstance(data, dict):
        return _record(data), False
    return _esc(str(data)), False


def parse_json(text: str) -> Any:
    stripped = (text or "").strip()
    if stripped[:1] not in ("{", "["):
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None


# ── A message, ready to send ──────────────────────────────────────────────────


@dataclass
class Message:
    parts: list[str] = field(default_factory=list)     # each one message, HTML or plain text
    html: bool = True
    cut: bool = False                                   # the full content did not fit: offer the web page


def _split(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Markdown cut into pieces at blank lines outside code blocks, so each piece converts on its own."""
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    fence = False
    for line in (text or "").split("\n"):
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and not line.strip() and size > limit * 0.6:
            pieces.append("\n".join(current))
            current, size = [], 0
            continue
        if size + len(line) > limit and current and not fence:
            pieces.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        pieces.append("\n".join(current))
    return [p for p in pieces if p.strip()]


def _hard_split(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    parts, rest = [], text.strip()
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        parts.append(rest[:cut])
        rest = rest[cut:].lstrip()
    return [*parts, rest] if rest else parts


def compose(text: str, style: str = "auto", summarize: bool = False) -> Message:
    """What to send for ``text``. ``style`` "auto" makes it read well, "plain" sends it as it is.

    With ``summarize`` (a web page can hold the rest), long text or a long table becomes one short message.
    """
    if style == "plain":
        return Message(_hard_split(text), html=False)
    data = parse_json(text)
    if data is not None:
        body, cut = json_to_html(data, SUMMARY_ROWS if summarize else 40)
        if not summarize and len(body) > MESSAGE_LIMIT:
            return Message([markdown_to_html(p) for p in _split(text)] or [_esc(text[:MESSAGE_LIMIT])], cut=True)
        return Message([body], cut=cut)
    if summarize and len(text) > LONG_TEXT:
        pieces = _split(text, LONG_TEXT)
        return Message([markdown_to_html(pieces[0]) + "\n\n<i>…bersambung di halaman lengkap</i>"], cut=True)
    return Message([markdown_to_html(piece) for piece in _split(text)] or [""])


# ── The page a button opens ───────────────────────────────────────────────────


_page_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True}).enable(["table", "strikethrough"])


def _cell(value: Any) -> str:
    if isinstance(value, dict):
        title = _title_of(value)
        return _esc(title[1]) if title else _esc(json.dumps(value, ensure_ascii=False))
    if isinstance(value, list):
        return _esc(", ".join(_show("", v) for v in value[:20]))
    return _esc(_show("", value) if not isinstance(value, str) else value)


def body_html(text: str) -> str:
    """The content of a page: rows as a table, an object as a list of fields, anything else as Markdown."""
    data = parse_json(text)
    if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
        columns: list[str] = []
        for record in data:
            for key in record:
                if key not in columns and not key.startswith("_"):
                    columns.append(key)
        head = "".join(f"<th>{_esc(c.replace('_', ' '))}</th>" for c in columns)
        rows = "".join("<tr>" + "".join(f"<td>{_cell(r.get(c))}</td>" for c in columns) + "</tr>" for r in data)
        return f'<p class="count">{len(data)} baris</p><div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>'
    if isinstance(data, dict):
        items = "".join(f"<dt>{_esc(k.replace('_', ' '))}</dt><dd>{_cell(v)}</dd>" for k, v in data.items() if not k.startswith("_"))
        return f"<dl>{items}</dl>"
    if data is not None:
        return f"<pre>{_esc(json.dumps(data, ensure_ascii=False, indent=2))}</pre>"
    return str(_page_md.render(text or ""))


def page_html(title: str, subtitle: str, text: str) -> str:
    """A whole page for Telegram's in-app window or a browser. Colours follow the Telegram theme when there is one."""
    return f"""<!doctype html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex">
<title>{_esc(title)}</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root {{ color-scheme: light dark; --bg: var(--tg-theme-bg-color, #fbfaf7); --text: var(--tg-theme-text-color, #1d1b17);
  --hint: var(--tg-theme-hint-color, #6b665c); --link: var(--tg-theme-link-color, #1f6f5c); --line: color-mix(in srgb, var(--text) 14%, transparent);
  --soft: var(--tg-theme-secondary-bg-color, #f1eee6); }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: var(--tg-theme-bg-color, #161512); --text: var(--tg-theme-text-color, #ece8de);
  --hint: var(--tg-theme-hint-color, #a39d90); --link: var(--tg-theme-link-color, #7fd1b9); --soft: var(--tg-theme-secondary-bg-color, #22201b); }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text); font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 760px; margin: 0 auto; padding: 20px 16px 48px; }}
header {{ margin-bottom: 18px; padding-bottom: 14px; border-bottom: 1px solid var(--line); }}
h1 {{ margin: 0; font-size: 22px; line-height: 1.25; letter-spacing: -0.01em; }}
.sub {{ margin: 4px 0 0; color: var(--hint); font-size: 13.5px; }}
a {{ color: var(--link); }}
h2, h3 {{ margin: 22px 0 8px; font-size: 18px; }}
p, ul, ol {{ margin: 0 0 12px; }}
blockquote {{ margin: 0 0 12px; padding: 4px 12px; border-left: 3px solid var(--link); color: var(--hint); }}
code {{ padding: 1px 5px; border-radius: 5px; background: var(--soft); font: 13.5px ui-monospace, SFMono-Regular, Menlo, monospace; }}
pre {{ padding: 12px; overflow-x: auto; border-radius: 10px; background: var(--soft); font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }}
pre code {{ padding: 0; background: none; }}
.scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; font-variant-numeric: tabular-nums; }}
th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; white-space: nowrap; }}
th {{ position: sticky; top: 0; background: var(--soft); color: var(--hint); font-size: 12.5px; font-weight: 600; text-transform: capitalize; }}
tr:last-child td {{ border-bottom: 0; }}
.count {{ color: var(--hint); font-size: 13px; }}
dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 8px 16px; margin: 0; }}
dt {{ color: var(--hint); text-transform: capitalize; }}
dd {{ margin: 0; overflow-wrap: anywhere; }}
img {{ max-width: 100%; border-radius: 10px; }}
</style></head>
<body><main><header><h1>{_esc(title)}</h1>{f'<p class="sub">{_esc(subtitle)}</p>' if subtitle else ''}</header>
{body_html(text)}
</main>
<script>try {{ const app = window.Telegram && window.Telegram.WebApp; if (app) {{ app.ready(); app.expand(); }} }} catch (e) {{ /* opened outside Telegram */ }}</script>
</body></html>"""
