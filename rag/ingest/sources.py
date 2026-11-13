"""Discover source documents (PDF, Word) under documents/ and resolve user selectors."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

import fitz

from rag.models import FileType, SourceDocument

FILE_TYPES: dict[str, FileType] = {".pdf": "pdf", ".docx": "docx"}


class SourceError(ValueError):
    """Document directory or selector problem the user must fix."""


def document_id(relative_path: str) -> str:
    """Stable ID derived from the path, so editing a PDF keeps its identity."""
    return hashlib.sha256(relative_path.casefold().encode("utf-8")).hexdigest()[:16]


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            digest.update(data)
    return digest.hexdigest()


def _ignored(relative: Path) -> bool:
    return any(part.startswith(".") or part.startswith("~$") for part in relative.parts)


def _files(root: Path) -> list[tuple[str, Path]]:
    root = root.resolve()
    if not root.is_dir():
        raise SourceError(f"文档目录不存在：{root}")
    found = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_file() and not _ignored(relative):
            found.append((relative.as_posix(), path.resolve()))
    return found


def unsupported(root: Path, catalog: str = "documents.toml") -> tuple[str, ...]:
    """Files under documents/ of a type the system does not read (the catalog file excepted)."""
    return tuple(sorted((relative for relative, path in _files(root)
                         if path.suffix.casefold() not in FILE_TYPES and relative != catalog),
                        key=lambda item: (item.casefold(), item)))


def discover(root: Path) -> tuple[SourceDocument, ...]:
    found = [(relative, path) for relative, path in _files(root) if path.suffix.casefold() in FILE_TYPES]
    found.sort(key=lambda pair: (pair[0].casefold(), pair[0]))
    sources: list[SourceDocument] = []
    seen: set[str] = set()
    for relative, absolute in found:
        identity = document_id(relative)
        if identity in seen:
            raise SourceError(f"两个文档的路径仅大小写不同，无法区分：{relative}")
        seen.add(identity)
        sources.append(SourceDocument(identity, absolute.name, relative, absolute,
                                      file_hash(absolute), file_type=FILE_TYPES[absolute.suffix.casefold()]))
    return tuple(sources)


def select(selector: str, sources: tuple[SourceDocument, ...]) -> SourceDocument:
    """Match a relative path, or a file name when it is unique."""
    normalized = selector.strip().replace("\\", "/")
    if (not normalized or PurePosixPath(normalized).is_absolute() or ":" in normalized
            or any(part in {".", "..", ""} for part in normalized.split("/"))):
        raise SourceError(f"文档选择无效：{selector}")
    folded = normalized.casefold()
    exact = [item for item in sources if item.relative_path.casefold() == folded]
    if exact:
        return exact[0]
    by_name = [item for item in sources if item.document_name.casefold() == folded
               or Path(item.document_name).stem.casefold() == folded]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise SourceError(f"有多个同名文档，请使用相对路径：{selector}")
    raise SourceError(f"找不到文档：{selector}")


def has_text(source: SourceDocument, *, ocr: bool = False) -> bool:
    """False for unreadable files, and for PDFs without any text-layer page unless OCR is configured
    and some page is scanned. A Word file is readable when it is a package with a main document."""
    if source.file_type == "docx":
        import zipfile
        try:
            with zipfile.ZipFile(source.absolute_path) as package:
                return "word/document.xml" in package.namelist()
        except (OSError, zipfile.BadZipFile):
            return False
    from rag.ingest.ocr.pages import page_kind
    try:
        with fitz.open(source.absolute_path) as pdf:
            kinds = {page_kind(page) for page in pdf}
    except Exception:
        return False
    return "text_layer" in kinds or (ocr and "scanned" in kinds)
