"""Read PDF words without applying scoring policy."""
from pathlib import Path
def read_page_words(source_pdf: Path, page_numbers: list[int]) -> dict[int, list[tuple]]:
    import fitz
    with fitz.open(source_pdf) as document:
        return {number: document[number-1].get_text("words", sort=False) for number in sorted(set(page_numbers))}
