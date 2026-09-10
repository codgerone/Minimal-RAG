"""Execute injected extraction adapters; SDK and publishing live outside."""
from pathlib import Path
from .models import ExtractionRequest, ExtractionResult
from .ports import ArtifactPublisher, TableExtractor

def execute_extraction(request: ExtractionRequest, extractor: TableExtractor,
                       publisher: ArtifactPublisher) -> ExtractionResult:
    if not request.source_pdf.is_file():
        raise FileNotFoundError(request.source_pdf)
    with publisher.stage(request.output_dir) as staging:
        result = extractor.extract(request, staging)
    result.manifest_path = request.output_dir / 'manifest.json'
    return result
