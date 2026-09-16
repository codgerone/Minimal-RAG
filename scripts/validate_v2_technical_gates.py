from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp" / "v2-technical-validation"


def emit(name: str, payload: dict[str, object]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{name}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def validate_chroma() -> None:
    import chromadb

    root = OUT / "chroma"
    if root.exists():
        shutil.rmtree(root)
    client = chromadb.PersistentClient(path=str(root))
    collection = client.get_or_create_collection("snapshot_probe")
    ids = ["a", "b"]
    documents = ["alpha", "beta"]
    embeddings = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    metadatas = [
        {"document_id": "doc-1", "build_id": "old"},
        {"document_id": "doc-2", "build_id": "other"},
    ]
    collection.add(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
    one = collection.get(
        where={"document_id": "doc-1"},
        include=["documents", "embeddings", "metadatas"],
    )
    empty = collection.get(
        where={"document_id": "absent"},
        include=["documents", "embeddings", "metadatas"],
    )
    full = collection.get(include=["documents", "embeddings", "metadatas"])
    collection.delete(where={"document_id": "doc-1"})
    collection.add(
        ids=list(one["ids"]),
        documents=list(one["documents"] or []),
        embeddings=[list(item) for item in one["embeddings"]],
        metadatas=list(one["metadatas"] or []),
    )
    restored = collection.get(
        where={"document_id": "doc-1"},
        include=["documents", "embeddings", "metadatas"],
    )
    del collection
    del client
    restarted = chromadb.PersistentClient(path=str(root)).get_collection("snapshot_probe")
    after_restart = restarted.get(include=["documents", "embeddings", "metadatas"])
    checks = {
        "document_snapshot_ids": list(one["ids"]) == ["a"],
        "document_snapshot_embedding": [list(x) for x in one["embeddings"]] == [[1.0, 0.0, 0.0]],
        "empty_snapshot": list(empty["ids"]) == [],
        "collection_snapshot_count": len(full["ids"]) == 2,
        "restore_round_trip": list(restored["ids"]) == ["a"],
        "restart_count": len(after_restart["ids"]) == 2,
        "restart_dimensions": all(len(x) == 3 for x in after_restart["embeddings"]),
    }
    emit(
        "chroma",
        {
            "version": importlib.metadata.version("chromadb"),
            "checks": checks,
            "passed": all(checks.values()),
        },
    )


def validate_tokenizer() -> None:
    from transformers import AutoTokenizer

    model = "intfloat/multilingual-e5-small"
    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True)

    def count(text: str) -> int:
        return len(tokenizer(f"passage: {text}", add_special_tokens=True, truncation=False)["input_ids"])

    special_only = len(tokenizer("", add_special_tokens=True, truncation=False)["input_ids"])
    prefix_with_special = count("")
    sample = "销售订单 12345"
    sample_count = count(sample)
    repeated = "a " * 700
    ids = tokenizer(f"passage: {repeated}", add_special_tokens=True, truncation=False)["input_ids"]
    checks = {
        "cached_load": True,
        "no_truncation": len(ids) > 512,
        "special_tokens_counted": special_only > 0,
        "prefix_increases_count": prefix_with_special > special_only,
        "deterministic": sample_count == count(sample),
        "configured_limit_within_model": tokenizer.model_max_length >= 512,
    }
    emit(
        "tokenizer",
        {
            "transformers_version": importlib.metadata.version("transformers"),
            "model": model,
            "tokenizer_class": type(tokenizer).__name__,
            "model_max_length": tokenizer.model_max_length,
            "special_only_tokens": special_only,
            "passage_prefix_with_special_tokens": prefix_with_special,
            "sample_tokens": sample_count,
            "over_limit_probe_tokens": len(ids),
            "checks": checks,
            "passed": all(checks.values()),
        },
    )


def validate_windows() -> None:
    root = OUT / "windows-files"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    old = root / "manifest.json"
    temp = root / "manifest.build.tmp"
    old.write_text("old", encoding="utf-8")
    temp.write_text("new", encoding="utf-8")
    with temp.open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(old)
    replace_ok = old.read_text(encoding="utf-8") == "new" and not temp.exists()

    staging = root / "staging"
    published = root / "published"
    staging.mkdir()
    (staging / "winner-review.html").write_text("<html>ok</html>", encoding="utf-8")
    staging.rename(published)
    rename_ok = published.is_dir() and not staging.exists()

    html = published / "winner-review.html"
    handle = html.open("r", encoding="utf-8")
    cleanup_while_open_error = None
    try:
        shutil.rmtree(published)
    except Exception as exc:  # platform fact is the result under test
        cleanup_while_open_error = f"{type(exc).__name__}: {exc}"
    finally:
        handle.close()
    cleanup_while_open_succeeded = not published.exists()
    if published.exists():
        shutil.rmtree(published)

    directory_fsync_supported = False
    directory_fsync_error = None
    try:
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
            directory_fsync_supported = True
        finally:
            os.close(descriptor)
    except Exception as exc:
        directory_fsync_error = f"{type(exc).__name__}: {exc}"

    checks = {"same_volume_replace": replace_ok, "directory_rename": rename_ok}
    emit(
        "windows-files",
        {
            "platform": sys.platform,
            "checks": checks,
            "cleanup_while_html_open_succeeded": cleanup_while_open_succeeded,
            "cleanup_while_html_open_error": cleanup_while_open_error,
            "directory_fsync_supported": directory_fsync_supported,
            "directory_fsync_error": directory_fsync_error,
            "passed": all(checks.values()),
        },
    )


def validate_docling_sdk() -> None:
    import inspect
    from docling_core.types.doc import (
        BoundingBox,
        CoordOrigin,
        DocItemLabel,
        GroupItem,
        ListItem,
        TableItem,
        TextItem,
    )
    from docling_core.types.doc.document import DoclingDocument

    iterate = inspect.signature(DoclingDocument.iterate_items)
    fields = set(DoclingDocument.model_fields)
    text_fields = set(TextItem.model_fields)
    list_fields = set(ListItem.model_fields)
    table_fields = set(TableItem.model_fields)
    bbox_fields = set(BoundingBox.model_fields)
    checks = {
        "iterate_with_groups": "with_groups" in iterate.parameters,
        "iterate_traverse_pictures": "traverse_pictures" in iterate.parameters,
        "document_body": "body" in fields,
        "document_furniture": "furniture" in fields,
        "document_tables": "tables" in fields,
        "text_has_prov": "prov" in text_fields,
        "text_has_text": "text" in text_fields,
        "list_has_marker": "marker" in list_fields,
        "list_has_orig": "orig" in list_fields,
        "table_has_captions": "captions" in table_fields,
        "table_has_footnotes": "footnotes" in table_fields,
        "bbox_has_coord_origin": "coord_origin" in bbox_fields,
        "top_left_origin": hasattr(CoordOrigin, "TOPLEFT"),
        "bottom_left_origin": hasattr(CoordOrigin, "BOTTOMLEFT"),
        "labels_available": all(hasattr(DocItemLabel, name) for name in ["TITLE", "SECTION_HEADER", "TEXT", "TABLE", "PICTURE", "FORMULA"]),
        "group_model_available": bool(GroupItem.model_fields),
    }
    emit(
        "docling-sdk",
        {
            "docling_version": importlib.metadata.version("docling"),
            "docling_core_version": importlib.metadata.version("docling-core"),
            "iterate_items_signature": str(iterate),
            "document_fields": sorted(fields),
            "text_fields": sorted(text_fields),
            "list_fields": sorted(list_fields),
            "table_fields": sorted(table_fields),
            "bbox_fields": sorted(bbox_fields),
            "checks": checks,
            "passed": all(checks.values()),
        },
    )


def validate_docling_conversion(pdf: Path) -> None:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = False
    options.do_table_structure = True
    options.table_structure_options.mode = TableFormerMode.ACCURATE
    options.table_structure_options.do_cell_matching = True
    options.generate_page_images = False
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    result = converter.convert(pdf)
    document = result.document
    items = list(document.iterate_items(with_groups=True, traverse_pictures=True))
    labels: dict[str, int] = {}
    provenance_count = 0
    multi_provenance_count = 0
    for item, _level in items:
        label = str(getattr(item, "label", type(item).__name__))
        labels[label] = labels.get(label, 0) + 1
        prov = list(getattr(item, "prov", []) or [])
        provenance_count += len(prov)
        multi_provenance_count += int(len(prov) > 1)
    raw = document.export_to_dict()
    fixture = OUT / "docling-real-output.json"
    fixture.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checks = {
        "items_present": bool(items),
        "raw_export_present": bool(raw),
        "pages_present": bool(document.pages),
        "provenance_present": provenance_count > 0,
    }
    emit(
        "docling-conversion",
        {
            "pdf": str(pdf.relative_to(ROOT)).replace("\\", "/"),
            "configuration": {
                "do_ocr": options.do_ocr,
                "do_table_structure": options.do_table_structure,
                "table_mode": str(options.table_structure_options.mode),
                "do_cell_matching": options.table_structure_options.do_cell_matching,
                "generate_page_images": options.generate_page_images,
            },
            "item_count": len(items),
            "labels": labels,
            "page_count": len(document.pages),
            "provenance_count": provenance_count,
            "multi_provenance_item_count": multi_provenance_count,
            "table_count": len(document.tables),
            "picture_count": len(document.pictures),
            "fixture": str(fixture.relative_to(ROOT)).replace("\\", "/"),
            "checks": checks,
            "passed": all(checks.values()),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=["chroma", "tokenizer", "windows", "docling-sdk", "docling-conversion"])
    parser.add_argument("--pdf", type=Path)
    args = parser.parse_args()
    if args.gate == "chroma":
        validate_chroma()
    elif args.gate == "tokenizer":
        validate_tokenizer()
    elif args.gate == "windows":
        validate_windows()
    elif args.gate == "docling-sdk":
        validate_docling_sdk()
    elif args.gate == "docling-conversion":
        if args.pdf is None:
            parser.error("--pdf is required")
        validate_docling_conversion(args.pdf.resolve())


if __name__ == "__main__":
    main()
