"""Reads images into text on this machine, with no model call to a chat provider.

The models this project talks to take text only, so a scanned page must become text before it
reaches them. RapidOCR runs PaddleOCR's models on the CPU through ONNX Runtime. Everything here is
Apache, MIT or BSD licensed, which matters because SquidWings is closed source: the markitdown-ocr
plugin would pull in pymupdf, which is AGPL.

Tiers (``OCR_TIER``)
--------------------
* ``max``: PP-OCRv6 medium. The strongest, about a minute per page on a laptop CPU: opt in.
* ``best`` (default): PP-OCRv6 small. Whole lines with the spaces between words, in Indonesian,
  English and about 40 other languages; a few seconds per page.
* ``latin``: PP-OCRv5 detection with the Latin recogniser.
* ``fast``: the PP-OCRv4 models that ship inside ``rapidocr_onnxruntime``. Works offline from the
  first run, but its Chinese recogniser often drops the spaces between Latin words.

On 14 hard test pages (tiny, faint, white on dark, turned 90/180/270, tilted, blurred, noisy JPEG,
a 4,600-pixel scan, transparent, coloured serif, a table) ``best``, ``max`` and ``latin`` read every
word; ``fast`` lost spaces and misread some (o as 0).

The upper tiers come from the ``rapidocr`` package, which downloads their models once (to its own
folder) the first time they are used. If that package or its download is unavailable, the next tier
down is used, ending at ``fast``.

How an image is read, so that nearly every image with text gives its text
----------------------------------------------------------------------------
1. Prepared: the camera's orientation (EXIF) applied, transparency flattened onto white, a huge
   scan scaled down to ``MAX_SIDE`` and a tiny one scaled up.
2. Read once. When most text boxes stand upright, the page lies on its side: it is read turned 90
   and 270 degrees and the better reading is kept.
3. Upside down: the clearest lines are read again both ways up without the line classifier. When
   most read better turned over, the page is turned 180 degrees and read again, so the lines come
   out in reading order instead of last line first.
4. Unsure (average confidence below ``GOOD_ENOUGH``) or nothing found: read again from cleaned-up
   copies (contrast-equalised grey, twice the size, inverted when the page is dark, sharpened),
   and the reading with the most confident text wins.

It reads text only. It cannot describe a photo or a chart.
"""

from __future__ import annotations

import io
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)

MIN_CONFIDENCE = 0.3
GOOD_ENOUGH = 0.88        # average confidence above which no clean-up pass is tried
ROW_TOLERANCE = 0.6       # share of a line's height two boxes may differ by and still be one row
MAX_SIDE = 4000           # a bigger scan is scaled down first: past this, detail only costs time
MIN_SIDE = 48             # a smaller image is scaled up first
UPRIGHT_SHARE = 0.6       # this share of boxes standing upright means the page lies on its side
FLIP_SHARE = 0.6          # this share of probed lines reading better turned over means upside down
PROBE_LINES = 6
BLANK_STD = 4.0           # pixel spread below this with no text found: a blank page, not retried
TIERS = ("max", "best", "latin", "fast")

_engines: dict[str, Any] = {}
_failed: set[str] = set()
_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("DOCUMENT_OCR", "true").strip().lower() != "false"


def max_images() -> int:
    try:
        return max(0, int(os.getenv("OCR_MAX_IMAGES", "20")))
    except ValueError:
        return 20


