from scripts.render_test_set_review import physical_pages


def test_physical_pages_reads_v2_serialized_page_numbers() -> None:
    assert physical_pages({"page_numbers_json": "[2,3]"}) == "2, 3"


def test_physical_pages_reads_v1_single_page() -> None:
    assert physical_pages({"page_number": 4}) == "4"


def test_physical_pages_falls_back_for_invalid_metadata() -> None:
    assert physical_pages({"page_numbers_json": "invalid"}) == "?"
