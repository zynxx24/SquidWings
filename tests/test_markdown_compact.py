"""Markdown compaction must save tokens without changing what the text says."""

from __future__ import annotations

from pathlib import Path

from tool.execute.execute_upload_file.read_file_tool import read_file
from utils.markdown_compact import compact_markdown, fit_markdown


def test_code_blocks_stay_byte_for_byte() -> None:
    code = "def f():\n    return   {'a':  1}  # <!-- not a comment -->\n\n\n    x = 1"
    text = f"<!-- note -->\n\n\n# T\n\n```python title=x\n{code}\n```\n\n---\n"
    assert f"```python\n{code}\n```" in compact_markdown(text)


def test_noise_is_removed() -> None:
    text = (
        "<!-- hidden note -->\n\n---\n\n"
        "[![build](https://img.shields.io/badge/ok-green.svg)](https://ci.test/x)\n\n"
        "![foto](data:image/png;base64,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA)\n\n"
        "| Nama     | Nilai    |\n|----------|----------|\n| satu     | 1        |\n"
    )
    result = compact_markdown(text)
    for noise in ("hidden note", "shields.io", "base64", "---\n\n"):
        assert noise not in result
    assert "foto" in result
    assert "|Nama|Nilai|\n|-|-|\n|satu|1|" in result


def test_content_is_kept() -> None:
    text = (
        "Rumus a*b*c dan **PENTING**, juga `kode  dua` dan List<String>.\n"
        "Baris kedua [sumber](https://x.test/a?id=1&utm_source=mail) ada.\n\n"
        "1. satu\n2. dua\n   - anak\n\n> kutipan\n"
    )
    result = compact_markdown(text)
    for kept in ("a*b*c", "**PENTING**", "`kode  dua`", "List<String>", "kutipan", "- anak", "2. dua"):
        assert kept in result
    assert "[sumber](https://x.test/a?id=1)" in result


def test_front_matter_and_local_images_survive() -> None:
    text = "---\ntitle: Catatan\ntags: [a]\n---\n\n\n# Isi\n\n![denah](img/denah.png)\n\n---\n"
    result = compact_markdown(text)
    assert result.startswith("---\ntitle: Catatan\ntags: [a]\n---\n")
    assert "![denah](img/denah.png)" in result


def test_never_longer_than_the_input() -> None:
    for text in ("", "kata", "a\nb", "# H"):
        assert len(compact_markdown(text)) <= len(text)


def test_fit_keeps_every_heading_and_the_relevant_section() -> None:
    filler = "isi umum yang panjang. " * 80
    text = f"# Judul\n\npembuka\n\n## Satu\n\n{filler}\n\n## Harga kopi\n\nharga kopi naik.\n\n## Tiga\n\n{filler}\n"
    result = fit_markdown(text, 800, "harga kopi")
    assert len(result) < 1000
    for heading in ("# Judul", "## Satu", "## Harga kopi", "## Tiga"):
        assert heading in result
    assert "harga kopi naik." in result
    assert "dilewati" in result


def test_a_small_markdown_file_is_read_exactly(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text("# H\n\n\n\n<!-- c -->\nteks\n", encoding="utf-8")
    assert read_file(str(path))["content"] == "# H\n\n\n\n<!-- c -->\nteks\n"


def test_a_long_markdown_file_is_compacted_unless_a_range_is_asked(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    original = "# H\n\n<!-- komentar -->\n\n" + "kalimat isi.\n\n\n" * 1000
    path.write_text(original, encoding="utf-8")
    compacted = read_file(str(path))
    assert "komentar" not in compacted["content"] and "file_patch" in compacted["note"]
    exact = read_file(str(path), start_line=1, end_line=4)
    assert exact["content"] == "# H\n\n<!-- komentar -->\n"
    assert "note" not in exact


def test_workspace_documents_become_markdown(tmp_path: Path) -> None:
    import openpyxl

    path = tmp_path / "data.xlsx"
    book = openpyxl.Workbook()
    book.active.append(["Nama", "Nilai"])  # type: ignore[union-attr]
    book.active.append(["kopi", 42])  # type: ignore[union-attr]
    book.save(path)
    result = read_file(str(path))
    assert result["converted_from"] == "xlsx"
    assert "kopi" in result["content"] and "42" in result["content"]


def test_a_broken_document_still_answers(tmp_path: Path) -> None:
    path = tmp_path / "rusak.pdf"
    path.write_bytes(b"bukan pdf")
    assert read_file(str(path))["success"] is True
