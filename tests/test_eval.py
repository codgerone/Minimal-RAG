from rag.eval.dataset import Case, EvidenceGroup, Excerpt, Scheme
from rag.eval.judge import GroupEvidence, Mappings, chunk_key, judge_case
from rag.eval.metrics import aggregate, score_question
from rag.index.store import StoredChunk

ORDER = "Model HXF300 quantity 10,000 unit price USD 96.80 total USD 968,000.00"
PAYMENT = "Bank check payable at 100% within 120 days counted from the B/L date"
DATES = "Delivery Date: To be confirmed the date, total 5 deliveries of 2,000 units"


def stored(cid, text, doc="d1", pages=(1,)):
    return StoredChunk(cid, doc, f"{doc}.pdf", f"{doc}.pdf", 0, "text", pages, None, text)


def excerpt(xid, text, pages=(1,)):
    return Excerpt(xid, "d1", "d1.pdf", pages, text)


def case(excerpts, *groups):
    """groups: (group_id, [[excerpt ids of scheme 1], [excerpt ids of scheme 2], ...])"""
    return Case("Q1", "d1", "d1.pdf", "q", "a", True, tuple(excerpts), tuple(
        EvidenceGroup(gid, "item", tuple(Scheme(f"s{n}", tuple(ids)) for n, ids in enumerate(schemes, 1)), "")
        for gid, schemes in groups))


def one(text, pages=(1,)):
    return case([excerpt("x1", text, pages)], ("e1", [["x1"]]))


def test_confirmed_mapping_follows_chunk_text_not_chunk_id(tmp_path):
    mappings = Mappings(tmp_path / "m.json")
    target = one("anything")
    mappings.add("Q1", target.excerpts[0], [chunk_key("d1", ORDER)])
    chunks = [stored("new-id-7", ORDER), stored("other", "unrelated text")]
    [evidence] = judge_case(target, chunks, mappings).groups
    assert evidence.mode == "confirmed" and evidence.acceptable_sets == (frozenset({"new-id-7"}),)


def test_confirmed_mapping_stops_applying_when_excerpt_text_changes(tmp_path):
    mappings = Mappings(tmp_path / "m.json")
    mappings.add("Q1", excerpt("x1", "old wording"), [chunk_key("d1", ORDER)])
    [evidence] = judge_case(one(ORDER), [stored("a", ORDER)], mappings).groups
    assert evidence.mode == "auto"


def test_auto_judge_needs_same_document_and_page(tmp_path):
    mappings = Mappings(tmp_path / "m.json")
    chunks = [stored("right", "Header line\n" + ORDER),stored("wrong-page", ORDER, pages=(2,)),
              stored("wrong-doc", ORDER, doc="d2")]
    [evidence] = judge_case(one(ORDER), chunks, mappings).groups
    assert evidence.mode == "auto" and evidence.acceptable_sets == (frozenset({"right"}),)


def test_auto_judge_combines_chunks_when_excerpt_is_split(tmp_path):
    first, second = ORDER[:36], ORDER[36:]
    chunks = [stored("a", first), stored("b", second), stored("c", "nothing relevant here at all")]
    [evidence] = judge_case(one(ORDER), chunks, Mappings(tmp_path / "m.json")).groups
    assert evidence.acceptable_sets == (frozenset({"a", "b"}),)


def test_unmatched_excerpt_is_reported_not_guessed(tmp_path):
    judged = judge_case(one("Payment within thirty days of invoice"), [stored("a", ORDER)],
                        Mappings(tmp_path / "m.json"))
    assert judged.groups[0].mode == "unmapped" and judged.groups[0].acceptable_sets == ()
    assert judged.excerpts["x1"].mode == "unmapped"


def test_group_is_covered_by_any_scheme_and_a_scheme_needs_all_its_excerpts(tmp_path):
    target = case([excerpt("x1", ORDER), excerpt("x2", PAYMENT), excerpt("x3", DATES)],
                  ("e1", [["x1"], ["x2", "x3"]]), ("e2", [["x2", "x3"]]))
    chunks = [stored("a", ORDER), stored("b", PAYMENT), stored("c", DATES)]
    e1, e2 = judge_case(target, chunks, Mappings(tmp_path / "m.json")).groups
    assert set(e1.acceptable_sets) == {frozenset({"a"}), frozenset({"b", "c"})}
    assert [s.acceptable_sets for s in e1.schemes] == [(frozenset({"a"}),), (frozenset({"b", "c"}),)]
    assert e2.acceptable_sets == (frozenset({"b", "c"}),)


def test_scheme_with_an_unmapped_excerpt_is_unusable(tmp_path):
    target = case([excerpt("x1", ORDER), excerpt("x2", "Payment within thirty days of invoice")],
                  ("e1", [["x1", "x2"]]))
    [evidence] = judge_case(target, [stored("a", ORDER)], Mappings(tmp_path / "m.json")).groups
    assert evidence.mode == "unmapped" and evidence.acceptable_sets == ()


def test_metrics_follow_the_documented_definitions():
    evidence = [GroupEvidence("e1", "confirmed", (frozenset({"a"}),)),
                GroupEvidence("e2", "confirmed", (frozenset({"b", "c"}),))]
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
