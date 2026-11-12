"""OCR facts: what each page was judged to be, and what the engine read on scanned pages.

Facts are written once to `.rag/ocr/<key>/ocr.json` and never rewritten by later stages; the text-layer
PDF `ocr.pdf` is derived from them. Coordinates of words and lines are PDF points on the `ocr.pdf`
page (top-left origin, the same space as BoundingBox); line quadrilaterals are also kept in pixels of
the image the engine read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from rag.models import BoundingBox

PageKind: TypeAlias = Literal["text_layer", "scanned", "blank"]
Quad: TypeAlias = tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]


@dataclass(frozen=True)
class PageClass:
    page_number: int
    kind: PageKind
    rotation: int          # the source page's /Rotate


@dataclass(frozen=True)
class OcrWord:
    text: str
    box: BoundingBox
    confidence: float


@dataclass(frozen=True)
class OcrLine:
    text: str
    quad: Quad             # pixels of the recognised image, as the engine gave it
    box: BoundingBox
    confidence: float
    words: tuple[OcrWord, ...]


@dataclass(frozen=True)
class OrientationTrial:
    orientation: int       # clockwise degrees the rendered image was turned before recognition
    valid_chars: int       # letters and digits in the recognised text
    mean_confidence: float  # over lines; 0 when no line was found
    vertical_lines: int    # lines whose box is taller than wide
    line_count: int

    @property
    def score(self) -> float:
        return self.mean_confidence * self.valid_chars


@dataclass(frozen=True)
class PageOcr:
    page_number: int
    source_rotation: int
    dpi: int
    orientation: int       # chosen trial
    trials: tuple[OrientationTrial, ...]
    skew_degrees: float    # median top-edge angle of long lines; the image was turned back by it when ≥ threshold
    deskewed: bool
    image_width: int       # of the recognised image (after orientation and deskew)
    image_height: int
    lines: tuple[OcrLine, ...]

    @property
    def width_pt(self) -> float:
        return self.image_width * 72 / self.dpi

    @property
    def height_pt(self) -> float:
        return self.image_height * 72 / self.dpi

    @property
    def words(self) -> tuple[OcrWord, ...]:
        return tuple(word for line in self.lines for word in line.words)


@dataclass(frozen=True)
class OcrFacts:
    file_hash: str                  # of the source PDF
    settings: dict[str, Any]        # [ocr] settings and engine identity; their hash names the folder
    pages: tuple[PageClass, ...]
    ocr_pages: tuple[PageOcr, ...]  # scanned pages only, in page order
    schema_version: Literal["ocr_facts_v1"] = "ocr_facts_v1"


@dataclass(frozen=True)
class DocumentPages:
    """Per-document page record kept with the intermediate results of every index."""
    pages: tuple[PageClass, ...]
    ocr_key: str | None             # folder under .rag/ocr/ when the document has scanned pages and was OCR'd

    @property
    def document_type(self) -> Literal["digital", "scanned", "partly_scanned"]:
        kinds = {page.kind for page in self.pages if page.kind != "blank"}
        if "scanned" not in kinds:
            return "digital"
        return "scanned" if kinds == {"scanned"} else "partly_scanned"
