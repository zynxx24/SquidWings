"""Make real office files in the session workspace and hand them to the chat: Word, PowerPoint, Excel, CSV, PDF.

A cheap model writes content well but formats files badly by hand. These tools take
plain content (Markdown for a document, a list of slides, rows for a sheet) and do the
formatting in code. With ``share`` (the default) the finished file is shown under the
answer at once, as share_file would, within the same size limit.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from utils import shared_files
from utils.workspace_context import get_active_session

from . import _files, _themes
from ._themes import Theme
from .catalog import group, param, spec

group("office", "Documents, slides and spreadsheets",
      "word, docx, dokumen word, file word, ms word, ppt, pptx, powerpoint, presentasi, slide, slides, excel, xlsx, "
      "spreadsheet, lembar kerja, csv, pdf, laporan, report, surat, proposal, makalah, cv, resume, invoice, faktur, "
      "kuitansi, unduh, download, file")

_MAX_MARKDOWN = 2_000_000
_HEX = re.compile(r"#?([0-9a-fA-F]{6})")


def _color(value: str | None, fallback: str = "1F4E79") -> str:
    m = _HEX.fullmatch((value or "").strip())
    return (m.group(1) if m else fallback).upper()


def _output(output: str, suffix: str, workspace_path: str | None) -> Path:
    name = output.strip() or f"dokumen{suffix}"
    if not name.lower().endswith(suffix):
        name = f"{Path(name).with_suffix('')}{suffix}" if Path(name).suffix else f"{name}{suffix}"
    return _files.destination(name, workspace_path)


def deliver(dest: Path, workspace_path: str | None, share: bool, caption: str = "") -> dict[str, Any]:
    """Report the file, and show it in the chat when asked and it fits the share limit."""
    root = _files.workspace(workspace_path)
    relative = dest.relative_to(root).as_posix()
    size = dest.stat().st_size
    out: dict[str, Any] = {"ok": True, "saved": relative, "size_bytes": size, "size": _human(size)}
    if not share:
        out["next"] = "call share_file with this path to give it to the user"
        return out
    limit = shared_files.MAX_SHARE_BYTES
    if size > limit:
        out.update(shared=False, warning=f"the file is {_human(size)}, above the {_human(limit)} share limit; "
                                         "make it smaller (fewer or smaller images, split it) before sharing")
        return out
    session = get_active_session()
    if not session:
        out.update(shared=False, warning="no chat session is open, so the file was saved but not shown")
        return out
    shared_files.add(root, relative)
    out.update(shared=True, markdown=shared_files.markdown(session, relative, caption.strip() or dest.name),
               note="The file is shown to the user under your answer. Do not paste the link again.")
    return out


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


# ── Word ──────────────────────────────────────────────────────────────────────
def _inline(paragraph: Any, children: list[Any], workspace_path: str | None, base_size: float,
            link_color: str = "1F4E79") -> None:
    from docx.shared import Pt, RGBColor

    bold = italic = strike = False
    link: str | None = None
    for tok in children:
        kind = tok.type
        if kind == "strong_open":
            bold = True
        elif kind == "strong_close":
            bold = False
        elif kind == "em_open":
            italic = True
        elif kind == "em_close":
            italic = False
        elif kind == "s_open":
            strike = True
        elif kind == "s_close":
            strike = False
        elif kind == "link_open":
            link = str(tok.attrs.get("href") or "")
        elif kind == "link_close":
            if link and link.startswith(("http://", "https://")):
                run = paragraph.add_run(f" ({link})")
                run.font.size = Pt(base_size - 2)
                run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
            link = None
        elif kind in ("softbreak",):
            paragraph.add_run(" ")
        elif kind == "hardbreak":
            paragraph.add_run().add_break()
        elif kind == "code_inline":
            run = paragraph.add_run(tok.content)
            run.font.name = "Consolas"
            run.font.size = Pt(base_size - 1)
        elif kind == "image":
            _picture(paragraph, str(tok.attrs.get("src") or ""), workspace_path)
        elif kind == "text":
            run = paragraph.add_run(tok.content)
            run.bold, run.italic, run.font.strike = bold, italic, strike
            if link:
                run.font.color.rgb = RGBColor.from_string(link_color)
                run.underline = True


def _picture(paragraph: Any, src: str, workspace_path: str | None, width_cm: float = 15.0) -> None:
    from docx.shared import Cm

    if not src or src.startswith(("http://", "https://", "data:")):
        paragraph.add_run(f"[gambar: {src}]")
        return
    try:
        image = _files.source(src, workspace_path)
        paragraph.add_run().add_picture(str(image), width=Cm(width_cm))
    except Exception as exc:  # noqa: BLE001 - a missing picture must not lose the whole document
        paragraph.add_run(f"[gambar {src} tidak bisa dimuat: {exc}]")


def markdown_to_docx(markdown: str, doc: Any, workspace_path: str | None, base_size: float,
                     theme: Theme | None = None) -> int:
    """Write Markdown into a python-docx Document. Returns the number of blocks written."""
    from docx.enum.text import WD_BREAK
    from docx.shared import Pt
    from markdown_it import MarkdownIt

    link = theme.primary if theme else "1F4E79"
    md = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")
    tokens = md.parse(markdown)
    lists: list[str] = []
    blocks = 0
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        kind = tok.type
        if kind == "heading_open":
            level = min(int(tok.tag[1]), 4)
            heading = doc.add_heading("", level=level)
            _inline(heading, tokens[i + 1].children or [], workspace_path, base_size, link)
            blocks += 1
            i += 3
            continue
        if kind in ("bullet_list_open", "ordered_list_open"):
            lists.append("Bullet" if kind == "bullet_list_open" else "Number")
        elif kind in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
        elif kind == "paragraph_open":
            inline = tokens[i + 1]
            if inline.content.strip() in ("\\pagebreak", "<!-- pagebreak -->", "[pagebreak]"):
                doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
                i += 3
                continue
            if lists:
                depth = len(lists)
                style = f"List {lists[-1]}" + (f" {depth}" if depth > 1 else "")
                try:
                    paragraph = doc.add_paragraph(style=style)
                except KeyError:
                    paragraph = doc.add_paragraph(style=f"List {lists[-1]}")
            elif _in_blockquote(tokens, i):
                paragraph = doc.add_paragraph(style="Intense Quote")
            else:
                paragraph = doc.add_paragraph()
            _inline(paragraph, inline.children or [], workspace_path, base_size, link)
            blocks += 1
            i += 3
            continue
        elif kind in ("fence", "code_block"):
            for line in tok.content.rstrip("\n").split("\n"):
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(0)
                run = p.add_run(line or " ")
                run.font.name = "Consolas"
                run.font.size = Pt(base_size - 2)
            blocks += 1
        elif kind == "hr":
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        elif kind == "table_open":
            i = _table(doc, tokens, i, workspace_path, base_size, theme)
            blocks += 1
            continue
        i += 1
    return blocks


def _in_blockquote(tokens: list[Any], index: int) -> bool:
    depth = 0
    for tok in tokens[:index]:
        if tok.type == "blockquote_open":
            depth += 1
        elif tok.type == "blockquote_close":
            depth -= 1
    return depth > 0


def _rgb(hex_color: str) -> Any:
    from docx.shared import RGBColor

    return RGBColor.from_string(hex_color)


def _luminance(hex_color: str) -> float:
    channels = []
    for i in (0, 2, 4):
        c = int(hex_color[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _readable(hex_color: str, background: str = "FFFFFF", target: float = 4.5) -> str:
    """The colour, darkened just enough to read as text on ``background`` (WCAG contrast)."""
    color = hex_color
    for step in range(1, 11):
        lighter, darker = sorted((_luminance(color), _luminance(background)), reverse=True)
        if (lighter + 0.05) / (darker + 0.05) >= target:
            return color
        color = _mix(hex_color, "000000", step * 0.08)
    return color


def _mix(a: str, b: str, share: float) -> str:
    """``a`` moved ``share`` of the way to ``b`` (hex colours)."""
    ca, cb = [int(a[i:i + 2], 16) for i in (0, 2, 4)], [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{round(x + (y - x) * share):02X}" for x, y in zip(ca, cb, strict=True))


def _shade(cell_or_paragraph: Any, hex_color: str) -> None:
    """Fill a table cell or a paragraph with a colour."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    element = cell_or_paragraph._tc.get_or_add_tcPr() if hasattr(cell_or_paragraph, "_tc") \
        else cell_or_paragraph._p.get_or_add_pPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), hex_color)
    element.append(shading)


