"""Formal evaluation admission against reviewed evidence and the active V3 index."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path

from rag.v3.application.assembly import canonical_json_bytes
from rag.v3.contracts.evaluation import EvaluationRunRequest, LoadedEvaluationData
from rag.v3.contracts.storage import Manifest, VectorRecord


class EvaluationPreflightError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


class ReviewedEvidencePreflight:
    def __init__(self, workspace_root: Path):
        self.root = workspace_root.resolve()

    def _source_hash(self, relative_path: str) -> str:
        if not relative_path or "\\" in relative_path:
            raise EvaluationPreflightError("source_mismatch", "source path is unsafe")
        source_root = (self.root / "documents").resolve()
        path = (source_root / relative_path).resolve()
        if not path.is_relative_to(source_root) or path.is_symlink() or not path.is_file():
            raise EvaluationPreflightError("source_mismatch", "source PDF is absent or unsafe")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def validate(self, request: EvaluationRunRequest, data: LoadedEvaluationData,
                 manifest: Manifest, records: tuple[VectorRecord, ...]) -> None:
        ground, test = data.ground_truth_manifest, data.test_set_manifest
        if ground.review_status != "approved" or test.review_status != "approved":
            raise EvaluationPreflightError("ground_truth_incomplete" if ground.review_status
                                           != "approved" else "test_set_incomplete",
                                           "formal evaluation requires approved datasets")
        if (request.ground_truth.dataset_version != ground.dataset_version
                or request.ground_truth.ground_truth_fingerprint != ground.ground_truth_fingerprint
                or request.test_set.test_set_version != test.test_set_version
                or request.test_set.test_set_fingerprint != test.test_set_fingerprint
                or request.test_set.annotation_rule_version != test.annotation_rule_version
                or test.ground_truth_version != ground.dataset_version
                or test.ground_truth_fingerprint != ground.ground_truth_fingerprint):
            raise EvaluationPreflightError("dataset_identity_mismatch", "dataset identity differs")
        if (request.query_config.top_k <= 0 or request.query_config.document_filter is not None
                or request.query_config.query_prefix != "query: "
                or not request.run_id or not request.evaluation_protocol_version):
            raise EvaluationPreflightError("mapping_invalid", "formal query configuration differs")
        request_projection = canonical_json_bytes(asdict(request.build_config))
        if (request.index_identity != test.index_identity
                or request.index_identity != manifest.index_identity
                or request.configuration_name != test.configuration_name
                or request.index_identity.build_fingerprint != test.build_config_fingerprint
                or request_projection != canonical_json_bytes(asdict(test.build_config))
                or request_projection != canonical_json_bytes(asdict(manifest.build_configuration))):
            raise EvaluationPreflightError("index_identity_mismatch", "test set and active build differ")
        if len(data.ground_truth_documents) != len(data.test_set_documents):
            raise EvaluationPreflightError("dataset_identity_mismatch", "document set differs")
        gt_by_id = {item.document_id: item for item in data.ground_truth_documents}
        test_by_id = {item.document_id: item for item in data.test_set_documents}
        if (len(gt_by_id) != len(data.ground_truth_documents)
                or len(test_by_id) != len(data.test_set_documents)
                or set(gt_by_id) != set(test_by_id)
                or set(gt_by_id) != set(manifest.documents)):
            raise EvaluationPreflightError("dataset_identity_mismatch", "PDF set differs")
        by_chunk = {item.chunk_id: item for item in records}
        if len(by_chunk) != len(records):
            raise EvaluationPreflightError("mapping_invalid", "duplicate indexed chunk ID")
        by_document: dict[str, list[VectorRecord]] = {}
        for item in records:
            by_document.setdefault(item.document_id, []).append(item)
        all_case_ids: set[str] = set()
        for document_id, gt in gt_by_id.items():
            mapping = test_by_id[document_id]
            active = manifest.documents[document_id]
            test_entry = next((item for item in test.files if item.document_id == document_id), None)
            if (test_entry is None or mapping.document_key != gt.document_key
                    or (mapping.document_name, mapping.relative_path, mapping.file_hash)
                       != (gt.document_name, gt.relative_path, gt.file_hash)
                    or active.file_hash != gt.file_hash
                    or self._source_hash(gt.relative_path) != gt.file_hash
                    or len(by_document.get(document_id, ())) != active.chunk_count
                    or test_entry.chunk_count != active.chunk_count):
                raise EvaluationPreflightError("source_mismatch", "PDF or chunk count differs")
            if len(gt.cases) != len(mapping.cases):
                raise EvaluationPreflightError("mapping_invalid", "case count differs")
            for case, test_case in zip(gt.cases, mapping.cases):
                if (case.case_id in all_case_ids or test_case.case_id != case.case_id
                        or {item.evidence_group_id for item in case.evidence_groups}
                           != {item.evidence_group_id for item in test_case.groups}):
                    raise EvaluationPreflightError("mapping_invalid", "case or group identity differs")
                all_case_ids.add(case.case_id)
                groups = {item.evidence_group_id: item for item in case.evidence_groups}
                for group_mapping in test_case.groups:
                    group = groups[group_mapping.evidence_group_id]
                    evidence_documents = {item.document_id for item in group.excerpts}
                    evidence_pages = {page for item in group.excerpts for page in item.page_numbers}
                    excerpts = {item.excerpt_id: item for item in group.excerpts}
                    if set(excerpts) != {item.excerpt_id for item in group_mapping.excerpt_mappings}:
                        raise EvaluationPreflightError("mapping_invalid", "excerpt mapping IDs differ")
                    for excerpt_mapping in group_mapping.excerpt_mappings:
                        excerpt = excerpts[excerpt_mapping.excerpt_id]
                        for chunk_set in excerpt_mapping.chunk_sets:
                            for chunk_id in chunk_set:
                                record = by_chunk.get(chunk_id)
                                if (record is None or not record.text.strip()
                                        or record.metadata.build_fingerprint
                                           != request.index_identity.build_fingerprint
                                        or record.document_id != excerpt.document_id
                                        or not set(record.metadata.page_numbers)
                                           & set(excerpt.page_numbers)):
                                    raise EvaluationPreflightError("mapping_invalid",
                                        "excerpt chunk source differs")
                    for acceptable in group_mapping.acceptable_chunk_sets:
                        for chunk_id in acceptable:
                            record = by_chunk.get(chunk_id)
                            if (record is None or not record.text.strip()
                                    or record.metadata.build_fingerprint
                                       != request.index_identity.build_fingerprint
                                    or record.document_id not in evidence_documents
                                    or not set(record.metadata.page_numbers) & evidence_pages):
                                raise EvaluationPreflightError("mapping_invalid", "acceptable chunk source differs")
