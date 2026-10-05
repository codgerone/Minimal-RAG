"""Deterministic V3 artifact names derived from public document identity."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from rag.v3.contracts.storage import ManifestDocumentRecord


def document_directory(relative_path: str, document_id: str, *, legacy: bool = False) -> str:
    stem = PurePosixPath(relative_path.replace("\\", "/")).stem
    readable = re.sub(r"[^\w.-]+", "-", stem, flags=re.UNICODE).strip(" .-_")
    # Keep browser-openable review paths on Windows despite the 64-character
    # index namespace and build identifier. Existing 56-character paths remain
    # valid as historical build locations.
    readable = re.sub(r"-+", "-", readable)[:56 if legacy else 8].rstrip(" .-_")
    return f"{readable or 'document'}--{document_id}"


def build_artifact_path(record: ManifestDocumentRecord) -> str:
    return (f"artifacts/documents/{document_directory(record.relative_path, record.document_id)}"
            f"/{record.build_id}")


def valid_artifact_paths(record: ManifestDocumentRecord) -> set[str]:
    return {build_artifact_path(record),
            (f"artifacts/documents/"
             f"{document_directory(record.relative_path, record.document_id, legacy=True)}"
             f"/{record.build_id}")}
