"""Migrate the pre-namespace .rag layout into isolated system-version roots."""

from __future__ import annotations

import argparse
import gc
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import chromadb

from rag.document_registry import make_artifact_document_name


OLD_ENTRIES = (
    "chroma", "artifacts", "recovery", "manifest.json",
    "manifest.v1.json", "manifest.v2.json",
)
COLLECTIONS = (
    ("minimal_rag_documents", "legacy"),
    ("minimal_rag_documents_v1", "current"),
    ("minimal_rag_documents_v2", "current"),
)


def _copy_collection(source, target, name: str) -> int:
    existing = {item.name for item in source.list_collections()}
    if name not in existing:
        return 0
    origin = source.get_collection(name)
    create_options = {"metadata": origin.metadata} if origin.metadata else {}
    destination = target.create_collection(name, **create_options)
    count = origin.count()
    for offset in range(0, count, 500):
        payload = origin.get(
            limit=500, offset=offset,
            include=["documents", "embeddings", "metadatas"],
        )
        embeddings = payload["embeddings"]
        destination.add(
            ids=payload["ids"],
            documents=payload["documents"],
            embeddings=(embeddings.tolist() if hasattr(embeddings, "tolist") else embeddings),
            metadatas=payload["metadatas"],
        )
    if destination.count() != count:
        raise RuntimeError(f"collection copy count mismatch: {name}")
    return count


