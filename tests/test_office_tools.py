"""Word, PowerPoint, Excel, CSV and PDF files made from plain content, then shown in the chat."""

from __future__ import annotations

import asyncio
import io
import json
import shutil
from typing import Any

import pytest
from PIL import Image

from tool.execute.execute_toolbox import CATALOG


def run(name: str, **kwargs: Any) -> dict[str, Any]:
    out = CATALOG[name].func(**kwargs)
    if asyncio.iscoroutine(out):
        out = asyncio.run(out)
    return out


def ok(name: str, **kwargs: Any) -> dict[str, Any]:
    out = run(name, **kwargs)
    assert out["ok"], out
    return out


@pytest.fixture()
def chat(tmp_path):
    """A chat session with its own folder, as the web server sets up."""
    from utils import workspace_context

    t1 = workspace_context._active_workspace.set(str(tmp_path))
    t2 = workspace_context._active_session.set("s_office")
    yield tmp_path
    workspace_context._active_workspace.reset(t1)
    workspace_context._active_session.reset(t2)


def _png(path) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(buffer, "PNG")
    path.write_bytes(buffer.getvalue())


MAKALAH = """# BAB I Pendahuluan

## 1.1 Latar Belakang

Banjir adalah **bencana** yang *sering* terjadi. Lihat [BNPB](https://bnpb.go.id).

- Curah hujan tinggi
  - Musim hujan
- Drainase buruk

1. Pertama
2. Kedua

| Kota | Kejadian |
|---|---|
| Jakarta | 12 |
| Bandung | 4 |

![Peta](peta.png)

---

# BAB II Pembahasan

> Kutipan penting.

```
kode = 1
```
"""


def test_doc_create_writes_a_real_word_file(chat):
    from docx import Document

    _png(chat / "peta.png")
    out = ok("doc_create", output="dokumen/makalah", markdown=MAKALAH, title="Makalah Banjir",
             subtitle="Ani\nNIM 123", font="Times New Roman", font_size=12, line_spacing=1.5,
             margins_cm=[3, 3, 3, 4], justify=True)
    assert out["saved"] == "dokumen/makalah.docx" and out["shared"] is True
    doc = Document(str(chat / "dokumen" / "makalah.docx"))
    texts = [p.text for p in doc.paragraphs]
    styles = {p.text: p.style.name for p in doc.paragraphs}
    assert styles["BAB I Pendahuluan"] == "Heading 1" and styles["1.1 Latar Belakang"] == "Heading 2"
    assert styles["Curah hujan tinggi"] == "List Bullet" and styles["Musim hujan"] == "List Bullet 2"
    assert styles["Pertama"] == "List Number"
    assert any("Banjir adalah bencana" in t and "bnpb.go.id" in t for t in texts)
    bold = [r.text for p in doc.paragraphs for r in p.runs if r.bold]
    assert "bencana" in bold
    assert doc.tables[0].cell(1, 0).text == "Jakarta" and doc.tables[0].cell(0, 1).paragraphs[0].runs[0].bold
    assert len(doc.inline_shapes) == 1
    assert doc.core_properties.title == "Makalah Banjir"
    assert round(doc.sections[0].left_margin.cm, 1) == 4.0 and round(doc.sections[0].page_width.cm, 1) == 21.0
    assert doc.styles["Normal"].font.name == "Times New Roman"
    assert "PAGE" in doc.sections[0].footer._element.xml


def test_doc_create_prints_a_repeated_title_once(chat):
    from docx import Document

    ok("doc_create", output="t.docx", markdown="# Laporan Tes\n\n## Isi\n\nTeks.", title="Laporan Tes")
    texts = [p.text for p in Document(str(chat / "t.docx")).paragraphs if p.text]
    assert texts.count("Laporan Tes") == 1 and "Isi" in texts


def test_doc_create_survives_a_missing_image(chat):
    from docx import Document

    ok("doc_create", output="a.docx", markdown="Teks\n\n![x](tidak-ada.png)\n\n![y](../../etc/passwd)")
    text = "\n".join(p.text for p in Document(str(chat / "a.docx")).paragraphs)
    assert "tidak bisa dimuat" in text


