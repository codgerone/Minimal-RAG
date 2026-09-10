"""从原始 PDF 建立 Slot 文本参照，并读取候选单元格 token。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import unicodedata


from experiments.table_extraction.domain.scoring.config import REFERENCE_SOURCE
from experiments.table_extraction.domain.models.scoring import CandidateToken, ReferenceWord, SlotTextReference, TokenCount
from experiments.table_extraction.domain.models.selection import TableSlot
from experiments.table_extraction.domain.models.tables import BoundingBox, TableCandidate


_NUMERIC_APOSTROPHE_PATTERN = re.compile(
    r"(?<=\d)[^\S\r\n]*['\u2018\u2019\u02bc\u0301][^\S\r\n]*(?=\d)"
)


def canonicalize_text(value: str) -> str:
    """统一 Unicode 字形，并修复数字撇号分隔符周围的噪声空格。"""
    if not isinstance(value, str):
        raise TypeError("text must be a string")
    normalized = unicodedata.normalize("NFKC", value)
    return _NUMERIC_APOSTROPHE_PATTERN.sub("'", normalized)


def normalize_token(value: str) -> str:
    """生成用于精确匹配的统一 token，保留非等价的内部结构。"""
    return canonicalize_text(value).casefold().strip()


def token_counts(counter: Counter[str]) -> list[TokenCount]:
    """把 token 多重集按 token 升序转换为可审计列表。"""
    return [TokenCount(token, counter[token]) for token in sorted(counter) if counter[token] > 0]


def tokenize_candidate(candidate: TableCandidate) -> tuple[list[CandidateToken], list[str]]:
    """逐物理单元格切分候选文本，不跨 token 或单元格拼接。"""
    if "candidate_text_unreadable" in candidate.warnings:
        return [], ["candidate_text_unreadable"]
    if any(cell.text is not None and not isinstance(cell.text, str) for cell in candidate.cells):
        return [], ["candidate_text_unreadable"]

    tokens: list[CandidateToken] = []
    for cell in candidate.cells:
        if cell.text is None:
            continue
        canonical_text = canonicalize_text(cell.text)
        fragments = re.split(r"\s+", canonical_text.strip()) if canonical_text.strip() else []
        for token_index, raw_text in enumerate(fragments):
            normalized = normalize_token(raw_text)
            if normalized:
                tokens.append(CandidateToken(cell.cell_id, token_index, raw_text, normalized))
    if candidate.unplaced_text:
        for index, fragment in enumerate(re.split(r'\s+', canonicalize_text(candidate.unplaced_text).strip())):
            normalized = normalize_token(fragment)
            if normalized:
                tokens.append(CandidateToken(None, index, fragment, normalized))
    return tokens, []


def _word_bbox(raw_word: tuple) -> BoundingBox:
    """读取 PyMuPDF word 元组中的公共坐标 bbox。"""
    return BoundingBox(*(float(raw_word[index]) for index in range(4)))


def _center_inside(word_bbox: BoundingBox, slot_bbox: BoundingBox) -> bool:
    """判断 word bbox 中心点是否落在 Slot 闭区间内。"""
    center_x = (word_bbox.x0 + word_bbox.x1) / 2
    center_y = (word_bbox.y0 + word_bbox.y1) / 2
    return (
        slot_bbox.x0 <= center_x <= slot_bbox.x1
        and slot_bbox.y0 <= center_y <= slot_bbox.y1
    )


def build_slot_text_references(
    page_words: dict[int, list[tuple]],
    slots: list[TableSlot],
) -> tuple[dict[str, SlotTextReference], list[str]]:
    """一次打开 PDF，为全部 eligible Slot 建立原生 word 参照。"""
    references: dict[str, SlotTextReference] = {}
    warnings: list[str] = []
    for slot in slots:
        if slot.status != "eligible" or slot.page_number is None or slot.bbox is None:
            raise ValueError(f"scoring slot is not eligible: {slot.slot_id}")
        if slot.page_number not in page_words:
            raise ValueError(f"slot page is outside source PDF: {slot.slot_id}")

        raw_words = page_words[slot.page_number]
        selected: list[tuple[tuple, BoundingBox]] = []
        for raw_word in raw_words:
            if not isinstance(raw_word, (tuple, list)) or len(raw_word) < 8:
                warnings.append(f"{slot.slot_id}:invalid_pymupdf_word_record")
                continue
            try:
                bbox = _word_bbox(raw_word)
            except (TypeError, ValueError):
                warnings.append(f"{slot.slot_id}:invalid_pymupdf_word_bbox")
                continue
            if _center_inside(bbox, slot.bbox):
                selected.append((tuple(raw_word), bbox))

        selected.sort(key=lambda item: (
            int(item[0][5]), int(item[0][6]), int(item[0][7]),
            item[1].y0, item[1].x0,
        ))
        words: list[ReferenceWord] = []
        for raw_word, bbox in selected:
            raw_text = str(raw_word[4])
            normalized = normalize_token(raw_text)
            if not normalized:
                continue
            words.append(ReferenceWord(
                word_id=f"{slot.slot_id}_word_{len(words) + 1:04d}",
                page_number=slot.page_number,
                bbox=bbox,
                raw_text=raw_text,
                normalized_token=normalized,
                block_index=int(raw_word[5]),
                line_index=int(raw_word[6]),
                word_index=int(raw_word[7]),
                is_critical=any(character.isdecimal() for character in normalized),
            ))

        all_counter = Counter(word.normalized_token for word in words)
        critical_counter = Counter(word.normalized_token for word in words if word.is_critical)
        references[slot.slot_id] = SlotTextReference(
            slot_id=slot.slot_id,
            source=REFERENCE_SOURCE,
            page_number=slot.page_number,
            slot_bbox=slot.bbox,
            words=words,
            token_counts=token_counts(all_counter),
            critical_token_counts=token_counts(critical_counter),
        )
    return references, warnings
