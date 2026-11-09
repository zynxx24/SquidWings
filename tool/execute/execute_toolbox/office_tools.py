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

from . import _files
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
def _inline(paragraph: Any, children: list[Any], workspace_path: str | None, base_size: float) -> None:
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
                run.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
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


def markdown_to_docx(markdown: str, doc: Any, workspace_path: str | None, base_size: float) -> int:
    """Write Markdown into a python-docx Document. Returns the number of blocks written."""
    from docx.enum.text import WD_BREAK
    from docx.shared import Pt
    from markdown_it import MarkdownIt

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
            _inline(heading, tokens[i + 1].children or [], workspace_path, base_size)
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
            _inline(paragraph, inline.children or [], workspace_path, base_size)
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
            i = _table(doc, tokens, i, workspace_path, base_size)
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


def _table(doc: Any, tokens: list[Any], start: int, workspace_path: str | None, base_size: float) -> int:
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
        for r, row in enumerate(rows):
            for c, children in enumerate(row):
                cell = table.cell(r, c).paragraphs[0]
                _inline(cell, children, workspace_path, base_size)
                if r == 0:
                    for run in cell.runs:
                        run.bold = True
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
                 param("font", description="e.g. 'Times New Roman' (academic) or 'Calibri'.", required=False, default="Calibri"),
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
               author: str | None = None, font: str = "Calibri", font_size: float = 11, line_spacing: float = 1.15,
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
    for name in ("Heading 1", "Heading 2", "Heading 3", "Heading 4", "Title"):
        doc.styles[name].font.name = font
        doc.styles[name].element.rPr.rFonts.set(qn("w:eastAsia"), font)
    section = doc.sections[0]
    width, height = {"A4": (21.0, 29.7), "Letter": (21.59, 27.94), "F4": (21.5, 33.0)}[paper]
    section.page_width, section.page_height = Cm(width), Cm(height)
    top, right, bottom, left = (list(margins_cm) + [2.54] * 4)[:4] if margins_cm else (2.54, 2.54, 2.54, 2.54)
    section.top_margin, section.right_margin = Cm(float(top)), Cm(float(right))
    section.bottom_margin, section.left_margin = Cm(float(bottom)), Cm(float(left))
    if title:
        doc.core_properties.title = title
        heading = doc.add_heading(title, level=0)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
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
    blocks = markdown_to_docx(markdown, doc, workspace_path, size)
    if page_numbers:
        _page_numbers(section)
    doc.save(str(dest))
    return {**deliver(dest, workspace_path, share, title or ""), "blocks": blocks}


# ── PowerPoint ────────────────────────────────────────────────────────────────
_LAYOUTS = {"title": 0, "bullets": 1, "section": 2, "two_column": 3, "title_only": 5, "blank": 6, "image": 5, "table": 5}


def _bullets(frame: Any, items: list[Any], font: str, size: int) -> None:
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
            continue
        p = frame.paragraphs[0] if first else frame.add_paragraph()
        first = False
        p.text = text.strip().lstrip("-*• ").strip()
        p.level = min(level, 4)
        p.font.size, p.font.name = Pt(max(size - 4 * p.level, 10)), font


@spec("slides_create", "Create a PowerPoint deck (.pptx, 16:9) from a list of slides. Each slide is an object: "
      "{title, subtitle?, bullets? (indent two spaces per level), left? / right? (two columns), image? (workspace path), "
      "table? {headers, rows}, notes? (speaker notes), layout? (title, bullets, section, two_column, image, table)}. "
      "One accent colour and one font for the whole deck. Shows the file in the chat when done.",
      "office", [param("output", description="e.g. 'presentasi/kelompok3.pptx'."),
                 param("slides", "list", "Slide objects in order. The first is usually layout 'title'.", items="obj"),
                 param("accent_color", description="Hex like '#1F4E79'.", required=False, default="#1F4E79"),
                 param("font", required=False, default="Calibri"),
                 param("font_size", "int", "Body text size in points.", required=False, default=24),
                 param("slide_numbers", "bool", required=False, default=True),
                 param("share", "bool", required=False, default=True)],
      keywords="buat ppt, buat powerpoint, bikin ppt, presentasi, slide, slides, pptx, powerpoint, deck, bahan presentasi",
      lead=True, workspace=True, read_only=False)
