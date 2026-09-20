"""Strict-health vector retrieval without generation."""

from __future__ import annotations

import json

from rag.config import SelectedPipelineSettings, Settings
from rag.document_registry import discover_documents, resolve_document_selector
from rag.embeddings import E5Embedder
from rag.errors import IndexNotReadyError, RagError
from rag.manifest import load_manifest, validate_index
from rag.models import RetrievalHit, V2RetrievalHit
from rag.vector_store import ChromaVectorStore


def query_with_stable_ties(
    vector_store: ChromaVectorStore,
    query_embedding: list[float],
    top_k: int,
    document_id: str | None = None,
) -> list[RetrievalHit | V2RetrievalHit]:
    """Return strict top-k while deterministically resolving an equal-distance boundary."""
    available = (
        vector_store.count_document(document_id)
        if document_id is not None
        else vector_store.count_all()
    )
    if available == 0:
        return []
    candidate_count = min(available, top_k + 1)
    while True:
        hits = vector_store.query(
            query_embedding, candidate_count, document_id=document_id
        )
        if len({item.chunk_id for item in hits}) != len(hits):
            raise RagError("向量检索返回了重复 chunk ID。")
        ranked = sorted(hits, key=lambda item: (item.distance, item.chunk_id))
        if len(ranked) <= top_k:
            return ranked
        if candidate_count >= available or ranked[top_k - 1].distance != ranked[-1].distance:
            return ranked[:top_k]
        candidate_count = min(
            available, max(candidate_count + 1, candidate_count * 2)
        )


class Retriever:
    def __init__(
        self,
        settings: Settings,
        embedder: E5Embedder,
        vector_store: ChromaVectorStore,
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self.vector_store = vector_store

    def search(
        self,
        question: str,
        top_k: int | None = None,
        document_selector: str | None = None,
    ) -> list[RetrievalHit | V2RetrievalHit]:
        stripped = question.strip()
        if not stripped:
            raise RagError("检索问题不能为空。")
        resolved_top_k = self.settings.top_k if top_k is None else top_k
        if resolved_top_k <= 0:
            raise RagError("top_k 必须是正整数。")

        discovered = discover_documents(self.settings.documents_dir)
        legacy_selected_v1 = False
        if isinstance(self.settings, SelectedPipelineSettings) and self.settings.identity.pipeline_id == "v1" and self.settings.manifest_path.exists():
            try:
                legacy_selected_v1 = json.loads(self.settings.manifest_path.read_text(encoding="utf-8")).get("schema_version") != "pipeline_manifest_v2"
            except Exception:
                legacy_selected_v1 = False
        if isinstance(self.settings, SelectedPipelineSettings) and not legacy_selected_v1:
            from rag.cli_readiness import inspect_index_for_cli
            health = inspect_index_for_cli(self.settings, self.vector_store).health
        else:
            manifest = load_manifest(self.settings.manifest_path)
            health = validate_index(self.settings, discovered, manifest, self.vector_store)
        if not health.usable:
            messages = "\n".join(f"- {issue.message}" for issue in health.issues)
            remediations = sorted({issue.remediation for issue in health.issues})
            raise IndexNotReadyError(
                f"索引未就绪：\n{messages}",
                "；".join(remediations),
            )

        document_id = None
        if document_selector is not None:
            document_id = resolve_document_selector(
                document_selector, discovered
            ).document_id
        query_embedding = self.embedder.embed_query(stripped)
        return query_with_stable_ties(
            self.vector_store, query_embedding, resolved_top_k, document_id=document_id
        )
