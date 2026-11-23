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
4. Small text (lines under ``SMALL_TEXT_PX`` tall, a phone screenshot or the fine print of a
   receipt): read again at twice the size even when the first reading looked sure, since tiny
   glyphs are often read wrong with confidence.
5. Unsure (average confidence below ``GOOD_ENOUGH``) or nothing found: read again from cleaned-up
   copies (contrast-equalised grey, twice the size, inverted when the page is dark, sharpened),
   and the reading with the most confident text wins.
6. Missed lines: where the gap between two lines is about one line tall or more and that band
   holds ink, the band is detected again from a sharpened, enlarged copy, so a blurred line the
   detector skipped is still read.
7. Line by line: each line still read with less than ``LINE_RETRY_BELOW`` confidence is cut out
   with a margin and read again on its own, enlarged, contrast-equalised and as black on white,
   and the surest of those readings replaces it. One smudged line no longer costs the page.

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
SMALL_TEXT_PX = 14        # median line height below this: also read at twice the size
LINE_RETRY_BELOW = 0.8    # a line read less surely than this is read again on its own
LINE_PAD = 4              # pixels of margin around a line cut out for a second look
MAX_LINE_RETRIES = 60
GAP_LINES = 0.9           # a gap this many line heights tall between two lines may hide a missed one
GAP_INK_STD = 12.0        # a band with less pixel spread than this is empty paper
MAX_GAPS = 12
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


def _median_height(boxes: list[Box]) -> float:
    heights = sorted(
        min(abs(c[3][1] - c[0][1]), abs(c[2][1] - c[1][1])) or (max(p[1] for p in c) - min(p[1] for p in c))
        for c, _, _ in boxes if len(c) == 4
    )
    return float(heights[len(heights) // 2]) if heights else 0.0


def _twice(engine: Any, image: np.ndarray) -> Reading:
    import cv2

    big = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    trial = Reading(_kept(engine.run(big)), "2x")
    trial.boxes = [([[x / 2, y / 2] for x, y in c], t, s) for c, t, s in trial.boxes]
    return trial


def _line_variants(crop: np.ndarray) -> list[np.ndarray]:
    """One line, enlarged, as it is, contrast-equalised, and as black on white (Otsu)."""
    import cv2

    scale = 2 if crop.shape[0] < 64 else 1
    big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale > 1 else crop
    grey = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(4, 4)).apply(grey)
    _, otsu = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if float(otsu.mean()) < 127:      # light text on dark: make it dark on light
        otsu = 255 - otsu
    return [big, cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR), cv2.cvtColor(otsu, cv2.COLOR_GRAY2BGR)]


def _refine_lines(engine: Any, image: np.ndarray, reading: Reading, deadline: float) -> int:
    """Read each unsure line again on its own; keep a clearly surer reading. Returns lines improved."""
    h, w = image.shape[:2]
    improved = 0
    unsure = sorted((i for i, b in enumerate(reading.boxes) if b[2] < LINE_RETRY_BELOW), key=lambda i: reading.boxes[i][2])
    for i in unsure[:MAX_LINE_RETRIES]:
        if time.monotonic() > deadline:
            break
        corners, text, score = reading.boxes[i]
        xs, ys = [p[0] for p in corners], [p[1] for p in corners]
        x0, x1 = max(0, int(min(xs)) - LINE_PAD), min(w, int(math.ceil(max(xs))) + LINE_PAD)
        y0, y1 = max(0, int(min(ys)) - LINE_PAD), min(h, int(math.ceil(max(ys))) + LINE_PAD)
        crop = np.ascontiguousarray(image[y0:y1, x0:x1])
        if crop.size == 0 or min(crop.shape[:2]) < 6:
            continue
        best_text, best_score = text, score
        for variant in _line_variants(crop):
            for _, found, found_score in engine.run(variant, det=False, cls=False):
                if found.strip() and found_score > best_score:
                    best_text, best_score = found, found_score
        # A clear gain only: a near tie keeps what the whole-page reading saw in context.
        if best_score >= score + 0.05 and best_text.strip():
            reading.boxes[i] = (corners, best_text, best_score)
            improved += 1
    return improved


def _fill_gaps(engine: Any, image: np.ndarray, reading: Reading, deadline: float) -> int:
    """Detect again inside suspicious gaps between lines. Returns the lines added."""
    import cv2

    spans = sorted(
        (min(p[1] for p in c), max(p[1] for p in c), min(p[0] for p in c), max(p[0] for p in c))
        for c, _, _ in reading.boxes
    )
    if len(spans) < 2:
        return 0
    height = _median_height(reading.boxes) or 1.0
    h, w = image.shape[:2]
    left, right = max(0, int(min(s[2] for s in spans)) - 8), min(w, int(max(s[3] for s in spans)) + 8)
    added: list[Box] = []
    gaps = 0
    bottom = spans[0][1]
    for top, low, _, _ in spans[1:]:
        if top - bottom >= GAP_LINES * height + 0.5 * height and gaps < MAX_GAPS and time.monotonic() < deadline:
            gaps += 1
            y0, y1 = max(0, int(bottom) + 1), min(h, int(top) - 1)
            band = image[y0:y1, left:right]
            if band.size and float(cv2.cvtColor(band, cv2.COLOR_BGR2GRAY).std()) >= GAP_INK_STD:
                grey = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
                sharp = cv2.addWeighted(grey, 1.8, cv2.GaussianBlur(grey, (0, 0), 1.2), -0.8, 0)
                big = cv2.resize(cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR), None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                pad = cv2.copyMakeBorder(big, 16, 16, 16, 16, cv2.BORDER_REPLICATE)
                for corners, text, score in _kept(engine.run(pad)):
                    page = [[(x - 16) / 2 + left, (y - 16) / 2 + y0] for x, y in corners]
                    added.append((page, text, score))
        bottom = max(bottom, low)
    reading.boxes.extend(added)
    return len(added)


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
    tried_twice = False
    if (best.chars and 0 < _median_height(best.boxes) < SMALL_TEXT_PX and max(image.shape[:2]) * 2 <= MAX_SIDE
            and time.monotonic() < deadline):
        trial = _twice(engine, image)
        passes.append("2x (teks kecil)")
        tried_twice = True
        if trial.weight > best.weight:
            best = trial
    if (best.confidence < GOOD_ENOUGH or not best.chars) and not blank:
        for name, variant in _variants(image):
            if name == "2x" and tried_twice:
                continue
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
    if best.boxes and time.monotonic() < deadline:
        found = _fill_gaps(engine, image, best, deadline)
        if found:
            passes.append(f"celah ({found})")
    if best.boxes and time.monotonic() < deadline:
        # Line boxes are in page coordinates; a reading from a turned copy matches the turned image.
        fixed = _refine_lines(engine, image, best, deadline)
        if fixed:
            passes.append(f"baris ulang ({fixed})")
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
