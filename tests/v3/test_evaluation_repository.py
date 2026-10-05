"""Reviewed V2-era evidence is imported as read-only V3 evaluation input."""

from pathlib import Path
from shutil import copytree

import pytest

from rag.v3.adapters.evaluation_repository import (
    EvaluationRepositoryError, LocalReviewedEvidenceRepository,
)
from rag.v3.contracts.evaluation import GroundTruthIdentity, TestSetIdentity as SetIdentity


def test_existing_reviewed_ground_truth_keeps_identity_and_all_cases():
    workspace = Path(__file__).resolve().parents[2]
    repository = LocalReviewedEvidenceRepository(workspace)
    manifest, documents = repository.load_ground_truth(GroundTruthIdentity(
        "2.0.0", "4612ed552d4a523a22af19989b8e2b40589d3dc174b891400b9133bb5f98ac23"))
    assert manifest.review_status == "approved"
    assert len(documents) == 5
    assert sum(len(item.cases) for item in documents) == 24
    assert all(entry.document_id and entry.file_hash for entry in manifest.files)


def test_ground_truth_modified_bytes_are_rejected(tmp_path):
    workspace = Path(__file__).resolve().parents[2]
    copytree(workspace / "eval" / "ground-truth", tmp_path / "eval" / "ground-truth")
    path = next(item for item in (tmp_path / "eval" / "ground-truth").glob("*.json")
                if item.name != "manifest.json")
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(EvaluationRepositoryError, match="hash differs"):
        LocalReviewedEvidenceRepository(tmp_path).load_ground_truth()


@pytest.mark.parametrize("name,fingerprint,expected_ids", [
    ("plain_text", "498ee87fb9b86b997f474a0b0337cc0d0fc5c867337296883a2b1e9632cfd52a",
     ("d359c37e0bc3c87a-p2-c13", "d359c37e0bc3c87a-p3-c02")),
    ("structured", "4f5c74e72fecd78855a7aadd13f49b7112ec198bd6812f33187dd20bc1020515",
     ("d359c37e0bc3c87a-v2-c000008", "d359c37e0bc3c87a-v2-c000010")),
])
def test_v3_approved_sets_map_every_excerpt_without_promoting_partial_evidence(
        name, fingerprint, expected_ids):
    workspace = Path(__file__).resolve().parents[2]
    repository = LocalReviewedEvidenceRepository(workspace)
    ground, _ = repository.load_ground_truth()
    data = repository.load(GroundTruthIdentity(ground.dataset_version,
        ground.ground_truth_fingerprint),
        SetIdentity("3.0.0", fingerprint, "evidence_chunk_mapping_v3"))
    assert data.test_set_manifest.configuration_name == name
    assert data.test_set_manifest.review_status == "approved"
    assert len(data.test_set_documents) == 5
    assert sum(len(item.cases) for item in data.test_set_documents) == 24
    assert sum(len(case.groups) for item in data.test_set_documents
               for case in item.cases) == 43
    assert sum(len(group.excerpt_mappings) for item in data.test_set_documents
               for case in item.cases for group in case.groups) == 49
    for source, mapped in zip(data.ground_truth_documents, data.test_set_documents):
        for case, mapping in zip(source.cases, mapped.cases):
            for group, group_mapping in zip(case.evidence_groups, mapping.groups):
                assert {item.excerpt_id for item in group.excerpts} == {
                    item.excerpt_id for item in group_mapping.excerpt_mappings}
    contract = next(item for item in data.test_set_documents
                    if item.document_key.startswith("Contrato"))
    q002 = next(item for item in contract.cases if item.case_id == "CONTRACT-105-Q002")
    group = q002.groups[0]
    assert len(group.excerpt_mappings) == 4
    assert expected_ids[0] in group.excerpt_mappings[0].chunk_sets[0]
    assert expected_ids[1] in group.excerpt_mappings[1].chunk_sets[0]
    assert all(expected_id not in chunk_set for expected_id in expected_ids
               for chunk_set in group.acceptable_chunk_sets)
