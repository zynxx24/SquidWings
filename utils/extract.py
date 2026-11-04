"""Pulls the text and the images out of any file, without guessing what the file is.

Nothing is filtered or interpreted here. Every line of text stays, in page order, and the model
reads the result and decides what it means. The only things skipped are images under
MIN_IMAGE_SIDE, which are bullets and line art, not content.

* PDF: text line by line with tables in place, plus the images on each page. A page with no text
  is a scan, so the whole page becomes an image.
* Office, EPUB, ZIP, Outlook, notebooks: the text through MarkItDown, plus every image in the archive.
* Anything else: readable bytes as text, or a plain note that the file has no text.

With ``ocr=True``, images are then read into text on this machine (utils/local_ocr.py), and that
text goes right after each image's link. The models take text only, so this is how they see a scan.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils import local_ocr
from utils.logger import get_logger

logger = get_logger(__name__)

MIN_CHARS_PER_PAGE = 40          # below this a page holds no real text
MIN_TABLE_FILL = 0.6             # share of cells with something in them
MIN_IMAGE_SIDE = 48
MAX_IMAGE_SIDE = 2000
MAX_IMAGES = 40
MAX_ARCHIVE_MEMBER_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
RENDER_SCALE = 3.0
INTENT_HEAD_CHARS = 400
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
ARCHIVE_SUFFIXES = {".docx", ".pptx", ".xlsx", ".epub", ".zip", ".odt", ".odp", ".ods"}
DOCUMENT_SUFFIXES = {".pdf", ".xls", ".msg", ".ipynb", *ARCHIVE_SUFFIXES}
NO_TEXT = "[Tidak ada teks yang bisa dibaca dari file ini.]"
OCR_FAILED = "[Gambar ini belum terbaca.]"
TOO_BIG = "[Arsip ini terlalu besar untuk dibuka: {size} MB setelah dibuka, batasnya {limit} MB.]"

PREVIEW_FOLDERS = ("docProps/", "Thumbnails/")
_XML_TAG = re.compile(r"<[^>]+>")
_LOCAL_IMAGE_LINK = re.compile(r"!\[[^\]]*\]\((?!https?:)[^)]*\)")
_FILE_BLOCK = re.compile(r'<file name="([^"]*)"[^>]*>\n?(.*?)\n?</file>', re.S)


@dataclass
class Image:
    name: str
    data: bytes
    text: str = ""      # what OCR read from it
    source: bytes = b""     # lossless copy for OCR: JPEG artefacts cost it the spaces between words


@dataclass
class Extracted:
    markdown: str
    pages: int = 0
    images: list[Image] = field(default_factory=list)
    scanned: bool = False      # every page was an image
    ocr_failed: int = 0


def _lossless(picture: Any) -> bytes:
    buffer = io.BytesIO()
    picture.convert("RGB").save(buffer, "PNG", compress_level=1)
    return buffer.getvalue()


def _image(picture: Any, stem: str) -> Image:
    """A saved copy of a PIL image (PNG for graphics, JPEG for large photos), plus the one OCR reads."""
    source = _lossless(picture)
    keep = picture.copy()
    keep.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buffer = io.BytesIO()
    if keep.mode in ("RGBA", "LA", "P") or keep.width * keep.height < 200_000:
        keep.convert("RGBA").save(buffer, "PNG", optimize=True)
        return Image(f"{stem}.png", buffer.getvalue(), source=source)
    keep.convert("RGB").save(buffer, "JPEG", quality=85)
    return Image(f"{stem}.jpg", buffer.getvalue(), source=source)


def _table_markdown(table: Any) -> str:
    rows = [[" ".join(str(cell or "").split()) for cell in row] for row in table.extract()]
    if not rows or not rows[0]:
        return ""
    return "\n".join(["|" + "|".join(rows[0]) + "|", "|" + "-|" * len(rows[0]), *("|" + "|".join(r) + "|" for r in rows[1:])])


def _text(page: Any) -> str:
    # Two trailing spaces are a Markdown line break, so compaction keeps each line as a line.
    return "  \n".join(line.rstrip() for line in (page.extract_text() or "").split("\n") if line.strip())


def _is_data_table(table: Any) -> bool:
    """A grid of values. Decorative boxes in a designed PDF also come back as tables, mostly empty."""
    rows = table.extract()
    cells = [cell for row in rows for cell in row]
    return len(rows) >= 2 and len(rows[0]) >= 2 and sum(1 for cell in cells if cell) >= len(cells) * MIN_TABLE_FILL


def _page_blocks(page: Any) -> list[str]:
    """The page from top to bottom, with each table where it stands. Never less text than the page has."""
    whole = _text(page)
    tables = sorted((t for t in page.find_tables() if _is_data_table(t)), key=lambda t: t.bbox[1])
    if not tables:
        return [whole] if whole else []
    left, top, right, bottom = page.bbox      # not always (0, 0): some pages start above zero
    blocks: list[str] = []
    for table in tables:
        if table.bbox[1] > top:
            blocks.append(_text(page.crop((left, top, right, table.bbox[1]))))
        blocks.append(_table_markdown(table))
        top = max(top, table.bbox[3])
    if top < bottom:
        blocks.append(_text(page.crop((left, top, right, bottom))))
    blocks = [block for block in blocks if block]
    return blocks if _letters(" ".join(blocks)) >= _letters(whole) else [whole]


def _letters(text: str) -> int:
    return sum(character.isalnum() for character in text)


def _page_pictures(page: Any, number: int, room: int) -> list[Any]:
    """The images on a PDF page at their stored size, not resampled to the page."""
    import pypdfium2.raw as pdfium

    pictures: list[Any] = []
    for obj in page.get_objects(filter=(pdfium.FPDF_PAGEOBJ_IMAGE,), max_depth=5):
        if len(pictures) >= room:
            break
        try:
            picture = obj.get_bitmap(render=False).to_pil()
        except Exception as exc:  # noqa: BLE001 - one broken image must not lose the rest
            logger.debug(f"[extract] image skipped on page {number}: {exc}")
            continue
        if min(picture.size) >= MIN_IMAGE_SIDE:
            pictures.append(picture)
    return pictures


def _scanned_page(page: Any, number: int) -> Image:
    """A page with no text. A scan is one stored image, far sharper than a render of the page."""
    stored = _page_pictures(page, number, 2)
    return _image(stored[0] if len(stored) == 1 else page.render(scale=RENDER_SCALE).to_pil(), f"p{number}")


def extract_pdf(data: bytes, prefix: str = "") -> Extracted:
    import pdfplumber
    import pypdfium2

    parts: list[str] = []
    images: list[Image] = []
    image_only = 0
    pdf = pypdfium2.PdfDocument(data)
    try:
        with pdfplumber.open(io.BytesIO(data)) as reader:
            total = len(reader.pages)
            for number, page in enumerate(reader.pages, 1):
                blocks = _page_blocks(page)
                found: list[Image] = []
                room = MAX_IMAGES - len(images)
                if room > 0 and sum(len(block) for block in blocks) < MIN_CHARS_PER_PAGE:
                    image_only += 1
                    found = [_scanned_page(pdf[number - 1], number)]
                elif room > 0:
                    pictures = _page_pictures(pdf[number - 1], number, room)
                    found = [_image(picture, f"p{number}-{index}") for index, picture in enumerate(pictures, 1)]
                images += found
                heading = [f"## Halaman {number}"] if total > 1 else []
                pictures = [f"![halaman {number}]({prefix}{item.name})" for item in found]
                parts.append("\n\n".join([*heading, *blocks, *pictures]))
    finally:
        pdf.close()
    return Extracted("\n\n".join(parts), total, images, scanned=total > 0 and image_only == total)


def _archive_images(data: bytes) -> list[Image]:
    from PIL import Image as Pillow

    images: list[Image] = []
    budget = MAX_ARCHIVE_BYTES
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if len(images) >= MAX_IMAGES or budget <= 0:
                break
            if info.filename.startswith(PREVIEW_FOLDERS) or info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                continue      # a preview of the file itself is not content
            if Path(info.filename).suffix.lower() not in IMAGE_SUFFIXES:
                continue
            budget -= info.file_size
            try:
                with Pillow.open(io.BytesIO(archive.read(info))) as picture:
                    picture.load()
                    if min(picture.size) < MIN_IMAGE_SIDE:
                        continue
                    item = _image(picture.copy(), f"{len(images) + 1}-{Path(info.filename).stem[:40]}")
            except Exception as exc:  # noqa: BLE001 - not every file named .png is a picture
                logger.debug(f"[extract] skipped {info.filename}: {exc}")
                continue
            images.append(item)
    return images


def _open_document_text(data: bytes) -> str:
    """OpenDocument files, which MarkItDown does not read."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("content.xml").decode("utf-8", "replace")
    return "\n\n".join(line for line in (_XML_TAG.sub(" ", part).strip() for part in re.split(r"</text:p>|</text:h>", xml)) if line)


