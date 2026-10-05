"""Full V3 chunk metadata codec for Chroma's scalar metadata limit."""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from typing import Any

from rag.v3.application.assembly import canonical_json_bytes
from rag.v3.application.vector_records import make_vector_records
from rag.v3.contracts.documents import (
    BoundingBox, ChunkBatch, ChunkSource, PageSpan, SourceDocument,
)
from rag.v3.contracts.retrieval import PassageEmbeddingBatch
from rag.v3.contracts.storage import ChunkMetadata, VectorRecord

SCHEMA = "vector_metadata_v3"
FIELDS = frozenset({
    "schema_version", "document_id", "document_name", "relative_path", "file_hash",
    "build_id", "build_fingerprint", "text_sha256", "chunk_index", "chunk_kind",
    "token_count_or_minus_one", "parent_unit_id_or_empty", "fragment_index",
    "fragment_count", "page_numbers_json", "sources_json",
})


def encode_metadata(value: ChunkMetadata) -> dict[str, str | int | float | bool]:
    return {
        "schema_version": SCHEMA,
        "document_id": value.document_id,
        "document_name": value.document_name,
        "relative_path": value.relative_path,
        "file_hash": value.file_hash,
        "build_id": value.build_id,
        "build_fingerprint": value.build_fingerprint,
        "text_sha256": value.text_sha256,
        "chunk_index": value.chunk_index,
        "chunk_kind": value.kind,
        "token_count_or_minus_one": value.token_count if value.token_count is not None else -1,
        "parent_unit_id_or_empty": value.parent_unit_id or "",
        "fragment_index": value.fragment_index,
        "fragment_count": value.fragment_count,
        "page_numbers_json": canonical_json_bytes(value.page_numbers).decode("utf-8"),
        "sources_json": canonical_json_bytes([asdict(item) for item in value.sources]).decode("utf-8"),
    }


def _shape(cls: type, value: Any) -> dict:
    expected = {field.name for field in fields(cls)}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{cls.__name__} shape invalid")
    return value


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate nested metadata key")
        output[key] = value
    return output


def decode_metadata(raw: dict[str, object]) -> ChunkMetadata:
    if set(raw) != FIELDS or raw.get("schema_version") != SCHEMA:
        raise ValueError("vector metadata schema or fields invalid")
    string_fields = ("document_id", "document_name", "relative_path", "file_hash",
                     "build_id", "build_fingerprint", "text_sha256", "chunk_kind",
                     "parent_unit_id_or_empty", "page_numbers_json", "sources_json")
    integer_fields = ("chunk_index", "token_count_or_minus_one", "fragment_index", "fragment_count")
    if any(type(raw[key]) is not str for key in string_fields) or any(
            type(raw[key]) is not int for key in integer_fields):
        raise ValueError("vector metadata scalar types invalid")
    token_count = raw["token_count_or_minus_one"]
    if token_count < -1 or token_count == 0:
        raise ValueError("vector metadata token count invalid")
    pages = json.loads(raw["page_numbers_json"], object_pairs_hook=_strict_pairs)
    sources_data = json.loads(raw["sources_json"], object_pairs_hook=_strict_pairs)
    if not isinstance(pages, list) or any(type(page) is not int for page in pages):
        raise ValueError("vector metadata pages invalid")
    if not isinstance(sources_data, list):
        raise ValueError("vector metadata sources invalid")
    sources = []
    for source_data in sources_data:
        source_data = _shape(ChunkSource, source_data)
        spans = []
        if not isinstance(source_data["page_spans"], list):
            raise ValueError("page spans must be an array")
        for span_data in source_data["page_spans"]:
            span_data = _shape(PageSpan, span_data)
            box = span_data["bbox"]
            spans.append(PageSpan(span_data["page_number"],
                                  BoundingBox(**_shape(BoundingBox, box)) if box is not None else None,
                                  span_data["source_ref"]))
        sources.append(ChunkSource(source_data["node_id"], tuple(spans),
                                   source_data["source_text_start"],
                                   source_data["source_text_end"],
                                   source_data["repeated_context"],
                                   source_data["context_kind"]))
    return ChunkMetadata(
        "chunk_metadata_v3", raw["document_id"], raw["document_name"],
        raw["relative_path"], raw["build_id"], raw["file_hash"],
        raw["build_fingerprint"], raw["text_sha256"], raw["chunk_index"],
        raw["chunk_kind"], None if token_count == -1 else token_count,
        tuple(pages), tuple(sources), raw["parent_unit_id_or_empty"] or None,
        raw["fragment_index"], raw["fragment_count"],
    )
