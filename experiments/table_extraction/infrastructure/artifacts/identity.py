"""Content identity for reproducible stage inputs."""
from hashlib import sha256
from pathlib import Path
import json

def digest(path: Path) -> str:
    result = sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()

def extraction_fingerprints(root: Path, stem: str) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): digest(path)
            for tool in ('pymupdf','camelot','docling','unstructured')
            for path in sorted((root/tool/stem).rglob('*.json'))}

def validate_manifest_files(directory: Path, manifest: dict) -> None:
    for relative, expected in manifest.get('artifact_sha256', {}).items():
        path = (directory/relative).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or digest(path) != expected:
            raise ValueError(f'extraction artifact changed or missing: {relative}')

def verify_extraction_source(directory: Path, source_pdf: Path) -> list[str]:
    manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    recorded = manifest.get('source_pdf', manifest.get('source_file'))
    if not recorded or Path(recorded).resolve() != source_pdf.resolve():
        raise ValueError(f'extraction source does not match PDF: {directory}')
    if manifest.get('source_sha256') is not None and manifest['source_sha256'] != digest(source_pdf):
        raise ValueError('source PDF changed; extraction is stale')
    validate_manifest_files(directory, manifest)
    return [f'{directory.name}: legacy extraction has no page/content verification'] if 'executions' not in manifest else [
        f"{run['tool']}/{run['strategy']}/page {page['page_number']}: {page['error']}"
        for run in manifest['executions'] for page in run['page_results'] if page['status']=='failed']