def _seconds(name: str, default: float) -> float:
    try:
        return max(1.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


OCR_TOTAL_S = _seconds("OCR_TOTAL_S", 300.0)       # all images of one upload
OCR_IMAGE_S = _seconds("OCR_IMAGE_S", 60.0)        # one image, before clean-up passes stop


def tier() -> str:
    wanted = os.getenv("OCR_TIER", "best").strip().lower()
    return wanted if wanted in TIERS else "best"


# ── engines ─────────────────────────────────────────────────────────────────
Box = tuple[list[list[float]], str, float]          # four corners, text, confidence


class _V3:
    """``rapidocr`` (3.x): the PP-OCRv6 (max, best) and PP-OCRv5 Latin (latin) models."""

    def __init__(self, level: str) -> None:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        if level in ("max", "best"):
            v6, size = OCRVersion.PPOCRV6, ModelType.MEDIUM if level == "max" else ModelType.SMALL
            params = {"Det.ocr_version": v6, "Det.model_type": size, "Det.lang_type": "en",
                      "Rec.ocr_version": v6, "Rec.model_type": size, "Rec.lang_type": LangRec.EN}
        else:
            v5 = OCRVersion.PPOCRV5
            params = {"Det.ocr_version": v5, "Det.model_type": ModelType.MOBILE,
                      "Rec.ocr_version": v5, "Rec.model_type": ModelType.MOBILE, "Rec.lang_type": LangRec.LATIN}
        params["Global.log_level"] = "error"
        self._ocr: Any
        try:
            self._ocr = RapidOCR(params=params)
        except Exception:  # noqa: BLE001 - older 3.x without the log option
            params.pop("Global.log_level")
            self._ocr = RapidOCR(params=params)

    def run(self, image: np.ndarray, *, det: bool = True, cls: bool = True) -> list[Box]:
        out = self._ocr(image, use_det=det, use_cls=cls, use_rec=True)
        txts, scores = out.txts or (), out.scores or ()
        if not det:
            h, w = image.shape[:2]
            return [([[0, 0], [w, 0], [w, h], [0, h]], str(t), float(s)) for t, s in zip(txts, scores, strict=False)]
        boxes = out.boxes if out.boxes is not None else []
        return [(np.asarray(b, dtype=float).tolist(), str(t), float(s)) for b, t, s in zip(boxes, txts, scores, strict=False)]


class _V1:
    """``rapidocr_onnxruntime`` (1.x): the PP-OCRv4 models inside the package."""

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = RapidOCR()

    def run(self, image: np.ndarray, *, det: bool = True, cls: bool = True) -> list[Box]:
        found, _ = self._ocr(image, use_det=det, use_cls=cls, use_rec=True)
        if not det:
            h, w = image.shape[:2]
            return [([[0, 0], [w, 0], [w, h], [0, h]], str(item[0]), float(item[1])) for item in found or []]
        return [(np.asarray(c, dtype=float).tolist(), str(t), float(s)) for c, t, s in found or []]


def _engine(level: str) -> tuple[str, Any]:
    """The engine for ``level``, or the next one down when it cannot be loaded."""
    for name in TIERS[TIERS.index(level):]:
        if name in _engines:
            return name, _engines[name]
        if name in _failed:
            continue
        try:
            _engines[name] = _V1() if name == "fast" else _V3(name)
            return name, _engines[name]
        except Exception as exc:  # noqa: BLE001 - not installed, or its models could not be fetched
            _failed.add(name)
            logger.warning(f"[ocr] tier {name} unavailable ({type(exc).__name__}: {str(exc)[:160]}); trying the next")
    raise RuntimeError("no OCR engine: install rapidocr or rapidocr-onnxruntime")


def engine_name() -> str:
    """The tier that will actually read, after falling back. Loads it if needed."""
    with _lock:
        return _engine(tier())[0]


# ── one reading ─────────────────────────────────────────────────────────────
@dataclass
class Reading:
    boxes: list[Box] = field(default_factory=list)
    variant: str = "asli"

    @property
    def chars(self) -> int:
        return sum(len(t.strip()) for _, t, _ in self.boxes)

    @property
    def confidence(self) -> float:
        n = self.chars
        return sum(len(t.strip()) * s for _, t, s in self.boxes) / n if n else 0.0

    @property
    def weight(self) -> float:
        """What a reading is worth: confident characters, so more text read well beats less."""
        return sum(len(t.strip()) * s * s for _, t, s in self.boxes)


@dataclass
class OcrResult:
    text: str
    confidence: float
    engine: str
    passes: list[str]


def _prepare(data: bytes) -> np.ndarray:
    """RGB pixels the engines read well: upright per EXIF, no transparency, a workable size."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as opened:
        opened.load()
        picture: Image.Image = ImageOps.exif_transpose(opened) or opened
        if picture.mode in ("RGBA", "LA", "P") or "transparency" in picture.info:
            rgba = picture.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.split()[-1])
            picture = flat
        else:
            picture = picture.convert("RGB")
        w, h = picture.size
        if max(w, h) > MAX_SIDE:
            scale = MAX_SIDE / max(w, h)
            picture = picture.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
        elif min(w, h) < MIN_SIDE:
            scale = MIN_SIDE / max(1, min(w, h))
            picture = picture.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        return np.ascontiguousarray(np.asarray(picture)[:, :, ::-1])     # the engines expect BGR


def _kept(boxes: list[Box]) -> list[Box]:
    return [b for b in boxes if b[2] >= MIN_CONFIDENCE and b[1].strip()]


def _upright_share(boxes: list[Box]) -> float:
    """Share of boxes (with a few characters) that are taller than wide: text running up or down the page."""
    long = [b for b in boxes if len(b[1].strip()) >= 3]
    if not long:
        return 0.0
    upright = 0
    for corners, _, _ in long:
        xs, ys = [p[0] for p in corners], [p[1] for p in corners]
        upright += (max(ys) - min(ys)) > 1.5 * (max(xs) - min(xs))
    return upright / len(long)


def _rotate(image: np.ndarray, degrees: int) -> np.ndarray:
    """Turn counter-clockwise by 90, 180 or 270 degrees."""
    return np.ascontiguousarray(np.rot90(image, k=degrees // 90))


def _crop(image: np.ndarray, corners: list[list[float]]) -> np.ndarray:
    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
    h, w = image.shape[:2]
    x0, x1 = max(0, int(min(xs))), min(w, int(math.ceil(max(xs))))
    y0, y1 = max(0, int(min(ys))), min(h, int(math.ceil(max(ys))))
    return np.ascontiguousarray(image[y0:y1, x0:x1])


def _upside_down(engine: Any, image: np.ndarray, boxes: list[Box]) -> bool:
    """Read the clearest lines both ways up, without the line classifier that hides the answer."""
    lines = sorted((b for b in boxes if len(b[1].strip()) >= 4), key=lambda b: -len(b[1]))[:PROBE_LINES]
    if len(lines) < 2:
        return False
    flipped = 0
    for corners, _, _ in lines:
        crop = _crop(image, corners)
        if crop.size == 0 or min(crop.shape[:2]) < 4:
            continue
        straight = engine.run(crop, det=False, cls=False)
        turned = engine.run(_rotate(crop, 180), det=False, cls=False)
        s0 = max((s for _, _, s in straight), default=0.0)
        s1 = max((s for _, _, s in turned), default=0.0)
        flipped += s1 > s0 + 0.05
    return flipped / len(lines) >= FLIP_SHARE


def _variants(image: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Cleaned-up copies for a second try."""
    import cv2

    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    out: list[tuple[str, np.ndarray]] = []
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(grey)
    out.append(("kontras", cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR)))
    if float(grey.mean()) < 110:
        out.append(("dibalik", cv2.cvtColor(255 - grey, cv2.COLOR_GRAY2BGR)))
    if max(image.shape[:2]) * 2 <= MAX_SIDE:
        big = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        out.append(("2x", big))
    blur = cv2.GaussianBlur(grey, (0, 0), 1.2)
    sharp = cv2.addWeighted(grey, 1.8, blur, -0.8, 0)
    out.append(("tajam", cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR)))
    return out


