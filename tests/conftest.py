from __future__ import annotations

import hashlib
from pathlib import Path

import fitz
import pytest

from rag.config import AssemblyConfig, Component
from rag.ingest.chunkers.characters import CharacterChunker
from rag.ingest.parsers.pymupdf_pages import PyMuPDFPagesParser
from rag.paths import Workspace
from rag.registry import Assembly


class FakeEmbedder:
    """Deterministic bag-of-letters vectors: no model download, stable similarities."""

    def identity(self):
        return {"model": "fake"}

    def _vector(self, text: str) -> list[float]:
        counts = [0.0] * 26
        for char in text.lower():
            if "a" <= char <= "z":
                counts[ord(char) - 97] += 1
        norm = sum(v * v for v in counts) ** 0.5 or 1.0
        return [v / norm for v in counts]

    def encode_passages(self, texts):
        return [self._vector(t) for t in texts]

    def encode_query(self, text):
        return self._vector(text)


def make_pdf(path: Path, pages: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as pdf:
        for text in pages:
            page = pdf.new_page(width=300, height=300)
            page.insert_textbox(fitz.Rect(20, 20, 280, 280), text, fontsize=9)
        pdf.save(path)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    (tmp_path / "configs").mkdir()
    (tmp_path / "documents").mkdir()
    return Workspace(tmp_path)


@pytest.fixture
def fake_assembly(tmp_path: Path) -> Assembly:
    config = AssemblyConfig("fake", "", tmp_path / "configs" / "fake.toml",
                            Component("pymupdf_pages"), (), None,
                            Component("characters", {"chunk_size": 80, "chunk_overlap": 10}),
                            Component("fake"), Component("semantic"), 3, Component("openrouter"))
    return Assembly(config, PyMuPDFPagesParser(), (), None, FakeEmbedder(),
                    CharacterChunker(80, 10))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