def _copy_manifest(source: Path, target: Path) -> dict:
    raw = json.loads(source.read_text(encoding="utf-8"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return raw


def _migrate_v2_artifacts(project_root: Path, raw: dict, target_root: Path) -> dict:
    migrated = json.loads(json.dumps(raw))
    for record in migrated["documents"].values():
        old_build = project_root / record["artifact_path"]
        if not old_build.is_dir():
            raise RuntimeError(f"active artifact is missing: {old_build}")
        folder = make_artifact_document_name(record["relative_path"], record["document_id"])
        relative = Path(".rag/system-v2/artifacts/v2/documents") / folder / record["build_id"]
        new_build = target_root / "artifacts/v2/documents" / folder / record["build_id"]
        new_build.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(old_build, new_build)
        nested_raw = new_build / "raw/docling-document.json"
        flat_raw = new_build / "raw-docling-document.json"
        if nested_raw.is_file():
            nested_raw.replace(flat_raw)
            nested_raw.parent.rmdir()
        if not flat_raw.is_file():
            raise RuntimeError(f"raw Docling artifact is missing: {new_build}")
        record["artifact_path"] = relative.as_posix()
    return migrated


def _backup_old_layout(rag_root: Path, backup: Path) -> None:
    backup.mkdir(parents=True)
    for name in OLD_ENTRIES:
        source = rag_root / name
        if not source.exists():
            continue
        target = backup / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)


def _collection_counts(path: Path) -> dict[str, int]:
    client = chromadb.PersistentClient(path=str(path))
    counts = {item.name: item.count() for item in client.list_collections()}
    del client
    gc.collect()
    return counts


def _cleanup_old_layout(project_root: Path) -> int:
    rag_root = project_root / ".rag"
    marker_path = rag_root / "system-v2/storage-layout-migration.json"
    if not marker_path.is_file():
        raise SystemExit("migration marker is missing; refusing cleanup")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    expected = marker["copied_collections"]
    actual_current = _collection_counts(rag_root / "system-v2/chroma")
    actual_legacy = _collection_counts(rag_root / "legacy-system-v1/chroma")
    for name in ("minimal_rag_documents_v1", "minimal_rag_documents_v2"):
        if actual_current.get(name) != expected.get(name):
            raise RuntimeError(f"current collection verification failed: {name}")
    if actual_legacy.get("minimal_rag_documents") != expected.get("minimal_rag_documents"):
        raise RuntimeError("legacy collection verification failed")
    backup = Path(marker["backup"])
    if not backup.is_dir():
        raise RuntimeError(f"backup is missing: {backup}")
    for name in OLD_ENTRIES:
        old = rag_root / name
        if old.is_dir():
            shutil.rmtree(old)
        elif old.exists():
            old.unlink()
    for staging in rag_root.glob(".layout-migration-*"):
        if staging.is_dir():
            shutil.rmtree(staging)
    marker["old_layout_removed"] = True
    marker_path.write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "complete", "backup": str(backup)}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="perform the migration")
    parser.add_argument(
        "--cleanup-old", action="store_true",
        help="remove the verified old layout after --apply has completed",
    )
    parser.add_argument("--project-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.apply and args.cleanup_old:
        parser.error("--apply and --cleanup-old are mutually exclusive")

    project_root = (args.project_root or Path(__file__).resolve().parent.parent).resolve()
    if args.cleanup_old:
        return _cleanup_old_layout(project_root)
    rag_root = project_root / ".rag"
    old_chroma = rag_root / "chroma"
    if not old_chroma.is_dir():
        raise SystemExit("old .rag/chroma does not exist; nothing to migrate")
    if (rag_root / "system-v2").exists() or (rag_root / "legacy-system-v1").exists():
        raise SystemExit("new storage layout already exists; refusing to merge")
    journals = list((rag_root / "recovery").glob("**/journal.json"))
    if journals:
        raise SystemExit("pending recovery journal exists; recover it before migration")

    source_client = chromadb.PersistentClient(path=str(old_chroma))
    counts = {item.name: item.count() for item in source_client.list_collections()}
    print(json.dumps({"source_collections": counts}, ensure_ascii=False, indent=2))
    if not args.apply:
        print("dry run only; pass --apply to migrate")
        return 0

    migration = rag_root / f".layout-migration-{uuid4().hex[:8]}"
    staged_current = migration / "system-v2"
    staged_legacy = migration / "legacy-system-v1"
    backup = project_root / "tmp" / (
        "storage-layout-backup-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    try:
        current_client = chromadb.PersistentClient(path=str(staged_current / "chroma"))
        legacy_client = chromadb.PersistentClient(path=str(staged_legacy / "chroma"))
        copied = {}
        for name, destination in COLLECTIONS:
            copied[name] = _copy_collection(
                source_client,
                legacy_client if destination == "legacy" else current_client,
                name,
            )

        legacy_manifest = rag_root / "manifest.json"
        if legacy_manifest.is_file():
            shutil.copy2(legacy_manifest, staged_legacy / "manifest.json")

        v1_source = rag_root / "manifest.v1.json"
        v2_source = rag_root / "manifest.v2.json"
        if not v1_source.is_file() or not v2_source.is_file():
            raise RuntimeError("both current-system manifests are required")
        _copy_manifest(v1_source, staged_current / "pipelines/v1/manifest.json")
        v2_raw = json.loads(v2_source.read_text(encoding="utf-8"))
        migrated_v2 = _migrate_v2_artifacts(project_root, v2_raw, staged_current)
        v2_target = staged_current / "pipelines/v2/manifest.json"
        v2_target.parent.mkdir(parents=True, exist_ok=True)
        v2_target.write_text(
            json.dumps(migrated_v2, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for pipeline in ("v1", "v2"):
            (staged_current / "recovery" / pipeline).mkdir(parents=True, exist_ok=True)
        (staged_current / "artifacts/v2/staging").mkdir(parents=True, exist_ok=True)

        expected_current = {
            name: counts.get(name, 0) for name, destination in COLLECTIONS
            if destination == "current"
        }
        actual_current = {
            item.name: item.count() for item in current_client.list_collections()
        }
        actual_legacy = {
            item.name: item.count() for item in legacy_client.list_collections()
        }
        if actual_current != expected_current:
            raise RuntimeError(f"current collection verification failed: {actual_current}")
        expected_legacy = {"minimal_rag_documents": counts.get("minimal_rag_documents", 0)}
        if actual_legacy != expected_legacy:
            raise RuntimeError(f"legacy collection verification failed: {actual_legacy}")

        _backup_old_layout(rag_root, backup)
        del current_client, legacy_client
        gc.collect()
        # Chroma may retain SQLite handles until process exit on Windows, so
        # publish verified staging by copying and defer staging deletion to the
        # independent --cleanup-old process.
        shutil.copytree(staged_current, rag_root / "system-v2")
        shutil.copytree(staged_legacy, rag_root / "legacy-system-v1")
        marker = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "copied_collections": copied,
            "backup": backup.as_posix(),
            "old_layout_removed": False,
        }
        marker_path = rag_root / "system-v2/storage-layout-migration.json"
        marker_path.write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({
            "status": "copied_and_verified", "copied_collections": copied,
            "backup": backup.as_posix(),
            "next": "run again with --cleanup-old after this process exits",
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        print(f"migration staging preserved for diagnosis: {migration}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