def _border(paragraph: Any, side: str, hex_color: str, size: int = 12, space: int = 4) -> None:
    """A line on one side of a paragraph (bottom rule, left bar)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    ppr = paragraph._p.get_or_add_pPr()
    borders = ppr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        ppr.append(borders)
    line = OxmlElement(f"w:{side}")
    for key, value in (("val", "single"), ("sz", str(size)), ("space", str(space)), ("color", hex_color)):
        line.set(qn(f"w:{key}"), value)
    borders.append(line)


def _table_borders(table: Any, hex_color: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        line = OxmlElement(f"w:{side}")
        for key, value in (("val", "single"), ("sz", "6"), ("space", "0"), ("color", hex_color)):
            line.set(qn(f"w:{key}"), value)
        borders.append(line)
    tbl_pr.append(borders)


def _style_document(doc: Any, theme: Theme, heading_font: str) -> None:
    """Colour and fonts of headings, quotes and the title, from the theme."""
    from docx.oxml.ns import qn
    from docx.shared import Pt

    colors = {"Title": _readable(theme.primary), "Heading 1": _readable(theme.primary),
              "Heading 2": _readable(theme.secondary), "Heading 3": theme.ink, "Heading 4": theme.ink}
    for name, color in colors.items():
        style = doc.styles[name]
        style.font.name = heading_font
        style.element.rPr.rFonts.set(qn("w:eastAsia"), heading_font)
        style.font.color.rgb = _rgb(color)
        if name == "Title":
            style.font.size = Pt(28)
            # The default Title has a blue rule under it; the theme draws its own.
            ppr = style.element.find(qn("w:pPr"))
            if ppr is not None and ppr.find(qn("w:pBdr")) is not None:
                ppr.remove(ppr.find(qn("w:pBdr")))
    try:
        quote = doc.styles["Intense Quote"]
        quote.font.color.rgb = _rgb(_readable(theme.secondary))
        # Its rules come from the default template in blue; give them the theme's colour.
        for line in quote.element.iter(qn("w:top"), qn("w:bottom"), qn("w:left"), qn("w:right")):
            line.set(qn("w:color"), theme.secondary)
    except KeyError:
        pass


def _style_title(heading: Any, theme: Theme) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    if theme.title == "band":
        _shade(heading, theme.primary)
        for run in heading.runs:
            run.font.color.rgb = _rgb("FFFFFF")
        heading.paragraph_format.space_before = Pt(6)
        heading.paragraph_format.space_after = Pt(12)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif theme.title == "rule":
        _border(heading, "bottom", theme.secondary, size=18, space=6)
        heading.alignment = WD_ALIGN_PARAGRAPH.LEFT
    elif theme.title == "side":
        _border(heading, "left", theme.primary, size=36, space=10)
        heading.alignment = WD_ALIGN_PARAGRAPH.LEFT
    else:
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _for_paper(theme: Theme) -> Theme:
    """A dark slide theme on white paper: darker accents and dark text."""
    from dataclasses import replace

    if not theme.dark:
        return theme
    return replace(theme, primary="0077B6", secondary="B5179E", light="E6F6FB", ink="1D2733", dark=False)


def _table(doc: Any, tokens: list[Any], start: int, workspace_path: str | None, base_size: float,
           theme: Theme | None = None) -> int:
    rows: list[list[Any]] = []
    i = start
    while tokens[i].type != "table_close":
        if tokens[i].type == "tr_open":
            rows.append([])
        elif tokens[i].type == "inline":
            rows[-1].append(tokens[i].children or [])
        i += 1
    if rows:
        width = max(len(r) for r in rows)
        table = doc.add_table(rows=len(rows), cols=width)
        table.style = "Table Grid"
        if theme:
            _table_borders(table, _mix(theme.primary, "FFFFFF", 0.55))
        for r, row in enumerate(rows):
            for c, children in enumerate(row):
                cell = table.cell(r, c)
                paragraph = cell.paragraphs[0]
                _inline(paragraph, children, workspace_path, base_size, theme.primary if theme else "1F4E79")
                if r == 0:
                    for run in paragraph.runs:
                        run.bold = True
                        if theme:
                            run.font.color.rgb = _rgb("FFFFFF")
                    if theme:
                        _shade(cell, theme.primary)
                elif theme and r % 2 == 0:
                    _shade(cell, theme.light)
        doc.add_paragraph()
    return i + 1


def _page_numbers(section: Any) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    paragraph = section.footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


@spec("doc_create", "Create a Word document (.docx) from Markdown: headings, paragraphs, bold/italic, bullet and numbered "
      "lists (nested), tables, code, quotes, images from the workspace (![](gambar.png)), and page breaks ('---' or "
      "\\pagebreak). Sets font, size, line spacing, margins, A4/Letter, page numbers, and an optional cover title. "
      "Shows the file in the chat when done. Use it for reports, makalah, letters, proposals, CVs.",
      "office", [param("output", description="File name, e.g. 'laporan/praktikum.docx'."),
                 param("markdown", description="The whole document as Markdown."),
                 param("title", description="Cover title (also the document property).", required=False),
                 param("subtitle", description="Line under the title: name, NIM, class, date.", required=False),
                 param("author", required=False),
                 param("theme", description="Look: " + ", ".join(_themes.names()) + ". Leave empty for a fresh look "
                                            "each file; 'formal' for school and campus papers.", required=False),
                 param("font", description="Overrides the theme's font, e.g. 'Times New Roman' (academic).", required=False),
                 param("font_size", "num", required=False, default=11),
                 param("line_spacing", "num", "1, 1.15, 1.5 or 2.", required=False, default=1.15),
                 param("margins_cm", "list", "[top, right, bottom, left]; Indonesian academic style is [3, 3, 3, 4].",
                       required=False, items="num"),
                 param("paper", enum=["A4", "Letter", "F4"], required=False, default="A4"),
                 param("page_numbers", "bool", required=False, default=True),
                 param("cover_page", "bool", "Put the title on its own first page.", required=False, default=False),
                 param("justify", "bool", "Justify body text (rata kanan-kiri).", required=False, default=False),
                 param("share", "bool", "Show the file in the chat.", required=False, default=True)],
      keywords="buat word, file word, docx, dokumen word, ms word, bikin dokumen, buat dokumen, laporan word, makalah word, "
               "surat word, export word, simpan sebagai word, jadikan word", lead=True, workspace=True, read_only=False)
def doc_create(output: str, markdown: str, title: str | None = None, subtitle: str | None = None,
               author: str | None = None, theme: str | None = None, font: str | None = None, font_size: float = 11,
               line_spacing: float = 1.15,
               margins_cm: list[float] | None = None, paper: str = "A4", page_numbers: bool = True,
               cover_page: bool = False, justify: bool = False, share: bool = True,
               workspace_path: str | None = None) -> dict[str, Any]:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    if len(markdown) > _MAX_MARKDOWN:
        raise ValueError("markdown longer than 2 MB; split the document")
    dest = _output(output, ".docx", workspace_path)
    look = _for_paper(_themes.pick(theme, workspace_path, formal_hint="times" in (font or "").lower()))
    font = (font or "").strip() or look.body_font
    heading_font = font if (font != look.body_font) else look.heading_font
    doc = Document()
    size = max(8.0, min(float(font_size), 20.0))
    normal = doc.styles["Normal"]
    normal.font.name = font
    normal.font.size = Pt(size)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), font)
    normal.paragraph_format.line_spacing = float(line_spacing)
    normal.paragraph_format.space_after = Pt(6)
    if justify:
        normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    normal.font.color.rgb = _rgb(look.ink)
    _style_document(doc, look, heading_font)
    section = doc.sections[0]
    width, height = {"A4": (21.0, 29.7), "Letter": (21.59, 27.94), "F4": (21.5, 33.0)}[paper]
    section.page_width, section.page_height = Cm(width), Cm(height)
    top, right, bottom, left = (list(margins_cm) + [2.54] * 4)[:4] if margins_cm else (2.54, 2.54, 2.54, 2.54)
    section.top_margin, section.right_margin = Cm(float(top)), Cm(float(right))
    section.bottom_margin, section.left_margin = Cm(float(bottom)), Cm(float(left))
    if title:
        doc.core_properties.title = title
        heading = doc.add_heading(title, level=0)
        _style_title(heading, look)
        if subtitle:
            for line in subtitle.splitlines():
                p = doc.add_paragraph(line)
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if cover_page:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    if author:
        doc.core_properties.author = author
    if title:
        # Models often repeat the title as the first "# heading"; print it once.
        first = re.match(r"\s*#{1,2}\s+(.+?)\s*(?:\n|$)", markdown)
        if first and first.group(1).strip().strip("*").lower() == title.strip().lower():
            markdown = markdown[first.end():]
    blocks = markdown_to_docx(markdown, doc, workspace_path, size, look)
    if page_numbers:
        _page_numbers(section)
    doc.save(str(dest))
    return {**deliver(dest, workspace_path, share, title or ""), "blocks": blocks, "theme": look.name,
            "theme_note": _themes.note(look)}


# ── PowerPoint ────────────────────────────────────────────────────────────────
_LAYOUTS = {"title": 0, "bullets": 1, "section": 2, "two_column": 3, "title_only": 5, "blank": 6, "image": 5, "table": 5}


def _bullets(frame: Any, items: list[Any], font: str, size: int, color: Any = None) -> None:
    from pptx.util import Pt

    frame.clear()
    first = True
    for item in items:
        text = str(item)
        level = (len(text) - len(text.lstrip(" "))) // 2
        if isinstance(item, (list, tuple)):
            for sub in item:
                p = frame.add_paragraph()
                p.text, p.level = str(sub), 1
                p.font.size, p.font.name = Pt(size - 4), font
                if color is not None:
                    p.font.color.rgb = color
            continue
        p = frame.paragraphs[0] if first else frame.add_paragraph()
        first = False
        p.text = text.strip().lstrip("-*• ").strip()
        p.level = min(level, 4)
        p.font.size, p.font.name = Pt(max(size - 4 * p.level, 10)), font
        if color is not None:
            p.font.color.rgb = color


def _to_back(shapes: list[Any]) -> None:
    """Behind the slide's text, in the order made: shapes added later are drawn over the placeholders."""
    for offset, shape in enumerate(shapes):
        element = shape._element
        tree = element.getparent()
        tree.remove(element)
        tree.insert(2 + offset, element)


