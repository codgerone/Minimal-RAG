"""Location labels: what the index stores and reports show for where a chunk comes from.

A PDF chunk has page numbers; a Word chunk has the labels its parser gave the elements ("第 N 块",
"表 N", "页眉"…), in reading order. One chunk comes from one file, so a list holds one kind.
"""

from __future__ import annotations

from collections.abc import Iterable

from rag.models import PageSpan, Span

Location = int | str


def span_location(span: Span) -> Location:
    return span.page_number if isinstance(span, PageSpan) else span.label


def merge(locations: Iterable[Location]) -> tuple[Location, ...]:
    """Distinct locations: page numbers in ascending order, Word labels in the order given."""
    unique = tuple(dict.fromkeys(locations))
    if all(isinstance(item, int) for item in unique):
        return tuple(sorted(unique))   # type: ignore[type-var]
    return unique


def describe(locations: Iterable[Location], sep: str = ",") -> str:
    """'第 1,2 页' for pages; the labels joined for Word."""
    items = tuple(locations)
    if items and all(isinstance(item, str) for item in items):
        return "、".join(items)    # type: ignore[arg-type]
    return f"第 {sep.join(map(str, items))} 页"
