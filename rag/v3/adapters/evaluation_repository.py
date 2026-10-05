"""Read reviewed PDF evidence without modifying historical ground truth files."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from rag.v3.application.assembly import canonical_json_bytes
from rag.v3.contracts.assembly import BuildProjection, BuildProjectionBinding
from rag.v3.contracts.evaluation import (
    EvidenceExcerpt, EvidenceGroup, GroundTruthCase, GroundTruthDocument,
    GroundTruthFileEntry, GroundTruthIdentity, GroundTruthManifest,
    EvidenceGroupMapping, ExcerptChunkMapping, LoadedEvaluationData, TestCaseMapping, TestSetDocument,
    TestSetFileEntry, TestSetIdentity, TestSetManifest,
)
from rag.v3.contracts.storage import IndexIdentity


class EvaluationRepositoryError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise EvaluationRepositoryError("ground_truth_incomplete", "duplicate JSON field")
        result[key] = value
    return result


def _shape(value: object, keys: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EvaluationRepositoryError("ground_truth_incomplete", f"{label} fields differ")
    return value


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class LocalReviewedEvidenceRepository:
    def __init__(self, workspace_root: Path):
        self.root = workspace_root.resolve()

    def _read(self, relative: str) -> bytes:
        if not relative.startswith("eval/ground-truth/") or "\\" in relative:
            raise EvaluationRepositoryError("ground_truth_incomplete", "ground truth path is outside root")
        root = (self.root / "eval" / "ground-truth").resolve()
        path = (self.root / relative).resolve()
        if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
            raise EvaluationRepositoryError("ground_truth_incomplete", "ground truth file absent or unsafe")
        return path.read_bytes()

    @staticmethod
    def _document(raw: bytes) -> GroundTruthDocument:
        value = _shape(json.loads(raw, object_pairs_hook=_pairs), {
            "schema_version", "document_key", "document_id", "document_name",
            "relative_path", "file_hash", "cases"}, "ground truth document")
        if value["schema_version"] != "ground_truth_document_v1":
            raise EvaluationRepositoryError("ground_truth_incomplete", "document schema differs")
        cases = []
        for item in value["cases"]:
            item = _shape(item, {"case_id", "question", "answerable", "reference_answer",
                                 "evidence_groups"}, "ground truth case")
            groups = []
            for group in item["evidence_groups"]:
                group = _shape(group, {"evidence_group_id", "excerpts"}, "evidence group")
                excerpts = []
                for excerpt in group["excerpts"]:
                    excerpt = _shape(excerpt, {"excerpt_id", "document_id", "document_name",
                                               "relative_path", "page_numbers", "text"},
                                     "evidence excerpt")
                    excerpts.append(EvidenceExcerpt(
                        excerpt["excerpt_id"], excerpt["document_id"],
                        excerpt["document_name"], excerpt["relative_path"],
                        tuple(excerpt["page_numbers"]), excerpt["text"]))
                groups.append(EvidenceGroup(group["evidence_group_id"], tuple(excerpts)))
            cases.append(GroundTruthCase(item["case_id"], item["question"],
                                         item["reference_answer"], item["answerable"],
                                         tuple(groups)))
        return GroundTruthDocument("ground_truth_document_v1", value["document_key"],
                                   value["document_id"], value["document_name"],
                                   value["relative_path"], value["file_hash"], tuple(cases))

    def load_ground_truth(self, identity: GroundTruthIdentity | None = None
                          ) -> tuple[GroundTruthManifest, tuple[GroundTruthDocument, ...]]:
        try:
            raw = self._read("eval/ground-truth/manifest.json")
            manifest_data = _shape(json.loads(raw, object_pairs_hook=_pairs), {
                "schema_version", "dataset_version", "ground_truth_fingerprint",
                "review_status", "files", "created_at", "reviewed_at", "superseded_by"},
                "ground truth manifest")
            if manifest_data["schema_version"] != "ground_truth_manifest_v1":
                raise EvaluationRepositoryError("ground_truth_incomplete", "manifest schema differs")
            documents = []
            entries = []
            for item in manifest_data["files"]:
                if not isinstance(item, dict):
                    raise EvaluationRepositoryError("ground_truth_incomplete", "manifest entry differs")
                legacy = {"document_key", "json_path", "content_sha256", "case_count"}
                current = {"document_key", "json_path", "json_sha256", "case_count",
                           "document_id", "file_hash"}
                if set(item) not in (legacy, current):
                    raise EvaluationRepositoryError("ground_truth_incomplete", "manifest entry fields differ")
                path = item["json_path"]
                if path != f'eval/ground-truth/{item["document_key"]}.json':
                    raise EvaluationRepositoryError("ground_truth_incomplete", "document path differs")
                content = self._read(path)
                digest = item.get("content_sha256", item.get("json_sha256"))
                if _sha(content) != digest:
                    raise EvaluationRepositoryError("ground_truth_incomplete", "document hash differs")
                document = self._document(content)
                if (document.document_key != item["document_key"]
                        or len(document.cases) != item["case_count"]
                        or (set(item) == current and
                            (document.document_id != item["document_id"]
                             or document.file_hash != item["file_hash"]))):
                    raise EvaluationRepositoryError("ground_truth_incomplete", "document identity differs")
                documents.append(document)
                entries.append(GroundTruthFileEntry(document.document_key, path, digest,
                                                     len(document.cases), document.document_id,
                                                     document.file_hash))
            fingerprint = _sha(canonical_json_bytes([asdict(item) for item in documents]))
            if (fingerprint != manifest_data["ground_truth_fingerprint"]
                    or len({item.document_key for item in entries}) != len(entries)
                    or len({case.case_id for document in documents for case in document.cases})
                       != sum(len(document.cases) for document in documents)):
                raise EvaluationRepositoryError("ground_truth_incomplete", "ground truth fingerprint or IDs differ")
            manifest = GroundTruthManifest("ground_truth_manifest_v1",
                manifest_data["dataset_version"], fingerprint, manifest_data["review_status"],
                tuple(entries), manifest_data["created_at"], manifest_data["reviewed_at"],
                manifest_data["superseded_by"])
            if identity is not None and (manifest.dataset_version != identity.dataset_version
                                          or manifest.ground_truth_fingerprint
                                          != identity.ground_truth_fingerprint):
                raise EvaluationRepositoryError("dataset_identity_mismatch", "ground truth identity differs")
            return manifest, tuple(documents)
        except EvaluationRepositoryError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise EvaluationRepositoryError("ground_truth_incomplete", "ground truth readback failed") from exc

    def _read_test_file(self, directory: Path, name: str) -> bytes:
        root = (self.root / "eval" / "test-sets").resolve()
        path = (directory / name).resolve()
        if (not directory.is_relative_to(root) or path.parent != directory
                or path.is_symlink() or not path.is_file()):
            raise EvaluationRepositoryError("test_set_incomplete", "test set path is unsafe or absent")
        return path.read_bytes()

    @staticmethod
    def _projection(value: object) -> BuildProjection:
        keys = set(BuildProjection.__dataclass_fields__)
        raw = _shape(value, keys, "build projection")
        bindings = tuple(BuildProjectionBinding(**_shape(item,
            set(BuildProjectionBinding.__dataclass_fields__), "projection binding"))
                         for item in raw["bindings"])
        return BuildProjection(**{key: item for key, item in raw.items() if key != "bindings"},
                               bindings=bindings)

    @staticmethod
    def _test_document(raw: bytes) -> TestSetDocument:
        data = _shape(json.loads(raw, object_pairs_hook=_pairs), {
            "schema_version", "document_key", "document_id", "document_name",
            "relative_path", "file_hash", "cases"}, "test set document")
        if data["schema_version"] != "test_set_document_v3":
            raise EvaluationRepositoryError("test_set_incomplete", "test set document schema differs")
        cases = []
        for value in data["cases"]:
            value = _shape(value, {"case_id", "groups"}, "test case mapping")
            groups = []
            for group in value["groups"]:
                group = _shape(group, {"evidence_group_id", "status",
                    "acceptable_chunk_sets", "excerpt_mappings"}, "group mapping")
                excerpt_mappings = tuple(ExcerptChunkMapping(
                    excerpt["excerpt_id"], excerpt["status"],
                    tuple(tuple(item) for item in excerpt["chunk_sets"]))
                    for excerpt in (_shape(item, {"excerpt_id", "status", "chunk_sets"},
                                                "excerpt mapping")
                                    for item in group["excerpt_mappings"]))
                groups.append(EvidenceGroupMapping(group["evidence_group_id"], group["status"],
                    tuple(tuple(item) for item in group["acceptable_chunk_sets"]),
                    excerpt_mappings))
            cases.append(TestCaseMapping(value["case_id"], tuple(groups)))
        return TestSetDocument("test_set_document_v3", data["document_key"],
                               data["document_id"], data["document_name"],
                               data["relative_path"], data["file_hash"], tuple(cases))

    def load(self, ground_truth: GroundTruthIdentity,
             test_set: TestSetIdentity) -> LoadedEvaluationData:
        gt_manifest, gt_documents = self.load_ground_truth(ground_truth)
        root = (self.root / "eval" / "test-sets").resolve()
        try:
            directories = tuple(sorted(item for item in root.iterdir() if item.is_dir()
                                       and item.name.startswith("system-v3.0__assembly-")))
            matches = []
            for directory in directories:
                if not (directory / "manifest.json").is_file():
                    continue
                raw = self._read_test_file(directory, "manifest.json")
                data = json.loads(raw, object_pairs_hook=_pairs)
                if not isinstance(data, dict):
                    raise EvaluationRepositoryError("test_set_incomplete", "test set manifest is not an object")
                if (data.get("test_set_version"), data.get("test_set_fingerprint"),
                        data.get("annotation_rule_version")) == (
                        test_set.test_set_version, test_set.test_set_fingerprint,
                        test_set.annotation_rule_version):
                    matches.append((directory, data))
            if len(matches) != 1:
                raise EvaluationRepositoryError("test_set_incomplete", "matching V3 test set is not unique")
            directory, raw = matches[0]
            raw = _shape(raw, set(TestSetManifest.__dataclass_fields__), "test set manifest")
            index = IndexIdentity(**_shape(raw["index_identity"],
                set(IndexIdentity.__dataclass_fields__), "test set index"))
            projection = self._projection(raw["build_config"])
            if (_sha(canonical_json_bytes(asdict(projection))) != index.build_fingerprint
                    or raw["build_config_fingerprint"] != index.build_fingerprint
                    or raw["system_version"] != "3.0"):
                raise EvaluationRepositoryError("test_set_incomplete", "test set build identity differs")
            gt_by_key = {item.document_key: item for item in gt_documents}
            entries = []
            documents = []
            for value in raw["files"]:
                value = _shape(value, set(TestSetFileEntry.__dataclass_fields__),
                               "test set file entry")
                name = f'{value["document_key"]}.json'
                if value["json_path"] != f'eval/test-sets/{directory.name}/{name}':
                    raise EvaluationRepositoryError("test_set_incomplete", "test set file path differs")
                content = self._read_test_file(directory, name)
                if _sha(content) != value["json_sha256"]:
                    raise EvaluationRepositoryError("test_set_incomplete", "test set file hash differs")
                document = self._test_document(content)
                gt = gt_by_key.get(document.document_key)
                if (gt is None or
                        (document.document_id, document.document_name, document.relative_path,
                         document.file_hash) !=
                        (gt.document_id, gt.document_name, gt.relative_path, gt.file_hash) or
                        len(document.cases) != value["case_count"] or
                        document.document_id != value["document_id"] or
                        document.file_hash != value["file_hash"] or
                        tuple(item.case_id for item in document.cases)
                        != tuple(item.case_id for item in gt.cases)):
                    raise EvaluationRepositoryError("test_set_incomplete", "test set document differs")
                for mapping, case in zip(document.cases, gt.cases):
                    if {item.evidence_group_id for item in mapping.groups} != {
                            item.evidence_group_id for item in case.evidence_groups}:
                        raise EvaluationRepositoryError("mapping_invalid", "evidence group IDs differ")
                entries.append(TestSetFileEntry(**value))
                documents.append(document)
            if (set(gt_by_key) != {item.document_key for item in documents}
                    or len(entries) != len(documents)
                    or raw["ground_truth_version"] != gt_manifest.dataset_version
                    or raw["ground_truth_fingerprint"] != gt_manifest.ground_truth_fingerprint):
                raise EvaluationRepositoryError("dataset_identity_mismatch", "test set ground truth differs")
            expected = _sha(canonical_json_bytes({
                "ground_truth_fingerprint": raw["ground_truth_fingerprint"],
                "annotation_rule_version": raw["annotation_rule_version"],
                "build_config_fingerprint": raw["build_config_fingerprint"],
                "documents": [asdict(item) for item in documents]}))
            if expected != raw["test_set_fingerprint"]:
                raise EvaluationRepositoryError("test_set_incomplete", "test set fingerprint differs")
            manifest = TestSetManifest("test_set_manifest_v3", raw["test_set_version"],
                expected, raw["review_status"], "3.0", raw["configuration_name"],
                index, projection, raw["build_config_fingerprint"],
                raw["ground_truth_version"], raw["ground_truth_fingerprint"],
                raw["annotation_rule_version"], tuple(entries), raw["created_at"],
                raw["reviewed_at"], raw["superseded_by"])
            return LoadedEvaluationData(gt_manifest, gt_documents, manifest, tuple(documents))
        except EvaluationRepositoryError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise EvaluationRepositoryError("test_set_incomplete", "V3 test set readback failed") from exc