def _box(shapes: Any, kind: Any, left: int, top: int, width: int, height: int, fill: str | None,
         line: str | None = None) -> Any:
    from pptx.dml.color import RGBColor
    from pptx.util import Pt

    shape = shapes.add_shape(kind, left, top, width, height)
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor.from_string(fill)
    else:
        shape.fill.background()
    if line:
        shape.line.color.rgb = RGBColor.from_string(line)
        shape.line.width = Pt(1.5)
    else:
        shape.line.fill.background()
    return shape


def _decorate(slide: Any, theme: Theme, layout: str, width: int, height: int) -> str:
    """Background and the theme's shapes. Returns the colour the title should take."""
    from pptx.dml.color import RGBColor

    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string(theme.slide_bg)
    shapes = slide.shapes
    made: list[Any] = []

    def add(*args: Any) -> None:
        made.append(_box(shapes, *args))

    title_color = _decorate_shapes(add, theme, layout, width, height)
    _to_back(made)
    return title_color


def _decorate_shapes(add: Any, theme: Theme, layout: str, width: int, height: int) -> str:
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    title_color = theme.primary
    if layout in ("title", "section"):
        if theme.title == "band":
            add(MSO_SHAPE.RECTANGLE, 0, 0, width, height, theme.primary)
            add(MSO_SHAPE.RECTANGLE, 0, height - Inches(0.35), width, Inches(0.35), theme.secondary)
            return "FFFFFF"
        if theme.title == "side":
            add(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.6), height, theme.primary)
            add(MSO_SHAPE.RECTANGLE, Inches(0.6), 0, Inches(0.15), height, theme.secondary)
        elif theme.title == "rule":
            # Between the title (ends near 3.9 in) and the subtitle (starts near 4.25 in), centred.
            add(MSO_SHAPE.RECTANGLE, int((width - Inches(3.2)) / 2), Inches(4.02), Inches(3.2), Inches(0.08),
                theme.secondary)
        else:
            add(MSO_SHAPE.OVAL, width - Inches(3.2), -Inches(1.2), Inches(4.4), Inches(4.4), theme.light)
            add(MSO_SHAPE.OVAL, -Inches(1.0), height - Inches(2.2), Inches(3.0), Inches(3.0), theme.secondary)
        return title_color
    motif = theme.motif
    if motif == "bar":
        add(MSO_SHAPE.RECTANGLE, 0, height - Inches(0.14), width, Inches(0.14), theme.primary)
        add(MSO_SHAPE.RECTANGLE, 0, height - Inches(0.2), width, Inches(0.05), theme.secondary)
    elif motif == "side":
        add(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.28), height, theme.primary)
    elif motif == "corner":
        add(MSO_SHAPE.RIGHT_TRIANGLE, width - Inches(2.4), height - Inches(1.6), Inches(2.4), Inches(1.6),
             theme.secondary)
        add(MSO_SHAPE.RECTANGLE, 0, 0, Inches(1.6), Inches(0.12), theme.primary)
    elif motif == "top":
        add(MSO_SHAPE.RECTANGLE, 0, 0, width, Inches(0.22), theme.primary)
        add(MSO_SHAPE.RECTANGLE, 0, Inches(0.22), width, Inches(0.05), theme.secondary)
    elif motif == "frame":
        add(MSO_SHAPE.RECTANGLE, Inches(0.22), Inches(0.22), width - Inches(0.44), height - Inches(0.44),
             None, theme.primary)
    else:  # dots
        for i, color in enumerate((theme.primary, theme.secondary, theme.light)):
            add(MSO_SHAPE.OVAL, width - Inches(1.5) + Inches(0.42) * i, Inches(0.35), Inches(0.28),
                 Inches(0.28), color)
    return title_color