def slides_create(output: str, slides: list[dict[str, Any]], accent_color: str = "#1F4E79", font: str = "Calibri",
                  font_size: int = 24, slide_numbers: bool = True, share: bool = True,
                  workspace_path: str | None = None) -> dict[str, Any]:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Emu, Inches, Pt

    if not slides:
        raise ValueError("give at least one slide")
    if len(slides) > 200:
        raise ValueError("at most 200 slides")
    dest = _output(output, ".pptx", workspace_path)
    deck = Presentation()
    # 16:9. Kept as locals: python-pptx types the deck's own size as optional.
    slide_w, slide_h = Inches(13.333), Inches(7.5)
    deck.slide_width, deck.slide_height = slide_w, slide_h
    accent = RGBColor.from_string(_color(accent_color))
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
        shapes = slide.shapes
        if slide.shapes.title is not None:
            shapes.title.text = str(item.get("title") or "")
            for p in shapes.title.text_frame.paragraphs:
                p.font.name, p.font.bold, p.font.color.rgb = font, True, accent
        bar = shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, slide_h - Inches(0.12), slide_w, Inches(0.12))
        bar.fill.solid()
        bar.fill.fore_color.rgb = accent
        bar.line.fill.background()
        placeholders = [ph for ph in slide.placeholders if ph.placeholder_format.idx != 0]
        if layout == "title":
            if placeholders and item.get("subtitle"):
                placeholders[0].text = str(item["subtitle"])
                for p in placeholders[0].text_frame.paragraphs:
                    p.font.name = font
        elif layout == "section":
            if placeholders:
                placeholders[0].text = str(item.get("subtitle") or "")
        elif layout == "bullets" and placeholders:
            _bullets(placeholders[0].text_frame, list(item.get("bullets") or []), font, size)
        elif layout == "two_column" and len(placeholders) >= 2:
            _bullets(placeholders[0].text_frame, list(item.get("left") or []), font, size - 2)
            _bullets(placeholders[1].text_frame, list(item.get("right") or []), font, size - 2)
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
                frame = shapes.add_table(len(rows) + (1 if headers else 0), cols, Inches(0.6), Inches(1.5),
                                         slide_w - Inches(1.2), Inches(0.4) * (len(rows) + 1))
                table = frame.table
                all_rows = ([headers] if headers else []) + rows
                for r, row in enumerate(all_rows):
                    for c in range(cols):
                        cell = table.cell(r, c)
                        cell.text = str(row[c]) if c < len(row) else ""
                        for p in cell.text_frame.paragraphs:
                            p.font.size, p.font.name = Pt(max(size - 8, 10)), font
                        if r == 0 and headers:
                            cell.fill.solid()
                            cell.fill.fore_color.rgb = accent
        if item.get("notes"):
            slide.notes_slide.notes_text_frame.text = str(item["notes"])
        if slide_numbers and number > 1:
            box = shapes.add_textbox(slide_w - Inches(1.0), slide_h - Inches(0.6), Inches(0.8), Inches(0.4))
            box.text_frame.text = str(number)
            box.text_frame.paragraphs[0].font.size = Pt(12)
    deck.save(str(dest))
    return {**deliver(dest, workspace_path, share), "slides": len(slides), "problems": problems}


# ── Excel and CSV ─────────────────────────────────────────────────────────────
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
                 param("accent_color", required=False, default="#1F4E79"),
                 param("csv_delimiter", description="For .csv: ',' or ';' (Excel in Indonesian locale prefers ';').",
                       required=False, default=","),
                 param("share", "bool", required=False, default=True)],
      keywords="buat excel, bikin excel, file excel, xlsx, spreadsheet, lembar kerja, buat csv, export excel, export csv, "
               "tabel excel, rekap excel, simpan ke excel", lead=True, workspace=True, read_only=False)
def sheet_create(output: str, sheets: list[dict[str, Any]], accent_color: str = "#1F4E79", csv_delimiter: str = ",",
                 share: bool = True, workspace_path: str | None = None) -> dict[str, Any]:
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

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    book.remove(book.active)
    fill = PatternFill("solid", fgColor=_color(accent_color))
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
        if headers:
            ws.append(headers)
            for cell in ws[1]:
                cell.font, cell.fill = Font(bold=True, color="FFFFFF"), fill
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            ws.freeze_panes = "A2"
        for row in rows:
            ws.append([_cell_value(v) for v in row])
        last = ws.max_row
        if totals and headers and rows:
            # One blank row, then the totals. openpyxl's append([]) adds no row, so place it by number.
            total_row = last + 2
            ws.cell(total_row, 1, "Total").font = Font(bold=True)
            for column in totals:
                if column in headers:
                    letter = get_column_letter(headers.index(column) + 1)
                    cell = ws.cell(total_row, headers.index(column) + 1, f"=SUM({letter}2:{letter}{last})")
                    cell.font = Font(bold=True)
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
            longest = max((len(str(c.value)) for c in ws[letter] if c.value is not None and not str(c.value).startswith("=")),
                          default=8)
            ws.column_dimensions[letter].width = min(max(longest + 2, 8), 60)
        total_rows += len(rows)
    book.save(dest)
    return {**deliver(dest, workspace_path, share), "sheets": [p[0] for p in prepared], "rows": total_rows}


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
