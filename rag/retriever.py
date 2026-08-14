"""Strict-health vector retrieval without generation."""

from __future__ import annotations

from rag.config import Settings
from rag.document_registry import discover_documents, resolve_document_selector
from rag.embeddings import E5Embedder
from rag.errors import IndexNotReadyError, RagError
from rag.manifest import load_manifest, validate_index
from rag.models import RetrievalHit
from rag.vector_store import ChromaVectorStore


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
    ) -> list[RetrievalHit]:
        stripped = question.strip()
        if not stripped:
            raise RagError("检索问题不能为空。")
        resolved_top_k = self.settings.top_k if top_k is None else top_k
        if resolved_top_k <= 0:
            raise RagError("top_k 必须是正整数。")

        discovered = discover_documents(self.settings.documents_dir)
        manifest = load_manifest(self.settings.manifest_path)
        health = validate_index(
            self.settings, discovered, manifest, self.vector_store
        )
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
        return self.vector_store.query(
            query_embedding, resolved_top_k, document_id=document_id
        )

