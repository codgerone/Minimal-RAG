"""Decide which chunks of the current index count as evidence for each evidence group.

1. Confirmed mappings (eval/mappings.json) identify chunks by document + text hash, so
   they keep working across rebuilds as long as the chunk text is unchanged.
2. Groups without a usable confirmed mapping (e.g. after a chunking change) fall back to
   automatic text matching, and are flagged so the user can check and confirm them.
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
class GroupEvidence:
    group_id: str
    mode: Mode
    acceptable_sets: tuple[frozenset[str], ...]    # chunk IDs in the current index
    auto_coverage: dict[str, float]                 # chunk_id → best share of an excerpt (auto mode)


class Mappings:
    def __init__(self, path: Path):
        self.path = path
        self.data = read_json(path) if path.is_file() else {
            "schema": "evidence_mappings_v1", "about": "", "groups": {}}

    def sets(self, case_id: str, group_id: str) -> list[list[str]]:
        return self.data["groups"].get(f"{case_id}/{group_id}", [])

    def add(self, case_id: str, group_id: str, keys: list[str]) -> None:
        sets = self.data["groups"].setdefault(f"{case_id}/{group_id}", [])
        entry = sorted(keys)
        if entry not in sets:
            sets.append(entry)

    def save(self) -> None:
        self.data["groups"] = dict(sorted(self.data["groups"].items()))
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


def judge_case(case: Case, chunks: list[StoredChunk], mappings: Mappings) -> list[GroupEvidence]:
    by_key = {chunk_key(c.document_id, c.text): c.chunk_id for c in chunks}
    result: list[GroupEvidence] = []
    for group in case.groups:
        confirmed = [frozenset(by_key[k] for k in keys)
                     for keys in mappings.sets(case.case_id, group.group_id)
                     if keys and all(k in by_key for k in keys)]
        if confirmed:
            result.append(GroupEvidence(group.group_id, "confirmed",
                                        tuple(dict.fromkeys(confirmed)), {}))
            continue
        per_excerpt = []
        coverage: dict[str, float] = {}
        for excerpt in group.excerpts:
            sets, shares = _excerpt_sets(excerpt, chunks)
            per_excerpt.append(sets)
            for cid, share in shares.items():
                coverage[cid] = max(coverage.get(cid, 0.0), share)
        if not all(per_excerpt):
            result.append(GroupEvidence(group.group_id, "unmapped", (), coverage))
            continue
        combined: list[frozenset[str]] = []
        for parts in itertools.product(*per_excerpt):
            union = frozenset().union(*parts)
            if not any(existing <= union for existing in combined):
                combined = [c for c in combined if not union <= c] + [union]
            if len(combined) >= MAX_GROUP_SETS:
                break
        result.append(GroupEvidence(group.group_id, "auto", tuple(combined), coverage))
    return result
