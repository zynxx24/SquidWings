"""Uploaded documents: clean PDF text, the brief, scans, and keeping the document out of routing. Offline."""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

import web.server as server
from agent.harness.briefing import skill_guidance
from agent.harness.tiers import scaffold_for
from brain.classifier.intent_classifier import IntentClassifier
from config.constants import TaskType
from utils import extract as extract_module
from utils import local_ocr
from utils.extract import extract, intent_of


def _pdf(pages: list[list[str]]) -> bytes:
    """A small real PDF with text, built by hand so the test needs no PDF writer."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    for lines in pages:
        body = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
        page = len(objects) + 1
        kids.append(f"{page} 0 R")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {page + 1} 0 R "
                       "/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>")
        objects.append(f"<< /Length {len(body)} >>\nstream\n{body}\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = "%PDF-1.4\n"
    offsets = []
    for number, item in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{item}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    return (out + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF").encode("latin-1")


def _scan() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buffer, "PDF")
    return buffer.getvalue()


WORKSHEET = [
    ["SMA Negeri 1", "Petunjuk: Jawablah semua soal berikut", "1. Ibu kota Indonesia adalah", "A. Bandung", "B. Jakarta", "C. Medan", "2", ],
    ["SMA Negeri 1", "2. Rumus air adalah", "A. H2O", "B. CO2", "C. O2", "3"],
    ["SMA Negeri 1", "3. Planet terbesar adalah", "A. Bumi", "B. Jupiter", "C. Mars", "4"],
]


def test_every_line_stays_in_order_with_nothing_filtered():
    found = extract(_pdf(WORKSHEET), "soal.pdf")
    assert found.pages == 3 and not found.scanned
    assert found.markdown.count("SMA Negeri 1") == 3, "repeated lines are content too"
    assert "A. Bandung  \nB. Jakarta" in found.markdown and "## Halaman 2" in found.markdown


def test_lines_survive_compaction():
    from utils.markdown_compact import compact_markdown

    text = compact_markdown(extract(_pdf(WORKSHEET), "soal.pdf").markdown)
    assert [line.strip() for line in text.split("\n")][5:8] == ["A. Bandung", "B. Jakarta", "C. Medan"]


def test_a_scanned_page_becomes_an_image_with_a_link_in_its_place():
    found = extract(_scan(), "scan.pdf", "uploads/scan_img/")
    assert found.scanned and len(found.images) == 1
    assert f"![halaman 1](uploads/scan_img/{found.images[0].name})" in found.markdown


def test_images_come_out_of_a_powerpoint():
    from pptx import Presentation
    from pptx.util import Inches

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = "Siklus Air"
    picture = io.BytesIO()
    Image.new("RGB", (200, 150), "blue").save(picture, "PNG")
    picture.seek(0)
    slide.shapes.add_picture(picture, Inches(1), Inches(2))
    out = io.BytesIO()
    deck.save(out)
    found = extract(out.getvalue(), "materi.pptx", "uploads/materi_img/")
    assert "Siklus Air" in found.markdown and len(found.images) == 1, "the deck's own thumbnail is not content"
    assert found.markdown.count("![") == 1, "no link to an image that was not saved"
    assert f"(uploads/materi_img/{found.images[0].name})" in found.markdown


class _Table:
    def __init__(self, rows, bbox=(0, 20, 500, 60)):
        self.rows, self.bbox = rows, bbox

    def extract(self):
        return self.rows


class _Page:
    width, height = 500, 800
    bbox = (0, -8, 500, 834)

    def __init__(self, tables, whole="teks halaman", cropped="teks halaman"):
        self.tables, self.whole, self.cropped, self.is_crop = tables, whole, cropped, False

    def find_tables(self):
        return self.tables

    def crop(self, box):
        assert box[1] >= self.bbox[1] and box[3] <= self.bbox[3], "a crop outside the page raises in pdfplumber"
        piece = _Page([], self.cropped, self.cropped)
        piece.is_crop = True
        return piece

    def extract_text(self):
        return self.whole


def test_a_table_stays_in_place_on_a_page_that_starts_above_zero():
    page = _Page([_Table([["A", "B"], ["1", "2"]])], whole="judul A B 1 2 akhir", cropped="judul akhir")
    assert extract_module._page_blocks(page) == ["judul akhir", "|A|B|\n|-|-|\n|1|2|", "judul akhir"]


def test_a_decorative_box_is_not_a_table_and_its_text_stays():
    box = [["", "", ""], ["", "BI", ""], ["", "", ""]]
    page = _Page([_Table(box, (-46, -82, 726, 901))], whole="BINTANG LEONITA\nDATA PRIBADI")
    assert extract_module._page_blocks(page) == ["BINTANG LEONITA  \nDATA PRIBADI"]


def test_text_is_never_lost_even_if_a_table_swallows_it():
    page = _Page([_Table([["A", "B"], ["1", "2"]])], whole="kalimat panjang di luar sel tabel ini", cropped="")
    assert extract_module._page_blocks(page) == ["kalimat panjang di luar sel tabel ini"]


def test_tiny_images_and_broken_files_named_png_are_skipped():
    tiny = io.BytesIO()
    Image.new("RGB", (8, 8)).save(tiny, "PNG")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a/icon.png", tiny.getvalue())
        z.writestr("a/rusak.png", b"bukan gambar")
        z.writestr("catatan.txt", "halo")
    assert extract_module._archive_images(archive.getvalue()) == []


def test_open_document_text_is_read():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("content.xml", "<office:text><text:p>Halo guru</text:p><text:p>Baris dua</text:p></office:text>")
    assert extract(archive.getvalue(), "tugas.odt").markdown == "Halo guru\n\nBaris dua"


def test_an_unknown_binary_says_it_has_no_text_and_unknown_text_is_kept():
    assert extract(b"\0\1\2", "data.bin").markdown == extract_module.NO_TEXT
    assert extract(b"isi bebas", "catatan.xyz").markdown == "isi bebas"


def test_the_document_does_not_decide_the_intent_when_the_user_typed_something():
    message = '<file name="a.pdf">\nkeluarga sedih capek putus def class\n</file>\n\nrangkum ini'
    assert intent_of(message) == "[dokumen] a.pdf: rangkum ini"
    assert intent_of("halo") == "halo"


def test_with_nothing_typed_the_top_of_the_document_decides():
    message = '<file name="soal.pdf" saved="uploads/soal.md">\n' + "Jawablah soal biologi. " + "x" * 2000 + "\n</file>\n\n"
    intent = intent_of(message)
    assert intent.startswith("[dokumen] soal.pdf: Jawablah soal biologi") and len(intent) < 500


def test_a_document_about_feelings_does_not_load_the_friend_guide():
    message = '<file name="novel.pdf">\ncerita sedih tentang keluarga dan patah hati\n</file>\n\nrangkum'
    lite = scaffold_for("kg/glm-5-3-flash")
    _, chosen = skill_guidance(intent_of(message), TaskType.ACTIVITY, lite.skill_chars)
    assert "baca-dokumen" in chosen and "teman-curhat" not in chosen


def test_classifying_a_long_code_like_document_uses_the_typed_words():
    intent = intent_of('<file name="m.pdf">\n' + "def class import error bug " * 200 + "\n</file>\n\ntolong jelaskan materi sejarah ini")
    assert IntentClassifier().classify(intent).task_type != TaskType.CODE


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "session_workspace", lambda sid, create=True: tmp_path)
    return TestClient(server.app)


def test_upload_saves_the_full_text_with_nothing_added(client, tmp_path):
    out = client.post("/api/upload", params={"name": "soal.pdf", "session": "s1"}, content=_pdf(WORKSHEET)).json()
    assert out["markdown"].startswith("## Halaman 1") and out["pages"] == 3 and "RINGKASAN" not in out["markdown"]
    assert out["saved"] == "uploads/soal.md"
    assert "Jupiter" in (tmp_path / "uploads" / "soal.md").read_text(encoding="utf-8")


def test_upload_of_a_scan_saves_its_pages_and_sends_no_image(client, tmp_path):
    out = client.post("/api/upload", params={"name": "scan.pdf", "session": "s1"}, content=_scan()).json()
    assert out["scanned"] and out["images"] == 1 and "image_data_urls" not in out
    assert len(list((tmp_path / "uploads" / "scan_img").iterdir())) == 1


def test_a_long_document_keeps_its_headings_instead_of_a_blind_cut(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "UPLOAD_INLINE_CHARS", 2_000)
    text = "\n\n".join(f"# Bab {i}\n\n" + "isi bab yang panjang " * 60 for i in range(1, 7))
    out = client.post("/api/upload", params={"name": "buku.md", "session": "s1"}, content=text.encode()).json()
    assert out["truncated"] and "# Bab 6" in out["markdown"] and "dilewati" in out["markdown"]
    assert "Bab 6" in (tmp_path / "uploads" / "buku.md").read_text(encoding="utf-8")
    assert out["total_chars"] > out["chars"]


def test_a_filename_cannot_leave_the_uploads_folder(client, tmp_path):
    out = client.post("/api/upload", params={"name": "../../evil.md", "session": "s1"}, content=b"# halo\n\nisi").json()
    assert out["saved"] == "uploads/evil.md" and (tmp_path / "uploads" / "evil.md").exists()


def test_upload_without_a_session_still_works(client):
    out = client.post("/api/upload", params={"name": "catatan.txt"}, content=b"catatan pendek").json()
    assert out["saved"] == "" and "catatan pendek" in out["markdown"]


def _workspace_with_documents(tmp_path):
    (tmp_path / "bab1").mkdir()
    (tmp_path / "bab1" / "soal.pdf").write_bytes(_pdf(WORKSHEET))
    (tmp_path / "scan.pdf").write_bytes(_scan())
    (tmp_path / "catatan.txt").write_text("teks biasa", encoding="utf-8")
    (tmp_path / ".rahasia").mkdir()
    (tmp_path / ".rahasia" / "x.pdf").write_bytes(_pdf(WORKSHEET))
    return tmp_path


def test_extract_files_handles_the_whole_workspace(tmp_path):
    from tool.execute.execute_upload_file.extract_tool import extract_files

    root = _workspace_with_documents(tmp_path)
    out = asyncio.run(extract_files(workspace_path=str(root)))
    assert sorted(item["file"] for item in out["files"]) == ["bab1/soal.pdf", "scan.pdf"]
    assert "Jupiter" in (root / "extracted" / "bab1" / "soal.pdf.md").read_text(encoding="utf-8")
    assert len(list((root / "extracted" / "scan.pdf_img").iterdir())) == 1


def test_extract_files_skips_what_has_not_changed(tmp_path):
    from tool.execute.execute_upload_file.extract_tool import extract_files

    root = _workspace_with_documents(tmp_path)
    asyncio.run(extract_files(workspace_path=str(root)))
    again = asyncio.run(extract_files(workspace_path=str(root)))
    assert all(item.get("cached") for item in again["files"])
    assert any(item["images"] for item in again["files"])


def test_extract_files_stays_inside_the_workspace(tmp_path):
    from tool.execute.execute_upload_file.extract_tool import extract_files

    out = asyncio.run(extract_files(path="../..", workspace_path=str(tmp_path)))
    assert out["success"] is False


def test_extract_files_reports_a_broken_file_and_continues(tmp_path):
    from tool.execute.execute_upload_file.extract_tool import extract_files

    (tmp_path / "rusak.xlsx").write_bytes(b"bukan excel")
    (tmp_path / "soal.pdf").write_bytes(_pdf(WORKSHEET))
    out = asyncio.run(extract_files(workspace_path=str(tmp_path)))
    by_name = {item["file"]: item for item in out["files"]}
    assert "error" in by_name["rusak.xlsx"] and "chars" in by_name["soal.pdf"]


def test_read_file_saves_the_images_of_a_document(tmp_path):
    from tool.execute.execute_upload_file.read_file_tool import read_file

    path = tmp_path / "scan.pdf"
    path.write_bytes(_scan())
    out = read_file(str(path))
    pictures = list((tmp_path / "scan.pdf_img").iterdir())
    assert out["success"] and out["images"] == [str(pictures[0])]


@pytest.fixture
def ocr(monkeypatch):
    monkeypatch.setenv("DOCUMENT_OCR", "true")
    calls = []

    def fake(images):
        calls.append(len(images))
        return ["Teks dari gambar"] * len(images)

    monkeypatch.setattr(local_ocr, "read_images", fake)
    return calls


def test_ocr_text_sits_right_after_the_image_it_came_from(ocr):
    found = extract(_scan(), "scan.pdf", "uploads/s_img/", ocr=True)
    link = f"![halaman 1](uploads/s_img/{found.images[0].name})"
    assert found.markdown == f"{link}\n\nTeks dari gambar" and found.images[0].text


def test_ocr_text_reaches_the_model_as_text(client, ocr):
    out = client.post("/api/upload", params={"name": "scan.pdf", "session": "s1"}, content=_scan()).json()
    assert "Teks dari gambar" in out["markdown"] and out["ocr_read"] == 1


def test_an_uploaded_picture_goes_as_text_and_not_as_an_image(client, ocr):
    buffer = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(buffer, "PNG")
    out = client.post("/api/upload", params={"name": "foto.png", "session": "s1"}, content=buffer.getvalue()).json()
    assert "Teks dari gambar" in out["markdown"] and "image_data_url" not in out


def test_a_failed_read_is_said_out_loud(client, monkeypatch):
    monkeypatch.setenv("DOCUMENT_OCR", "true")
    monkeypatch.setattr(local_ocr, "read_images", lambda images: [None] * len(images))
    out = client.post("/api/upload", params={"name": "scan.pdf", "session": "s1"}, content=_scan()).json()
    assert extract_module.OCR_FAILED in out["markdown"] and out["ocr_failed"] == 1


def test_ocr_cost_has_a_limit(ocr, monkeypatch):
    monkeypatch.setenv("OCR_MAX_IMAGES", "1")
    pages = io.BytesIO()
    blank = Image.new("RGB", (300, 400), "white")
    blank.save(pages, "PDF", save_all=True, append_images=[blank, blank])
    found = extract(pages.getvalue(), "tiga.pdf", ocr=True)
    assert len(found.images) == 3 and ocr == [1]


def test_ocr_off_makes_no_calls(monkeypatch):
    monkeypatch.setenv("DOCUMENT_OCR", "false")
    monkeypatch.setattr(local_ocr, "read_images", lambda images: pytest.fail("no call expected"))
    assert extract(_scan(), "scan.pdf", ocr=True).images


def test_one_unreadable_image_does_not_stop_the_others(monkeypatch):
    def flaky(data):
        if data == b"rusak":
            raise ValueError("not an image")
        return "ok"

    monkeypatch.setattr(local_ocr, "read_image", flaky)
    assert local_ocr.read_images([b"baik", b"rusak", b"baik"]) == ["ok", None, "ok"]


def test_ocr_stops_at_the_time_limit_and_marks_the_rest(monkeypatch):
    monkeypatch.setattr(local_ocr, "OCR_TOTAL_S", -1.0)
    monkeypatch.setattr(local_ocr, "read_image", lambda data: pytest.fail("no time left"))
    assert local_ocr.read_images([b"a", b"b"]) == [None, None]


def test_boxes_side_by_side_share_a_row_and_rows_keep_their_order():
    boxes = [(100.0, 300.0, 20.0, "Fungsi"), (102.0, 50.0, 20.0, "Organel"), (10.0, 50.0, 20.0, "Judul"), (140.0, 50.0, 20.0, "Ribosom")]
    assert local_ocr._lines(boxes) == "Judul  \nOrganel  Fungsi  \nRibosom"


def test_local_ocr_reads_a_real_image_without_any_network():
    picture = Image.new("RGB", (900, 160), "white")
    ImageDraw.Draw(picture).text((30, 40), "Ibu kota Indonesia", fill="black", font=ImageFont.load_default(size=60))
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    assert "Indonesia" in local_ocr.read_image(buffer.getvalue())


def test_a_real_scan_becomes_text_end_to_end(monkeypatch):
    monkeypatch.setenv("DOCUMENT_OCR", "true")
    page = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(page)
    draw.text((40, 60), "Sebutkan dua planet", fill="black", font=ImageFont.load_default(size=48))
    draw.text((40, 200), "Jawab dengan singkat", fill="black", font=ImageFont.load_default(size=48))
    buffer = io.BytesIO()
    page.save(buffer, "PDF")
    found = extract(buffer.getvalue(), "scan.pdf", "x/", ocr=True)
    assert found.scanned and "planet" in found.markdown and found.markdown.index("planet") < found.markdown.index("singkat")


def test_a_huge_archive_is_refused_before_it_is_unpacked():
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("nol.txt", b"\0" * (extract_module.MAX_ARCHIVE_BYTES + 1024))
    assert "terlalu besar" in extract(packed.getvalue(), "bom.zip").markdown


def test_a_notebook_and_outlook_are_handled_by_markitdown():
    notebook = b'{"cells":[{"cell_type":"markdown","metadata":{},"source":["# Judul catatan"]}],"metadata":{},"nbformat":4,"nbformat_minor":5}'
    assert "Judul catatan" in extract(notebook, "tugas.ipynb").markdown
    assert ".msg" in extract_module.DOCUMENT_SUFFIXES


def test_the_agpl_library_is_not_a_dependency():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "pymupdf" not in text and "markitdown-ocr" not in text


def test_ocr_reads_the_lossless_copy_and_not_the_saved_jpeg(monkeypatch):
    monkeypatch.setenv("DOCUMENT_OCR", "true")
    seen = []
    monkeypatch.setattr(local_ocr, "read_images", lambda images: seen.extend(images) or ["teks"] * len(images))
    photo = Image.effect_noise((900, 900), 60).convert("RGB")
    buffer = io.BytesIO()
    photo.save(buffer, "PDF")
    found = extract(buffer.getvalue(), "scan.pdf", ocr=True)
    assert found.images[0].name.endswith(".jpg") and seen[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_ocr_lines_stay_lines_after_compaction(monkeypatch):
    from utils.markdown_compact import compact_markdown

    monkeypatch.setenv("DOCUMENT_OCR", "true")
    monkeypatch.setattr(local_ocr, "read_images", lambda images: ["baris satu  \nbaris dua"] * len(images))
    assert "baris satu\nbaris dua" in compact_markdown(extract(_scan(), "s.pdf", ocr=True).markdown)

