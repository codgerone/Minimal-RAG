"""Decide which chunks of the current index count as evidence for each evidence group.

1. Confirmed mappings (eval/mappings.json) give, per excerpt, the chunks that contain it. Chunks are
   identified by document + text hash, so they keep working across rebuilds as long as the chunk
   text is unchanged; an entry also records the excerpt's text hash and stops applying if the
   excerpt is edited.
2. Excerpts without a usable confirmed mapping (e.g. after a chunking change) fall back to
   automatic text matching, and are flagged so the user can check and confirm them.
3. A group is covered by any of its schemes; a scheme needs every one of its excerpts.
"""

from __future__ import annotations

import hashlib
import itertools
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

from rag.eval.dataset import Case, Excerpt
from rag.index.store import StoredChunk
from rag.jsonio import read_json, write_json

# Automatic matching parameters (see docs/rules/evaluation.md).
MIN_BLOCK_CHARS = 6        # shortest common run of characters that counts as a match
MIN_CHUNK_SHARE = 0.2      # a chunk must cover at least this share of an excerpt to take part
COVERAGE_REQUIRED = 0.8    # chunks together must cover this share of the excerpt
MAX_SET_SIZE = 3           # largest chunk combination considered for one excerpt
MAX_GROUP_SETS = 20

Mode = Literal["confirmed", "auto", "unmapped"]


def chunk_key(document_id: str, text: str) -> str:
    return f"{document_id}/{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _normalized(text: str) -> str:
    return "".join(re.findall(r"\w", unicodedata.normalize("NFKC", text).casefold()))


def _covered(excerpt: str, chunk: str) -> set[int]:
    matcher = SequenceMatcher(None, excerpt, chunk, autojunk=False)
    positions: set[int] = set()
    for block in matcher.get_matching_blocks():
        if block.size >= MIN_BLOCK_CHARS:
            positions.update(range(block.a, block.a + block.size))
    return positions


@dataclass(frozen=True)
class ExcerptEvidence:
    excerpt_id: str
    mode: Mode
    acceptable_sets: tuple[frozenset[str], ...]    # chunk IDs in the current index
    auto_coverage: dict[str, float]                 # chunk_id → share of the excerpt (auto mode)


@dataclass(frozen=True)
class SchemeEvidence:
    scheme_id: str
    acceptable_sets: tuple[frozenset[str], ...]    # empty when an excerpt of the scheme is unmapped


@dataclass(frozen=True)
class GroupEvidence:
    group_id: str
    mode: Mode            # confirmed: every excerpt confirmed; unmapped: no scheme usable; else auto
    acceptable_sets: tuple[frozenset[str], ...]    # union over schemes
    schemes: tuple[SchemeEvidence, ...] = ()


@dataclass(frozen=True)
class CaseEvidence:
    excerpts: dict[str, ExcerptEvidence]
    groups: list[GroupEvidence]


class Mappings:
    def __init__(self, path: Path):
        self.path = path
        self.data = read_json(path) if path.is_file() else {
            "schema": "evidence_mappings_v2", "about": "", "excerpts": {}}

    def sets(self, case_id: str, excerpt: Excerpt) -> list[list[str]]:
        entry = self.data["excerpts"].get(f"{case_id}/{excerpt.excerpt_id}")
        if not entry or entry["text_sha256"] != text_hash(excerpt.text):
            return []
        return entry["chunk_sets"]

    def add(self, case_id: str, excerpt: Excerpt, keys: list[str]) -> None:
        ref = f"{case_id}/{excerpt.excerpt_id}"
        entry = self.data["excerpts"].get(ref)
        if not entry or entry["text_sha256"] != text_hash(excerpt.text):
            entry = self.data["excerpts"][ref] = {"text_sha256": text_hash(excerpt.text), "chunk_sets": []}
        chunk_set = sorted(keys)
        if chunk_set not in entry["chunk_sets"]:
            entry["chunk_sets"].append(chunk_set)

    def save(self) -> None:
        self.data["excerpts"] = dict(sorted(self.data["excerpts"].items()))
        write_json(self.path, self.data)


def _excerpt_sets(excerpt: Excerpt, chunks: list[StoredChunk]) -> tuple[list[frozenset[str]], dict[str, float]]:
    target = _normalized(excerpt.text)
    if not target:
        return [], {}
    candidates: dict[str, set[int]] = {}
    for chunk in chunks:
        if chunk.document_id != excerpt.document_id or not set(chunk.pages) & set(excerpt.pages):
            continue
        covered = _covered(target, _normalized(chunk.text))
        if len(covered) / len(target) >= MIN_CHUNK_SHARE:
            candidates[chunk.chunk_id] = covered
    shares = {cid: len(pos) / len(target) for cid, pos in candidates.items()}
    found: list[frozenset[str]] = []
    ordered = sorted(candidates, key=lambda cid: -shares[cid])[:12]
    for size in range(1, MAX_SET_SIZE + 1):
        for combo in itertools.combinations(ordered, size):
            if any(existing <= set(combo) for existing in found):
                continue
            union = set().union(*(candidates[cid] for cid in combo))
            if len(union) / len(target) >= COVERAGE_REQUIRED:
                found.append(frozenset(combo))
    return found, shares


def _judge_excerpt(case_id: str, excerpt: Excerpt, chunks: list[StoredChunk],
                   by_key: dict[str, str], mappings: Mappings) -> ExcerptEvidence:
    confirmed = [frozenset(by_key[k] for k in keys) for keys in mappings.sets(case_id, excerpt)
                 if keys and all(k in by_key for k in keys)]
    if confirmed:
        return ExcerptEvidence(excerpt.excerpt_id, "confirmed", tuple(dict.fromkeys(confirmed)), {})
    sets, shares = _excerpt_sets(excerpt, chunks)
    return ExcerptEvidence(excerpt.excerpt_id, "auto" if sets else "unmapped", tuple(sets), shares)


def _minimal(sets: list[frozenset[str]]) -> tuple[frozenset[str], ...]:
    kept: list[frozenset[str]] = []
    for candidate in sorted(dict.fromkeys(sets), key=len):
        if not any(existing <= candidate for existing in kept):
            kept.append(candidate)
        if len(kept) >= MAX_GROUP_SETS:
            break
    return tuple(kept)


def judge_case(case: Case, chunks: list[StoredChunk], mappings: Mappings) -> CaseEvidence:
    by_key = {chunk_key(c.document_id, c.text): c.chunk_id for c in chunks}
    excerpts = {x.excerpt_id: _judge_excerpt(case.case_id, x, chunks, by_key, mappings)
                for x in case.excerpts}
    groups: list[GroupEvidence] = []
    for group in case.groups:
        schemes = []
        for scheme in group.schemes:
            parts = [excerpts[x].acceptable_sets for x in scheme.excerpt_ids]
            unions = [frozenset().union(*combo) for combo in itertools.product(*parts)] if all(parts) else []
            schemes.append(SchemeEvidence(scheme.scheme_id, _minimal(unions)))
        modes = {excerpts[x].mode for scheme in group.schemes for x in scheme.excerpt_ids}
        union = _minimal([s for scheme in schemes for s in scheme.acceptable_sets])
        mode: Mode = ("unmapped" if not union else "confirmed" if modes == {"confirmed"} else "auto")
        groups.append(GroupEvidence(group.group_id, mode, union, tuple(schemes)))
    return CaseEvidence(excerpts, groups)
