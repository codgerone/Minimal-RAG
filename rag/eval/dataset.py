"""Load the human-labelled questions and evidence from eval/ground-truth/."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Excerpt:
    excerpt_id: str
    document_id: str
    document_name: str
    pages: tuple[int, ...]
    text: str


@dataclass(frozen=True)
class EvidenceGroup:
    """One independent fact the answer needs; its excerpts must all be retrieved."""
    group_id: str
    excerpts: tuple[Excerpt, ...]


@dataclass(frozen=True)
class Case:
    case_id: str
    document_id: str
    document_name: str
    question: str
    reference_answer: str
    answerable: bool
    groups: tuple[EvidenceGroup, ...]


@dataclass(frozen=True)
class Dataset:
    version: str
    fingerprint: str        # sha256 over the ground-truth files, to tell datasets apart
    cases: tuple[Case, ...]
    document_hashes: dict[str, str]   # document_id → PDF hash the evidence was labelled on


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
            groups = tuple(EvidenceGroup(group["evidence_group_id"], tuple(
                Excerpt(x["excerpt_id"], x["document_id"], x["document_name"],
                        tuple(x["page_numbers"]), x["text"]) for x in group["excerpts"]))
                for group in case["evidence_groups"])
            cases.append(Case(case["case_id"], data["document_id"], data["document_name"],
                              case["question"].strip(), case["reference_answer"],
                              bool(case["answerable"]), groups))
    return Dataset(manifest.get("dataset_version", "?"), digest.hexdigest(), tuple(cases), hashes)
