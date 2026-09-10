"""Adapt tool runners to the application extraction port and v2 manifests."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
from typing import Callable

from experiments.table_extraction.application.models import (
    ExtractionRequest, ExtractionResult, PageExtractionResult, StrategyExecution,
)
from experiments.table_extraction.infrastructure.artifacts.scoring_input import _candidate
from experiments.table_extraction.infrastructure.artifacts.writers import _write_json
from experiments.table_extraction.infrastructure.pdf.pages import requested_pages
from .configuration import configuration_identity

EXTRACTION_FORMAT = 'table_extraction_v2'

def file_digest(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

@dataclass
class ToolAdapter:
    tool: str
    strategies: tuple[str, ...]
    run_and_export: Callable[[ExtractionRequest, Path], None]

    def extract(self, request: ExtractionRequest, staging_dir: Path) -> ExtractionResult:
        strategies = request.strategies or self.strategies
        startup_error = None
        try:
            numbers = requested_pages(request.source_pdf, request.pages)
        except Exception as exc:
            numbers = []
            startup_error = f'{type(exc).__name__}: {exc}'
        if startup_error is None:
            try:
                self.run_and_export(request, staging_dir)
            except ImportError as exc:
                startup_error = f'{type(exc).__name__}: {exc}'
            except Exception:
                # Unexpected serialization/normalization errors must not publish a new
                # manifest over an old valid run. Expected page failures are runner facts.
                raise
        manifest_path = staging_dir / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
        candidates = []
        for path in sorted((staging_dir / 'normalized').glob('*/tables.json')):
            payload = json.loads(path.read_text(encoding='utf-8'))
            payload['format_version'] = 'table_candidates_v2'
            _write_json(path, payload)
            candidates.extend(_candidate(value) for value in payload['tables'])
        executions = []
        for strategy in strategies:
            facts = manifest
            if self.tool == 'camelot':
                facts = next((run for run in manifest.get('runs', []) if run['flavor'] == strategy), {})
            metadata = facts.get('run_metadata', {})
            page_facts = metadata.get('page_results')
            if self.tool == 'pymupdf':
                raw_file = staging_dir / 'raw' / f'{strategy}.json'
                if raw_file.exists():
                    raw = json.loads(raw_file.read_text(encoding='utf-8'))
                    page_facts = raw['pages']
                    metadata = {'configuration': raw.get('configuration', {}),
                                'pymupdf_version': raw.get('pymupdf_version')}
            error = startup_error or metadata.get('startup_error')
            if page_facts is None:
                error = error or facts.get('error') or 'Missing page execution facts'
            run = StrategyExecution(self.tool, strategy, numbers, error, run_metadata=metadata)
            if not error:
                for page in page_facts:
                    number = page['page_number']
                    identifiers = [c.candidate_id for c in candidates if c.strategy == strategy
                                   and any(r.page_number == number for r in c.regions)]
                    run.page_results.append(PageExtractionResult(number, page['status'],
                        identifiers if page['status'] == 'success' else [], page.get('error')))
            executions.append(run)
        for run in executions:
            if run.startup_error is None:
                if sorted(p.page_number for p in run.page_results) != sorted(numbers):
                    raise ValueError(f'{run.strategy}: missing or duplicate page execution facts')
                for page in run.page_results:
                    if (page.status not in ('success', 'failed')
                            or (page.status == 'failed' and (not page.error or page.candidate_ids))
                            or (page.status == 'success' and page.error is not None)):
                        raise ValueError(f'{run.strategy}: inconsistent page result {page.page_number}')
        manifest.update(format_version=EXTRACTION_FORMAT, tool=self.tool,
                        configuration_identity=configuration_identity(self.tool),
                        source_pdf=str(request.source_pdf), source_file=str(request.source_pdf),
                        source_sha256=file_digest(request.source_pdf),
                        executions=[run.to_dict() for run in executions])
        # Pin files used by downstream computations, independently of HTML display files.
        manifest['artifact_sha256'] = {
            path.relative_to(staging_dir).as_posix(): file_digest(path)
            for path in sorted(staging_dir.rglob('*.json')) if path != manifest_path
        }
        _write_json(manifest_path, manifest)
        return ExtractionResult(candidates, executions, manifest_path)
