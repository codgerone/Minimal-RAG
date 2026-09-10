"""Validate requested PDF pages without depending on any extraction SDK."""
from pathlib import Path

def requested_pages(pdf_path: Path, selection: str = 'all') -> list[int]:
    import fitz
    with fitz.open(pdf_path) as doc:
        count = len(doc)
    if selection == 'all':
        return list(range(1, count + 1))
    selected: set[int] = set()
    for part in selection.split(','):
        bounds = part.strip().split('-')
        if len(bounds) == 1:
            selected.add(int(bounds[0]))
        elif len(bounds) == 2:
            first, last = map(int, bounds)
            if first > last:
                raise ValueError('page range is reversed')
            selected.update(range(first, last + 1))
        else:
            raise ValueError('invalid page range')
    if not selected or min(selected) < 1 or max(selected) > count:
        raise ValueError('requested page is outside PDF')
    return sorted(selected)
