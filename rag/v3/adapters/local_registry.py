"""Project-local PDF discovery without importing the legacy runtime."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from rag.v3.contracts.documents import SourceDocument


class RegistryError(ValueError):
    def __init__(self, code: Literal["directory_missing", "directory_invalid", "path_conflict",
                                     "selector_invalid", "selector_ambiguous", "document_missing"],
                 detail: str):
        self.code = code
        super().__init__(detail)


@dataclass(frozen=True)
class RegistryRequest:
    documents_root: Path
    selector: str | None


def document_id(relative_path: str) -> str:
    return hashlib.sha256(relative_path.casefold().encode("utf-8")).hexdigest()[:16]


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            digest.update(data)
    return digest.hexdigest()


def _ignored(relative: Path) -> bool:
    return any(part.startswith(".") or part.startswith("~$") for part in relative.parts)


class LocalPdfRegistry:
    def discover(self, request: RegistryRequest) -> tuple[SourceDocument, ...]:
        root = request.documents_root.resolve()
        if not root.exists():
            raise RegistryError("directory_missing", str(root))
        if not root.is_dir():
            raise RegistryError("directory_invalid", str(root))
        candidates = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() != ".pdf":
                continue
            relative = path.relative_to(root)
            if _ignored(relative):
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise RegistryError("selector_invalid", f"PDF escapes documents root: {relative}")
            candidates.append((relative.as_posix(), resolved))
        candidates.sort(key=lambda pair: (pair[0].casefold(), pair[0]))
        seen_paths: set[str] = set()
        seen_ids: set[str] = set()
        sources = []
        for relative, absolute in candidates:
            folded = relative.casefold()
            if folded in seen_paths:
                raise RegistryError("path_conflict", relative)
            seen_paths.add(folded)
            identity = document_id(relative)
            if identity in seen_ids:
                raise RegistryError("path_conflict", f"document ID collision: {relative}")
            seen_ids.add(identity)
            sources.append(SourceDocument(identity, absolute.name, relative, absolute,
                                          _hash_file(absolute)))
        if request.selector is None:
            return tuple(sources)
        return (self.resolve_selector(request.selector, tuple(sources)),)

    @staticmethod
    def resolve_selector(selector: str, sources: tuple[SourceDocument, ...]) -> SourceDocument:
        normalized = selector.strip().replace("\\", "/")
        path = PurePosixPath(normalized)
        if (not normalized or path.is_absolute() or ":" in normalized or
                any(part in {".", "..", ""} for part in normalized.split("/"))):
            raise RegistryError("selector_invalid", selector)
        exact = [source for source in sources if source.relative_path.casefold() == normalized.casefold()]
        if len(exact) == 1:
            return exact[0]
        if "/" in normalized:
            raise RegistryError("document_missing", selector)
        matches = [source for source in sources
                   if source.document_name.casefold() == normalized.casefold()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RegistryError("selector_ambiguous", selector)
        raise RegistryError("document_missing", selector)
