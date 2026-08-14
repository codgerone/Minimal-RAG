"""Discover local PDFs and resolve safe user document selectors."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from rag.errors import DocumentDirectoryError, DocumentSelectionError
from rag.models import SourceDocument


HASH_READ_SIZE = 1024 * 1024


def normalize_relative_path(path: Path) -> str:
    return path.as_posix().lstrip("./")


def make_document_id(relative_path: str) -> str:
    normalized = PurePosixPath(relative_path.replace("\\", "/")).as_posix()
    return hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()[:16]


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(HASH_READ_SIZE):
            digest.update(block)
    return digest.hexdigest()


def _is_ignored(relative_path: Path) -> bool:
    return any(part.startswith(".") for part in relative_path.parts) or any(
        part.startswith("~$") for part in relative_path.parts
    )


def discover_documents(documents_dir: Path) -> list[SourceDocument]:
    documents_dir = documents_dir.resolve()
    if not documents_dir.exists():
        raise DocumentDirectoryError(
            f"documents 目录不存在：{documents_dir}",
            "请创建该目录并放入至少一份 PDF。",
        )
    if not documents_dir.is_dir():
        raise DocumentDirectoryError(
            f"DOCUMENTS_DIR 不是目录：{documents_dir}",
            "请检查 .env 中的 DOCUMENTS_DIR。",
        )

    candidates: list[tuple[str, Path]] = []
    for path in documents_dir.rglob("*"):
        if not path.is_file() or path.suffix.casefold() != ".pdf":
            continue
        relative = path.relative_to(documents_dir)
        if _is_ignored(relative):
            continue
        candidates.append((normalize_relative_path(relative), path.resolve()))

    candidates.sort(key=lambda item: item[0].casefold())
    seen: dict[str, str] = {}
    documents: list[SourceDocument] = []
    for relative_path, absolute_path in candidates:
        folded = relative_path.casefold()
        if folded in seen:
            raise DocumentDirectoryError(
                "发现大小写不敏感路径冲突："
                f"{seen[folded]!r} 与 {relative_path!r}。",
                "请重命名其中一份 PDF，确保规范化路径唯一。",
            )
        seen[folded] = relative_path
        documents.append(
            SourceDocument(
                document_id=make_document_id(relative_path),
                document_name=absolute_path.name,
                relative_path=relative_path,
                absolute_path=absolute_path,
                file_hash=hash_file(absolute_path),
            )
        )
    return documents


def resolve_document_selector(
    selector: str,
    documents: Sequence[SourceDocument],
) -> SourceDocument:
    raw = selector.strip()
    candidate_path = Path(raw)
    normalized = raw.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if (
        not raw
        or candidate_path.is_absolute()
        or any(part == ".." for part in parts)
    ):
        raise DocumentSelectionError(
            f"文档选择器不安全或无效：{selector!r}",
            "请使用相对于 documents/ 的路径或唯一文件名。",
        )

    exact = [
        document
        for document in documents
        if document.relative_path.casefold() == normalized.casefold()
    ]
    if len(exact) == 1:
        return exact[0]

    basename = PurePosixPath(normalized).name.casefold()
    matches = [
        document
        for document in documents
        if document.document_name.casefold() == basename
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        choices = "、".join(document.relative_path for document in matches)
        raise DocumentSelectionError(
            f"文件名 {selector!r} 不唯一，候选项：{choices}",
            "请提供相对于 documents/ 的完整路径。",
        )
    available = "、".join(document.relative_path for document in documents) or "无"
    raise DocumentSelectionError(
        f"未找到文档 {selector!r}。当前可用文档：{available}",
        "请检查文件名，或先把 PDF 放入 documents/。",
    )

