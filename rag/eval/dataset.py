"""Load the human-labelled questions and evidence from eval/ground-truth/."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Box = tuple[float, float, float, float]
AnchorPiece = tuple[str, tuple[tuple[int, Box], ...]]    # piece text, its words (page, box)


@dataclass(frozen=True)
class Excerpt:
    excerpt_id: str
    document_id: str
    document_name: str
    pages: tuple[int, ...]
    text: str
    table_header: bool = False   # the excerpt is table header text that itself answers the question
    locate_hint: str | None = None   # header text of the excerpt's column; only for locating it in the PDF
    anchor: tuple[AnchorPiece, ...] | None = None   # confirmed PDF position; None unless anchor_state == "ok"
    anchor_state: Literal["ok", "missing", "stale"] = "missing"   # stale: text edited after locating


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
            page, *box = value.split()
            words.append((int(page), tuple(float(v) for v in box)))
        pieces.append((piece["text"], tuple(words)))
    return tuple(pieces), "ok"


def _excerpt(x: dict) -> Excerpt:
    anchor, state = _anchor(x)
    return Excerpt(x["excerpt_id"], x["document_id"], x["document_name"], tuple(x["page_numbers"]),
                   x["text"], bool(x.get("table_header", False)), x.get("locate_hint"), anchor, state)


def load_dataset(folder: Path) -> Dataset:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    cases: list[Case] = []
    hashes: dict[str, str] = {}
    for path in sorted(folder.glob("*.json"), key=lambda p: p.name):
        if path.name == "manifest.json":
            continue
        raw = path.read_bytes()
        digest.update(path.name.encode("utf-8") + b"\0" + raw)
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
                              bool(case["answerable"]), excerpts, groups))
    return Dataset(manifest.get("dataset_version", "?"), digest.hexdigest(), tuple(cases), hashes)
