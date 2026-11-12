"""Step result caches: a hit gives back exactly what was computed; anything else is a miss."""

import json

from conftest import FakeEmbedder, make_pdf
from rag.index.builder import ingest, open_store
from rag.index.manifest import Manifest
from rag.index.step_cache import StepCache


def test_hit_returns_the_computed_value_and_refresh_recomputes(tmp_path):
    cache = StepCache(tmp_path)
    calls = []

    def compute():
        calls.append(1)
        return {"n": len(calls)}

    assert cache.get_or_compute("s", {"k": 1}, compute, dict, dict) == ({"n": 1}, False)
    assert cache.get_or_compute("s", {"k": 1}, compute, dict, dict) == ({"n": 1}, True)
    assert cache.get_or_compute("s", {"k": 2}, compute, dict, dict) == ({"n": 2}, False)
    assert StepCache(tmp_path, refresh=True).get_or_compute("s", {"k": 1}, compute, dict, dict) == ({"n": 3}, False)
    assert cache.get_or_compute("s", {"k": 1}, compute, dict, dict) == ({"n": 3}, True)   # overwritten
    assert cache.log == [("s", False), ("s", True), ("s", False), ("s", True)]


def test_an_entry_whose_stored_key_differs_is_a_miss(tmp_path):
    cache = StepCache(tmp_path)
    cache.get_or_compute("s", {"k": 1}, lambda: "first", str, str)
    [path] = (tmp_path / "s").glob("*.json")
    path.write_text(json.dumps({"key": {"k": "other"}, "value": "forged"}), encoding="utf-8")
    assert cache.get_or_compute("s", {"k": 1}, lambda: "again", str, str) == ("again", False)
    path.write_text("{broken", encoding="utf-8")
    assert cache.get_or_compute("s", {"k": 1}, lambda: "third", str, str) == ("third", False)


class CountingEmbedder(FakeEmbedder):
    def __init__(self, model="fake"):
        self.model, self.batches = model, []

    def identity(self):
        return {"model": self.model}

    def encode_passages(self, texts):
        self.batches.append(list(texts))
        return super().encode_passages(texts)


def test_vectors_are_encoded_one_by_one_and_read_back_exactly(tmp_path):
    embedder = CountingEmbedder()
    cache = StepCache(tmp_path).embeddings()
    first, hits = cache.encode_passages(embedder, ["alpha", "beta", "alpha"])
    assert hits == 0 and embedder.batches == [["alpha"], ["beta"]]
    assert first == FakeEmbedder().encode_passages(["alpha", "beta", "alpha"])
    second, hits = cache.encode_passages(embedder, ["beta", "gamma", "alpha"])
    assert hits == 2 and embedder.batches[-1] == ["gamma"]
    assert second == [first[1], FakeEmbedder().encode_passages(["gamma"])[0], first[0]]
    other, hits = cache.encode_passages(CountingEmbedder("other"), ["alpha"])
    assert hits == 0                                     # another encoder identity is another key
    again, hits = StepCache(tmp_path, refresh=True).embeddings().encode_passages(embedder, ["alpha"])
    assert hits == 0 and again == [first[0]]


def test_rebuild_reads_vectors_from_the_cache_and_stores_the_same_ones(workspace, fake_assembly):
    make_pdf(workspace.documents / "a.pdf", ["Invoice total USD 968,000.00.", "Delivery in five batches."])
    ingest(workspace, fake_assembly)
    entry = next(iter(Manifest.load(workspace.index_dir("fake") / "manifest.json").documents.values()))
    assert entry.cache["embed"] == f"0/{entry.chunk_count}"
    before = open_store(workspace, "fake")._open(create=False).get(include=["embeddings"])
    ingest(workspace, fake_assembly, force=True)
    entry = next(iter(Manifest.load(workspace.index_dir("fake") / "manifest.json").documents.values()))
    assert entry.cache["embed"] == f"{entry.chunk_count}/{entry.chunk_count}"
    after = open_store(workspace, "fake")._open(create=False).get(include=["embeddings"])
    assert before["ids"] == after["ids"]
    assert all((a == b).all() for a, b in zip(before["embeddings"], after["embeddings"]))
