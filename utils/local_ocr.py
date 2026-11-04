"""Reads images into text on this machine, with no model call and no network.

The models this project talks to take text only, so a scanned page must become text before it
reaches them. RapidOCR runs PP-OCR on the CPU through ONNX Runtime, and its models come inside the
package. Everything here is Apache, MIT or BSD licensed, which matters because SquidWings is closed
source: the markitdown-ocr plugin would pull in pymupdf, which is AGPL.

It reads text only. It cannot describe a photo or a chart.
"""

from __future__ import annotations

import io
import os
import threading
import time
from typing import Any

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)

MIN_CONFIDENCE = 0.3
OCR_TOTAL_S = 120.0       # someone is watching the upload
ROW_TOLERANCE = 0.6       # share of a line's height two boxes may differ by and still be one row

_engine: Any = None
_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("DOCUMENT_OCR", "true").strip().lower() != "false"


def max_images() -> int:
    try:
        return max(0, int(os.getenv("OCR_MAX_IMAGES", "20")))
    except ValueError:
        return 20


def _load() -> Any:
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


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


def read_image(data: bytes) -> str:
    """All the text in one image, in reading order. Empty when there is none."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as picture:
        pixels = np.asarray(picture.convert("RGB"))[:, :, ::-1]      # the engine expects BGR
    with _lock:      # one page at a time: the engine already uses every core
        found, _ = _load()(pixels)
    boxes = []
    for corners, text, confidence in found or []:
        if float(confidence) >= MIN_CONFIDENCE:
            ys = [point[1] for point in corners]
            boxes.append((float(min(ys)), float(min(p[0] for p in corners)), float(max(ys) - min(ys)), str(text)))
    return _lines(boxes)


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


__all__ = ["enabled", "max_images", "read_image", "read_images"]
