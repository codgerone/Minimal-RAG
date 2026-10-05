"""Discover PDFs under documents/ and resolve user selectors."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

import fitz

from rag.models import SourceDocument


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


def discover(root: Path) -> tuple[SourceDocument, ...]:
    root = root.resolve()
    if not root.is_dir():
        raise SourceError(f"文档目录不存在：{root}")
    found = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() != ".pdf":
            continue
        relative = path.relative_to(root)
        if not _ignored(relative):
            found.append((relative.as_posix(), path.resolve()))
    found.sort(key=lambda pair: (pair[0].casefold(), pair[0]))
    sources: list[SourceDocument] = []
    seen: set[str] = set()
    for relative, absolute in found:
        identity = document_id(relative)
        if identity in seen:
            raise SourceError(f"两个 PDF 的路径仅大小写不同，无法区分：{relative}")
        seen.add(identity)
        sources.append(SourceDocument(identity, absolute.name, relative, absolute,
                                      file_hash(absolute)))
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
        raise SourceError(f"有多个同名 PDF，请使用相对路径：{selector}")
    raise SourceError(f"找不到文档：{selector}")


def has_text(source: SourceDocument) -> bool:
    """False for unreadable PDFs or scans without a text layer."""
    try:
        with fitz.open(source.absolute_path) as pdf:
            return any(page.get_text("text").strip() for page in pdf)
    except Exception:
        return False
