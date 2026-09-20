"""Strict pipeline_manifest_v2 persistence and compatibility validation."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from rag.build_config import (
    BuildConfig, DocumentTextConfig, EmbeddingConfig, TableHeaderConfig,
    TableSelectionConfig, TableSerializationConfig, TokenizerConfig,
    V1BuildConfig, V1ChunkerConfig, V1DependencyVersions, V1ParserConfig,
    V2BuildConfig, V2ChunkerConfig, V2DependencyVersions, V2ParserConfig,
    validate_build_config,
)
from rag.errors import ManifestError
from rag.document_registry import make_artifact_document_name
from rag.pipeline_registry import build_config_fingerprint
from rag.pipeline_types import PipelineId


@dataclass(frozen=True)
class ManifestDocumentRecord:
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    build_id: str
    build_config_fingerprint: str
    page_count: int
    character_count: int
    chunk_count: int
    artifact_path: str | None
    indexed_at: str

    def __post_init__(self) -> None:
        strings = (self.document_id, self.document_name, self.relative_path, self.file_hash,
                   self.build_id, self.build_config_fingerprint, self.indexed_at)
        if any(not isinstance(item, str) or not item for item in strings):
            raise ManifestError("ManifestDocumentRecord 必需字符串不能为空。")
        counts = (self.page_count, self.character_count, self.chunk_count)
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in counts):
            raise ManifestError("ManifestDocumentRecord 计数必须是非负整数。")
        if self.page_count == 0 or self.chunk_count == 0:
            raise ManifestError("已发布文档的 page_count 和 chunk_count 必须大于零。")


@dataclass(frozen=True)
class PipelineManifest:
    pipeline_id: PipelineId
    collection_name: str
    build_config: BuildConfig
    build_config_fingerprint: str
    documents: dict[str, ManifestDocumentRecord]
    created_at: str
    updated_at: str
    schema_version: Literal["pipeline_manifest_v2"] = "pipeline_manifest_v2"

    def __post_init__(self) -> None:
        if self.schema_version != "pipeline_manifest_v2":
            raise ManifestError("Manifest schema_version 不受支持。")
        if self.pipeline_id not in {"v1", "v2"}:
            raise ManifestError("Manifest pipeline_id 无效。")
        if not isinstance(self.collection_name, str) or not self.collection_name:
            raise ManifestError("Manifest collection_name 不能为空。")
        if not isinstance(self.documents, dict):
            raise ManifestError("Manifest documents 必须是 object。")
        if self.build_config.pipeline_id != self.pipeline_id:
            raise ManifestError("Manifest pipeline 与 BuildConfig 不一致。")
        if build_config_fingerprint(self.build_config) != self.build_config_fingerprint:
            raise ManifestError("Manifest BuildConfig fingerprint 不一致。")
        for key, record in self.documents.items():
            if key != record.document_id or record.build_config_fingerprint != self.build_config_fingerprint:
                raise ManifestError("Manifest 文档 key 或 fingerprint 不一致。")
            if self.pipeline_id == "v1" and record.artifact_path is not None:
                raise ManifestError("V1 ManifestDocument artifact_path 必须为空。")
            if self.pipeline_id == "v2" and record.artifact_path is None:
                raise ManifestError("V2 ManifestDocument artifact_path 不能为空。")
            if self.pipeline_id == "v2":
                artifact = PurePosixPath(record.artifact_path or "")
                artifact_document = make_artifact_document_name(
                    record.relative_path, record.document_id
                )
                expected = PurePosixPath(
                    ".rag", "system-v2", "artifacts", "v2", "documents",
                    artifact_document, record.build_id,
                )
                if artifact.is_absolute() or artifact != expected or ".." in artifact.parts:
                    raise ManifestError("V2 artifact_path 未精确指向规定的 active build。")


def make_empty_manifest(runtime, timestamp: str) -> PipelineManifest:
    return PipelineManifest(runtime.pipeline_id, runtime.collection_name, runtime.build_config,
                            runtime.build_config_fingerprint, {}, timestamp, timestamp)


def _exact(data: dict, expected: set[str], label: str) -> None:
    if set(data) != expected:
        raise ManifestError(f"{label} 字段不匹配：缺少 {sorted(expected - set(data))}，额外 {sorted(set(data) - expected)}。")


def deserialize_build_config(data: dict) -> BuildConfig:
    pipeline = data.get("pipeline_id")
    if pipeline == "v1":
        _exact(data, {"parser", "chunker", "tokenizer", "embedding", "dependency_versions", "schema_version", "pipeline_id"}, "V1BuildConfig")
        config = V1BuildConfig(V1ParserConfig(**data["parser"]), V1ChunkerConfig(**data["chunker"]),
                               TokenizerConfig(**data["tokenizer"]), EmbeddingConfig(**data["embedding"]),
                               V1DependencyVersions(**data["dependency_versions"]),
                               schema_version=data["schema_version"], pipeline_id=data["pipeline_id"])
    elif pipeline == "v2":
        _exact(data, {"parser", "table_selection", "table_header", "table_serialization", "document_text", "chunker", "tokenizer", "embedding", "dependency_versions", "schema_version", "pipeline_id"}, "V2BuildConfig")
        selection = dict(data["table_selection"])
        for key in ("strategies", "metric_weights", "tool_tiebreak_order"):
            selection[key] = tuple(selection[key])
        config = V2BuildConfig(
            V2ParserConfig(**data["parser"]), TableSelectionConfig(**selection),
            TableHeaderConfig(**data["table_header"]), TableSerializationConfig(**data["table_serialization"]),
            DocumentTextConfig(**data["document_text"]), V2ChunkerConfig(**data["chunker"]),
            TokenizerConfig(**data["tokenizer"]), EmbeddingConfig(**data["embedding"]),
            V2DependencyVersions(**data["dependency_versions"]),
            schema_version=data["schema_version"], pipeline_id=data["pipeline_id"],
        )
    else:
        raise ManifestError("Manifest BuildConfig pipeline_id 无效。")
    validate_build_config(config)
    return config


def load_pipeline_manifest(path: Path, runtime=None) -> PipelineManifest | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ManifestError("Manifest 顶层必须是 object。")
        _exact(raw, {"pipeline_id", "collection_name", "build_config", "build_config_fingerprint",
                     "documents", "created_at", "updated_at", "schema_version"}, "PipelineManifest")
        if raw["schema_version"] != "pipeline_manifest_v2" or not isinstance(raw["documents"], dict):
            raise ManifestError("Manifest schema 或 documents 无效。")
        config = deserialize_build_config(raw["build_config"])
        records = {}
        expected_record = set(ManifestDocumentRecord.__dataclass_fields__)
        for key, value in raw["documents"].items():
            if not isinstance(value, dict):
                raise ManifestError("ManifestDocumentRecord 必须是 object。")
            _exact(value, expected_record, "ManifestDocumentRecord")
            records[key] = ManifestDocumentRecord(**value)
        manifest = PipelineManifest(raw["pipeline_id"], raw["collection_name"], config,
                                    raw["build_config_fingerprint"], records,
                                    raw["created_at"], raw["updated_at"], raw["schema_version"])
        if runtime is not None and (
            manifest.pipeline_id != runtime.pipeline_id
            or manifest.collection_name != runtime.collection_name
        ):
            raise ManifestError("Manifest 与所选 pipeline 身份不一致。")
        return manifest
    except ManifestError:
        raise
    except Exception as exc:
        raise ManifestError(f"无法严格解析 Manifest：{path}", cause=exc) from exc


def save_pipeline_manifest_atomic(path: Path, manifest: PipelineManifest, build_id: str) -> None:
    payload = asdict(manifest)
    payload["documents"] = {key: payload["documents"][key] for key in sorted(payload["documents"])}
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{build_id}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception as exc:
        if temporary.exists():
            temporary.unlink()
        raise ManifestError(f"无法原子保存 Manifest：{path}", cause=exc) from exc
