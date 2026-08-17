"""Collection-level manifest serialization and strict health validation."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from rag.chunker import DEFAULT_SEPARATORS
from rag.config import Settings
from rag.errors import ManifestError
from rag.models import (
    DocumentState,
    DocumentStatus,
    IndexHealth,
    IndexIssue,
    Manifest,
    ManifestDocument,
    SourceDocument,
)
from rag.vector_store import ChromaVectorStore


SCHEMA_VERSION = 1


def chunking_config(settings: Settings) -> dict[str, Any]:
    return {
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "length_function": "len",
        "keep_separator": "end",
        "separators": list(DEFAULT_SEPARATORS),
    }


def make_empty_manifest(settings: Settings) -> Manifest:
    return Manifest(
        schema_version=SCHEMA_VERSION,
        collection_name=settings.collection_name,
        embedding_model=settings.embedding_model,
        chunking=chunking_config(settings),
        documents={},
        updated_at="",
    )


def global_config_matches(manifest: Manifest, settings: Settings) -> bool:
    return (
        manifest.schema_version == SCHEMA_VERSION
        and manifest.collection_name == settings.collection_name
        and manifest.embedding_model == settings.embedding_model
        and manifest.chunking == chunking_config(settings)
    )


def _require(value: Any, expected: type, field_name: str) -> Any:
    if not isinstance(value, expected):
        raise ManifestError(f"Manifest 字段 {field_name} 类型无效。")
    return value


def load_manifest(path: Path) -> Manifest | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        _require(raw, dict, "root")
        schema_version = _require(raw.get("schema_version"), int, "schema_version")
        if schema_version != SCHEMA_VERSION:
            raise ManifestError(
                f"不支持的 Manifest schema_version：{schema_version}",
                "请执行 python -m rag ingest --force。",
            )
        raw_documents = _require(raw.get("documents"), dict, "documents")
        documents: dict[str, ManifestDocument] = {}
        seen_paths: set[str] = set()
        for document_id, item in raw_documents.items():
            _require(document_id, str, "documents key")
            _require(item, dict, f"documents.{document_id}")
            relative_path = _require(
                item.get("relative_path"), str, f"{document_id}.relative_path"
            )
            folded_path = relative_path.casefold()
            if folded_path in seen_paths:
                raise ManifestError("Manifest 中存在重复文档路径。")
            seen_paths.add(folded_path)
            counts = {
                field: _require(item.get(field), int, f"{document_id}.{field}")
                for field in ("page_count", "character_count", "chunk_count")
            }
            if any(value < 0 for value in counts.values()):
                raise ManifestError(f"Manifest 文档 {document_id} 包含负计数。")
            documents[document_id] = ManifestDocument(
                relative_path=relative_path,
                document_name=_require(
                    item.get("document_name"), str, f"{document_id}.document_name"
                ),
                file_hash=_require(
                    item.get("file_hash"), str, f"{document_id}.file_hash"
                ),
                page_count=counts["page_count"],
                character_count=counts["character_count"],
                chunk_count=counts["chunk_count"],
                indexed_at=_require(
                    item.get("indexed_at"), str, f"{document_id}.indexed_at"
                ),
            )
        return Manifest(
            schema_version=schema_version,
            collection_name=_require(
                raw.get("collection_name"), str, "collection_name"
            ),
            embedding_model=_require(
                raw.get("embedding_model"), str, "embedding_model"
            ),
            chunking=_require(raw.get("chunking"), dict, "chunking"),
            documents=documents,
            updated_at=_require(raw.get("updated_at"), str, "updated_at"),
        )
    except ManifestError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(
            f"无法读取 Manifest：{path}",
            "请检查文件内容；必要时执行 python -m rag ingest --force。",
            cause=exc,
        ) from exc


def save_manifest_atomic(path: Path, manifest: Manifest) -> None:
    payload = asdict(manifest)
    payload["documents"] = {
        document_id: asdict(manifest.documents[document_id])
        for document_id in sorted(manifest.documents)
    }
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
        temporary.replace(path)
    except OSError as exc:
        raise ManifestError(
            f"无法原子保存 Manifest：{path}",
            "请检查目录权限和磁盘空间。",
            cause=exc,
        ) from exc


def validate_index(
    settings: Settings,
    discovered: list[SourceDocument],
    manifest: Manifest | None,
    vector_store: ChromaVectorStore,
) -> IndexHealth:
    statuses: list[DocumentStatus] = []
    issues: list[IndexIssue] = []

    def unassessed_statuses(detail: str) -> tuple[DocumentStatus, ...]:
        discovered_by_id = {source.document_id: source for source in discovered}
        result: list[DocumentStatus] = []
        for source in discovered:
            recorded = manifest.documents.get(source.document_id) if manifest else None
            result.append(
                DocumentStatus(
                    DocumentState.UNASSESSED,
                    source.document_id,
                    source.relative_path,
                    source.document_name,
                    file_hash=source.file_hash,
                    page_count=recorded.page_count if recorded else None,
                    chunk_count=recorded.chunk_count if recorded else None,
                    indexed_at=recorded.indexed_at if recorded else None,
                    detail=detail,
                )
            )
        if manifest:
            for document_id, recorded in manifest.documents.items():
                if document_id not in discovered_by_id:
                    result.append(
                        DocumentStatus(
                            DocumentState.UNASSESSED,
                            document_id,
                            recorded.relative_path,
                            recorded.document_name,
                            file_hash=recorded.file_hash,
                            page_count=recorded.page_count,
                            chunk_count=recorded.chunk_count,
                            indexed_at=recorded.indexed_at,
                            detail=detail,
                        )
                    )
        return tuple(sorted(result, key=lambda item: item.relative_path.casefold()))

    if manifest is None:
        issues.append(
            IndexIssue(
                "manifest_missing",
                "索引 Manifest 不存在。",
                "执行 python -m rag ingest。",
            )
        )
        for source in discovered:
            statuses.append(
                DocumentStatus(
                    DocumentState.NEW,
                    source.document_id,
                    source.relative_path,
                    source.document_name,
                    file_hash=source.file_hash,
                )
            )
        if vector_store.count_all():
            issues.append(
                IndexIssue(
                    "orphan_collection",
                    "Chroma 中存在没有 Manifest 的记录。",
                    "执行 python -m rag ingest --force。",
                )
            )
        return IndexHealth(False, tuple(statuses), tuple(issues))

    if not global_config_matches(manifest, settings):
        detail = "当前 Embedding 模型、Collection 或分块配置与已有索引不一致，需要全量重建。"
        issues.append(
            IndexIssue(
                "global_config_mismatch",
                "当前 Embedding 或分块配置与已有索引不一致。",
                "执行 python -m rag ingest --force。",
            )
        )
        return IndexHealth(False, unassessed_statuses(detail), tuple(issues))
    if not vector_store.collection_exists():
        detail = "目标 Collection 不存在，逐文档索引状态尚未检查，需要重新建立索引。"
        issues.append(
            IndexIssue(
                "collection_missing",
                f"Collection {settings.collection_name!r} 不存在。",
                "执行 python -m rag ingest。",
            )
        )
        return IndexHealth(False, unassessed_statuses(detail), tuple(issues))

    discovered_by_id = {source.document_id: source for source in discovered}
    for source in discovered:
        recorded = manifest.documents.get(source.document_id)
        if recorded is None:
            state = DocumentState.NEW
            issues.append(
                IndexIssue(
                    f"new:{source.document_id}",
                    f"发现尚未索引的文档：{source.relative_path}",
                    "执行 python -m rag ingest。",
                )
            )
        elif recorded.file_hash != source.file_hash:
            state = DocumentState.CHANGED
            issues.append(
                IndexIssue(
                    f"changed:{source.document_id}",
                    f"文档内容已变化：{source.relative_path}",
                    "执行 python -m rag ingest。",
                )
            )
        else:
            count = vector_store.count_document(source.document_id)
            metadata = vector_store.get_metadatas(source.document_id)
            metadata_valid = all(
                item.get("file_hash") == recorded.file_hash
                and item.get("relative_path", "").casefold()
                == recorded.relative_path.casefold()
                for item in metadata
            )
            if count != recorded.chunk_count or not metadata_valid:
                state = DocumentState.INVALID
                issues.append(
                    IndexIssue(
                        f"invalid:{source.document_id}",
                        f"文档索引计数或 metadata 不一致：{source.relative_path}",
                        "重新执行 python -m rag ingest。",
                    )
                )
            else:
                state = DocumentState.CURRENT
        statuses.append(
            DocumentStatus(
                state,
                source.document_id,
                source.relative_path,
                source.document_name,
                file_hash=source.file_hash,
                page_count=recorded.page_count if recorded else None,
                chunk_count=recorded.chunk_count if recorded else None,
                indexed_at=recorded.indexed_at if recorded else None,
            )
        )

    for document_id, recorded in manifest.documents.items():
        if document_id not in discovered_by_id:
            statuses.append(
                DocumentStatus(
                    DocumentState.MISSING,
                    document_id,
                    recorded.relative_path,
                    recorded.document_name,
                    file_hash=recorded.file_hash,
                    page_count=recorded.page_count,
                    chunk_count=recorded.chunk_count,
                    indexed_at=recorded.indexed_at,
                )
            )
            issues.append(
                IndexIssue(
                    f"missing:{document_id}",
                    f"已索引文档已从 documents/ 移除：{recorded.relative_path}",
                    "恢复文件或执行 python -m rag ingest --prune。",
                )
            )

    expected_count = sum(item.chunk_count for item in manifest.documents.values())
    actual_count = vector_store.count_all()
    if actual_count != expected_count:
        issues.append(
            IndexIssue(
                "collection_count_mismatch",
                f"Collection 总记录数 {actual_count} 与 Manifest {expected_count} 不一致。",
                "执行 python -m rag ingest；必要时使用 --force。",
            )
        )
    known_ids = set(manifest.documents)
    stored_ids = {
        str(metadata.get("document_id"))
        for metadata in vector_store.get_metadatas()
        if metadata.get("document_id") is not None
    }
    if stored_ids - known_ids:
        issues.append(
            IndexIssue(
                "unknown_documents",
                "Collection 中存在 Manifest 未记录的文档。",
                "执行 python -m rag ingest --force。",
            )
        )

    statuses.sort(key=lambda item: item.relative_path.casefold())
    return IndexHealth(not issues, tuple(statuses), tuple(issues))