def _read(engine: Any, image: np.ndarray, deadline: float) -> tuple[Reading, list[str]]:
    passes = ["asli"]
    best = Reading(_kept(engine.run(image)), "asli")

    if best.boxes and _upright_share(best.boxes) >= UPRIGHT_SHARE:
        for degrees in (90, 270):
            turned = Reading(_kept(engine.run(_rotate(image, degrees))), f"putar {degrees}")
            passes.append(turned.variant)
            if turned.weight > best.weight:
                best, image = turned, _rotate(image, degrees)
    elif best.boxes and time.monotonic() < deadline and _upside_down(engine, image, best.boxes):
        image = _rotate(image, 180)
        turned = Reading(_kept(engine.run(image)), "putar 180")
        passes.append(turned.variant)
        if turned.chars:
            best = turned

    blank = not best.chars and float(image.std()) < BLANK_STD          # an empty page: nothing to clean up
    if (best.confidence < GOOD_ENOUGH or not best.chars) and not blank:
        for name, variant in _variants(image):
            if time.monotonic() > deadline:
                passes.append("waktu habis")
                break
            trial = Reading(_kept(engine.run(variant)), name)
            passes.append(name)
            if name == "2x":                      # boxes back to the size of the page
                trial.boxes = [([[x / 2, y / 2] for x, y in c], t, s) for c, t, s in trial.boxes]
            if trial.weight > best.weight:
                best = trial
            if best.confidence >= GOOD_ENOUGH and best.chars:
                break
    return best, passes


