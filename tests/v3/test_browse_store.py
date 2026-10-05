import hashlib

from rag.v3.adapters.chroma_store import ChromaV3Store
from rag.v3.adapters.vector_codec import encode_metadata
from rag.v3.application.assembly import builtin_configuration, index_identity
from rag.v3.contracts.documents import ChunkSource, PageSpan
from rag.v3.contracts.storage import ChunkMetadata


def test_browse_store_never_requests_embedding(tmp_path):
    index = index_identity(builtin_configuration("plain_text"))
    text = "body"
    source = ChunkSource("node", (PageSpan(1, None, "p1"),), 0, 4, False, "none")
    metadata = ChunkMetadata("chunk_metadata_v3", "doc", "a.pdf", "a.pdf",
                             "build", "a" * 64, index.build_fingerprint,
                             hashlib.sha256(text.encode()).hexdigest(), 0, "text", None,
                             (1,), (source,), None, 0, 1)

    class Collection:
        def get(self, **kwargs):
            assert kwargs["include"] == ["documents", "metadatas"]
            if kwargs["offset"]:
                return {"ids": [], "documents": [], "metadatas": []}
            return {"ids": ["doc-p1-c00"], "documents": [text],
                    "metadatas": [encode_metadata(metadata)]}

    store = ChromaV3Store(tmp_path)
    store._collection = lambda *_args, **_kwargs: Collection()
    result = store.list_text_metadata(index)
    assert len(result) == 1
    assert result[0].metadata == metadata
    assert not hasattr(result[0], "embedding")
