from rag.eval.dataset import Case, EvidenceGroup, Excerpt, Scheme
from rag.eval.judge import GroupEvidence, JudgeChunk, judge_case
from rag.eval.metrics import aggregate, score_question


def word(x, y=10.0, page=1):
    return (page, (x, y, x + 8.0, y + 6.0))


def excerpt(xid, pieces, doc="d1", header=False):
    """pieces: [(text, [x positions of its words on page 1])]"""
    return Excerpt(xid, doc, f"{doc}.pdf", (1,), " | ".join(t for t, _ in pieces), header, None,
                   tuple((t, tuple(word(x) for x in xs)) for t, xs in pieces), "ok")


def chunk(cid, xs=(), text="", doc="d1", parent=None, coarse=()):
    """A chunk whose fine regions are the word boxes at xs (page 1)."""
    return JudgeChunk(cid, doc, text, parent, tuple(word(x) for x in xs), tuple(coarse))


def case(excerpts, *groups):
    """groups: (group_id, [[excerpt ids of scheme 1], [excerpt ids of scheme 2], ...])"""
    return Case("Q1", "d1", "d1.pdf", "q", "a", True, tuple(excerpts), tuple(
        EvidenceGroup(gid, "item", tuple(Scheme(f"s{n}", tuple(ids)) for n, ids in enumerate(schemes, 1)), "")
        for gid, schemes in groups))


def one(x):
    return case([x], ("e1", [["x1"]]))


def test_a_chunk_covers_the_words_whose_centres_lie_in_its_regions():
    chunks = [chunk("right", [0, 20]), chunk("other-doc", [0, 20], doc="d2"), chunk("elsewhere", [300])]
    [evidence] = judge_case(one(excerpt("x1", [("Qty 10,000", [0, 20])])), chunks).groups
    assert evidence.mode == "coordinate" and evidence.acceptable_sets == (frozenset({"right"}),)


def test_words_split_over_chunks_need_all_of_them_and_overlaps_give_alternatives():
    # word 0 in a; word 20 in a and b (overlap); word 40 in b and c
    chunks = [chunk("a", [0, 20]), chunk("b", [20, 40]), chunk("c", [40])]
    [evidence] = judge_case(one(excerpt("x1", [("one two three", [0, 20, 40])])), chunks).groups
    assert set(evidence.acceptable_sets) == {frozenset({"a", "b"}), frozenset({"a", "c"})}


def test_word_in_no_region_makes_the_excerpt_unmapped():
    judged = judge_case(one(excerpt("x1", [("a b", [0, 500])])), [chunk("a", [0])])
    assert judged.excerpts["x1"].mode == "unmapped" and judged.groups[0].acceptable_sets == ()
    assert "不在任何 chunk 的区域内" in judged.excerpts["x1"].problem


def test_coarse_region_falls_back_to_the_piece_text():
    table = (1, (0.0, 0.0, 200.0, 50.0))
    chunks = [chunk("t1", text="第2行：Model = \"HXE12ESX\"；Qty = \"20,000.00\"", coarse=[table]),
              chunk("t2", text="第3行：Model = \"Meterbox\"", coarse=[table])]
    judged = judge_case(one(excerpt("x1", [("HXE12ESX 20,000.00", [10, 60])])), chunks)
    assert judged.excerpts["x1"].mode == "fallback"
    assert judged.groups[0].acceptable_sets == (frozenset({"t1"}),)
    both = [chunk("t1", text="HXE12ESX 20,000.00", coarse=[table]),
            chunk("t2", text="HXE12ESX 20,000.00", coarse=[table])]
    ambiguous = judge_case(one(excerpt("x1", [("HXE12ESX 20,000.00", [10])])), both)
    assert "环境文字" in ambiguous.excerpts["x1"].problem


def test_header_excerpt_also_maps_to_other_chunks_of_the_split_table_that_repeat_it():
    header = excerpt("x1", [("4th Delivery", [0])], header=True)
    chunks = [chunk("first", [0], text="4th Delivery = 1,000", parent="tbl"),
              chunk("second", [100], text="第5行：4th Delivery = 500", parent="tbl"),
              chunk("third", [200], text="表头未确定。第6行：第2列 = 9", parent="tbl"),
              chunk("other-table", [300], text="4th Delivery", parent="tbl2")]
    [evidence] = judge_case(one(header), chunks).groups
    assert set(evidence.acceptable_sets) == {frozenset({"first"}), frozenset({"second"})}
    plain = excerpt("x1", [("1,000", [0])])
    [evidence] = judge_case(one(plain), chunks).groups
    assert evidence.acceptable_sets == (frozenset({"first"}),)


def test_group_is_covered_by_any_scheme_and_a_scheme_needs_all_its_excerpts():
    target = case([excerpt("x1", [("a", [0])]), excerpt("x2", [("b", [100])]), excerpt("x3", [("c", [200])])],
                  ("e1", [["x1"], ["x2", "x3"]]), ("e2", [["x2", "x3"]]))
    chunks = [chunk("a", [0]), chunk("b", [100]), chunk("c", [200])]
    e1, e2 = judge_case(target, chunks).groups
    assert set(e1.acceptable_sets) == {frozenset({"a"}), frozenset({"b", "c"})}
    assert [s.acceptable_sets for s in e1.schemes] == [(frozenset({"a"}),), (frozenset({"b", "c"}),)]
    assert e2.acceptable_sets == (frozenset({"b", "c"}),)


def test_scheme_with_an_unmapped_excerpt_is_unusable():
    target = case([excerpt("x1", [("a", [0])]), excerpt("x2", [("b", [500])])], ("e1", [["x1", "x2"]]))
    [evidence] = judge_case(target, [chunk("a", [0])]).groups
    assert evidence.mode == "unmapped" and evidence.acceptable_sets == ()


def test_metrics_follow_the_documented_definitions():
    evidence = [GroupEvidence("e1", "coordinate", (frozenset({"a"}),)),
                GroupEvidence("e2", "coordinate", (frozenset({"b", "c"}),))]
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


def test_run_name_shows_config_k_and_filter_state():
    from rag.eval.runner import run_name
    assert run_name("structured", 3, True).endswith("_structured_k3_filter-on")
    assert run_name("plain_text", 5, False).endswith("_plain_text_k5_filter-off")
