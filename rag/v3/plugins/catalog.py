"""Installed interface slots and plugin declarations; configurations select these records."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from rag.v3.contracts.assembly import PluginDefinition, SlotDefinition


@dataclass(frozen=True)
class ParameterField:
    kind: type
    default: object
    choices: tuple[object, ...] = ()
    minimum: Decimal | None = None
    build_effect: bool = True


def _field(kind: type, default: object, *, choices: tuple[object, ...] = (),
           minimum: str | None = None, build: bool = True) -> ParameterField:
    return ParameterField(kind, default, choices,
                          Decimal(minimum) if minimum is not None else None, build)


SLOT_SPECS: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("system.registry", "DocumentRegistry", "required", "one", None),
    ("system.indexer", "Indexer", "required", "one", None),
    ("system.artifact_builder", "ArtifactBuilder", "required", "one", None),
    ("system.index_health", "IndexHealth", "required", "one", None),
    ("system.retriever", "Retriever", "required", "one", None),
    ("system.chatbot", "Chatbot", "required", "one", None),
    ("system.recovery", "Recovery", "required", "one", None),
    ("system.evaluator", "Evaluator", "required", "one", None),
    ("indexer.document_processor", "DocumentProcessor", "required", "one", None),
    ("indexer.chunker", "Chunker", "required", "one", None),
    ("indexer.embedder", "Embedder", "required", "one", None),
    ("indexer.publisher", "Publisher", "required", "one", None),
    ("document_processor.main_parser", "MainParser", "required", "one", None),
    ("document_processor.table_extractor", "TableExtractor", "optional", "multi", None),
    ("document_processor.table_selector", "TableSelector", "conditional", "one", "table_extractor_present_v1"),
    ("document_processor.document_assembler", "DocumentAssembler", "required", "one", None),
    ("table_selector.pdf_evidence_reader", "PdfEvidenceReader", "conditional", "one", "table_extractor_present_v1"),
    ("document_assembler.table_content_preparation", "TableContentPreparation", "conditional", "one", "assembler_consumes_table_v1"),
    ("document_assembler.document_composer", "DocumentComposer", "required", "one", None),
    ("table_content_preparation.header_detector", "HeaderDetector", "conditional", "one", "assembler_consumes_table_v1"),
    ("table_content_preparation.table_serializer", "TableSerializer", "conditional", "one", "assembler_consumes_table_v1"),
    ("publisher.vector_store", "VectorStore", "required", "one", None),
    ("publisher.manifest_store", "ManifestStore", "required", "one", None),
    ("publisher.recovery_store", "RecoveryStore", "required", "one", None),
    ("publisher.artifact_store", "ArtifactStore", "required", "one", None),
    ("index_health.source_probe", "SourceProbe", "required", "one", None),
    ("index_health.vector_reader", "VectorReader", "required", "one", None),
    ("index_health.manifest_store", "ManifestStore", "required", "one", None),
    ("index_health.recovery_store", "RecoveryStore", "required", "one", None),
    ("index_health.artifact_store", "ArtifactStore", "required", "one", None),
    ("retriever.index_health", "IndexHealth", "required", "one", None),
    ("retriever.embedder", "Embedder", "required", "one", None),
    ("retriever.vector_reader", "VectorReader", "required", "one", None),
    ("chatbot.retriever", "Retriever", "required", "one", None),
    ("chatbot.prompt_builder", "PromptBuilder", "required", "one", None),
    ("chatbot.language_model", "LanguageModel", "required", "one", None),
    ("evaluator.evaluation_repository", "EvaluationRepository", "required", "one", None),
    ("evaluator.preflight_validator", "PreflightValidator", "required", "one", None),
    ("evaluator.retriever", "Retriever", "required", "one", None),
    ("evaluator.metric_calculator", "MetricCalculator", "required", "one", None),
    ("evaluator.evaluation_reporter", "EvaluationReporter", "required", "one", None),
    ("evaluator.evaluation_run_store", "EvaluationRunStore", "required", "one", None),
    ("recovery.vector_store", "VectorStore", "required", "one", None),
    ("recovery.manifest_store", "ManifestStore", "required", "one", None),
    ("recovery.recovery_store", "RecoveryStore", "required", "one", None),
    ("recovery.artifact_store", "ArtifactStore", "required", "one", None),
)


SLOTS = {slot_id: SlotDefinition(slot_id, None if slot_id.startswith("system.")
         else slot_id.split(".", 1)[0], interface_id, access, cardinality, condition)
         for slot_id, interface_id, access, cardinality, condition in SLOT_SPECS}


PLUGIN_SPECS: tuple[tuple[str, str, bool, tuple[str, ...]], ...] = (
    ("registry.local_pdf", "DocumentRegistry", False, ()),
    ("indexer.incremental", "Indexer", False, ()),
    ("artifacts.stage_snapshot_v3", "ArtifactBuilder", True, ("consumes_native_parser_evidence_v1",)),
    ("health.strict", "IndexHealth", False, ()),
    ("retriever.semantic_stable_topk", "Retriever", False, ()),
    ("chatbot.single_turn", "Chatbot", False, ()),
    ("recovery.journaled", "Recovery", False, ()),
    ("evaluator.formal", "Evaluator", False, ()),
    ("processor.composed_v1", "DocumentProcessor", False, ()),
    ("chunker.page_characters", "Chunker", True, ("guarantees_single_page_chunks_v1",)),
    ("chunker.structured_tokens", "Chunker", True, ("requires_matching_passage_tokenizer_v1",)),
    ("embedder.e5_small", "Embedder", True, ()),
    ("publisher.journaled", "Publisher", False, ()),
    ("parser.pymupdf_pages", "MainParser", True, ("no_table_slots_v1", "provides_native_parser_evidence_v1")),
    ("parser.docling_layout", "MainParser", True, ("produces_table_slots_v1", "provides_native_table_fallback_v1", "provides_native_parser_evidence_v1")),
    ("extractor.pymupdf", "TableExtractor", True, ()),
    ("extractor.camelot", "TableExtractor", True, ()),
    ("extractor.docling", "TableExtractor", True, ()),
    ("extractor.unstructured", "TableExtractor", True, ()),
    ("selector.table_v1", "TableSelector", True, ()),
    ("assembler.composed_v1", "DocumentAssembler", True, ("consumes_table_slots_v1",)),
    ("pdf_evidence.pymupdf", "PdfEvidenceReader", False, ()),
    ("table_content.serial_v1", "TableContentPreparation", True, ()),
    ("assembler.ordered", "DocumentComposer", True, ()),
    ("header.table_v1", "HeaderDetector", True, ()),
    ("serializer.table_text_v1", "TableSerializer", True, ()),
    ("store.chroma_cosine", "VectorStore", True, ()),
    ("store.json_manifest", "ManifestStore", True, ()),
    ("store.local_recovery", "RecoveryStore", False, ()),
    ("store.local_artifacts", "ArtifactStore", True, ()),
    ("probe.pymupdf", "SourceProbe", False, ()),
    ("reader.chroma_cosine", "VectorReader", False, ()),
    ("prompt.grounded", "PromptBuilder", False, ()),
    ("llm.openrouter", "LanguageModel", False, ()),
    ("eval.local_reviewed", "EvaluationRepository", False, ()),
    ("eval.preflight_v1", "PreflightValidator", False, ()),
    ("eval.evidence_metrics", "MetricCalculator", False, ()),
    ("eval.json_html", "EvaluationReporter", False, ()),
    ("eval.immutable_local", "EvaluationRunStore", False, ()),
)


PLUGINS = {plugin_id: PluginDefinition(plugin_id, "1.0", interface_id, ("1.0",), caps,
           f"{plugin_id}.params.v1", plugin_id, (), build)
           for plugin_id, interface_id, build, caps in PLUGIN_SPECS}


PARAMETERS: dict[str, dict[str, ParameterField]] = {
    "parser.pymupdf_pages": {"rule_version": _field(str, "pymupdf_text_v1", choices=("pymupdf_text_v1",))},
    "parser.docling_layout": {
        "rule_version": _field(str, "docling_layout_v1", choices=("docling_layout_v1",)),
        "mapper_version": _field(str, "docling_mapper_v2", choices=("docling_mapper_v2",)),
        "do_ocr": _field(bool, False, choices=(False,)),
        "do_table_structure": _field(bool, True, choices=(True,)),
        "table_mode": _field(str, "accurate", choices=("accurate",)),
        "do_cell_matching": _field(bool, True, choices=(True,)),
        "generate_page_images": _field(bool, False, choices=(False,)),
        "docling_version": _field(str, "2.121.0", choices=("2.121.0",)),
    },
    "extractor.pymupdf": {"strategies": _field(tuple, ("lines", "lines_strict", "text"), choices=(("lines", "lines_strict", "text"),))},
    "extractor.camelot": {"strategies": _field(tuple, ("lattice", "stream", "network", "hybrid"), choices=(("lattice", "stream", "network", "hybrid"),))},
    "extractor.docling": {"strategies": _field(tuple, ("accurate",), choices=(("accurate",),))},
    "extractor.unstructured": {"strategies": _field(tuple, ("hi_res",), choices=(("hi_res",),))},
    "selector.table_v1": {
        "rule_version": _field(str, "table_selection_v1", choices=("table_selection_v1",)),
        "metric_weights": _field(tuple, (Decimal("0.25"),) * 4,
                                  choices=((Decimal("0.25"),) * 4,)),
        "tool_tiebreak_order": _field(tuple, ("pymupdf", "camelot", "docling", "unstructured"),
                                       choices=(("pymupdf", "camelot", "docling", "unstructured"),)),
        "page_bounds_tolerance_pt": _field(Decimal, Decimal("0.000001"), choices=(Decimal("0.000001"),)),
        "page_size_tolerance_pt": _field(Decimal, Decimal("1.0"), choices=(Decimal("1.0"),)),
        "boundary_cluster_tolerance_pt": _field(Decimal, Decimal("2.0"), choices=(Decimal("2.0"),)),
        "comparison_epsilon": _field(Decimal, Decimal("1e-12"), choices=(Decimal("1e-12"),)),
        "candidate_coverage_minimum": _field(Decimal, Decimal("0.65"), choices=(Decimal("0.65"),)),
        "slot_coverage_minimum": _field(Decimal, Decimal("0.77"), choices=(Decimal("0.77"),)),
    },
    "assembler.composed_v1": {"table_content_enabled": _field(bool, False)},
    "header.table_v1": {"rule_version": _field(str, "table_header_v1", choices=("table_header_v1",)),
                        "sample_row_budget": _field(int, 8, choices=(8,)),
                        "minimum_independent_observations": _field(int, 2, choices=(2,))},
    "serializer.table_text_v1": {"rule_version": _field(str, "table_text_v1", choices=("table_text_v1",))},
    "chunker.page_characters": {"chunk_size_characters": _field(int, 300, minimum="1"),
                                "chunk_overlap_characters": _field(int, 50, minimum="0"),
                                "rule_version": _field(str, "recursive_character_v1", choices=("recursive_character_v1",)),
                                "separator_version": _field(str, "legacy_default_v1", choices=("legacy_default_v1",))},
    "chunker.structured_tokens": {"maximum_input_tokens": _field(int, 512, minimum="1"),
                                  "text_overlap_tokens": _field(int, 32, minimum="0"),
                                  "rule_version": _field(str, "structured_chunk_v1", choices=("structured_chunk_v1",)),
                                  "separator_version": _field(str, "recursive_boundaries_v1", choices=("recursive_boundaries_v1",)),
                                  "document_text_rule_version": _field(str, "document_text_v1", choices=("document_text_v1",))},
    "embedder.e5_small": {"model_name": _field(str, "intfloat/multilingual-e5-small"),
                          "revision": _field(str, "614241f622f53c4eeff9890bdc4f31cfecc418b3"),
                          "vector_dimension": _field(int, 384, minimum="1"),
                          "normalize_embeddings": _field(bool, True, choices=(True,)),
                          "passage_prefix": _field(str, "passage: ", choices=("passage: ",)),
                          "query_prefix": _field(str, "query: ", choices=("query: ",)),
                          "add_special_tokens": _field(bool, True, choices=(True,))},
    "retriever.semantic_stable_topk": {"tie_break": _field(str, "distance_then_chunk_id", choices=("distance_then_chunk_id",), build=False),
                                       "health_gate": _field(bool, True, choices=(True,), build=False)},
    "prompt.grounded": {"source_label_style": _field(str, "page_set", choices=("single_page", "page_set"), build=False),
                        "prompt_rule_version": _field(str, "grounded_prompt_v1", choices=("grounded_prompt_v1",), build=False)},
    "llm.openrouter": {"model_name": _field(str, "google/gemini-2.5-flash-lite", build=False),
                       "temperature": _field(int, 0, choices=(0,), build=False)},
    "chatbot.single_turn": {"memory": _field(bool, False, choices=(False,), build=False)},
}