def test_slides_create_builds_every_layout(chat):
    from pptx import Presentation

    _png(chat / "grafik.png")
    slides = [
        {"title": "Pemanasan Global", "subtitle": "Kelompok 3"},
        {"title": "Penyebab", "bullets": ["Emisi CO2", "  Kendaraan", "Deforestasi"], "notes": "Jelaskan pelan-pelan"},
        {"title": "Dampak vs Solusi", "left": ["Es mencair"], "right": ["Energi bersih"]},
        {"title": "Data", "table": {"headers": ["Tahun", "Suhu"], "rows": [[2000, 0.4], [2020, 1.0]]}},
        {"title": "Grafik", "image": "grafik.png"},
        {"title": "Bab 2", "layout": "section"},
        {"title": "Aneh", "layout": "spiral", "bullets": ["x"]},
    ]
    out = ok("slides_create", output="presentasi/kelompok3", slides=slides, accent_color="#C00000")
    assert out["saved"] == "presentasi/kelompok3.pptx" and out["slides"] == 7
    assert any("spiral" in p for p in out["problems"])
    deck = Presentation(str(chat / "presentasi" / "kelompok3.pptx"))
    assert round(deck.slide_width / deck.slide_height, 2) == 1.78
    assert deck.slides[0].shapes.title.text == "Pemanasan Global"
    body = [s for s in deck.slides[1].placeholders if s.placeholder_format.idx != 0][0].text_frame
    assert [p.text for p in body.paragraphs] == ["Emisi CO2", "Kendaraan", "Deforestasi"] and body.paragraphs[1].level == 1
    assert deck.slides[1].notes_slide.notes_text_frame.text == "Jelaskan pelan-pelan"
    tables = [s for s in deck.slides[3].shapes if s.has_table]
    assert tables[0].table.cell(1, 0).text == "2000"
    assert any(s.shape_type == 13 for s in deck.slides[4].shapes)


def test_slides_create_needs_slides(chat):
    assert run("slides_create", output="x.pptx", slides=[])["ok"] is False


def test_sheet_create_writes_formats_totals_and_formulas(chat):
    from openpyxl import load_workbook

    sheets = [
        {"name": "Penjualan", "headers": ["Produk", "Harga", "Qty", "Subtotal"],
         "rows": [["Kopi", "20000", 3, "=B2*C2"], ["Teh", 15000, 5, "=B3*C3"]],
         "number_formats": {"Harga": "rupiah", "Subtotal": "rupiah"}, "totals": ["Qty", "Subtotal"]},
        {"name": "Penjualan", "rows": [{"kota": "Bandung", "nilai": 5}]},
        {"name": "a/b:c*?", "rows": [[1]]},
    ]
    out = ok("sheet_create", output="data/rekap", sheets=sheets)
    assert out["saved"] == "data/rekap.xlsx" and out["rows"] == 4
    book = load_workbook(chat / "data" / "rekap.xlsx")
    assert book.sheetnames == ["Penjualan", "Penjualan-2", "a-b-c--"]
    ws = book["Penjualan"]
    assert ws["B2"].value == 20000 and ws["D2"].value == "=B2*C2" and ws["B2"].number_format == '"Rp"#,##0'
    assert ws["A5"].value == "Total" and ws["C5"].value == "=SUM(C2:C3)" and ws["D5"].value == "=SUM(D2:D3)"
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref == "A1:D3" and ws["A1"].font.bold
    assert book["Penjualan-2"]["A1"].value == "kota" and book["Penjualan-2"]["B2"].value == 5


def test_sheet_create_keeps_leading_zeros_and_long_ids_as_text(chat):
    from openpyxl import load_workbook

    ok("sheet_create", output="x.xlsx", sheets=[{"headers": ["hp", "nik"], "rows": [["08123", "3201234567890001"]]}])
    ws = load_workbook(chat / "x.xlsx").active
    assert ws["A2"].value == "08123" and ws["B2"].value == "3201234567890001"


