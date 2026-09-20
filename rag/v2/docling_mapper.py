"""Map the locked Docling public SDK model into owned V2 layout contracts."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from rag.v2.common import PageSpan, ProcessingWarning, enclosing_bbox
from rag.v2.document_models import (
    DoclingRawArtifact, LayoutDocument, LayoutElement, LayoutList, LayoutListItem,
    LayoutParseResult, LayoutTablePlaceholder, LayoutText, SourceDocument, TextKind,
)
from rag.v2.extractors.docling import normalize_document_tables, page_spans
from rag.v2.table_models import PageExecution
from rag.v2.table_selection import TableSlot


def node_id(document_id: str, docling_ref: str) -> str:
    digest = hashlib.sha256(f"{document_id}|{docling_ref}".encode("utf-8")).hexdigest()[:20]
    return f"node_{digest}"


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _resolve(document: Any, reference: Any) -> Any:
    return reference.resolve(document) if hasattr(reference, "resolve") else reference


def _refs(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(getattr(value, "cref", getattr(value, "self_ref", "")) for value in values)


def _warning(code: str, message: str, refs: tuple[str, ...]) -> ProcessingWarning:
    return ProcessingWarning(code, "layout_mapping", message, refs)  # type: ignore[arg-type]


def _kind(label: str) -> TextKind | None:
    return {
        "section_header": "title", "title": "title", "text": "paragraph", "paragraph": "paragraph",
        "page_header": "header", "page_footer": "footer", "footnote": "footnote",
        "caption": "caption", "formula": "other_text",
    }.get(label)  # type: ignore[return-value]


def _sources(item: Any, page_sizes: dict[int, tuple[float, float]]) -> tuple[PageSpan, ...]:
    return page_spans(item, page_sizes, getattr(item, "self_ref", ""))


def _layout_text(
    document_id: str, item: Any, page_sizes: dict[int, tuple[float, float]],
    *, kind: TextKind | None = None, detached_list: bool = False,
) -> LayoutText | None:
    text = getattr(item, "orig", None) if detached_list else getattr(item, "text", None)
    label = _value(getattr(item, "label", ""))
    if label == "formula" and (not isinstance(text, str) or not text.strip()):
        original = getattr(item, "orig", None)
        if isinstance(original, str) and original.strip():
            text = original
    if text is None:
        return None
    ref = item.self_ref
    return LayoutText(node_id(document_id, ref), ref, kind or "other_text", str(text), _sources(item, page_sizes))


def _list(
    document: Any, group: Any, document_id: str, page_sizes: dict[int, tuple[float, float]],
    warnings: list[ProcessingWarning], handled: set[str],
) -> LayoutList:
    group_ref = group.self_ref
    list_node_id = node_id(document_id, group_ref)
    items: list[LayoutListItem] = []
    refs: list[str] = [group_ref]

    def walk(current: Any, level: int, parent_id: str | None) -> None:
        last_item_id = parent_id
        for child_ref in getattr(current, "children", ()):
            child = _resolve(document, child_ref)
            ref = child.self_ref
            label = _value(getattr(child, "label", ""))
            if label == "list_item":
                item_id = f"{list_node_id}_item_{len(items) + 1:06d}"
                marker = getattr(child, "marker", None)
                marker = marker.strip() if isinstance(marker, str) and marker.strip() else None
                item = LayoutListItem(item_id, ref, str(getattr(child, "text", "")), level,
                                      marker, parent_id if level > 0 else None, _sources(child, page_sizes))
                items.append(item)
                refs.append(ref)
                handled.add(ref)
                last_item_id = item_id
            elif _value(getattr(child, "label", "")) == "list" and _value(getattr(child, "name", "")) == "list":
                refs.append(ref)
                handled.add(ref)
                if last_item_id is None:
                    warnings.append(_warning("orphan_nested_list_group", "嵌套列表缺少可引用的上级列表项。", (ref,)))
                    walk(child, level, None)
                else:
                    walk(child, level + 1, last_item_id)
            else:
                # Non-list descendants are handled by the main transparent traversal.
                continue

    handled.add(group_ref)
    walk(group, 0, None)
    sources: list[PageSpan] = []
    seen_sources: set[tuple[int, str]] = set()
    for item in items:
        for source in item.sources:
            key = (source.page_number, source.source_ref)
            if key not in seen_sources:
                sources.append(source)
                seen_sources.add(key)
    return LayoutList(list_node_id, tuple(refs), tuple(items), tuple(sources))


def _slot(candidate: Any, slot_id: str, docling_ref: str, source_refs: tuple[str, ...]) -> TableSlot:
    located = tuple(region for region in candidate.regions if region.page_number is not None)
    pages = {region.page_number for region in located}
    if not candidate.regions:
        return TableSlot(slot_id, docling_ref, None, None, None, None, "deferred", "missing_slot_provenance", source_refs)
    if len(pages) > 1:
        return TableSlot(slot_id, docling_ref, None, None, None, None, "deferred", "cross_page_slot", source_refs)
    if not located:
        reason = "invalid_slot_page_geometry" if any(
            region.unavailable_reason == "invalid_page_geometry" for region in candidate.regions
        ) else "missing_slot_provenance"
        return TableSlot(slot_id, docling_ref, None, None, None, None, "deferred", reason, source_refs)
    if any(region.bbox is None for region in located):
        mapping = {
            "missing_bbox": "missing_slot_bbox",
            "coordinate_conversion_failed": "slot_coordinate_conversion_failed",
            "invalid_bbox": "invalid_slot_bbox",
            "invalid_page_geometry": "invalid_slot_page_geometry",
        }
        missing = next(region for region in located if region.bbox is None)
        reason = mapping[missing.unavailable_reason]
        return TableSlot(slot_id, docling_ref, None, None, None, None, "deferred", reason, source_refs)
    first = located[0]
    bbox = enclosing_bbox(tuple(region.bbox for region in located if region.bbox is not None))
    return TableSlot(slot_id, docling_ref, first.page_number, first.page_width, first.page_height,
                     bbox, "eligible", None, source_refs)


def map_docling_document(
    document: Any,
    source: SourceDocument,
    page_sizes: dict[int, tuple[float, float]],
    *,
    raw_relative_path: str = "raw-docling-document.json",
) -> LayoutParseResult:
    candidates = normalize_document_tables(document, page_sizes)
    candidate_by_ref = {candidate.source_ref.split("#", 1)[1]: candidate for candidate in candidates}
    handled: set[str] = set()
    warnings: list[ProcessingWarning] = []
    elements: list[LayoutElement] = []
    slots: list[TableSlot] = []

    table_attachments: set[str] = set()
    picture_attachments: set[str] = set()
    for table in getattr(document, "tables", ()):
        table_attachments.update(_refs(tuple(getattr(table, "captions", ()) or ()) + tuple(getattr(table, "footnotes", ()) or ())))
    for picture in getattr(document, "pictures", ()):
        picture_attachments.update(_refs(tuple(getattr(picture, "captions", ()) or ())))

    for item, _level in document.iterate_items(with_groups=True, traverse_pictures=True):
        ref = getattr(item, "self_ref", "")
        if ref in ("#/body", "#/furniture") or ref in handled or ref in table_attachments or ref in picture_attachments:
            continue
        label = _value(getattr(item, "label", ""))
        name = _value(getattr(item, "name", ""))
        if label == "list" and name == "list":
            layout_list = _list(document, item, source.document_id, page_sizes, warnings, handled)
            if layout_list.items:
                elements.append(layout_list)
            continue
        if label == "table":
            table_index = int(ref.rsplit("/", 1)[-1])
            for caption_ref in tuple(getattr(item, "captions", ()) or ()):
                caption = _resolve(document, caption_ref)
                text = _layout_text(source.document_id, caption, page_sizes, kind="caption")
                if text:
                    elements.append(text)
                handled.add(caption.self_ref)
            slot_id = f"slot_{len(slots) + 1:03d}"
            candidate = candidate_by_ref[f"/tables/{table_index}"]
            spans = _sources(item, page_sizes)
            source_refs = tuple(span.source_ref for span in spans) + _refs(tuple(getattr(item, "references", ()) or ()))
            slots.append(_slot(candidate, slot_id, ref, source_refs))
            elements.append(LayoutTablePlaceholder(node_id(source.document_id, ref), ref, slot_id, spans))
            handled.add(ref)
            for footnote_ref in tuple(getattr(item, "footnotes", ()) or ()):
                footnote = _resolve(document, footnote_ref)
                text = _layout_text(source.document_id, footnote, page_sizes, kind="table_note")
                if text:
                    elements.append(text)
                handled.add(footnote.self_ref)
            continue
        if label == "picture":
            for caption_ref in tuple(getattr(item, "captions", ()) or ()):
                caption = _resolve(document, caption_ref)
                text = _layout_text(source.document_id, caption, page_sizes, kind="caption")
                if text:
                    elements.append(text)
                handled.add(caption.self_ref)
            handled.add(ref)
            continue
        if label == "list_item":
            text = _layout_text(source.document_id, item, page_sizes, kind="other_text", detached_list=True)
            if text:
                elements.append(text)
            warnings.append(_warning("detached_list_item", "列表项不属于有效 list group，已降为普通文字。", (ref,)))
            handled.add(ref)
            continue
        known_kind = _kind(label)
        if known_kind is not None:
            # Detached furniture is inserted in the deterministic pass below.
            if label not in ("page_header", "page_footer"):
                text = _layout_text(source.document_id, item, page_sizes, kind=known_kind)
                if text:
                    elements.append(text)
                    if (
                        label == "formula" and text.text
                        and not str(getattr(item, "text", "")).strip()
                    ):
                        warnings.append(_warning(
                            "formula_orig_fallback",
                            "公式规范化 text 为空，已回退保留 Docling orig 文字。",
                            (ref,),
                        ))
                handled.add(ref)
            continue
        if hasattr(item, "text"):
            text = _layout_text(source.document_id, item, page_sizes, kind="other_text")
            if text:
                elements.append(text)
                warnings.append(_warning("unknown_docling_text_label", f"未知文字 label {label!r}，已降级保留。", (ref,)))
            handled.add(ref)
        elif ref:
            warnings.append(_warning("unsupported_nontext_item", f"未支持的无文字元素 {label!r}。", (ref,)))
            handled.add(ref)

    # Recover furniture and any remaining text. Body order remains authoritative;
    # recovered items are placed by page and vertical coordinate without splitting nodes.
    recovered: list[tuple[int | None, float, int, LayoutText]] = []
    for item in getattr(document, "texts", ()):
        ref = item.self_ref
        if ref in handled:
            continue
        label = _value(getattr(item, "label", ""))
        kind = _kind(label) or "other_text"
        text = _layout_text(source.document_id, item, page_sizes, kind=kind)
        if text is None:
            handled.add(ref)
            continue
        if not text.sources:
            warnings.append(_warning("source_location_unavailable", "文字缺少可用来源，已置于文末。", (ref,)))
            recovered.append((None, float("inf"), 1, text))
        else:
            source_span = text.sources[0]
            y = source_span.bbox.y0 if source_span.bbox else float("inf")
            category = -1 if kind == "header" else 1 if kind == "footer" else 0
            if kind not in ("header", "footer"):
                warnings.append(_warning("detached_text_recovered", "脱离 body 的文字已按来源位置恢复。", (ref,)))
            recovered.append((source_span.page_number, y, category, text))
        if label == "formula" and text.text and not str(getattr(item, "text", "")).strip():
            warnings.append(_warning(
                "formula_orig_fallback",
                "公式规范化 text 为空，已回退保留 Docling orig 文字。",
                (ref,),
            ))
        handled.add(ref)

    def pages_of(element: LayoutElement) -> set[int]:
        return {source_span.page_number for source_span in element.sources}

    for page, _y, category, text in sorted(recovered, key=lambda value: (value[0] is None, value[0] or 0, value[2], value[1], value[3].docling_ref)):
        if page is None:
            elements.append(text)
            continue
        matching = [index for index, element in enumerate(elements) if page in pages_of(element)]
        if not matching:
            elements.append(text)
        elif category < 0:
            elements.insert(matching[0], text)
        elif category > 0:
            elements.insert(matching[-1] + 1, text)
        else:
            elements.insert(matching[-1] + 1, text)

    layout = LayoutDocument(source.document_id, len(page_sizes), tuple(elements), tuple(slots), tuple(warnings))
    by_page: dict[int, list[str]] = {page: [] for page in sorted(page_sizes)}
    for candidate in candidates:
        pages = [region.page_number for region in candidate.regions if region.page_number in by_page]
        if pages:
            by_page[pages[0]].append(candidate.candidate_id)
    executions = tuple(PageExecution(page, "completed", tuple(by_page[page]), None, None) for page in sorted(by_page))
    raw = DoclingRawArtifact(raw_relative_path, str(getattr(document, "schema_name", "")),
                             str(getattr(document, "version", "")), len(page_sizes),
                             len(getattr(document, "texts", ())), len(getattr(document, "tables", ())),
                             len(getattr(document, "pictures", ())))
    return LayoutParseResult(raw, layout, candidates, executions)
