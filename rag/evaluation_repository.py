"""Strict readers and integrity checks for retrieval-evaluation datasets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from rag.document_registry import hash_file
from rag.errors import EvaluationDataError
from rag.evaluation_models import (
    EvidenceExcerpt,
    EvidenceGroup,
    EvidenceGroupMapping,
    GroundTruthDataset,
    GroundTruthDocument,
    GroundTruthFileEntry,
    GroundTruthManifest,
    GroundTruthCase,
    TestCaseMapping,
    TestSetDataset,
    TestSetDocument,
    TestSetFileEntry,
    TestSetManifest,
    fingerprint,
)
from rag.pipeline_manifest import deserialize_build_config
from rag.pipeline_registry import build_config_fingerprint


T = TypeVar("T")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationDataError(f"{label} 必须是 JSON object。")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvaluationDataError(f"{label} 必须是 JSON array。")
    return value


def _exact_fields(value: dict[str, Any], model: type[Any], label: str) -> None:
    expected = {item.name for item in fields(model)}
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise EvaluationDataError(f"{label} 字段不匹配；missing={missing}, extra={extra}。")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise EvaluationDataError(f"{label} 必须是字符串。")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationDataError(f"{label} 必须是整数。")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _relative_project_path(value: Any, label: str) -> str:
    path = _string(value, label)
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or "\\" in path:
        raise EvaluationDataError(f"{label} 必须是项目内 POSIX 相对路径。")
    return path


def _decode_excerpt(value: Any, label: str) -> EvidenceExcerpt:
    obj = _object(value, label)
    _exact_fields(obj, EvidenceExcerpt, label)
    pages = tuple(_integer(item, f"{label}.page_numbers") for item in _array(obj["page_numbers"], f"{label}.page_numbers"))
    return EvidenceExcerpt(
        excerpt_id=_string(obj["excerpt_id"], f"{label}.excerpt_id"),
        document_id=_string(obj["document_id"], f"{label}.document_id"),
        document_name=_string(obj["document_name"], f"{label}.document_name"),
        relative_path=_relative_project_path(obj["relative_path"], f"{label}.relative_path"),
        page_numbers=pages,
        text=_string(obj["text"], f"{label}.text"),
    )


def _decode_group(value: Any, label: str) -> EvidenceGroup:
    obj = _object(value, label)
    _exact_fields(obj, EvidenceGroup, label)
    excerpts = tuple(
        _decode_excerpt(item, f"{label}.excerpts[{index}]")
        for index, item in enumerate(_array(obj["excerpts"], f"{label}.excerpts"))
    )
    return EvidenceGroup(
        evidence_group_id=_string(obj["evidence_group_id"], f"{label}.evidence_group_id"),
        excerpts=excerpts,
    )


def _decode_case(value: Any, label: str) -> GroundTruthCase:
    obj = _object(value, label)
    _exact_fields(obj, GroundTruthCase, label)
    if not isinstance(obj["answerable"], bool):
        raise EvaluationDataError(f"{label}.answerable 必须是 boolean。")
    groups = tuple(
        _decode_group(item, f"{label}.evidence_groups[{index}]")
        for index, item in enumerate(_array(obj["evidence_groups"], f"{label}.evidence_groups"))
    )
    return GroundTruthCase(
        case_id=_string(obj["case_id"], f"{label}.case_id"),
        question=_string(obj["question"], f"{label}.question"),
        answerable=obj["answerable"],
        reference_answer=_string(obj["reference_answer"], f"{label}.reference_answer"),
        evidence_groups=groups,
    )


def decode_ground_truth_document(value: Any, label: str) -> GroundTruthDocument:
    obj = _object(value, label)
    _exact_fields(obj, GroundTruthDocument, label)
    cases = tuple(
        _decode_case(item, f"{label}.cases[{index}]")
        for index, item in enumerate(_array(obj["cases"], f"{label}.cases"))
    )
    return GroundTruthDocument(
        schema_version=_string(obj["schema_version"], f"{label}.schema_version"),  # type: ignore[arg-type]
        document_key=_string(obj["document_key"], f"{label}.document_key"),
        document_id=_string(obj["document_id"], f"{label}.document_id"),
        document_name=_string(obj["document_name"], f"{label}.document_name"),
        relative_path=_relative_project_path(obj["relative_path"], f"{label}.relative_path"),
        file_hash=_string(obj["file_hash"], f"{label}.file_hash"),
        cases=cases,
    )


def _decode_file_entry(value: Any, label: str) -> GroundTruthFileEntry:
    obj = _object(value, label)
    _exact_fields(obj, GroundTruthFileEntry, label)
    return GroundTruthFileEntry(
        document_key=_string(obj["document_key"], f"{label}.document_key"),
        json_path=_relative_project_path(obj["json_path"], f"{label}.json_path"),
        content_sha256=_string(obj["content_sha256"], f"{label}.content_sha256"),
        case_count=_integer(obj["case_count"], f"{label}.case_count"),
    )


def _decode_test_set_file_entry(value: Any, label: str) -> TestSetFileEntry:
    obj = _object(value, label)
    _exact_fields(obj, TestSetFileEntry, label)
    return TestSetFileEntry(
        document_key=_string(obj["document_key"], f"{label}.document_key"),
        json_path=_relative_project_path(obj["json_path"], f"{label}.json_path"),
        content_sha256=_string(obj["content_sha256"], f"{label}.content_sha256"),
        case_count=_integer(obj["case_count"], f"{label}.case_count"),
    )


def _decode_evidence_mapping(value: Any, label: str) -> EvidenceGroupMapping:
    obj = _object(value, label)
    _exact_fields(obj, EvidenceGroupMapping, label)
    chunk_sets = tuple(
        tuple(_string(chunk_id, f"{label}.acceptable_chunk_sets[{set_index}]") for chunk_id in _array(chunk_set, f"{label}.acceptable_chunk_sets[{set_index}]"))
        for set_index, chunk_set in enumerate(_array(obj["acceptable_chunk_sets"], f"{label}.acceptable_chunk_sets"))
    )
    return EvidenceGroupMapping(
        evidence_group_id=_string(obj["evidence_group_id"], f"{label}.evidence_group_id"),
        status=_string(obj["status"], f"{label}.status"),  # type: ignore[arg-type]
        acceptable_chunk_sets=chunk_sets,
    )


def decode_test_set_document(value: Any, label: str) -> TestSetDocument:
    obj = _object(value, label)
    _exact_fields(obj, TestSetDocument, label)
    cases = []
    for case_index, case_value in enumerate(_array(obj["cases"], f"{label}.cases")):
        case_label = f"{label}.cases[{case_index}]"
        case_obj = _object(case_value, case_label)
        _exact_fields(case_obj, TestCaseMapping, case_label)
        mappings = tuple(
            _decode_evidence_mapping(item, f"{case_label}.evidence_groups[{group_index}]")
            for group_index, item in enumerate(_array(case_obj["evidence_groups"], f"{case_label}.evidence_groups"))
        )
        cases.append(TestCaseMapping(_string(case_obj["case_id"], f"{case_label}.case_id"), mappings))
    return TestSetDocument(
        schema_version=_string(obj["schema_version"], f"{label}.schema_version"),  # type: ignore[arg-type]
        document_key=_string(obj["document_key"], f"{label}.document_key"),
        document_id=_string(obj["document_id"], f"{label}.document_id"),
        document_name=_string(obj["document_name"], f"{label}.document_name"),
        relative_path=_relative_project_path(obj["relative_path"], f"{label}.relative_path"),
        file_hash=_string(obj["file_hash"], f"{label}.file_hash"),
        cases=tuple(cases),
    )


def decode_test_set_manifest(value: Any, label: str = "manifest") -> TestSetManifest:
    obj = _object(value, label)
    _exact_fields(obj, TestSetManifest, label)
    try:
        build_config = deserialize_build_config(_object(obj["build_config"], f"{label}.build_config"))
    except Exception as error:
        raise EvaluationDataError(f"{label}.build_config 无效。") from error
    return TestSetManifest(
        schema_version=_string(obj["schema_version"], f"{label}.schema_version"),  # type: ignore[arg-type]
        test_set_version=_string(obj["test_set_version"], f"{label}.test_set_version"),
        test_set_fingerprint=_string(obj["test_set_fingerprint"], f"{label}.test_set_fingerprint"),
        review_status=_string(obj["review_status"], f"{label}.review_status"),  # type: ignore[arg-type]
        system_version=_string(obj["system_version"], f"{label}.system_version"),
        pipeline_id=_string(obj["pipeline_id"], f"{label}.pipeline_id"),  # type: ignore[arg-type]
        collection_name=_string(obj["collection_name"], f"{label}.collection_name"),
        ground_truth_version=_string(obj["ground_truth_version"], f"{label}.ground_truth_version"),
        ground_truth_fingerprint=_string(obj["ground_truth_fingerprint"], f"{label}.ground_truth_fingerprint"),
        annotation_rule_version=_string(obj["annotation_rule_version"], f"{label}.annotation_rule_version"),
        build_config=build_config,
        build_config_fingerprint=_string(obj["build_config_fingerprint"], f"{label}.build_config_fingerprint"),
        files=tuple(
            _decode_test_set_file_entry(item, f"{label}.files[{index}]")
            for index, item in enumerate(_array(obj["files"], f"{label}.files"))
        ),
        created_at=_string(obj["created_at"], f"{label}.created_at"),
        reviewed_at=_optional_string(obj["reviewed_at"], f"{label}.reviewed_at"),
        superseded_by=_optional_string(obj["superseded_by"], f"{label}.superseded_by"),
    )


def decode_ground_truth_manifest(value: Any, label: str = "manifest") -> GroundTruthManifest:
    obj = _object(value, label)
    _exact_fields(obj, GroundTruthManifest, label)
    files_value = _array(obj["files"], f"{label}.files")
    return GroundTruthManifest(
        schema_version=_string(obj["schema_version"], f"{label}.schema_version"),  # type: ignore[arg-type]
        dataset_version=_string(obj["dataset_version"], f"{label}.dataset_version"),
        ground_truth_fingerprint=_string(obj["ground_truth_fingerprint"], f"{label}.ground_truth_fingerprint"),
        review_status=_string(obj["review_status"], f"{label}.review_status"),  # type: ignore[arg-type]
        files=tuple(_decode_file_entry(item, f"{label}.files[{index}]") for index, item in enumerate(files_value)),
        created_at=_string(obj["created_at"], f"{label}.created_at"),
        reviewed_at=_optional_string(obj["reviewed_at"], f"{label}.reviewed_at"),
        superseded_by=_optional_string(obj["superseded_by"], f"{label}.superseded_by"),
    )


def _load_json(path: Path, label: str) -> tuple[Any, bytes]:
    try:
        payload = path.read_bytes()
        return json.loads(payload.decode("utf-8")), payload
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvaluationDataError(f"无法读取 {label}: {path}。") from error


def load_ground_truth_dataset(
    project_root: Path,
    *,
    required_status: str | None = None,
) -> GroundTruthDataset:
    root = project_root.resolve()
    base = (root / "eval" / "ground-truth").resolve()
    manifest_value, _ = _load_json(base / "manifest.json", "ground-truth manifest")
    manifest = decode_ground_truth_manifest(manifest_value)
    if required_status is not None and manifest.review_status != required_status:
        raise EvaluationDataError(
            f"ground truth 状态必须为 {required_status}，当前为 {manifest.review_status}。"
        )

    paths = [item.json_path for item in manifest.files]
    if paths != sorted(paths, key=lambda item: (item.casefold(), item)):
        raise EvaluationDataError("manifest.files 必须按 json_path 排序。")

    documents: list[GroundTruthDocument] = []
    case_ids: set[str] = set()
    identities: dict[str, tuple[str, str]] = {}
    for index, entry in enumerate(manifest.files):
        document_path = (root / Path(entry.json_path)).resolve()
        if document_path.parent != base:
            raise EvaluationDataError(f"manifest.files[{index}].json_path 不在 eval/ground-truth/。")
        value, payload = _load_json(document_path, entry.json_path)
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != entry.content_sha256:
            raise EvaluationDataError(f"{entry.json_path} content_sha256 不匹配。")
        document = decode_ground_truth_document(value, entry.json_path)
        if document.document_key != entry.document_key or len(document.cases) != entry.case_count:
            raise EvaluationDataError(f"{entry.json_path} 的 manifest 身份或 case_count 不匹配。")
        source_path = (root / "documents" / Path(document.relative_path)).resolve()
        if source_path.parent != (root / "documents").resolve() or not source_path.is_file():
            raise EvaluationDataError(f"{entry.json_path} 对应 PDF 不存在或越界。")
        if hash_file(source_path) != document.file_hash:
            raise EvaluationDataError(f"{entry.json_path} 的 PDF file_hash 不匹配。")
        identities[document.document_id] = (document.document_name, document.relative_path)
        for case in document.cases:
            if case.case_id in case_ids:
                raise EvaluationDataError(f"case_id 全局重复：{case.case_id}。")
            case_ids.add(case.case_id)
            expected_groups = tuple(f"e{number}" for number in range(1, len(case.evidence_groups) + 1))
            if tuple(group.evidence_group_id for group in case.evidence_groups) != expected_groups:
                raise EvaluationDataError(f"{case.case_id} 的 evidence group 必须从 e1 连续编号。")
            for group in case.evidence_groups:
                expected_excerpts = tuple(f"x{number}" for number in range(1, len(group.excerpts) + 1))
                if tuple(item.excerpt_id for item in group.excerpts) != expected_excerpts:
                    raise EvaluationDataError(f"{case.case_id}/{group.evidence_group_id} 的 excerpt 必须从 x1 连续编号。")
        documents.append(document)

    for document in documents:
        for case in document.cases:
            for group in case.evidence_groups:
                for item in group.excerpts:
                    identity = identities.get(item.document_id)
                    if identity != (item.document_name, item.relative_path):
                        raise EvaluationDataError(
                            f"{case.case_id}/{group.evidence_group_id}/{item.excerpt_id} 文档身份不一致。"
                        )

    actual_fingerprint = fingerprint(documents)
    if actual_fingerprint != manifest.ground_truth_fingerprint:
        raise EvaluationDataError("ground_truth_fingerprint 不匹配。")
    return GroundTruthDataset(manifest=manifest, documents=tuple(documents))


def load_test_set_dataset(
    project_root: Path,
    test_set_directory: Path,
    ground_truth: GroundTruthDataset,
    *,
    required_status: str | None = None,
) -> TestSetDataset:
    root = project_root.resolve()
    base = (test_set_directory if test_set_directory.is_absolute() else root / test_set_directory).resolve()
    expected_parent = (root / "eval" / "test-sets").resolve()
    if base.parent != expected_parent:
        raise EvaluationDataError("test set 目录必须是 eval/test-sets/ 的直接子目录。")
    manifest_value, _ = _load_json(base / "manifest.json", "test-set manifest")
    manifest = decode_test_set_manifest(manifest_value)
    if required_status is not None and manifest.review_status != required_status:
        raise EvaluationDataError(
            f"test set 状态必须为 {required_status}，当前为 {manifest.review_status}。"
        )
    if manifest.annotation_rule_version != "evidence_chunk_mapping_v2":
        raise EvaluationDataError("test set annotation_rule_version 不受支持。")
    if build_config_fingerprint(manifest.build_config) != manifest.build_config_fingerprint:
        raise EvaluationDataError("test set BuildConfig fingerprint 不匹配。")
    if manifest.build_config.pipeline_id != manifest.pipeline_id:
        raise EvaluationDataError("test set pipeline 与 BuildConfig 不一致。")
    if (
        manifest.ground_truth_version != ground_truth.manifest.dataset_version
        or manifest.ground_truth_fingerprint != ground_truth.manifest.ground_truth_fingerprint
    ):
        raise EvaluationDataError("test set 与 ground truth 身份不一致。")
    paths = [item.json_path for item in manifest.files]
    if paths != sorted(paths, key=lambda item: (item.casefold(), item)):
        raise EvaluationDataError("test-set manifest.files 必须按 json_path 排序。")

    gt_by_key = {document.document_key: document for document in ground_truth.documents}
    documents: list[TestSetDocument] = []
    raw_documents: list[dict[str, Any]] = []
    for index, entry in enumerate(manifest.files):
        path = (root / Path(entry.json_path)).resolve()
        if path.parent != base:
            raise EvaluationDataError(f"manifest.files[{index}].json_path 不在当前 test-set 目录。")
        raw, payload = _load_json(path, entry.json_path)
        if hashlib.sha256(payload).hexdigest() != entry.content_sha256:
            raise EvaluationDataError(f"{entry.json_path} content_sha256 不匹配。")
        document = decode_test_set_document(raw, entry.json_path)
        if document.document_key != entry.document_key or len(document.cases) != entry.case_count:
            raise EvaluationDataError(f"{entry.json_path} 的 manifest 身份或 case_count 不匹配。")
        gt = gt_by_key.get(document.document_key)
        if gt is None or (
            document.document_id, document.document_name, document.relative_path, document.file_hash
        ) != (gt.document_id, gt.document_name, gt.relative_path, gt.file_hash):
            raise EvaluationDataError(f"{entry.json_path} 的文档身份与 ground truth 不一致。")
        if tuple(case.case_id for case in document.cases) != tuple(case.case_id for case in gt.cases):
            raise EvaluationDataError(f"{entry.json_path} 的 case 集合或顺序不一致。")
        for mapping_case, gt_case in zip(document.cases, gt.cases, strict=True):
            if tuple(item.evidence_group_id for item in mapping_case.evidence_groups) != tuple(
                item.evidence_group_id for item in gt_case.evidence_groups
            ):
                raise EvaluationDataError(f"{mapping_case.case_id} 的 evidence group 集合或顺序不一致。")
        documents.append(document)
        raw_documents.append(_object(raw, entry.json_path))
    if set(gt_by_key) != {item.document_key for item in documents}:
        raise EvaluationDataError("test set 与 ground truth 的文档集合不一致。")

    actual_fingerprint = fingerprint({
        "ground_truth_fingerprint": manifest.ground_truth_fingerprint,
        "annotation_rule_version": manifest.annotation_rule_version,
        "build_config_fingerprint": manifest.build_config_fingerprint,
        "documents": raw_documents,
    })
    if actual_fingerprint != manifest.test_set_fingerprint:
        raise EvaluationDataError("test_set_fingerprint 不匹配。")
    return TestSetDataset(manifest, tuple(documents))


class FileEvaluationRepository:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def load_approved_ground_truth(self) -> GroundTruthDataset:
        return load_ground_truth_dataset(self.project_root, required_status="approved")
