from rag.eval.dataset import Case, EvidenceGroup, Excerpt
from rag.eval.judge import GroupEvidence, Mappings, chunk_key, judge_case
from rag.eval.metrics import aggregate, score_question
from rag.index.store import StoredChunk

ORDER = "Model HXF300 quantity 10,000 unit price USD 96.80 total USD 968,000.00"


def stored(cid, text, doc="d1", pages=(1,)):
    return StoredChunk(cid, doc, f"{doc}.pdf", f"{doc}.pdf", 0, "text", pages, None, text)


def case(*groups):
    return Case("Q1", "d1", "d1.pdf", "q", "a", True, tuple(groups))


def group(gid, *texts, pages=(1,)):
    return EvidenceGroup(gid, tuple(Excerpt(f"x{i}", "d1", "d1.pdf", pages, t) for i, t in enumerate(texts)))


def test_confirmed_mapping_follows_chunk_text_not_chunk_id(tmp_path):
    mappings = Mappings(tmp_path / "m.json")
    mappings.add("Q1", "e1", [chunk_key("d1", ORDER)])
    chunks = [stored("new-id-7", ORDER), stored("other", "unrelated text")]
    [evidence] = judge_case(case(group("e1", "anything")), chunks, mappings)
    assert evidence.mode == "confirmed" and evidence.acceptable_sets == (frozenset({"new-id-7"}),)


def test_auto_judge_needs_same_document_and_page(tmp_path):
    mappings = Mappings(tmp_path / "m.json")
    chunks = [stored("right", "Header line\n" + ORDER), stored("wrong-page", ORDER, pages=(2,)),
              stored("wrong-doc", ORDER, doc="d2")]
    [evidence] = judge_case(case(group("e1", ORDER)), chunks, mappings)
    assert evidence.mode == "auto" and evidence.acceptable_sets == (frozenset({"right"}),)


def test_auto_judge_combines_chunks_when_excerpt_is_split(tmp_path):
    first, second = ORDER[:36], ORDER[36:]
    chunks = [stored("a", first), stored("b", second), stored("c", "nothing relevant here at all")]
    [evidence] = judge_case(case(group("e1", ORDER)), chunks, Mappings(tmp_path / "m.json"))
    assert evidence.acceptable_sets == (frozenset({"a", "b"}),)


def test_unmatched_excerpt_is_reported_not_guessed(tmp_path):
    [evidence] = judge_case(case(group("e1", "Payment within thirty days of invoice")),
                            [stored("a", ORDER)], Mappings(tmp_path / "m.json"))
    assert evidence.mode == "unmapped" and evidence.acceptable_sets == ()


def test_metrics_follow_the_documented_definitions():
    evidence = [GroupEvidence("e1", "confirmed", (frozenset({"a"}),), {}),
                GroupEvidence("e2", "confirmed", (frozenset({"b", "c"}),), {})]
    full = score_question(["x", "a", "b", "c"], ["d2", "d1", "d1", "d1"], evidence, {"d1"})
    assert (full.hit, full.complete, full.first_relevant_rank, full.cross_document) == (True, True, 2, 1)
    assert full.group_completion_ranks == {"e1": 2, "e2": 4}
    partial = score_question(["b", "x", "y"], ["d1", "d2", "d2"], evidence, {"d1"})
    assert (partial.hit, partial.groups_covered, partial.relevant) == (False, 0, 1)
    metrics = aggregate([full, partial])
    assert metrics["hit_rate"].value == 0.5
    assert metrics["group_recall"].value == 0.5
    assert metrics["mrr"].value == (1 / 2 + 1 / 1) / 2
    assert metrics["chunk_precision"].value == (3 / 4 + 1 / 3) / 2
    assert metrics["cross_document"].value == (1 / 4 + 2 / 3) / 2
