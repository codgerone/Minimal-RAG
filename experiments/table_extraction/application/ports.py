"""Ports owned by use cases; no external SDK or concrete file adapter imports."""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager, Protocol

from .models import ExtractionRequest, ExtractionResult

class TableExtractor(Protocol):
    def extract(self, request: ExtractionRequest, staging_dir: Path) -> ExtractionResult: ...

class ArtifactPublisher(Protocol):
    def stage(self, destination: Path) -> ContextManager[Path]: ...

@dataclass(frozen=True)
class PipelineServices:
    page_geometries: Callable
    load_table_slots: Callable
    load_candidate_views: Callable
    validate_docling_baselines: Callable
    load_scoring_input: Callable
    read_page_words: Callable
    export_selection: Callable
    export_scoring: Callable
    failed_slot_pages: Callable
    unavailable_slot_refs: Callable
