"""Isolate normalization failures by source page, retaining original raw records."""
from dataclasses import dataclass, field
from typing import Callable

from experiments.table_extraction.domain.models.tables import TableCandidate

@dataclass
class NormalizationBatch:
    candidates: list[TableCandidate] = field(default_factory=list)
    rows_by_candidate: dict = field(default_factory=dict)
    failed_pages: dict[int, str] = field(default_factory=dict)

def normalize_page_groups(groups: dict[tuple[int, ...], list[int]], normalize: Callable) -> NormalizationBatch:
    result = NormalizationBatch()
    for pages, indices in groups.items():
        try:
            batch = normalize(indices)
            result.candidates.extend(batch.candidates)
            result.rows_by_candidate.update(batch.rows_by_candidate)
        except Exception as error:
            if not pages:
                raise ValueError('normalization failed with no attributable source page') from error
            result.failed_pages.update({n: f'normalization: {type(error).__name__}: {error}' for n in pages})
    # A page is the publication unit, even if another group touching it succeeded.
    result.candidates = [c for c in result.candidates if not any(r.page_number in result.failed_pages for r in c.regions)]
    result.rows_by_candidate = {c.candidate_id: result.rows_by_candidate[c.candidate_id] for c in result.candidates}
    return result

def apply_page_failures(metadata: dict, failures: dict[int, str]) -> None:
    for page in metadata.get('page_results', []):
        if page['page_number'] in failures:
            page.update(status='failed', error=failures[page['page_number']])