def _unpacked_bytes(data: bytes) -> int:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return sum(info.file_size for info in archive.infolist())


def _markitdown_text(data: bytes, suffix: str) -> str:
    from markitdown import MarkItDown, StreamInfo

    return str(MarkItDown(enable_plugins=False).convert_stream(io.BytesIO(data), stream_info=StreamInfo(extension=suffix)).markdown)


def _bytes_as_text(data: bytes) -> str:
    return NO_TEXT if b"\0" in data[:8192] else data.decode("utf-8", errors="replace")


def _add_ocr(result: Extracted, prefix: str) -> None:
    """Put each image's text right after its link, so it sits where the image stands."""
    images = result.images[:local_ocr.max_images()]
    for image, text in zip(images, local_ocr.read_images([item.source or item.data for item in images]), strict=True):
        link = re.search(rf"!\[[^\]]*\]\({re.escape(prefix + image.name)}\)", result.markdown)
        if link is None:
            continue
        image.text = text or ""
        result.ocr_failed += text is None
        note = OCR_FAILED if text is None else text
        if note:
            result.markdown = f"{result.markdown[:link.end()]}\n\n{note}{result.markdown[link.end():]}"


def extract(data: bytes, name: str, prefix: str = "", ocr: bool = False) -> Extracted:
    """Text and images from one file. `prefix` is the folder image links in the text point to."""
    result = _extract_raw(data, name, prefix)
    if ocr and local_ocr.enabled():
        _add_ocr(result, prefix)
    return result


