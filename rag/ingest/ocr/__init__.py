"""OCR of scanned pages: an engine reads text, boxes and confidences; nothing else.

Structure (titles, paragraphs, tables, reading order) is recovered downstream by the parser and table
tools reading the derived text-layer PDF, exactly as for digital PDFs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np


@dataclass(frozen=True)
class EngineWord:
    text: str
    quad: tuple[tuple[float, float], ...]   # pixels
    confidence: float


@dataclass(frozen=True)
class EngineLine:
    text: str
    quad: tuple[tuple[float, float], ...]   # pixels, clockwise from top-left
    confidence: float
    words: tuple[EngineWord, ...]


class OcrEngine(Protocol):
    def identity(self) -> dict[str, Any]:
        """Name, version and model files: part of the build fingerprint and the OCR folder key."""
        ...

    def recognize(self, image: np.ndarray) -> tuple[EngineLine, ...]:
        """Read a grayscale image (uint8, H × W). Every line carries its words."""
        ...
