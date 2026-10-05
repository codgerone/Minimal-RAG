"""Build and read back all stage snapshots before passage encoding or publication."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from rag.v3.application.assembly import canonical_json_bytes, readable_json_bytes
from rag.v3.application.review_pages import (
    render_extractor_review, render_preparation_review, render_selector_review,
)
from rag.v3.contracts.artifacts import (
    ArtifactEnvelope, ArtifactFileRef, BoundExtractionReport, SnapshotManifest,
    StagedArtifacts, TableContentPreparationSnapshot, TableExtractionSnapshot,
    TableSelectionSnapshot,
)
from rag.v3.contracts.assembly import BuildProjection
from rag.v3.contracts.documents import ChunkBatch
from rag.v3.contracts.processing import ProcessingResult


class ArtifactError(RuntimeError):
    def __init__(self, code: str, role: str | None = None):
        self.code = code
        self.role = role
        super().__init__(f"{code}: {role or ''}")


ROLES = (
    ("main_parser_native", "main-parser-native.json"),
    ("main_parser", "main-parser.json"),
    ("table_extractor", "table-extractor.json"),
    ("table_extractor_review", "table-extractor-review.html"),
    ("table_selector", "table-selector.json"),
    ("table_selector_review", "table-selector-review.html"),
    ("table_content_preparation", "table-content-preparation.json"),
    ("table_content_preparation_review", "table-content-preparation-review.html"),
    ("parsed_document", "parsed-document.json"),
    ("chunks", "chunks.json"),
    ("chunk_review", "chunk-review.html"),
)
FILENAMES = dict(ROLES)
SCHEMAS = {
    "main_parser_native": "main_parser_native_v3",
    "main_parser": "main_parser_v3",
    "table_extractor": "table_extractor_v3",
    "table_selector": "table_selector_v3",
    "table_content_preparation": "table_content_preparation_v3",
    "parsed_document": "parsed_document_v3",
    "chunks": "document_chunks_v3",
}
CONDITIONAL_SLOTS = (
    "document_processor.table_extractor",
    "document_processor.table_selector",
    "document_assembler.table_content_preparation",
)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _page(title: str, processing: ProcessingResult, body: str,
          *, count: int, main_parser_plugin_id: str) -> str:
    source = processing.source
    return (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        f'<title>{_escape(title)}</title><style>'
        'body{font:15px/1.6 system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem}'
        'section,article{border:1px solid #bbb;border-radius:7px;padding:1rem;margin:1rem 0}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f7f7;padding:.7rem}'
        'table{border-collapse:collapse}td,th{border:1px solid #888;padding:.35rem}'
        'th{background:#e7f2ff}</style></head><body>'
        f'<h1>{_escape(title)}</h1><p data-document-id="{_escape(source.document_id)}" '
        f'data-build-id="{_escape(processing.build_id)}">'
        f'{_escape(source.document_name)} · { _escape(source.document_id)} · '
        f'{_escape(processing.build_id)} · {_escape(main_parser_plugin_id)} · '
        f'table branch: {_escape(processing.table_branch_attached)} · count: {count}</p>'
        f'{body}</body></html>'
    )


def _chunk_review(processing: ProcessingResult, batch: ChunkBatch, parser_id: str) -> str:
    body = "".join(
        f'<article data-chunk-id="{_escape(chunk.chunk_id)}" '
        f'data-chunk-index="{chunk.chunk_index}" '
        f'data-text-sha256="{hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()}">'
        f'<h2>{_escape(chunk.chunk_id)} · {_escape(chunk.kind)}</h2>'
        f'<p>pages: {_escape(sorted({span.page_number for source in chunk.sources for span in source.page_spans}))}'
        f' · sources: {_escape(", ".join(source.node_id for source in chunk.sources))}'
        f' · contexts: {_escape(", ".join(source.context_kind for source in chunk.sources))}</p>'
        f'<pre>{_escape(chunk.text)}</pre></article>' for chunk in batch.chunks
    )
    return _page("Chunker 审核", processing, body, count=len(batch.chunks),
                 main_parser_plugin_id=parser_id)


def _json_bytes(value: object) -> bytes:
    try:
        return readable_json_bytes(asdict(value))
    except (TypeError, ValueError) as exc:
        raise ArtifactError("json_generation_failed") from exc


def _write_and_readback(path: Path, content: bytes, role: str) -> ArtifactFileRef:
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != content:
            raise ArtifactError("artifact_staging_readback", role)
    except ArtifactError:
        raise
    except OSError as exc:
        raise ArtifactError("artifact_staging_write", role) from exc
    return ArtifactFileRef(role, path.name, hashlib.sha256(content).hexdigest(), len(content))


def stage_artifacts(
    processing: ProcessingResult,
    batch: ChunkBatch,
    projection: BuildProjection,
    workspace_root: Path,
    transaction_id: str,
) -> StagedArtifacts:
    """Generate and verify the exact role set inside this index's private staging."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", transaction_id):
        raise ArtifactError("artifact_identity_mismatch")
    expected_fingerprint = hashlib.sha256(canonical_json_bytes(asdict(projection))).hexdigest()
    identity = processing.index_identity
    if (expected_fingerprint != identity.build_fingerprint
            or batch.document_id != processing.source.document_id
            or batch.file_hash != processing.source.file_hash
            or processing.parsed_document.file_hash != processing.source.file_hash
            or not processing.build_id):
        raise ArtifactError("artifact_identity_mismatch")
    nodes = {node.node_id for node in processing.parsed_document.nodes}
    nodes.update(item.item_id for node in processing.parsed_document.nodes
                 if hasattr(node, "items") for item in node.items)
    if any(source.node_id not in nodes for chunk in batch.chunks for source in chunk.sources):
        raise ArtifactError("artifact_reference_mismatch", "chunks")
    bindings = [item for item in projection.bindings if item.slot_id == "document_processor.table_extractor"]
    bindings.sort(key=lambda item: item.order)
    if len(bindings) != len(processing.extraction_reports):
        raise ArtifactError("artifact_identity_mismatch", "table_extractor")
    parser_bindings = [item for item in projection.bindings
                       if item.slot_id == "document_processor.main_parser"]
    if (len(parser_bindings) != 1
            or parser_bindings[0].plugin_id != processing.native_parser_evidence.parser_plugin_id):
        raise ArtifactError("artifact_identity_mismatch", "main_parser_native")
    branch = bool(bindings)
    if (branch != processing.table_branch_attached
            or branch != (processing.grouping_report is not None)
            or branch != (processing.scoring_report is not None)):
        raise ArtifactError("stage_result_missing", "table_selector")
    if branch:
        slots = tuple(item.slot_id for item in processing.primary_document.table_slots)
        if (tuple(item.slot_id for item in processing.resolutions) != slots
                or tuple(item.slot_id for item in processing.prepared_tables) != slots):
            raise ArtifactError("artifact_reference_mismatch", "table_content_preparation")
    elif processing.resolutions or processing.prepared_tables:
        raise ArtifactError("artifact_role_mismatch")

    workspace = workspace_root.resolve()
    namespace = (workspace / identity.namespace_path).resolve()
    if not namespace.is_relative_to(workspace):
        raise ArtifactError("artifact_identity_mismatch")
    staging = (namespace / "staging" / transaction_id / processing.source.document_id).resolve()
    if not staging.is_relative_to(namespace) or staging.exists():
        raise ArtifactError("artifact_identity_mismatch")
    staging.mkdir(parents=True)

    bound = TableExtractionSnapshot(tuple(
        BoundExtractionReport(item.binding_id, item.plugin_id, item.order, report)
        for item, report in zip(bindings, processing.extraction_reports)
    ))
    selection = (TableSelectionSnapshot(processing.grouping_report, processing.scoring_report,
                                        processing.resolutions) if branch else None)
    preparation = TableContentPreparationSnapshot(processing.prepared_tables) if branch else None
    payloads = {
        "main_parser_native": processing.native_parser_evidence,
        "main_parser": processing.primary_document,
        "table_extractor": bound,
        "table_selector": selection,
        "table_content_preparation": preparation,
        "parsed_document": processing.parsed_document,
        "chunks": batch,
    }
    parser_id = parser_bindings[0].plugin_id
    reviews = {
        "table_extractor_review": lambda: render_extractor_review(processing, parser_id),
        "table_selector_review": lambda: render_selector_review(processing, parser_id),
        "table_content_preparation_review": lambda: render_preparation_review(processing, parser_id),
        "chunk_review": lambda: _chunk_review(processing, batch, parser_id),
    }
    refs = []
    for role, filename in ROLES:
        if not branch and role.startswith("table_"):
            continue
        try:
            if role in SCHEMAS:
                envelope = ArtifactEnvelope(SCHEMAS[role], processing.source.document_id,
                                            processing.build_id, processing.source.file_hash,
                                            identity.build_fingerprint, payloads[role])
                content = _json_bytes(envelope)
            else:
                content = reviews[role]().encode("utf-8")
        except ArtifactError:
            raise
        except Exception as exc:
            raise ArtifactError("review_generation_failed" if role in reviews
                                else "json_generation_failed", role) from exc
        refs.append(_write_and_readback(staging / filename, content, role))
    attached = tuple(sorted(CONDITIONAL_SLOTS)) if branch else ()
    unattached = () if branch else tuple(sorted(CONDITIONAL_SLOTS))
    manifest = SnapshotManifest("snapshot_manifest_v3", processing.source.document_id,
                                processing.build_id, processing.source.file_hash,
                                identity.build_fingerprint, parser_id,
                                attached, unattached, tuple(refs))
    manifest_content = _json_bytes(manifest)
    _write_and_readback(staging / "snapshot-manifest.json", manifest_content,
                        "snapshot_manifest")
    staged = StagedArtifacts(staging, processing.source.document_id,
                             processing.build_id, processing.source.file_hash,
                             identity.build_fingerprint,
                             hashlib.sha256(manifest_content).hexdigest(), tuple(refs))
    verify_staged_artifacts(staged, branch=branch)
    return staged


