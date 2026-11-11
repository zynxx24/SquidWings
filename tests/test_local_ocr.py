"""The OCR reader's tiers and passes, with fake engines: no model is loaded here."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from utils import local_ocr


def png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def box(x: float, y: float, w: float, h: float, text: str, score: float = 0.99) -> local_ocr.Box:
    return ([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], text, score)


class Fake:
    """Answers by how the page is turned: ``by_turn`` maps a page signature to its boxes."""

    def __init__(self, read) -> None:
        self.read = read
        self.calls: list[tuple[tuple[int, int], bool, bool]] = []

    def run(self, image, *, det=True, cls=True):
        self.calls.append((image.shape[:2], det, cls))
        return self.read(image, det, cls)


@pytest.fixture(autouse=True)
def fresh_engines(monkeypatch):
    monkeypatch.setattr(local_ocr, "_engines", {})
    monkeypatch.setattr(local_ocr, "_failed", set())


def test_lines_come_out_in_reading_order_with_cells_side_by_side():
    reading = local_ocr.Reading([box(300, 10, 80, 20, "Harga"), box(10, 10, 80, 20, "Barang"),
                                 box(10, 50, 80, 20, "Kopi"), box(300, 52, 80, 20, "45000")])
    assert local_ocr._text(reading) == "Barang  Harga  \nKopi  45000"


def test_a_tilted_line_is_not_merged_with_the_next():
    def tilted(x, y, text):
        return ([[x, y + 30], [x + 400, y], [x + 402, y + 22], [x + 2, y + 52]], text, 0.99)

    reading = local_ocr.Reading([tilted(10, 0, "baris satu"), tilted(10, 40, "baris dua")])
    assert local_ocr._text(reading) == "baris satu  \nbaris dua"


def test_a_page_on_its_side_is_turned_until_it_reads_well():
    page = np.zeros((600, 200, 3), dtype=np.uint8)            # tall: text runs up the page

    def read(image, det, cls):
        if image.shape[0] > image.shape[1]:                     # still on its side: upright boxes, poor text
            return [box(10, 10, 20, 300, "xqz", 0.4)]
        return [box(10, 10, 300, 20, "Faktur Nomor 7", 0.99), box(10, 50, 300, 20, "Total 100", 0.98)]

    engine = Fake(read)
    reading, passes = local_ocr._read(engine, page, deadline=float("inf"))
    assert passes[:3] == ["asli", "putar 90", "putar 270"]
    assert [t for _, t, _ in reading.boxes] == ["Faktur Nomor 7", "Total 100"]


def test_an_upside_down_page_is_turned_so_the_first_line_comes_first(monkeypatch):
    page = np.full((200, 600, 3), 255, dtype=np.uint8)
    page[:, :300] = 0                                          # a mark that shows which way up it is
    upright = {"flag": False}

    def read(image, det, cls):
        if not det:                                            # a probed line: reads better turned over
            turned = image[0, 0, 0] == 255 if image.size else False
            return [box(0, 0, 1, 1, "x", 0.95 if turned else 0.4)]
        upright["flag"] = image[0, 0, 0] == 255
        lines = [box(10, 10, 500, 30, "Jatuh tempo 15"), box(10, 100, 500, 30, "Faktur Nomor 7")]
        if upright["flag"]:
            lines = [box(10, 10, 500, 30, "Faktur Nomor 7"), box(10, 100, 500, 30, "Jatuh tempo 15")]
        return lines

    reading, passes = local_ocr._read(Fake(read), page, deadline=float("inf"))
    assert "putar 180" in passes
    assert local_ocr._text(reading).startswith("Faktur Nomor 7")


def test_an_unsure_reading_tries_cleaned_up_copies_and_keeps_the_best():
    page = np.full((120, 400, 3), 40, dtype=np.uint8)          # dark: an inverted copy is tried too
    seen = []

    def read(image, det, cls):
        seen.append(float(image.mean()))
        if image.mean() > 150:                                 # the inverted copy is light and reads well
            return [box(10, 10, 300, 30, "Terbaca jelas", 0.97)]
        return [box(10, 10, 300, 30, "T3rb4c", 0.5)]

    reading, passes = local_ocr._read(Fake(read), page, deadline=float("inf"))
    assert passes[0] == "asli" and "dibalik" in passes
    assert [t for _, t, _ in reading.boxes] == ["Terbaca jelas"]


def test_a_blank_page_is_not_retried():
    page = np.full((300, 300, 3), 255, dtype=np.uint8)
    engine = Fake(lambda image, det, cls: [])
    reading, passes = local_ocr._read(engine, page, deadline=float("inf"))
    assert passes == ["asli"] and reading.chars == 0 and len(engine.calls) == 1


def test_a_missing_tier_falls_back_to_the_next(monkeypatch):
    class Broken:
        def __init__(self, level):
            raise ImportError("rapidocr not installed")

    class Offline:
        pass

    monkeypatch.setattr(local_ocr, "_V3", Broken)
    monkeypatch.setattr(local_ocr, "_V1", Offline)
    name, engine = local_ocr._engine("best")
    assert name == "fast" and isinstance(engine, Offline)
    assert local_ocr._failed == {"best", "latin"}


def test_the_tier_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("OCR_TIER", "max")
    assert local_ocr.tier() == "max"
    monkeypatch.setenv("OCR_TIER", "nonsense")
    assert local_ocr.tier() == "best"


def test_pictures_are_prepared_upright_flat_and_sized():
    clear = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    pixels = local_ocr._prepare(png(clear))
    assert pixels.shape == (100, 300, 3) and pixels.min() == 255, "transparency goes onto white"
    huge = local_ocr._prepare(png(Image.new("RGB", (9000, 1000), "white")))
    assert max(huge.shape[:2]) == local_ocr.MAX_SIDE
    tiny = local_ocr._prepare(png(Image.new("RGB", (200, 20), "white")))
    assert min(tiny.shape[:2]) >= local_ocr.MIN_SIDE


def test_read_image_detail_reports_the_engine_and_passes(monkeypatch):
    engine = Fake(lambda image, det, cls: [box(5, 5, 200, 20, "Halo dunia", 0.99)])
    monkeypatch.setattr(local_ocr, "_engine", lambda level: ("best", engine))
    result = local_ocr.read_image_detail(png(Image.new("RGB", (400, 100), "white")))
    assert result.text == "Halo dunia" and result.engine == "best" and result.passes == ["asli"]
    assert result.confidence == pytest.approx(0.99)
