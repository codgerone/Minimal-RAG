import math
from collections import Counter
from dataclasses import replace

import pytest

from conftest import make_pdf
from rag.config import ConfigError, load_config
from rag.index.analyzer import Analyzer, detect_language, words
from rag.index.builder import current_status, ingest, open_lexical
from rag.index.lexical import B, K1, LexicalChunk, LexicalStore, idf
from rag.index.store import StoredChunk
from rag.query.retriever import RRF_K, HybridRetriever
from rag.registry import assemble
from test_config import write


# --- analyzer ---------------------------------------------------------------

def test_words_keep_connected_runs_whole_and_split_their_parts():
    assert words("Modelo HXE34K-S1, total S/ 1,234.56") == [
        "modelo", "hxe34k-s1", "hxe34k", "s1", "total", "s", "1,234.56", "1", "234", "56"]


def test_words_fold_case_width_and_accents_and_split_han_characters():
    assert words("Garantía ＩＴＥＭ") == ["garantia", "item"]
    assert words("采购合同CONTRATO N°105") == ["采", "购", "合", "同", "contrato", "n", "105"]


def test_stopwords_of_both_languages_are_dropped_before_stemming():
    analyzer = Analyzer(stopwords=True, stemming=False)
    assert analyzer.terms("Penalidad por Mora of the order", "spanish") == Counter(
        {"penalidad": 1, "mora": 1, "order": 1})
    assert Analyzer(stopwords=False).terms("por la", "spanish") == Counter({"por": 1, "la": 1})


def test_stemming_uses_the_given_language_and_leaves_codes_alone():
    analyzer = Analyzer(stopwords=True, stemming=True)
    assert analyzer.terms("garantías entregas HXE34K", "spanish") == Counter(
        {"garanti": 1, "entreg": 1, "hxe34k": 1})
    assert analyzer.terms("deliveries", "english") == Counter({"deliveri": 1})
    query = analyzer.query_terms("entregas")
    assert query["spanish"] == {"entreg"} and query["english"] == {"entrega"}


def test_document_language_is_decided_by_stopword_counts():
    assert detect_language(["El plazo de entrega de los bienes es de 170 días"]) == "spanish"
    assert detect_language(["The delivery of the goods is due in 170 days"]) == "english"
    assert detect_language(["HXE34K 2,000"]) == "english"          # tie → English


# --- BM25 index ---------------------------------------------------------------

def chunk(chunk_id, language, *terms):
    return LexicalChunk(chunk_id, language, Counter(terms))


def test_bm25_scores_follow_the_formula_with_whole_index_statistics(tmp_path):
    store = LexicalStore(tmp_path / "bm25.sqlite")
    store.replace_document("d1", [chunk("d1-a", "english", "mora", "mora", "x"),
                                  chunk("d1-b", "english", "x", "y")])
    store.replace_document("d2", [chunk("d2-a", "english", "mora", "z", "z", "z")])
    terms = {"spanish": frozenset({"mora"}), "english": frozenset({"mora"})}
    average = (3 + 2 + 4) / 3
    expected = idf(3, 2) * 2 * (K1 + 1) / (2 + K1 * (1 - B + B * 3 / average))
    scores = store.score(terms)
    assert set(scores) == {"d1-a", "d2-a"}
    assert scores["d1-a"] == pytest.approx(expected)
    assert idf(3, 2) == pytest.approx(math.log(1 + 1.5 / 2.5))
    # The scope filters chunks but does not change the statistics.
    assert store.score(terms, ["d1"]) == {"d1-a": pytest.approx(expected)}


def test_each_chunk_is_scored_with_terms_in_its_own_language(tmp_path):
    store = LexicalStore(tmp_path / "bm25.sqlite")
    store.replace_document("es", [chunk("es-a", "spanish", "entreg")])
    store.replace_document("en", [chunk("en-a", "english", "entreg")])
    scores = store.score({"spanish": frozenset({"entreg"}), "english": frozenset({"entrega"})})
    assert set(scores) == {"es-a"}


def test_replacing_and_deleting_a_document_keeps_other_documents(tmp_path):
    store = LexicalStore(tmp_path / "bm25.sqlite")
    store.replace_document("d1", [chunk("d1-a", "english", "a")])
    store.replace_document("d2", [chunk("d2-a", "english", "a")])
    store.replace_document("d1", [chunk("d1-b", "english", "b")])
    assert store.ids_by_document() == {"d1": {"d1-b"}, "d2": {"d2-a"}}
    store.delete_document("d2")
    assert store.ids_by_document() == {"d1": {"d1-b"}}
    store.clear()
    assert store.ids_by_document() == {}


# --- hybrid retrieval ----------------------------------------------------------

def stored(chunk_id):
    return StoredChunk(chunk_id, "doc", "a.pdf", "a.pdf", 0, "text", (1,), None, chunk_id)


class FakeSemantic:
    def __init__(self, order):
        self.order = order

    def rank_all(self, question, document_ids=None):
        return [(stored(cid), 0.9 - i / 10) for i, cid in enumerate(self.order)]


class FakeBM25:
    def __init__(self, ranked):
        self.ranked = ranked

    def rank_all(self, question, document_ids=None):
        return self.ranked


