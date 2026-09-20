from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rag.errors import EvaluationDataError
from rag.evaluation_models import fingerprint
from rag.evaluation_repository import load_ground_truth_dataset


def _write_dataset(root: Path, *, status: str = "draft") -> None:
    documents = root / "documents"
    ground_truth = root / "eval" / "ground-truth"
    documents.mkdir(parents=True)
    ground_truth.mkdir(parents=True)
    pdf = documents / "sample.pdf"
    pdf.write_bytes(b"pdf")
    document = {
        "schema_version": "ground_truth_document_v1",
        "document_key": "sample--doc1",
        "document_id": "doc1",
        "document_name": "sample.pdf",
        "relative_path": "sample.pdf",
        "file_hash": hashlib.sha256(b"pdf").hexdigest(),
        "cases": [{
            "case_id": "SAMPLE-Q001",
            "question": "Question?",
            "answerable": True,
            "reference_answer": "Answer.",
            "evidence_groups": [{
                "evidence_group_id": "e1",
                "excerpts": [{
                    "excerpt_id": "x1",
                    "document_id": "doc1",
                    "document_name": "sample.pdf",
                    "relative_path": "sample.pdf",
                    "page_numbers": [1],
                    "text": "Answer.",
                }],
            }],
        }],
    }
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
    (ground_truth / "sample.json").write_bytes(payload)
    manifest = {
        "schema_version": "ground_truth_manifest_v1",
        "dataset_version": "test",
        "ground_truth_fingerprint": fingerprint([document]),
        "review_status": status,
        "files": [{
            "document_key": "sample--doc1",
            "json_path": "eval/ground-truth/sample.json",
            "content_sha256": hashlib.sha256(payload).hexdigest(),
            "case_count": 1,
        }],
        "created_at": "2026-01-01T00:00:00Z",
        "reviewed_at": "2026-01-01T00:00:00Z" if status == "approved" else None,
        "superseded_by": None,
    }
    (ground_truth / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def test_load_ground_truth_dataset_validates_integrity(tmp_path: Path) -> None:
    _write_dataset(tmp_path)

    dataset = load_ground_truth_dataset(tmp_path)

    assert dataset.manifest.review_status == "draft"
    assert dataset.documents[0].cases[0].case_id == "SAMPLE-Q001"


def test_load_ground_truth_dataset_rejects_required_status(tmp_path: Path) -> None:
    _write_dataset(tmp_path)

    with pytest.raises(EvaluationDataError, match="approved"):
        load_ground_truth_dataset(tmp_path, required_status="approved")


def test_load_ground_truth_dataset_rejects_changed_document(tmp_path: Path) -> None:
    _write_dataset(tmp_path)
    path = tmp_path / "eval" / "ground-truth" / "sample.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(EvaluationDataError, match="content_sha256"):
        load_ground_truth_dataset(tmp_path)