def _widen(slide: Any, factor: float) -> None:
    """The default template is 4:3; stretch its placeholders across a 16:9 slide."""
    from pptx.util import Emu

    for ph in slide.placeholders:
        left, top, width, height = ph.left, ph.top, ph.width, ph.height   # inherited from the layout
        if None in (left, top, width, height):
            continue
        # All four at once: setting only some writes the others as 0 and stacks every box at the top.
        ph.left, ph.top = Emu(int(left * factor)), Emu(int(top))
        ph.width, ph.height = Emu(int(width * factor)), Emu(int(height))


@spec("slides_create", "Create a PowerPoint deck (.pptx, 16:9) from a list of slides. Each slide is an object: "
      "{title, subtitle?, bullets? (indent two spaces per level), left? / right? (two columns), image? (workspace path), "
      "table? {headers, rows}, notes? (speaker notes), layout? (title, bullets, section, two_column, image, table)}. "
      "Every deck gets a theme (colours, fonts, background shapes); without one asked for, a fresh look each time. "
      "Shows the file in the chat when done.",
      "office", [param("output", description="e.g. 'presentasi/kelompok3.pptx'."),
                 param("slides", "list", "Slide objects in order. The first is usually layout 'title'.", items="obj"),
                 param("theme", description="Look: " + ", ".join(_themes.names()) + ". Leave empty for a fresh look.",
                       required=False),
                 param("accent_color", description="Hex like '#1F4E79'; overrides the theme's main colour.",
                       required=False),
                 param("font", description="Overrides the theme's fonts.", required=False),
                 param("font_size", "int", "Body text size in points.", required=False, default=24),
                 param("slide_numbers", "bool", required=False, default=True),
                 param("share", "bool", required=False, default=True)],
      keywords="buat ppt, buat powerpoint, bikin ppt, presentasi, slide, slides, pptx, powerpoint, deck, bahan presentasi",
      lead=True, workspace=True, read_only=False)
