"""Composition root: concrete implementations are assembled only here."""
from functools import partial
from .presentation import cli, workflow
from .presentation.review_views.tables import render_table
from .presentation.review_views.grouping import render_grouping_views
from .presentation.review_views.scoring import render_scoring_views
from .application.extraction import execute_extraction
from .application.models import ExtractionRequest
from .application.ports import PipelineServices
from .application.grouping import run_selection
from .application.scoring import run_scoring
from .infrastructure.artifacts import readiness, paths
from .infrastructure.artifacts.grouping import export_selection
from .infrastructure.artifacts.scoring import export_scoring
from .infrastructure.artifacts.candidates import load_candidate_views, validate_docling_baselines
from .infrastructure.artifacts.slots import load_table_slots
from .infrastructure.artifacts.scoring_input import load_scoring_input
from .application.calibration import run_calibration
from .infrastructure.artifacts import calibration as calibration_files
from .presentation.review_views.calibration import render_chart
run_calibration = partial(run_calibration, read_labels=calibration_files.read_labels,
    parse_labels=calibration_files.parse_labels, load_candidates=calibration_files.load_candidates,
    update_label_markdown=calibration_files.update_label_markdown, render_chart=render_chart,
    write_text=calibration_files.write_text)
from .infrastructure.artifacts.execution import failed_slot_pages, unavailable_slot_refs
from .infrastructure.artifacts.publication import FileArtifactPublisher
from .infrastructure.pdf.coordinates import page_geometries
from .infrastructure.pdf.words import read_page_words
from .infrastructure.extractors.adapter import ToolAdapter
from .infrastructure.extractors import operations
services = PipelineServices(
    unavailable_slot_refs=unavailable_slot_refs,
    failed_slot_pages=failed_slot_pages,
    page_geometries=page_geometries, load_table_slots=load_table_slots,
    load_candidate_views=load_candidate_views, validate_docling_baselines=validate_docling_baselines,
    load_scoring_input=load_scoring_input, read_page_words=read_page_words,
    export_selection=partial(export_selection, render_views=render_grouping_views),
    export_scoring=partial(export_scoring, render_views=render_scoring_views),
)
run_selection = partial(run_selection, services=services)
run_scoring = partial(run_scoring, services=services)

adapters = {
    tool: ToolAdapter(tool, strategies, partial(getattr(operations, tool), render_table=render_table))
    for tool, strategies in {
        'pymupdf': ('lines', 'lines_strict', 'text'),
        'camelot': ('lattice', 'stream', 'network', 'hybrid'),
        'docling': ('default',), 'unstructured': ('hi_res',),
    }.items()
}
publisher = FileArtifactPublisher()

def extract_tool(tool, pdf_path, requested_root=None, *, strategies=(), pages='all'):
    destination = (requested_root or paths.TOOLS_OUTPUT_ROOT / tool) / pdf_path.stem
    result = execute_extraction(ExtractionRequest(pdf_path, destination.resolve(), tool, strategies, pages),
                                adapters[tool], publisher)
    return result.succeeded

def configure_cli():
    for name in ('extraction_issue','incomplete_extractions','grouping_issue','STRATEGIES','SUPPORTED_FLAVORS'):
        setattr(workflow, name, getattr(readiness, name))
    for name in dir(paths):
        if name.isupper(): setattr(cli, name, getattr(paths, name))
    cli.SUPPORTED_FLAVORS = readiness.SUPPORTED_FLAVORS
    cli.grouping_issue = partial(readiness.grouping_issue, tools_output_root=paths.TOOLS_OUTPUT_ROOT)
    cli.incomplete_extractions = readiness.incomplete_extractions
    cli.run_selection = run_selection
    cli.run_scoring = run_scoring
    cli.run_calibration = run_calibration
    cli._execute_pymupdf = partial(extract_tool, 'pymupdf')
    cli._execute_docling = partial(extract_tool, 'docling')
    cli._execute_unstructured = partial(extract_tool, 'unstructured')
    cli._execute_camelot = lambda pdf, flavors=None, pages='all', requested_root=None: extract_tool(
        'camelot', pdf, requested_root, strategies=tuple(flavors or readiness.SUPPORTED_FLAVORS), pages=pages)

configure_cli()

def main():
    cli.main()
