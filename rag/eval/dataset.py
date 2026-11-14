"""Load the human-labelled questions and evidence from eval/ground-truth/."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Box = tuple[float, float, float, float]
# A word of a piece: (page, box) on a PDF, or (address, start, end) in a Word file.
AnchorWord = tuple[int, Box] | tuple[str, int, int]
AnchorPiece = tuple[str, tuple[AnchorWord, ...]]    # piece text as the file reads it, its words


@dataclass(frozen=True)
class Excerpt:
    excerpt_id: str
    document_id: str
    document_name: str
    pages: tuple[int, ...]       # PDF pages the excerpt is on; empty for Word
    text: str
    table_header: bool = False   # the excerpt is table header text that itself answers the question
    locate_hint: str | None = None   # header text of the excerpt's column; only for locating it in the PDF
    anchor: tuple[AnchorPiece, ...] | None = None   # confirmed PDF position; None unless anchor_state == "ok"
    anchor_state: Literal["ok", "missing", "stale"] = "missing"   # stale: text edited after locating
    locations: tuple[str, ...] = ()   # Word: location labels ("第 N 块", "表 N" …), written with the anchor


@dataclass(frozen=True)
class Scheme:
    """Excerpts that together suffice for the information item; all must be retrieved."""
    scheme_id: str
    excerpt_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceGroup:
    """One information item the question asks for; covered when any one scheme is retrieved."""
    group_id: str
    information_item: str
    schemes: tuple[Scheme, ...]
    note: str


@dataclass(frozen=True)
class Case:
    case_id: str
    document_id: str
    document_name: str
    question: str
    reference_answer: str
    answerable: bool
    excerpts: tuple[Excerpt, ...]
    groups: tuple[EvidenceGroup, ...]
    same_content_as: str | None = None   # the same question on another file of the same content


@dataclass(frozen=True)
class Dataset:
    version: str
    fingerprint: str        # sha256 over the ground-truth files, to tell datasets apart
    cases: tuple[Case, ...]
    document_hashes: dict[str, str]   # document_id → PDF hash the evidence was labelled on


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _anchor(raw: dict) -> tuple[tuple[AnchorPiece, ...] | None, str]:
    stored = raw.get("anchor")
    if not stored:
        return None, "missing"
    if stored["text_sha256"] != text_sha256(raw["text"]):
        return None, "stale"
    pieces = []
    for piece in stored["pieces"]:
        words = []
        for value in piece["words"]:
            first, *rest = value.split()
            if "/" in first:             # Word: "<address> <start> <end>"
                words.append((first, int(rest[0]), int(rest[1])))
            else:
                words.append((int(first), tuple(float(v) for v in rest)))
        # An approximate match on a scanned page also records what OCR read; chunk text holds that.
        pieces.append((piece.get("ocr_text", piece["text"]), tuple(words)))
    return tuple(pieces), "ok"


def _excerpt(x: dict) -> Excerpt:
    anchor, state = _anchor(x)
    return Excerpt(x["excerpt_id"], x["document_id"], x["document_name"], tuple(x.get("page_numbers", ())),
                   x["text"], bool(x.get("table_header", False)), x.get("locate_hint"), anchor, state,
                   tuple(x.get("locations", ())))


def load_dataset(folder: Path) -> Dataset:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    cases: list[Case] = []
    hashes: dict[str, str] = {}
    for path in sorted(folder.glob("*.json"), key=lambda p: p.name):
        if path.name == "manifest.json":
            continue
        raw = path.read_bytes()
        # LF bytes, so a checkout that rewrites line endings (core.autocrlf) keeps the fingerprint.
        digest.update(path.name.encode("utf-8") + b"\0" + raw.replace(b"\r\n", b"\n"))
        data = json.loads(raw)
        hashes[data["document_id"]] = data["file_hash"]
        for case in data["cases"]:
            excerpts = tuple(_excerpt(x) for x in case["excerpts"])
            known = {x.excerpt_id for x in excerpts}
            groups = tuple(EvidenceGroup(
                group["evidence_group_id"], group["information_item"],
                tuple(Scheme(s["scheme_id"], tuple(s["excerpt_ids"])) for s in group["schemes"]),
                group.get("note", "")) for group in case["evidence_groups"])
            unknown = {x for g in groups for s in g.schemes for x in s.excerpt_ids} - known
            if unknown:
                raise ValueError(f"{path.name} {case['case_id']}: 方案引用了不存在的 excerpt {sorted(unknown)}")
            cases.append(Case(case["case_id"], data["document_id"], data["document_name"],
                              case["question"].strip(), case["reference_answer"],
                              bool(case["answerable"]), excerpts, groups, case.get("same_content_as")))
    return Dataset(manifest.get("dataset_version", "?"), digest.hexdigest(), tuple(cases), hashes)