def slides_create(output: str, slides: list[dict[str, Any]], theme: str | None = None, accent_color: str | None = None,
                  font: str | None = None, font_size: int = 24, slide_numbers: bool = True, share: bool = True,
                  workspace_path: str | None = None) -> dict[str, Any]:
    from dataclasses import replace

    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Inches, Pt

    if not slides:
        raise ValueError("give at least one slide")
    if len(slides) > 200:
        raise ValueError("at most 200 slides")
    dest = _output(output, ".pptx", workspace_path)
    look = _themes.pick(theme, workspace_path, formal_hint="times" in (font or "").lower())
    if accent_color and _HEX.fullmatch(accent_color.strip()):
        look = replace(look, primary=_color(accent_color))
    body_font = (font or "").strip() or look.body_font
    head_font = (font or "").strip() or look.heading_font
    ink = RGBColor.from_string(look.ink)
    deck = Presentation()
    # 16:9. Kept as locals: python-pptx types the deck's own size as optional.
    old_w = deck.slide_width or Inches(10)
    slide_w, slide_h = Inches(13.333), Inches(7.5)
    deck.slide_width, deck.slide_height = slide_w, slide_h
    size = max(12, min(int(font_size), 40))
    problems: list[str] = []
    for number, item in enumerate(slides, 1):
        if not isinstance(item, dict):
            item = {"title": str(item)}
        layout = str(item.get("layout") or ("title" if number == 1 and not item.get("bullets") else
                                            "two_column" if item.get("left") or item.get("right") else
                                            "image" if item.get("image") else "table" if item.get("table") else "bullets"))
        if layout not in _LAYOUTS:
            problems.append(f"slide {number}: unknown layout '{layout}', used bullets")
            layout = "bullets"
        slide = deck.slides.add_slide(deck.slide_layouts[_LAYOUTS[layout]])
        _widen(slide, slide_w / old_w)
        title_color = RGBColor.from_string(_decorate(slide, look, layout, slide_w, slide_h))
        on_band = layout in ("title", "section") and look.title == "band"
        text_color = RGBColor.from_string("FFFFFF") if on_band else ink
        shapes = slide.shapes
        if slide.shapes.title is not None:
            shapes.title.text = str(item.get("title") or "")
            for p in shapes.title.text_frame.paragraphs:
                p.font.name, p.font.bold, p.font.color.rgb = head_font, True, title_color
        placeholders = [ph for ph in slide.placeholders if ph.placeholder_format.idx != 0]
        if layout in ("title", "section"):
            if placeholders and item.get("subtitle"):
                placeholders[0].text = str(item["subtitle"])
                for p in placeholders[0].text_frame.paragraphs:
                    p.font.name, p.font.color.rgb = body_font, text_color
        elif layout == "bullets" and placeholders:
            _bullets(placeholders[0].text_frame, list(item.get("bullets") or []), body_font, size, ink)
        elif layout == "two_column" and len(placeholders) >= 2:
            _bullets(placeholders[0].text_frame, list(item.get("left") or []), body_font, size - 2, ink)
            _bullets(placeholders[1].text_frame, list(item.get("right") or []), body_font, size - 2, ink)
        elif layout == "image":
            try:
                image = _files.source(str(item.get("image") or ""), workspace_path)
                top = Inches(1.5)
                picture = shapes.add_picture(str(image), Inches(0.8), top, height=slide_h - top - Inches(0.6))
                if picture.width > slide_w - Inches(1.6):
                    ratio = (slide_w - Inches(1.6)) / picture.width
                    picture.width, picture.height = Emu(int(picture.width * ratio)), Emu(int(picture.height * ratio))
                picture.left = Emu(int((slide_w - picture.width) / 2))
            except Exception as exc:  # noqa: BLE001
                problems.append(f"slide {number}: image not added ({exc})")
        elif layout == "table":
            data = item.get("table") or {}
            headers, rows = list(data.get("headers") or []), [list(r) for r in data.get("rows") or []]
            if headers or rows:
                cols = max(len(headers), *(len(r) for r in rows)) if rows else len(headers)
                frame = shapes.add_table(len(rows) + (1 if headers else 0), cols, Inches(0.8), Inches(1.6),
                                         slide_w - Inches(1.6), Inches(0.4) * (len(rows) + 1))
                table = frame.table
                all_rows = ([headers] if headers else []) + rows
                for r, row in enumerate(all_rows):
                    header = r == 0 and bool(headers)
                    for c in range(cols):
                        cell = table.cell(r, c)
                        cell.text = str(row[c]) if c < len(row) else ""
                        cell.fill.solid()
                        cell.fill.fore_color.rgb = RGBColor.from_string(
                            look.primary if header else (look.light if r % 2 == 0 else look.slide_bg))
                        for p in cell.text_frame.paragraphs:
                            p.font.size, p.font.name = Pt(max(size - 8, 10)), body_font
                            p.font.bold = header
                            p.font.color.rgb = RGBColor.from_string("FFFFFF") if header else ink
        if item.get("notes"):
            slide.notes_slide.notes_text_frame.text = str(item["notes"])
        if slide_numbers and number > 1:
            box = shapes.add_textbox(slide_w - Inches(1.0), slide_h - Inches(0.65), Inches(0.8), Inches(0.4))
            box.text_frame.text = str(number)
            box.text_frame.paragraphs[0].font.size = Pt(12)
            box.text_frame.paragraphs[0].font.color.rgb = ink
    deck.save(str(dest))
    return {**deliver(dest, workspace_path, share), "slides": len(slides), "problems": problems,
            "theme": look.name, "theme_note": _themes.note(look)}


