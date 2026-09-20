"""Structure-aware V2 chunking with exact embedding-input token checks."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Protocol

from rag.models import ChunkSource, V2DocumentChunk
from rag.v2.common import ProcessingWarning
from rag.v2.document_models import ListNode, ParsedDocument, TableNode, TextNode


class ChunkingError(RuntimeError):
    """Raised when even one source code point cannot fit the embedding budget."""


class TokenCounter(Protocol):
    def count_passage(self, text: str) -> int: ...


@dataclass(frozen=True)
class _PrefixedCounter:
    delegate: TokenCounter
    prefix: str

    def count_passage(self, text: str) -> int:
        return self.delegate.count_passage(self.prefix + text)


@dataclass(frozen=True)
class ChunkingResult:
    chunks: tuple[V2DocumentChunk, ...]
    warnings: tuple[ProcessingWarning, ...]
    character_count: int


_BOUNDARY_PATTERNS = (
    re.compile(r"(?:\n){2,}"), re.compile(r"\n"), re.compile(r"[。！？.!?]"),
    re.compile(r"[；;]"), re.compile(r"[：:]"), re.compile(r"[，,]"), re.compile(r"\s"),
)


def normalize_node_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def serialize_list(node: ListNode) -> tuple[tuple[str, str, int, int], ...]:
    """Return item_id, formatted text, normalized-body start and end."""
    result = []
    for item in node.items:
        body = normalize_node_text(item.text)
        marker = item.ordinal if item.ordinal is not None else "-"
        indent = "  " * item.level
        lines = body.split("\n")
        formatted = f"{indent}{marker} {lines[0]}"
        if len(lines) > 1:
            formatted += "\n" + "\n".join(f"{indent}  {line}" for line in lines[1:])
        result.append((item.item_id, formatted, 0, len(body)))
    return tuple(result)


def _fits(counter: TokenCounter, text: str, maximum: int) -> bool:
    return bool(text) and counter.count_passage(text) <= maximum


def _longest_end(text: str, start: int, prefix: str, counter: TokenCounter, maximum: int) -> int:
    for pattern in _BOUNDARY_PATTERNS:
        boundaries = [match.end() for match in pattern.finditer(text, start) if match.end() > start]
        for end in reversed(boundaries):
            if _fits(counter, prefix + text[start:end], maximum):
                return end
    for end in range(len(text), start, -1):
        if _fits(counter, prefix + text[start:end], maximum):
            return end
    raise ChunkingError("单个 Unicode code point 加最短定位上下文仍超过 token 上限。")


def _overlap(text: str, end: int, counter: TokenCounter, limit: int) -> tuple[int, str]:
    if limit <= 0 or end <= 0:
        return end, ""
    lower = 0
    candidates: list[int] = []
    for pattern in reversed(_BOUNDARY_PATTERNS):
        candidates.extend(match.end() for match in pattern.finditer(text, lower, end) if match.end() < end)
    candidates.extend(range(end - 1, -1, -1))
    for start in sorted(set(candidates)):
        suffix = text[start:end]
        token_count = counter.count_text(suffix) if hasattr(counter, "count_text") else counter.count_passage(suffix)
        if suffix and token_count <= limit:
            return start, suffix
    return end, ""


def _split_text(
    text: str, counter: TokenCounter, maximum: int, overlap_limit: int,
) -> tuple[tuple[str, int, int, int | None, int | None], ...]:
    """Return rendered text, new range and optional repeated range."""
    if _fits(counter, text, maximum):
        return ((text, 0, len(text), None, None),)
    pieces = []
    cursor = 0
    previous_end = 0
    while cursor < len(text):
        overlap_start, overlap = _overlap(text, previous_end, counter, overlap_limit) if pieces else (0, "")
        end = _longest_end(text, cursor, overlap, counter, maximum)
        rendered = overlap + text[cursor:end]
        if not _fits(counter, rendered, maximum) or end <= cursor:
            raise ChunkingError("分块未能消费新的 code point。")
        pieces.append((rendered, cursor, end,
                       overlap_start if overlap else None, previous_end if overlap else None))
        previous_end = end
        cursor = end
    return tuple(pieces)


def _pages(node: TextNode | ListNode | TableNode):
    return node.sources


def chunk_parsed_document(
    document: ParsedDocument,
    counter: TokenCounter,
    *,
    max_input_tokens: int = 512,
    overlap_tokens: int = 32,
) -> ChunkingResult:
    if max_input_tokens <= 0 or overlap_tokens < 0 or overlap_tokens >= max_input_tokens:
        raise ValueError("V2 token 预算无效。")
    drafts: list[tuple[str, str, tuple[ChunkSource, ...], str | None, int, int]] = []
    warnings: list[ProcessingWarning] = []
    character_count = 0
    pending_text = ""
    pending_sources: list[ChunkSource] = []

    def flush_text() -> None:
        nonlocal pending_text, pending_sources
        if pending_text:
            drafts.append(("text", pending_text, tuple(pending_sources), None, 0, 1))
        pending_text, pending_sources = "", []

    for node in document.nodes:
        if isinstance(node, TextNode):
            text = normalize_node_text(node.text)
            character_count += len(text)
            if not text:
                warnings.append(ProcessingWarning("empty_text_node", "structured_chunking",
                                                  "规范化后文字节点为空，未生成正文。", (node.docling_ref,)))
                continue
            whole_source = ChunkSource(node.node_id, node.sources, 0, len(text), False, "none")
            if _fits(counter, text, max_input_tokens):
                combined = f"{pending_text}\n\n{text}" if pending_text else text
                if pending_text and not _fits(counter, combined, max_input_tokens):
                    flush_text()
                    combined = text
                pending_text = combined
                pending_sources.append(whole_source)
                continue
            flush_text()
            parts = _split_text(text, counter, max_input_tokens,
                                0 if node.kind == "title" else overlap_tokens)
            total = len(parts)
            for index, (rendered, start, end, overlap_start, overlap_end) in enumerate(parts):
                sources = []
                if overlap_start is not None:
                    sources.append(ChunkSource(node.node_id, node.sources, overlap_start, overlap_end,
                                               True, "overlap"))
                sources.append(ChunkSource(node.node_id, node.sources, start, end, False, "none"))
                drafts.append(("text", rendered, tuple(sources), node.node_id if total > 1 else None, index, total))
            continue

        flush_text()
        if isinstance(node, ListNode):
            formatted_items = serialize_list(node)
            character_count += sum(len(normalize_node_text(item.text)) for item in node.items)
            # Group complete items greedily. Ancestors are repeated for nested items when needed.
            item_by_id = {item.item_id: item for item in node.items}
            formatted_by_id = {item_id: text for item_id, text, *_ in formatted_items}
            chunks_for_list: list[tuple[str, tuple[ChunkSource, ...]]] = []
            current_text, current_sources = "", []
            for item, (_item_id, formatted, _start, body_end) in zip(node.items, formatted_items):
                ancestors = []
                parent = item.parent_item_id
                while parent is not None:
                    ancestors.append(parent)
                    parent = item_by_id[parent].parent_item_id
                ancestor_chain = list(reversed(ancestors))
                ancestor_lines = [formatted_by_id[value] for value in ancestor_chain]
                context_sources = [
                    ChunkSource(parent_id, item_by_id[parent_id].sources, None, None,
                                True, "list_ancestor") for parent_id in ancestor_chain
                ]
                ancestor_text = "\n".join(ancestor_lines)
                unit = f"{ancestor_text}\n{formatted}" if ancestor_text else formatted
                if ancestors and not _fits(counter, unit, max_input_tokens):
                    for removed_count in range(1, len(ancestor_chain) + 1):
                        locators = []
                        for ancestor_id in ancestor_chain[:removed_count]:
                            ancestor = item_by_id[ancestor_id]
                            siblings = [entry for entry in node.items
                                        if entry.parent_item_id == ancestor.parent_item_id]
                            position = siblings.index(ancestor) + 1
                            locators.append(f"〔上级项：第{position}项〕")
                        kept = ancestor_chain[removed_count:]
                        context = "\n".join((*locators, *(formatted_by_id[value] for value in kept)))
                        candidate_unit = f"{context}\n{formatted}"
                        if _fits(counter, candidate_unit, max_input_tokens):
                            unit = candidate_unit
                            context_sources = [
                                *(ChunkSource(node.node_id, node.sources, None, None, True,
                                              "fallback_locator") for _ in locators),
                                *(ChunkSource(parent_id, item_by_id[parent_id].sources, None, None,
                                              True, "list_ancestor") for parent_id in kept),
                            ]
                            break
                if _fits(counter, unit, max_input_tokens):
                    proposed = f"{current_text}\n{formatted}" if current_text else unit
                    if current_text and not _fits(counter, proposed, max_input_tokens):
                        chunks_for_list.append((current_text, tuple(current_sources)))
                        current_text, current_sources = unit, [
                            *context_sources,
                            ChunkSource(item.item_id, item.sources, 0, body_end, False, "none"),
                        ]
                    else:
                        current_text = proposed
                        if not current_sources and ancestors:
                            current_sources.extend(context_sources)
                        current_sources.append(ChunkSource(item.item_id, item.sources, 0, body_end, False, "none"))
                else:
                    if current_text:
                        chunks_for_list.append((current_text, tuple(current_sources)))
                        current_text, current_sources = "", []
                    locator = f"〔List {node.node_id}，父项路径见完整列表〕\n" if ancestors else ""
                    body = normalize_node_text(item.text)
                    prefix = ("  " * item.level) + (item.ordinal or "-") + " "
                    fixed_context = locator + prefix
                    parts = _split_text(body, _PrefixedCounter(counter, fixed_context),
                                        max_input_tokens, overlap_tokens)
                    for rendered, start, end, overlap_start, overlap_end in parts:
                        content = fixed_context + rendered
                        sources = ([ChunkSource(node.node_id, node.sources, None, None, True, "fallback_locator")] if locator else [])
                        if overlap_start is not None:
                            sources.append(ChunkSource(item.item_id, item.sources, overlap_start, overlap_end, True, "overlap"))
                        sources.append(ChunkSource(item.item_id, item.sources, start, end, False, "none"))
                        chunks_for_list.append((content, tuple(sources)))
            if current_text:
                chunks_for_list.append((current_text, tuple(current_sources)))
            total = len(chunks_for_list)
            for index, (text, sources) in enumerate(chunks_for_list):
                drafts.append(("list", text, sources, node.node_id if total > 1 else None, index, total))
            continue

        assert isinstance(node, TableNode)
        character_count += len(node.serialized.text)
        lines = node.serialized.lines
        line_offsets: dict[str, tuple[int, int]] = {}
        offset = 0
        for serialized_line in lines:
            line_offsets[serialized_line.line_id] = (offset, offset + len(serialized_line.text))
            offset += len(serialized_line.text) + 1

        def table_source(serialized_line, *, repeated: bool = False,
                         context_kind: str = "none", local_start: int = 0,
                         local_end: int | None = None) -> ChunkSource:
            start, _end = line_offsets[serialized_line.line_id]
            resolved_end = len(serialized_line.text) if local_end is None else local_end
            return ChunkSource(node.node_id, node.sources, start + local_start,
                               start + resolved_end, repeated, context_kind)

        table_parts: list[tuple[str, tuple[ChunkSource, ...]]] = []
        current_lines: list[str] = []
        current_sources: list[ChunkSource] = []
        header_line = lines[0] if lines and lines[0].kind == "header" else None
        active_merged = []
        for line in lines:
            if line.source_rows:
                first_row = min(line.source_rows)
                active_merged = [item for item in active_merged
                                 if item is line or not item.source_rows
                                 or max(item.source_rows) >= first_row]
            if line.kind == "merged" and len(line.source_rows) > 1:
                active_merged.append(line)
            source = table_source(line)
            proposed_lines = current_lines + [line.text]
            proposed = "\n".join(proposed_lines)
            if _fits(counter, proposed, max_input_tokens):
                current_lines, current_sources = proposed_lines, current_sources + [source]
                continue
            if current_lines:
                table_parts.append(("\n".join(current_lines), tuple(current_sources)))
                current_lines, current_sources = [], []
            context_lines = []
            context_sources = []
            if header_line and line is not header_line:
                context_lines.append(header_line.text)
                context_sources.append(table_source(header_line, repeated=True,
                                                    context_kind="table_header"))
            for merged_line in active_merged:
                if merged_line is line or not set(merged_line.source_rows) & set(line.source_rows):
                    continue
                context_lines.append(merged_line.text)
                context_sources.append(table_source(merged_line, repeated=True,
                                                    context_kind="merged_cell"))
            prefix = "\n".join(context_lines)
            contextual = f"{prefix}\n{line.text}" if prefix else line.text
            if _fits(counter, contextual, max_input_tokens):
                current_lines = [*context_lines, line.text]
                current_sources = [*context_sources, source]
            else:
                pieces = _split_text(line.text, counter, max_input_tokens, 0)
                for rendered, start, end, _overlap_start, _overlap_end in pieces:
                    row_label = line.text.split("：", 1)[0] if "：" in line.text else "原始范围"
                    segment_start = max(line.text.rfind("；", 0, start), line.text.rfind("：", 0, start)) + 1
                    equals = line.text.find(" = ", segment_start)
                    field = line.text[segment_start:equals].strip() if equals >= segment_start else "内容"
                    locator = f"〔表格 {node.table.table_id}，{row_label}，{field}〕\n"
                    subparts = ((rendered, 0, len(rendered), None, None),)
                    if not _fits(counter, locator + rendered, max_input_tokens):
                        subparts = _split_text(rendered, _PrefixedCounter(counter, locator),
                                               max_input_tokens, 0)
                    for subtext, substart, subend, _sub_overlap_start, _sub_overlap_end in subparts:
                        sources = [ChunkSource(node.node_id, node.sources, None, None, True,
                                               "fallback_locator")]
                        sources.append(table_source(line, local_start=start + substart,
                                                    local_end=start + subend))
                        table_parts.append((locator + subtext, tuple(sources)))
        if current_lines:
            table_parts.append(("\n".join(current_lines), tuple(current_sources)))
        total = len(table_parts)
        for index, (text, sources) in enumerate(table_parts):
            drafts.append(("table", text, sources, node.node_id if total > 1 else None, index, total))

    flush_text()
    if not drafts:
        raise ChunkingError("文档没有任何可入库正文。")
    chunks = tuple(V2DocumentChunk(
        f"{document.document_id}-v2-c{index:06d}", document.document_id, "v2", index,
        kind, text, counter.count_passage(text), sources, parent, fragment_index, fragment_count,
    ) for index, (kind, text, sources, parent, fragment_index, fragment_count) in enumerate(drafts))
    if any(item.token_count > max_input_tokens for item in chunks):
        raise ChunkingError("最终 passage 超过 token 硬上限。")
    return ChunkingResult(chunks, tuple(warnings), character_count)