def test_sheet_create_csv_for_indonesian_excel(chat):
    out = ok("sheet_create", output="data/daftar.csv", csv_delimiter=";",
             sheets=[{"headers": ["nama", "nilai"], "rows": [["Ani", 90], ["Budi; Jr", 85]]}, {"rows": [[1]]}])
    raw = (chat / "data" / "daftar.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"nama;nilai" in raw and b'"Budi; Jr";85' in raw
    assert "one sheet" in out["warning"]


def test_office_files_appear_in_the_chat_and_only_once(chat):
    from utils import shared_files

    out = ok("doc_create", output="surat.docx", markdown="Halo", title="Surat Izin")
    assert out["markdown"] == "[Unduh Surat Izin](/api/session/s_office/files/surat.docx)"
    ok("doc_create", output="surat.docx", markdown="Halo lagi")
    assert shared_files.shared(chat) == ["surat.docx"]


def test_share_false_only_saves(chat):
    from utils import shared_files

    out = ok("sheet_create", output="x.xlsx", sheets=[{"rows": [[1]]}], share=False)
    assert "shared" not in out and "share_file" in out["next"] and shared_files.shared(chat) == []


def test_a_file_over_the_share_limit_is_kept_but_not_shared(chat, monkeypatch):
    from utils import shared_files

    monkeypatch.setattr(shared_files, "MAX_SHARE_BYTES", 1000)
    out = ok("doc_create", output="besar.docx", markdown="isi")
    assert out["shared"] is False and "share limit" in out["warning"] and (chat / "besar.docx").exists()
    assert shared_files.shared(chat) == []


def test_share_limit_is_configurable(monkeypatch):
    import importlib

    from utils import shared_files

    monkeypatch.setenv("SHARE_MAX_MB", "100")
    assert importlib.reload(shared_files).MAX_SHARE_BYTES == 100 * 1024 * 1024
    monkeypatch.setenv("SHARE_MAX_MB", "nonsense")
    assert importlib.reload(shared_files).MAX_SHARE_BYTES == 500 * 1024 * 1024
    monkeypatch.delenv("SHARE_MAX_MB")
    importlib.reload(shared_files)


def test_office_files_stay_inside_the_workspace(chat):
    assert run("doc_create", output="../../luar.docx", markdown="x")["ok"] is False
    assert run("sheet_create", output=".agent_memory/x.xlsx", sheets=[{"rows": [[1]]}])["ok"] is False


def test_without_a_session_the_file_is_saved_but_not_shown(tmp_path):
    from utils import workspace_context

    token = workspace_context._active_workspace.set(str(tmp_path))
    try:
        out = ok("doc_create", output="a.docx", markdown="x")
    finally:
        workspace_context._active_workspace.reset(token)
    assert out["shared"] is False and (tmp_path / "a.docx").exists()


def test_the_agent_loop_attaches_a_file_a_tool_shared_itself(chat):
    from agent.agent_loop import AgentLoop
    from tool.register_tools import register_all_tools

    loop = AgentLoop(object(), register_all_tools())  # type: ignore[arg-type]
    calls = [{"name": "doc_create", "arguments": {"output": "r.docx", "markdown": "# Hai"}}]

    class Client:
        def __init__(self) -> None:
            self.step = 0

        async def chat_completion(self, **_: Any) -> dict[str, Any]:
            self.step += 1
            if self.step == 1:
                message = {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c0", "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                    for c in calls]}
                return {"choices": [{"message": message, "finish_reason": "tool_calls"}], "usage": {}}
            return {"choices": [{"message": {"role": "assistant", "content": "Sudah."}, "finish_reason": "stop"}], "usage": {}}

    loop = AgentLoop(Client(), register_all_tools())  # type: ignore[arg-type]
    result = asyncio.run(loop.run("x", [{"role": "user", "content": "x"}], stream=False))
    assert result.shared == ["[Unduh r.docx](/api/session/s_office/files/r.docx)"]


def test_shared_office_file_downloads_from_the_web_server(chat, monkeypatch):
    from fastapi.testclient import TestClient

    import web.server as server

    monkeypatch.setattr(server, "session_workspace", lambda session_id, create=True: chat)
    ok("sheet_create", output="data/x.xlsx", sheets=[{"rows": [[1]]}])
    client = TestClient(server.app)
    response = client.get("/api/session/s_office/files/data/x.xlsx")
    assert response.status_code == 200 and response.headers["content-disposition"].startswith("attachment")
    assert response.content[:2] == b"PK"
    assert client.get("/api/session/s_office/files/data/other.xlsx").status_code == 404


@pytest.mark.skipif(shutil.which("soffice") is None and shutil.which("libreoffice") is None, reason="LibreOffice not installed")
def test_office_to_pdf_with_libreoffice(chat):
    ok("doc_create", output="dokumen/surat.docx", markdown="# Surat\n\nIsi surat.", share=False)
    out = ok("office_to_pdf", path="dokumen/surat.docx")
    assert out["saved"] == "dokumen/surat.pdf" and (chat / "dokumen" / "surat.pdf").read_bytes()[:4] == b"%PDF"
    assert out["shared"] is True


def test_office_to_pdf_refuses_other_files(chat):
    (chat / "x.py").write_text("print(1)", encoding="utf-8")
    assert run("office_to_pdf", path="x.py")["ok"] is False


def test_document_requests_wake_the_skill_and_its_tools():
    from agent.harness import scaffold_for
    from agent.harness.tool_select import select_tools
    from brain import skills
    from config.constants import TaskType
    from tool.register_tools import register_all_tools

    schemas = register_all_tools().get_all_schemas()
    for text, task in (("buatkan makalah tentang banjir dalam bentuk word", TaskType.SCHOOL_TASK),
                       ("bikin ppt presentasi kelompok tentang pemanasan global", TaskType.SCHOOL_TASK),
                       ("buat rekap penjualan ke excel", TaskType.ACTIVITY)):
        assert skills.woken(text, task.value) == ["buat-dokumen"]
        offered = {s["function"]["name"] for s in select_tools(schemas, task, text, scaffold_for("x-flash"))}
        assert {"doc_create", "slides_create", "sheet_create"} <= offered
    assert skills.woken("buat parser csv di python", "code") == []
    assert skills.woken("baca pdf ini", "activity") == []
