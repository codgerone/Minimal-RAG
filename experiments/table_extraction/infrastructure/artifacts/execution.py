"""Read and validate extraction execution facts and input provenance."""
import json
from pathlib import Path

def failed_slot_pages(root: Path, pdf_path: Path) -> set[int]:
    manifest = json.loads((root/'docling'/pdf_path.stem/'manifest.json').read_text(encoding='utf-8'))
    return {page['page_number'] for run in manifest.get('executions', [])
            for page in run['page_results'] if page['status'] == 'failed'}

def unavailable_slot_refs(root: Path, pdf_path: Path) -> set[str]:
    failed = failed_slot_pages(root, pdf_path)
    document = json.loads((root/'docling'/pdf_path.stem/'raw/document.json').read_text(encoding='utf-8'))
    return {f'#/tables/{i}' for i,table in enumerate(document['tables'])
            if any(p.get('page_no') in failed for p in table.get('prov', []))}

def validate_source(manifest: dict, pdf_path: Path) -> None:
    from .identity import digest
    if manifest.get('source_sha256') is not None and manifest['source_sha256'] != digest(pdf_path):
        raise ValueError('source PDF content changed; extraction must be rebuilt')