# ── Excel and CSV ─────────────────────────────────────────────────────────────
def _shown_width(value: Any, number_format: str) -> int:
    """About how many characters a cell shows, after its number format ("Rp221.000.000", not 221000000)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return len(str(value))
    digits = len(f"{abs(int(value))}")
    width = digits + (digits - 1) // 3 * ("#,##0" in number_format or "," in number_format) + (value < 0)
    if "Rp" in number_format:
        width += 3
    if "." in number_format.split(";")[0] or "%" in number_format:
        width += 4
    return max(width, len(str(value)) if number_format == "General" else width)


def _cell_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"-?\d+", text) and not (len(text) > 1 and text.startswith("0")) and len(text) < 16:
            return int(text)
        if re.fullmatch(r"-?\d+\.\d+", text):
            return float(text)
    return value


@spec("sheet_create", "Create an Excel workbook (.xlsx) with one or more sheets from rows, or a CSV. Bold coloured header, "
      "frozen header row, filters, fitted column widths, number formats (e.g. Rupiah), live formulas (cells starting "
      "with '='), and optional SUM totals row. Shows the file in the chat when done.",
      "office", [param("output", description="e.g. 'data/penjualan.xlsx' or 'data/penjualan.csv'."),
                 param("sheets", "list", "Sheet objects: {name, headers, rows (list of lists or objects), number_formats? "
                                         "{column: '#,##0' or 'rupiah' or '0.00%' or 'date'}, totals? [columns to sum]}.",
                       items="obj"),
                 param("theme", description="Look: " + ", ".join(_themes.names()) + ". Leave empty for a fresh look.",
                       required=False),
                 param("accent_color", description="Hex; overrides the theme's main colour.", required=False),
                 param("csv_delimiter", description="For .csv: ',' or ';' (Excel in Indonesian locale prefers ';').",
                       required=False, default=","),
                 param("share", "bool", required=False, default=True)],
      keywords="buat excel, bikin excel, file excel, xlsx, spreadsheet, lembar kerja, buat csv, export excel, export csv, "
               "tabel excel, rekap excel, simpan ke excel", lead=True, workspace=True, read_only=False)
def sheet_create(output: str, sheets: list[dict[str, Any]], theme: str | None = None, accent_color: str | None = None,
                 csv_delimiter: str = ",", share: bool = True, workspace_path: str | None = None) -> dict[str, Any]:
    if not sheets:
        raise ValueError("give at least one sheet")
    is_csv = output.strip().lower().endswith((".csv", ".tsv"))
    dest = _files.destination(output.strip(), workspace_path) if is_csv else _output(output, ".xlsx", workspace_path)
    prepared = [_prepare(sheet, i) for i, sheet in enumerate(sheets, 1)]
    if is_csv:
        name, headers, rows, _, _ = prepared[0]
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter="\t" if dest.suffix.lower() == ".tsv" else (csv_delimiter or ",")[:1])
        if headers:
            writer.writerow(headers)
        writer.writerows(rows)
        dest.write_text(buffer.getvalue(), encoding="utf-8-sig")
        out = deliver(dest, workspace_path, share)
        if len(prepared) > 1:
            out["warning"] = "a CSV holds one sheet; only the first was written"
        return {**out, "rows": len(rows)}

    from dataclasses import replace

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    look = _for_paper(_themes.pick(theme, workspace_path))
    if accent_color and _HEX.fullmatch(accent_color.strip()):
        look = replace(look, primary=_color(accent_color))
    book = Workbook()
    book.remove(book.active)
    fill = PatternFill("solid", fgColor=look.primary)
    band = PatternFill("solid", fgColor=look.light)
    total_fill = PatternFill("solid", fgColor=_mix(look.secondary, "FFFFFF", 0.75))
    edge = Side(style="thin", color=_mix(look.primary, "FFFFFF", 0.6))
    grid = Border(left=edge, right=edge, top=edge, bottom=edge)
    body_font = Font(name=look.body_font, color=look.ink)
    used: set[str] = set()
    total_rows = 0
    for name, headers, rows, formats, totals in prepared:
        title = re.sub(r"[\\/*?:\[\]]", "-", name)[:31] or "Sheet"
        base, n = title, 2
        while title.lower() in used:
            title = f"{base[:28]}-{n}"
            n += 1
        used.add(title.lower())
        ws = book.create_sheet(title)
        ws.sheet_properties.tabColor = look.primary
        if headers:
            ws.append(headers)
            for cell in ws[1]:
                cell.font, cell.fill = Font(name=look.heading_font, bold=True, color="FFFFFF"), fill
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                cell.border = grid
            ws.row_dimensions[1].height = 22
            ws.freeze_panes = "A2"
        for row in rows:
            ws.append([_cell_value(v) for v in row])
        last = ws.max_row
        first_data = 2 if headers else 1
        for r in range(first_data, last + 1):
            for cell in ws[r]:
                cell.font, cell.border = body_font, grid
                if (r - first_data) % 2 == 1:
                    cell.fill = band
        if totals and headers and rows:
            # One blank row, then the totals. openpyxl's append([]) adds no row, so place it by number.
            total_row = last + 2
            bold = Font(name=look.body_font, bold=True, color=look.ink)
            for c in range(1, len(headers) + 1):
                ws.cell(total_row, c).fill = total_fill
                ws.cell(total_row, c).border = grid
            ws.cell(total_row, 1, "Total").font = bold
            for column in totals:
                if column in headers:
                    letter = get_column_letter(headers.index(column) + 1)
                    cell = ws.cell(total_row, headers.index(column) + 1, f"=SUM({letter}2:{letter}{last})")
                    cell.font = bold
        for column, fmt in formats.items():
            if column not in headers:
                continue
            idx = headers.index(column) + 1
            code = {"rupiah": '"Rp"#,##0', "idr": '"Rp"#,##0', "date": "dd/mm/yyyy", "percent": "0.00%"}.get(fmt.lower(), fmt)
            for r in range(2, ws.max_row + 1):
                ws.cell(r, idx).number_format = code
        if headers and rows:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{last}"
        for idx in range(1, ws.max_column + 1):
            letter = get_column_letter(idx)
            longest = max((_shown_width(c.value, c.number_format) for c in ws[letter]
                           if c.value is not None and not str(c.value).startswith("=")), default=8)
            ws.column_dimensions[letter].width = min(max(longest + 3, 8), 60)
        total_rows += len(rows)
    book.save(dest)
    return {**deliver(dest, workspace_path, share), "sheets": [p[0] for p in prepared], "rows": total_rows,
            "theme": look.name, "theme_note": _themes.note(look)}


def _prepare(sheet: dict[str, Any], index: int) -> tuple[str, list[str], list[list[Any]], dict[str, str], list[str]]:
    if not isinstance(sheet, dict):
        raise ValueError("each sheet is an object like {name, headers, rows}")
    rows_in = list(sheet.get("rows") or [])
    headers = [str(h) for h in sheet.get("headers") or []]
    if rows_in and isinstance(rows_in[0], dict):
        headers = headers or list(dict.fromkeys(k for r in rows_in for k in r))
        rows = [[r.get(h, "") for h in headers] for r in rows_in]
    else:
        rows = [list(r) if isinstance(r, (list, tuple)) else [r] for r in rows_in]
    if len(rows) > 1_000_000:
        raise ValueError("Excel holds at most 1,048,576 rows per sheet")
    return (str(sheet.get("name") or f"Sheet{index}"), headers, rows,
            {str(k): str(v) for k, v in (sheet.get("number_formats") or {}).items()},
            [str(c) for c in sheet.get("totals") or []])


# ── PDF ───────────────────────────────────────────────────────────────────────
@spec("office_to_pdf", "Convert a Word, PowerPoint, Excel, ODF, or HTML file in the workspace to PDF with LibreOffice "
      "(installed on this machine), keeping the layout. Shows the PDF in the chat when done.",
      "office", [param("path", description="The .docx/.pptx/.xlsx/.odt/.html file."),
                 param("share", "bool", required=False, default=True)],
      keywords="pdf, jadikan pdf, ubah ke pdf, convert to pdf, export pdf, simpan pdf, word ke pdf, ppt ke pdf",
      lead=True, workspace=True, read_only=False)
async def office_to_pdf(path: str, share: bool = True, workspace_path: str | None = None) -> dict[str, Any]:
    source = _files.source(path, workspace_path)
    if source.suffix.lower() not in (".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods",
                                     ".rtf", ".html", ".htm", ".txt", ".csv"):
        raise ValueError(f"{source.suffix} cannot be converted to PDF")
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if not office:
        return {"ok": False, "error": "LibreOffice is not installed, so there is no PDF converter here",
                "install": "sudo apt install libreoffice-core libreoffice-writer libreoffice-impress libreoffice-calc"}
    with tempfile.TemporaryDirectory(prefix="lo-profile-") as profile, tempfile.TemporaryDirectory(prefix="lo-out-") as out_dir:
        # A private profile, so a LibreOffice the user has open does not block or capture the conversion.
        process = await asyncio.create_subprocess_exec(
            office, f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", "pdf", "--outdir", out_dir,
            str(source), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            log, _ = await asyncio.wait_for(process.communicate(), timeout=180)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return {"ok": False, "error": "LibreOffice took more than 3 minutes"}
        produced = Path(out_dir) / f"{source.stem}.pdf"
        if not produced.exists():
            return {"ok": False, "error": "LibreOffice produced no PDF", "log": (log or b"").decode("utf-8", "replace")[-800:]}
        dest = _files.destination(str(source.with_suffix(".pdf").relative_to(_files.workspace(workspace_path))), workspace_path)
        shutil.move(str(produced), dest)
    return deliver(dest, workspace_path, share)
