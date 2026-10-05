"""Readback comparison shared by publication and vector persistence."""

from __future__ import annotations

import math

from rag.v3.contracts.storage import VectorRecord, VectorSnapshot


VECTOR_ABSOLUTE_TOLERANCE = 1e-6


def vectors_equal(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
    return len(first) == len(second) and all(
        math.isfinite(left) and math.isfinite(right)
        and math.isclose(left, right, rel_tol=0.0, abs_tol=VECTOR_ABSOLUTE_TOLERANCE)
        for left, right in zip(first, second)
    )


def records_equal(expected: tuple[VectorRecord, ...], actual: tuple[VectorRecord, ...]) -> bool:
    return len(expected) == len(actual) and all(
        (left.chunk_id, left.document_id, left.build_id, left.file_hash,
         left.text, left.metadata) ==
        (right.chunk_id, right.document_id, right.build_id, right.file_hash,
         right.text, right.metadata)
        and vectors_equal(left.embedding, right.embedding)
        for left, right in zip(expected, actual)
    )


def snapshots_equal(expected: VectorSnapshot, actual: VectorSnapshot) -> bool:
    return (expected.index_identity == actual.index_identity
            and expected.collection_existed == actual.collection_existed
            and expected.document_scope == actual.document_scope
            and expected.ids == actual.ids and expected.documents == actual.documents
            and expected.metadatas == actual.metadatas
            and len(expected.embeddings) == len(actual.embeddings)
            and all(vectors_equal(left, right) for left, right in zip(
                expected.embeddings, actual.embeddings)))