def _extract_raw(data: bytes, name: str, prefix: str) -> Extracted:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        try:
            return extract_pdf(data, prefix)
        except Exception as exc:  # noqa: BLE001 - a damaged PDF may still open the other way
            logger.warning(f"[extract] {name}: PDF reader failed, using MarkItDown: {exc}")
    if suffix in ARCHIVE_SUFFIXES and _unpacked_bytes(data) > MAX_ARCHIVE_BYTES:
        return Extracted(TOO_BIG.format(size=_unpacked_bytes(data) // 2**20, limit=MAX_ARCHIVE_BYTES // 2**20))
    if suffix in {".odt", ".odp", ".ods"}:
        text = _open_document_text(data)
    elif suffix in DOCUMENT_SUFFIXES:
        # MarkItDown names images it does not save. The real ones are linked below.
        text = _LOCAL_IMAGE_LINK.sub("", _markitdown_text(data, suffix))
    else:
        text = _bytes_as_text(data)
    images = _archive_images(data) if suffix in ARCHIVE_SUFFIXES else []
    links = "\n\n".join(f"![{item.name}]({prefix}{item.name})" for item in images)
    return Extracted("\n\n".join(part for part in (text.strip(), links) if part) or NO_TEXT, images=images)


def save_images(result: Extracted, folder: Path) -> list[Path]:
    if not result.images:
        return []
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / item.name for item in result.images]
    for path, item in zip(paths, result.images, strict=True):
        path.write_bytes(item.data)
    return paths


def intent_of(message: str) -> str:
    """What to route a message by: what the user typed, not the whole document.

    With nothing typed, the top of the document decides. The marker wakes the document guide.
    """
    files = _FILE_BLOCK.findall(message)
    if not files:
        return message
    typed = _FILE_BLOCK.sub("", message).strip()
    names = ", ".join(name for name, _ in files)
    return f"[dokumen] {names}: {typed or files[0][1][:INTENT_HEAD_CHARS]}"


__all__ = ["DOCUMENT_SUFFIXES", "Extracted", "Image", "extract", "intent_of", "save_images"]