def test_reciprocal_rank_fusion_adds_both_rankings_and_breaks_ties_by_chunk_id():
    semantic = FakeSemantic(["c1", "c2", "c3", "c4"])
    lexical = FakeBM25([("c3", 7.0), ("c4", 2.0)])
    hits = HybridRetriever(semantic, lexical).retrieve("q", 3)
    assert [h.chunk.chunk_id for h in hits] == ["c3", "c4", "c1"]
    assert hits[0].score == pytest.approx(1 / (RRF_K + 3) + 1 / (RRF_K + 1))
    assert (hits[0].semantic_rank, hits[0].lexical_rank, hits[0].bm25) == (3, 1, 7.0)
    assert hits[2].lexical_rank is None and hits[2].bm25 is None
    # Equal fused scores are ordered by chunk_id.
    tied = HybridRetriever(FakeSemantic(["c2", "c1"]), FakeBM25([("c1", 1.0), ("c2", 1.0)])).retrieve("q", 2)
    assert [h.chunk.chunk_id for h in tied] == ["c1", "c2"]


# --- configuration and index consistency ----------------------------------------

def test_lexical_section_enters_the_fingerprint_only_when_present(tmp_path):
    plain = assemble(load_config(write(tmp_path)))
    assert plain.lexical is None and "lexical" not in plain.config.build_settings()
    with_bm25 = assemble(load_config(write(tmp_path, extra="[lexical]\n")))
    assert with_bm25.config.lexical == {"stopwords": True, "stemming": True}
    unstemmed = assemble(load_config(write(tmp_path, extra="[lexical]\nstemming = false\n")))
    assert len({plain.fingerprint(), with_bm25.fingerprint(), unstemmed.fingerprint()}) == 3


@pytest.mark.parametrize("extra, retriever, message", [
    ("", "bm25", "需要 BM25 索引"),
    ("", "hybrid", "需要 BM25 索引"),
    ("[lexical]\nstem = true\n", "hybrid", "只接受 stopwords 和 stemming"),
    ("[lexical]\nstemming = 1\n", "hybrid", "true 或 false"),
])
def test_invalid_lexical_settings_are_rejected(tmp_path, extra, retriever, message):
    path = write(tmp_path, extra=extra)
    path.write_text(path.read_text(encoding="utf-8").replace('use = "semantic"', f'use = "{retriever}"'),
                    encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        assemble(load_config(path))


@pytest.fixture
def lexical_assembly(fake_assembly):
    config = replace(fake_assembly.config, retriever=replace(fake_assembly.config.retriever, use="hybrid"),
                     lexical={"stopwords": True, "stemming": True})
    return replace(fake_assembly, config=config, lexical=Analyzer())


def test_ingest_builds_the_bm25_index_and_status_checks_it(workspace, lexical_assembly):
    make_pdf(workspace.documents / "a.pdf", ["Invoice total USD 968,000.00 for model HXF300."])
    ingest(workspace, lexical_assembly)
    store = open_lexical(workspace, lexical_assembly)
    document_id = current_status(workspace, lexical_assembly).documents[0].source.document_id
    assert store.score(lexical_assembly.lexical.query_terms("hxf300"))
    store.delete_document(document_id)
    assert current_status(workspace, lexical_assembly).documents[0].state == "incomplete"
    assert ingest(workspace, lexical_assembly)[0].action == "updated"
    assert current_status(workspace, lexical_assembly).documents[0].state == "current"


def test_full_rebuild_and_prune_keep_the_bm25_index_in_step(workspace, lexical_assembly):
    make_pdf(workspace.documents / "a.pdf", ["Payment 30 days after shipment."])
    make_pdf(workspace.documents / "b.pdf", ["Delivery in five batches."])
    ingest(workspace, lexical_assembly, force=True)
    store = open_lexical(workspace, lexical_assembly)
    assert len(store.ids_by_document()) == 2
    (workspace.documents / "b.pdf").unlink()
    ingest(workspace, lexical_assembly, prune=True)
    assert len(store.ids_by_document()) == 1
    assert {d.state for d in current_status(workspace, lexical_assembly).documents} == {"current"}


def test_retriever_override_is_named_in_the_eval_folder_and_previous_run_matches_it(workspace):
    from rag.eval.runner import run_name
    from rag.jsonio import write_json
    from rag.reports.evaluation import latest_before
    assert run_name("structured", 3, True).endswith("_structured_k3_filter-on")
    assert run_name("structured", 3, True, "hybrid").endswith("_structured_k3_filter-on_hybrid")
    base = {"config": "structured", "top_k": 3, "dataset": {"version": "3.2.0"}}
    write_json(workspace.eval_reports() / "a" / "result.json",
               {**base, "created_at": "2026-10-12T10:00:00+08:00"})          # pre-V3.9: semantic
    write_json(workspace.eval_reports() / "b" / "result.json",
               {**base, "retriever": "hybrid", "created_at": "2026-10-12T11:00:00+08:00"})
    now = {**base, "created_at": "2026-10-12T12:00:00+08:00"}
    assert latest_before(workspace, {**now, "retriever": "semantic"})["created_at"].startswith("2026-10-12T10")
    assert latest_before(workspace, {**now, "retriever": "hybrid"})["created_at"].startswith("2026-10-12T11")