def verify_staged_artifacts(staged: StagedArtifacts, *, branch: bool) -> None:
    expected_roles = tuple(role for role, _ in ROLES if branch or not role.startswith("table_"))
    if tuple(item.role for item in staged.files) != expected_roles:
        raise ArtifactError("artifact_role_mismatch")
    expected_paths = {ref.relative_path for ref in staged.files} | {"snapshot-manifest.json"}
    if {path.name for path in staged.staging_path.iterdir()} != expected_paths:
        raise ArtifactError("artifact_role_mismatch", "unexpected or missing artifact file")
    envelopes = {}
    for ref in staged.files:
        if FILENAMES[ref.role] != ref.relative_path:
            raise ArtifactError("artifact_role_mismatch", ref.role)
        path = staged.staging_path / ref.relative_path
        if not path.resolve().is_relative_to(staged.staging_path.resolve()):
            raise ArtifactError("artifact_role_mismatch", ref.role)
        content = path.read_bytes()
        if len(content) != ref.size_bytes or hashlib.sha256(content).hexdigest() != ref.sha256:
            raise ArtifactError("artifact_staging_readback", ref.role)
        if ref.relative_path.endswith(".json"):
            parsed = json.loads(content)
            if (parsed.get("document_id") != staged.document_id
                    or parsed.get("build_id") != staged.build_id
                    or parsed.get("file_hash") != staged.file_hash
                    or parsed.get("build_fingerprint") != staged.build_fingerprint):
                raise ArtifactError("artifact_identity_mismatch", ref.role)
            envelopes[ref.role] = parsed
    manifest_bytes = (staged.staging_path / "snapshot-manifest.json").read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != staged.snapshot_manifest_sha256:
        raise ArtifactError("artifact_staging_readback", "snapshot_manifest")
    manifest = json.loads(manifest_bytes)
    if (manifest.get("schema_version") != "snapshot_manifest_v3"
            or manifest.get("files") != [asdict(ref) for ref in staged.files]
            or (manifest.get("document_id"), manifest.get("build_id"),
                manifest.get("file_hash"), manifest.get("build_fingerprint")) !=
            (staged.document_id, staged.build_id, staged.file_hash, staged.build_fingerprint)
            or bool(manifest.get("attached_slots")) != branch):
        raise ArtifactError("artifact_role_mismatch", "snapshot_manifest")
    try:
        parsed = envelopes["parsed_document"]["payload"]
        chunks = envelopes["chunks"]["payload"]
        if (parsed["document_id"] != staged.document_id
                or chunks["document_id"] != staged.document_id
                or parsed["file_hash"] != staged.file_hash
                or chunks["file_hash"] != staged.file_hash):
            raise ValueError("parsed or chunk identity differs")
        node_ids = {node["node_id"] for node in parsed["nodes"]}
        node_ids.update(item["item_id"] for node in parsed["nodes"]
                        for item in node.get("items", ()))
        seen_chunk_ids = set()
        for position, chunk in enumerate(chunks["chunks"]):
            if (chunk["document_id"] != staged.document_id
                    or chunk["chunk_index"] != position
                    or not chunk["text"].strip()
                    or chunk["chunk_id"] in seen_chunk_ids
                    or any(source["node_id"] not in node_ids
                           for source in chunk["sources"])):
                raise ValueError("chunk reference or order differs")
            seen_chunk_ids.add(chunk["chunk_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ArtifactError("artifact_reference_mismatch", "chunks") from exc


class SnapshotArtifactBuilder:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def stage(self, processing: ProcessingResult, batch: ChunkBatch,
              projection: BuildProjection, transaction_id: str) -> StagedArtifacts:
        return stage_artifacts(processing, batch, projection,
                               self.workspace_root, transaction_id)