def _lines(boxes: list[tuple[float, float, float, str]]) -> str:
    """Boxes (top, left, height, text) as lines. Boxes side by side, like table cells, share a row."""
    rows: list[list[tuple[float, float, float, str]]] = []
    for box in sorted(boxes):
        if rows and abs(box[0] - rows[-1][0][0]) <= ROW_TOLERANCE * box[2]:
            rows[-1].append(box)
        else:
            rows.append([box])
    # Two trailing spaces are a Markdown line break, so compaction keeps each line as a line.
    return "  \n".join("  ".join(item[3] for item in sorted(row, key=lambda item: item[1])) for row in rows)


def _text(reading: Reading) -> str:
    """Lines in reading order. A tilted line's upright frame is taller than its text, so rows are
    grouped by each box's centre and its real text height (the short side of the box)."""
    boxes = []
    for corners, text, _ in reading.boxes:
        pts = np.asarray(corners, dtype=float)
        height = min(float(np.linalg.norm(pts[0] - pts[3])), float(np.linalg.norm(pts[1] - pts[2])))
        if len(pts) != 4 or height <= 0:
            height = float(pts[:, 1].max() - pts[:, 1].min())
        middle = float(pts[:, 1].mean())
        boxes.append((middle - height / 2, float(pts[:, 0].min()), height, text))
    return _lines(boxes)


def read_image_detail(data: bytes, budget_s: float | None = None) -> OcrResult:
    """All the text in one image in reading order, with how sure the reading is and how it was found."""
    image = _prepare(data)
    with _lock:      # one page at a time: the engine already uses every core
        name, engine = _engine(tier())
        reading, passes = _read(engine, image, time.monotonic() + (budget_s or OCR_IMAGE_S))
    return OcrResult(_text(reading), round(reading.confidence, 3), name, passes)


def read_image(data: bytes) -> str:
    """All the text in one image, in reading order. Empty when there is none."""
    return read_image_detail(data).text


def read_images(images: list[bytes]) -> list[str | None]:
    """The text of each image, in order. None marks an image that was not read."""
    deadline = time.monotonic() + OCR_TOTAL_S
    results: list[str | None] = []
    for data in images:
        if time.monotonic() > deadline:
            results.append(None)
            continue
        try:
            results.append(read_image(data))
        except Exception as exc:  # noqa: BLE001 - one bad image must not stop the upload
            logger.warning(f"[ocr] image not read: {exc}")
            results.append(None)
    return results


__all__ = ["enabled", "max_images", "read_image", "read_image_detail", "read_images", "engine_name", "tier", "OcrResult"]
