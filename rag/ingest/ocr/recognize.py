"""Render a scanned page, choose its orientation, correct skew, and read it (docs/rules/ocr.md)."""

from __future__ import annotations

import math
import statistics

import fitz
import numpy as np

from rag.ingest.ocr import EngineLine, OcrEngine
from rag.ingest.ocr.models import OcrLine, OcrWord, OrientationTrial, PageOcr
from rag.models import BoundingBox

ORIENTATIONS = (0, 90, 180, 270)
LONG_LINE_RATIO = 5          # a line is "long" when its top edge is more than 5 × its left edge
DESKEW_THRESHOLD = 0.3       # degrees


def render(page: fitz.Page, dpi: int) -> np.ndarray:
    """Grayscale image of the page as displayed (its /Rotate applied)."""
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


def turn(image: np.ndarray, orientation: int) -> np.ndarray:
    """Turn the image clockwise by a multiple of 90°."""
    return np.ascontiguousarray(np.rot90(image, k=-(orientation // 90)))


def straighten(image: np.ndarray, skew_degrees: float) -> np.ndarray:
    """Turn the image back by the measured skew about its centre; same size, white fill."""
    import cv2
    height, width = image.shape
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), skew_degrees, 1.0)
    return cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=255)


def prepare_image(page: fitz.Page, dpi: int, orientation: int, skew_degrees: float, deskewed: bool) -> np.ndarray:
    """The exact image a PageOcr was read from, rebuilt from its recorded settings."""
    image = turn(render(page, dpi), orientation)
    return straighten(image, skew_degrees) if deskewed else image


def _extent(quad) -> tuple[float, float]:
    xs = [x for x, _ in quad]
    ys = [y for _, y in quad]
    return max(xs) - min(xs), max(ys) - min(ys)


def trial(orientation: int, lines: tuple[EngineLine, ...]) -> OrientationTrial:
    valid = sum(ch.isalnum() for line in lines for ch in line.text)
    mean = statistics.fmean(line.confidence for line in lines) if lines else 0.0
    vertical = sum(1 for line in lines if _extent(line.quad)[1] > _extent(line.quad)[0])
    return OrientationTrial(orientation, valid, mean, vertical, len(lines))


def needs_orientation_search(first: OrientationTrial) -> bool:
    return first.valid_chars == 0 or first.vertical_lines * 2 > first.line_count


def skew_of(lines: tuple[EngineLine, ...]) -> float | None:
    """Median angle (degrees, y downwards) of the top edge of long lines; None without long lines."""
    angles = []
    for line in lines:
        if len(line.quad) != 4:
            continue
        (x0, y0), (x1, y1), _, (x3, y3) = line.quad
        top = math.hypot(x1 - x0, y1 - y0)
        left = math.hypot(x3 - x0, y3 - y0)
        if top > LONG_LINE_RATIO * left:
            angles.append(math.degrees(math.atan2(y1 - y0, x1 - x0)))
    return statistics.median(angles) if angles else None


def _box(quad, scale: float) -> BoundingBox:
    xs = [x for x, _ in quad]
    ys = [y for _, y in quad]
    return BoundingBox(min(xs) * scale, min(ys) * scale, max(xs) * scale, max(ys) * scale)


def to_facts(lines: tuple[EngineLine, ...], dpi: int) -> tuple[OcrLine, ...]:
    scale = 72 / dpi
    return tuple(OcrLine(line.text, tuple(line.quad), _box(line.quad, scale), line.confidence,  # type: ignore[arg-type]
                         tuple(OcrWord(word.text, _box(word.quad, scale), word.confidence)
                               for word in line.words))
                 for line in lines)


def recognize_page(page: fitz.Page, page_number: int, engine: OcrEngine, dpi: int) -> PageOcr:
    rendered = render(page, dpi)
    results = {0: engine.recognize(rendered)}
    trials = [trial(0, results[0])]
    if needs_orientation_search(trials[0]):
        for orientation in ORIENTATIONS[1:]:
            results[orientation] = engine.recognize(turn(rendered, orientation))
            trials.append(trial(orientation, results[orientation]))
    chosen = max(trials, key=lambda item: item.score).orientation   # ties keep the earlier orientation
    image = turn(rendered, chosen)
    lines = results[chosen]
    skew = skew_of(lines)
    deskewed = skew is not None and abs(skew) >= DESKEW_THRESHOLD
    if deskewed:
        image = straighten(image, skew)  # type: ignore[arg-type]
        lines = engine.recognize(image)
    height, width = image.shape
    return PageOcr(page_number, int(page.rotation), dpi, chosen, tuple(trials), skew or 0.0, deskewed,
                   width, height, to_facts(lines, dpi))
