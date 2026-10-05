"""V3 evaluation publication with one current run per configuration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from rag.v3.adapters.evaluation_history import _metrics, list_v2_snapshots
from rag.v3.adapters.evaluation_repository import LocalReviewedEvidenceRepository
from rag.v3.application.assembly import canonical_json_bytes
from rag.v3.application.evaluation_reporter import run_directory_name
from rag.v3.contracts.evaluation import (
    BaselineSelection, BaselineSelectionEntry, EvaluationError,
    EvaluationPublicationJournal, EvaluationRun, EvaluationRunRequest,
    EvaluationVersionRestoreEntry, GroundTruthIdentity, RenderedEvaluation,
    RunDocumentEntry, TestSetIdentity, EvaluationQueryConfig,
    ComparisonDocumentIdentity, ComparisonQueryConfig, ComparisonRunSnapshot,
    AggregateScope,
)
from rag.v3.contracts.storage import IndexIdentity


class EvaluationRunStoreError(RuntimeError):
    pass


_VERSION_FILES = (("system_version", "summary.md"),
                  ("comparison_root", "comparison.json"),
                  ("comparison_root", "comparison.md"))


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        if path.read_bytes() != content:
            raise EvaluationRunStoreError("atomic file readback differs")
    finally:
        if temp.exists():
            temp.unlink()


class LocalImmutableEvaluationRunStore:
    def __init__(self, workspace_root: Path):
        self.root = workspace_root.resolve()
        self.base = self.root / "validation" / "retrieval"
        self.system = self.base / "system-v3.0"
        self.runs = self.system / "runs"
        self.journal = self.system / "publication-journal.json"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.system.mkdir(parents=True, exist_ok=True)
        path = self.system / "publication.lock"
        try:
            with path.open("xb") as stream:
                stream.write(b"\0")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
        with path.open("r+b") as stream:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.lockf(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB, 1)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.lockf(stream.fileno(), fcntl.LOCK_UN, 1)

    def _version_path(self, location: str, relative: str) -> Path:
        if (location, relative) not in _VERSION_FILES:
            raise EvaluationRunStoreError("unexpected version file")
        return (self.system if location == "system_version" else self.base) / relative

    def _run_path(self, request: EvaluationRunRequest) -> Path:
        return self.runs / run_directory_name(request)

    @staticmethod
    def _run_entry(rendered: RenderedEvaluation, key: str) -> RunDocumentEntry:
        files = {(item.location, item.relative_path): item for item in rendered.files}
        prefix = f"documents/{key}"
        js = files["run", prefix + ".json"]
        page = files["run", prefix + ".html"]
        return RunDocumentEntry(key, js.relative_path, js.sha256,
                                page.relative_path, page.sha256)

    @staticmethod
    def _completed(request: EvaluationRunRequest,
                   rendered: RenderedEvaluation) -> EvaluationRun:
        files = {(item.location, item.relative_path): item for item in rendered.files}
        return EvaluationRun(request.run_id, "completed", request.configuration_name,
            request.index_identity, request.build_config,
            request.index_identity.build_fingerprint, request.query_config,
            request.evaluation_protocol_version, request.ground_truth, request.test_set,
            request.started_at, datetime.now(timezone.utc).isoformat(),
            tuple(LocalImmutableEvaluationRunStore._run_entry(rendered, doc.document_key)
                  for doc in rendered.documents), "aggregate.json",
            files["run", "aggregate.json"].sha256, None, "evaluation_run_v3")

    def _validate(self, request: EvaluationRunRequest,
                  rendered: RenderedEvaluation) -> None:
        if (request.run_id != rendered.run_id
                or request.index_identity.build_fingerprint != _sha(
                    canonical_json_bytes(asdict(request.build_config)))
                or rendered.aggregate.run_id != request.run_id
                or any(doc.run_id != request.run_id for doc in rendered.documents)):
            raise EvaluationRunStoreError("rendered run identity differs")
        files = {(item.location, item.relative_path): item for item in rendered.files}
        if len(files) != len(rendered.files):
            raise EvaluationRunStoreError("duplicate rendered file")
        expected = {("run", "aggregate.json"), *_VERSION_FILES}
        for doc in rendered.documents:
            key = doc.document_key
            if not key or any(char in key for char in "/\\:") or key in {".", ".."}:
                raise EvaluationRunStoreError("unsafe document key")
            expected.add(("run", f"documents/{key}.json"))
            expected.add(("run", f"documents/{key}.html"))
        if set(files) != expected:
            raise EvaluationRunStoreError("rendered file set differs")
        for (location, path), item in files.items():
            if item.location != location or item.relative_path != path or _sha(item.content) != item.sha256:
                raise EvaluationRunStoreError("rendered file hash differs")
            if location == "run" and path.endswith(".json"):
                data = json.loads(item.content)
                if data.get("run_id") != request.run_id:
                    raise EvaluationRunStoreError("rendered JSON run ID differs")

    def _read_journal(self) -> EvaluationPublicationJournal | None:
        if not self.journal.exists():
            return None
        try:
            raw = json.loads(self.journal.read_bytes())
            if set(raw) != set(EvaluationPublicationJournal.__dataclass_fields__):
                raise ValueError("journal fields differ")
            index = IndexIdentity(**raw.pop("index_identity"))
            entries = tuple(EvaluationVersionRestoreEntry(**item)
                            for item in raw.pop("version_files"))
            error = EvaluationError(**raw["error"]) if raw["error"] is not None else None
            result = EvaluationPublicationJournal(index_identity=index,
                version_files=entries, error=error,
                **{key: value for key, value in raw.items() if key != "error"})
            if (result.schema_version != "evaluation_publication_journal_v3"
                    or {(e.location, e.relative_path) for e in entries}
                       != set(_VERSION_FILES)
                    or len(entries) != 3):
                raise ValueError("journal identity differs")
            for relative in (result.staging_path, result.final_run_path):
                path = (self.root / relative).resolve()
                if not path.is_relative_to(self.system.resolve()):
                    raise ValueError("journal path escaped evaluation root")
            return result
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise EvaluationRunStoreError("publication journal unreadable") from exc

    def _write_journal(self, journal: EvaluationPublicationJournal) -> None:
        _atomic(self.journal, canonical_json_bytes(asdict(journal)))

    def _cleanup_obsolete_runs(self, current: Path,
                               configuration_name: str) -> None:
        """Remove verified older V3 run directories while the publication lock is held."""
        root = self.runs.resolve()
        if not self.runs.is_dir() or not current.resolve().is_relative_to(root):
            raise EvaluationRunStoreError("unsafe V3 run cleanup root")
        obsolete = []
        for directory in self.runs.iterdir():
            if directory == current:
                continue
            if (directory.is_symlink() or not directory.is_dir()
                    or not directory.resolve().is_relative_to(root)):
                raise EvaluationRunStoreError("unsafe V3 run directory")
            marker = directory / "run.json"
            if not marker.is_file() or marker.is_symlink():
                raise EvaluationRunStoreError("V3 run marker missing or unsafe")
            raw = json.loads(marker.read_bytes())
            if raw.get("configuration_name") != configuration_name:
                continue
            if (raw.get("schema_version") != "evaluation_run_v3"
                    or raw.get("status") not in {"completed", "failed", "invalid"}
                    or directory.name !=
                    f"assembly-{configuration_name}__cfg-"
                    f"{raw['index_identity']['build_fingerprint'][:12]}"
                    f"__k-{raw['query_config']['top_k']}__{raw['run_id']}"):
                raise EvaluationRunStoreError("obsolete V3 run identity invalid")
            self._read_completed(directory)
            for entry in directory.rglob("*"):
                if entry.is_symlink() or not entry.resolve().is_relative_to(directory.resolve()):
                    raise EvaluationRunStoreError("obsolete V3 run contains unsafe path")
            obsolete.append(directory)
        for directory in obsolete:
            if not directory.resolve().is_relative_to(root):
                raise EvaluationRunStoreError("obsolete V3 run escaped cleanup root")
            shutil.rmtree(directory)

    def _recover_locked(self) -> None:
        journal = self._read_journal()
        if journal is None:
            return
        staging = self.root / journal.staging_path
        final = self.root / journal.final_run_path
        try:
            marker = final / "run.json"
            committed = marker.is_file()
            if committed:
                raw = json.loads(marker.read_bytes())
                committed = (raw.get("status") == "completed"
                             and raw.get("run_id") == journal.run_id)
            committed = committed and all(
                self._version_path(e.location, e.relative_path).is_file()
                and _sha(self._version_path(e.location, e.relative_path).read_bytes())
                    == e.expected_new_sha256 for e in journal.version_files)
            if committed:
                try:
                    completed_run = self._read_completed(final)
                    committed = completed_run is not None
                except EvaluationRunStoreError:
                    committed = False
            if not committed:
                self._write_journal(replace(journal, state="restoring", current_step="restore"))
                for entry in journal.version_files:
                    target = self._version_path(entry.location, entry.relative_path)
                    if target.exists():
                        actual = _sha(target.read_bytes())
                        if actual not in {entry.old_sha256, entry.expected_new_sha256}:
                            raise EvaluationRunStoreError("version file changed outside transaction")
                    if entry.old_existed:
                        if not entry.old_snapshot_path or not entry.old_sha256:
                            raise EvaluationRunStoreError("old version snapshot missing")
                        snapshot = self.root / entry.old_snapshot_path
                        if not snapshot.is_file() and not staging.exists() and final.exists():
                            snapshot = final / snapshot.relative_to(staging)
                        if (not snapshot.resolve().is_relative_to(self.system.resolve())
                                or not snapshot.is_file()):
                            raise EvaluationRunStoreError("old version snapshot unsafe")
                        content = snapshot.read_bytes()
                        if _sha(content) != entry.old_sha256:
                            raise EvaluationRunStoreError("old version snapshot changed")
                        _atomic(target, content)
                    elif target.is_file() and _sha(target.read_bytes()) == entry.expected_new_sha256:
                        target.unlink()
                if staging.exists() and not final.exists():
                    final.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(staging, final)
                if final.exists():
                    failure = EvaluationError("publication", "publication_failed", "run",
                                              None, None, "publication interrupted")
                    old = json.loads(marker.read_bytes()) if marker.is_file() else None
                    if old is not None and old.get("status") == "completed":
                        old["status"] = "failed"
                        old["error"] = asdict(failure)
                        old["completed_at"] = None
                        _atomic(marker, canonical_json_bytes(old))
                    elif not marker.exists():
                        request_path = final / "request.json"
                        if not request_path.is_file():
                            raise EvaluationRunStoreError("staged request missing")
                        source = json.loads(request_path.read_bytes())
                        source.update({"schema_version": "evaluation_run_v3",
                                       "status": "failed", "build_config_fingerprint":
                                       journal.index_identity.build_fingerprint,
                                       "completed_at": None, "documents": [],
                                       "aggregate_path": None, "aggregate_sha256": None,
                                       "error": asdict(failure)})
                        _atomic(marker, canonical_json_bytes(source))
            if committed:
                self._cleanup_obsolete_runs(final, completed_run.configuration_name)
            if staging.exists():
                # Staging contains only this transaction's private files.
                import shutil
                shutil.rmtree(staging)
            if final.exists() and (final / "run.json").is_file():
                import shutil
                for private in (final / ".version", final / ".old"):
                    if private.exists():
                        shutil.rmtree(private)
            if self.journal.exists():
                self.journal.unlink()
        except Exception as exc:
            self._write_journal(replace(journal, state="recovery_failed",
                current_step="restore", error=EvaluationError("publication",
                    "publication_failed", "run", None, None, str(exc))))
            raise EvaluationRunStoreError("evaluation publication recovery failed") from exc

    def recover(self) -> None:
        with self._lock():
            self._recover_locked()

    def publish(self, request: EvaluationRunRequest,
                rendered: RenderedEvaluation) -> EvaluationRun:
        self._validate(request, rendered)
        final = self._run_path(request)
        staging = self.system / (".staging-" + request.run_id + "-" + uuid.uuid4().hex)
        files = {(item.location, item.relative_path): item for item in rendered.files}
        with self._lock():
            self._recover_locked()
            if final.exists():
                raise EvaluationRunStoreError("immutable run already exists")
            self.runs.mkdir(parents=True, exist_ok=True)
            staging.mkdir(parents=True, exist_ok=False)
            journal_written = False
            try:
                _atomic(staging / "request.json", canonical_json_bytes(asdict(request)))
                for (location, relative), item in files.items():
                    target = (staging / relative if location == "run" else
                              staging / ".version" / location / relative).resolve()
                    if not target.is_relative_to(staging):
                        raise EvaluationRunStoreError("staging path escaped")
                    _atomic(target, item.content)
                restore_entries = []
                for location, relative in _VERSION_FILES:
                    target = self._version_path(location, relative)
                    old = target.read_bytes() if target.exists() else None
                    snapshot_path = None
                    if old is not None:
                        snapshot = staging / ".old" / location / relative
                        _atomic(snapshot, old)
                        snapshot_path = snapshot.relative_to(self.root).as_posix()
                    restore_entries.append(EvaluationVersionRestoreEntry(location, relative,
                        old is not None, _sha(old) if old is not None else None,
                        snapshot_path, files[location, relative].sha256))
                journal = EvaluationPublicationJournal("evaluation_publication_journal_v3",
                    request.run_id, request.index_identity, "prepared", "none",
                    staging.relative_to(self.root).as_posix(),
                    final.relative_to(self.root).as_posix(), tuple(restore_entries),
                    datetime.now(timezone.utc).isoformat(), None)
                self._write_journal(journal)
                journal_written = True
                # Rename into an absent final path; the run marker is written last.
                os.rename(staging, final)
                moved_entries = tuple(replace(entry,
                    old_snapshot_path=entry.old_snapshot_path.replace(
                        journal.staging_path, journal.final_run_path, 1)
                    if entry.old_snapshot_path else None) for entry in journal.version_files)
                journal = replace(journal, state="publishing", current_step="run_publish",
                                  version_files=moved_entries)
                self._write_journal(journal)
                for location, relative in _VERSION_FILES:
                    _atomic(self._version_path(location, relative),
                            files[location, relative].content)
                journal = replace(journal, current_step="version_publish")
                self._write_journal(journal)
                completed = self._completed(request, rendered)
                _atomic(final / "run.json", canonical_json_bytes(asdict(completed)))
                journal = replace(journal, current_step="run_marker")
                self._write_journal(journal)
                self._recover_locked()
                return completed
            except Exception:
                if journal_written:
                    self._recover_locked()
                elif staging.exists():
                    import shutil
                    shutil.rmtree(staging)
                raise

    def record_failure(self, request: EvaluationRunRequest,
                       error: EvaluationError,
                       documents: tuple = ()) -> EvaluationRun:
        if error.scope not in {"run", "document", "case"}:
            raise EvaluationRunStoreError("invalid failure scope")
        status = "invalid" if error.stage == "preflight" else "failed"
        result = EvaluationRun(request.run_id, status, request.configuration_name,
            request.index_identity, request.build_config,
            request.index_identity.build_fingerprint, request.query_config,
            request.evaluation_protocol_version, request.ground_truth, request.test_set,
            request.started_at, None, (), None, None, error, "evaluation_run_v3")
        destination = self._run_path(request)
        with self._lock():
            self._recover_locked()
            if destination.exists():
                raise EvaluationRunStoreError("immutable run already exists")
            destination.mkdir(parents=True, exist_ok=False)
            try:
                if documents:
                    _atomic(destination / "diagnostic.json", canonical_json_bytes(
                        [asdict(item) for item in documents]))
                _atomic(destination / "run.json", canonical_json_bytes(asdict(result)))
            except Exception:
                # The directory is reserved for this run and retains diagnostic evidence.
                raise
        return result

    def _read_completed(self, directory: Path) -> EvaluationRun | None:
        marker = directory / "run.json"
        if not marker.is_file():
            raise EvaluationRunStoreError("run marker absent")
        try:
            raw = json.loads(marker.read_bytes())
            if raw.get("status") != "completed":
                if raw.get("status") not in {"failed", "invalid"}:
                    raise EvaluationRunStoreError("run status invalid")
                if (set(raw) != set(EvaluationRun.__dataclass_fields__)
                        or raw["schema_version"] != "evaluation_run_v3"
                        or raw["run_id"] not in directory.name
                        or raw["error"] is None or raw["completed_at"] is not None
                        or raw["aggregate_path"] is not None
                        or raw["aggregate_sha256"] is not None):
                    raise EvaluationRunStoreError("failed run marker invalid")
                return None
            if (set(raw) != set(EvaluationRun.__dataclass_fields__)
                    or raw["schema_version"] != "evaluation_run_v3"
                    or raw["error"] is not None or not raw["completed_at"]
                    or raw["aggregate_path"] != "aggregate.json"):
                raise EvaluationRunStoreError("completed run fields differ")
            index = IndexIdentity(**raw["index_identity"])
            projection = LocalReviewedEvidenceRepository._projection(raw["build_config"])
            if (_sha(canonical_json_bytes(asdict(projection))) != index.build_fingerprint
                    or raw["build_config_fingerprint"] != index.build_fingerprint):
                raise EvaluationRunStoreError("completed build identity differs")
            query = EvaluationQueryConfig(**raw["query_config"])
            ground = GroundTruthIdentity(**raw["ground_truth"])
            test = TestSetIdentity(**raw["test_set"])
            result = EvaluationRun(raw["run_id"], "completed", raw["configuration_name"],
                index, projection, raw["build_config_fingerprint"], query,
                raw["evaluation_protocol_version"], ground, test, raw["started_at"],
                raw["completed_at"], tuple(RunDocumentEntry(**entry)
                    for entry in raw["documents"]), raw["aggregate_path"],
                raw["aggregate_sha256"], None, "evaluation_run_v3")
            if directory.name != run_directory_name(EvaluationRunRequest(result.run_id,
                    result.configuration_name, result.index_identity, result.build_config,
                    result.query_config, result.ground_truth, result.test_set,
                    result.evaluation_protocol_version, result.started_at)):
                raise EvaluationRunStoreError("completed directory identity differs")
            aggregate = json.loads(self._read_run_file(directory, result.aggregate_path,
                                                       result.aggregate_sha256))
            if (aggregate.get("schema_version") != "evaluation_aggregate_v3"
                    or aggregate.get("run_id") != result.run_id):
                raise EvaluationRunStoreError("completed aggregate identity differs")
            _metrics(aggregate["overall"]["metrics"])
            if len(aggregate["documents"]) != len(result.documents):
                raise EvaluationRunStoreError("completed aggregate document count differs")
            seen = set()
            for entry in result.documents:
                if entry.document_key in seen:
                    raise EvaluationRunStoreError("duplicate completed document key")
                seen.add(entry.document_key)
                document = json.loads(self._read_run_file(directory, entry.json_path,
                                                           entry.json_sha256))
                self._read_run_file(directory, entry.html_path, entry.html_sha256)
                if (document.get("schema_version") != "evaluation_document_result_v3"
                        or document.get("run_id") != result.run_id
                        or document.get("document_key") != entry.document_key):
                    raise EvaluationRunStoreError("completed document identity differs")
            return result
        except (OSError, KeyError, ValueError, TypeError) as exc:
            if isinstance(exc, EvaluationRunStoreError):
                raise
            raise EvaluationRunStoreError("completed run readback failed") from exc

    @staticmethod
    def _read_run_file(directory: Path, relative: str, expected: str) -> bytes:
        if (not relative or "\\" in relative or relative.startswith("/")
                or ".." in relative.split("/")):
            raise EvaluationRunStoreError("unsafe run file path")
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory.resolve()) or path.is_symlink():
            raise EvaluationRunStoreError("unsafe run file path")
        content = path.read_bytes()
        if _sha(content) != expected:
            raise EvaluationRunStoreError("completed run file hash differs")
        return content

    def list_completed(self, ground_truth: GroundTruthIdentity
                       ) -> tuple[EvaluationRun, ...]:
        with self._lock():
            self._recover_locked()
            if not self.runs.is_dir():
                return ()
            runs = tuple(self._read_completed(path) for path in self.runs.iterdir()
                         if path.is_dir())
            return tuple(sorted((item for item in runs if item is not None
                                 and item.ground_truth == ground_truth),
                                key=lambda item: item.run_id))

    def list_comparison_runs(self) -> tuple[ComparisonRunSnapshot, ...]:
        with self._lock():
            self._recover_locked()
            snapshots = list(list_v2_snapshots(self.root))
            if self.runs.is_dir():
                for path in self.runs.iterdir():
                    if not path.is_dir():
                        continue
                    run = self._read_completed(path)
                    if run is None:
                        continue
                    aggregate = json.loads(self._read_run_file(path, "aggregate.json",
                                                               run.aggregate_sha256))
                    documents = []
                    for entry in run.documents:
                        document = json.loads(self._read_run_file(path, entry.json_path,
                                                                   entry.json_sha256))
                        cases = tuple(sorted((case["case_id"], case["question"])
                                             for case in document["cases"]))
                        if len({case[0] for case in cases}) != len(cases):
                            raise EvaluationRunStoreError("duplicate comparison case")
                        documents.append(ComparisonDocumentIdentity(document["document_id"],
                            document["file_hash"], cases))
                    documents.sort(key=lambda item: item.document_id)
                    scopes = tuple(sorted((AggregateScope("document", item["document_id"],
                        item["answerable_count"], item["unanswerable_count"],
                        item["unmappable_group_count"], _metrics(item["metrics"]))
                        for item in aggregate["documents"]), key=lambda item: item.document_id))
                    if tuple(item.document_id for item in scopes) != tuple(
                            item.document_id for item in documents):
                        raise EvaluationRunStoreError("comparison PDF scope differs")
                    snapshots.append(ComparisonRunSnapshot("3.0", run.run_id,
                        (path / "run.json").relative_to(self.root).as_posix(),
                        run.configuration_name, run.index_identity.collection_name,
                        "build_projection_v3",
                        canonical_json_bytes(asdict(run.build_config)),
                        run.build_config_fingerprint, run.ground_truth,
                        tuple(documents), ComparisonQueryConfig(**asdict(run.query_config)),
                        run.evaluation_protocol_version,
                        run.test_set.annotation_rule_version,
                        scopes,
                        _metrics(aggregate["overall"]["metrics"]),
                        "validation/retrieval/system-v3.0/summary.md"))
            latest_v3: dict[str, ComparisonRunSnapshot] = {}
            visible = []
            for item in snapshots:
                if item.system_version == "3.0":
                    previous = latest_v3.get(item.configuration_label)
                    if previous is None or item.run_id > previous.run_id:
                        latest_v3[item.configuration_label] = item
                else:
                    visible.append(item)
            return tuple(sorted((*visible, *latest_v3.values()),
                                key=lambda item: (item.system_version, item.run_id)))

    def load_baselines(self) -> BaselineSelection:
        path = self.base / "baselines.json"
        if not path.is_file():
            return BaselineSelection("evaluation_baselines_v3", (), None)
        try:
            raw = json.loads(path.read_bytes())
            if (set(raw) != set(BaselineSelection.__dataclass_fields__)
                    or raw["schema_version"] != "evaluation_baselines_v3"):
                raise ValueError("baseline schema differs")
            entries = tuple(BaselineSelectionEntry(**{**item,
                "query_config": EvaluationQueryConfig(**item["query_config"])})
                for item in raw["entries"])
            if bool(entries) != bool(raw["updated_at"]):
                raise ValueError("baseline timestamp differs")
            available = {(item.system_version, item.run_id): item
                         for item in self.list_comparison_runs()}
            keys = set()
            for entry in entries:
                key = (entry.ground_truth_fingerprint,
                    entry.evaluation_protocol_version, entry.query_config,
                    entry.annotation_rule_version)
                chosen = available.get((entry.system_version, entry.run_id))
                if (key in keys or chosen is None or not entry.reviewed_at
                        or (chosen.ground_truth.ground_truth_fingerprint,
                            chosen.evaluation_protocol_version,
                            chosen.query_config, chosen.annotation_rule_version) != key):
                    raise ValueError("baseline reference invalid")
                keys.add(key)
            return BaselineSelection("evaluation_baselines_v3", entries,
                                     raw["updated_at"])
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise EvaluationRunStoreError("baseline registry invalid") from exc
