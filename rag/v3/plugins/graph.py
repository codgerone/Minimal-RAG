"""The single forward dependency registry for V3 processing results."""

from __future__ import annotations

from collections import defaultdict, deque

from rag.v3.contracts.assembly import DependencyEdge, InterfaceDefinition, InterfacePort
from rag.v3.plugins.catalog import SLOTS


def _port(port_id: str, model_id: str, condition: str | None = None) -> InterfacePort:
    return InterfacePort(port_id, model_id, () if condition is None else (condition,))


C1 = "table_extractor_present_v1"
C2 = "assembler_consumes_table_v1"

# These are the published business ports. Store and Reader method families remain
# resource dependencies; they do not masquerade as processing result edges.
PORTS: dict[str, tuple[tuple[InterfacePort, ...], tuple[InterfacePort, ...]]] = {
    "DocumentRegistry": ((_port("request", "RegistryRequest"),), (_port("sources", "tuple[SourceDocument,...]"),)),
    "Indexer": ((_port("request", "IndexerInput"),), (_port("result", "IngestResult"),)),
    "DocumentProcessor": ((_port("request", "DocumentRequest"),), (_port("result", "ProcessingResult"),)),
    "MainParser": ((_port("source", "SourceDocument"),),
                   (_port("primary", "PrimaryDocument"), _port("native", "NativeParserEvidence"))),
    "TableExtractor": ((_port("source", "SourceDocument"),), (_port("report", "TableExtractionReport"),)),
    "TableSelector": ((_port("primary", "PrimaryDocument"),
                       _port("reports", "tuple[TableExtractionReport,...]")),
                      (_port("resolutions", "tuple[ContentResolution,...]"),
                       _port("grouping", "GroupingReport"), _port("scoring", "ScoringReport"))),
    "DocumentAssembler": ((_port("primary", "PrimaryDocument"),
                           _port("reports", "tuple[TableExtractionReport,...]", C1),
                           _port("resolutions", "tuple[ContentResolution,...]", C1)),
                          (_port("document", "ParsedDocument"),
                           _port("prepared_tables", "tuple[PreparedTableContent,...]", C2))),
    "TableContentPreparation": ((_port("primary", "PrimaryDocument"),
                                 _port("reports", "tuple[TableExtractionReport,...]", C1),
                                 _port("resolutions", "tuple[ContentResolution,...]", C1)),
                                (_port("prepared", "tuple[PreparedTableContent,...]"),)),
    "HeaderDetector": ((_port("table", "StructuredTable"),), (_port("decision", "HeaderDecision"),)),
    "TableSerializer": ((_port("table", "StructuredTable"), _port("header", "HeaderDecision")),
                        (_port("serialized", "SerializedTable"),)),
    "DocumentComposer": ((_port("primary", "PrimaryDocument"),
                          _port("resolutions", "tuple[ContentResolution,...]", C1),
                          _port("prepared_tables", "tuple[PreparedTableContent,...]", C2)),
                         (_port("document", "ParsedDocument"),)),
    "Chunker": ((_port("document", "ParsedDocument"), _port("context", "ChunkingContext")),
                (_port("chunks", "ChunkBatch"),)),
    "ArtifactBuilder": ((_port("processing", "ProcessingResult"),
                         _port("chunks", "ChunkBatch"), _port("build_projection", "BuildProjection")),
                        (_port("staged", "StagedArtifacts"),)),
    "Embedder": ((_port("chunks", "ChunkBatch"), _port("query_request", "RetrievalRequest")),
                 (_port("passages", "PassageEmbeddingBatch"), _port("query_vector", "QueryEmbedding"))),
    "Publisher": ((_port("request", "PublicationRequest"),), (_port("result", "PublicationResult"),)),
    "Retriever": ((_port("request", "RetrievalRequest"),), (_port("result", "RetrievalResult"),)),
    "Chatbot": ((_port("request", "AnswerRequest"),), (_port("result", "AnswerResult"),)),
    "PromptBuilder": ((_port("retrieval", "RetrievalResult"), _port("request", "AnswerRequest")),
                      (_port("messages", "tuple[PromptMessage,...]"),)),
    "LanguageModel": ((_port("request", "LanguageModelRequest"),), (_port("answer", "str"),)),
    "EvaluationRepository": ((), (_port("loaded", "LoadedEvaluationData"),)),
    "PreflightValidator": ((_port("loaded", "LoadedEvaluationData"),), ()),
    "MetricCalculator": ((_port("case_retrieval", "RetrievalResult"),), ()),
    "EvaluationReporter": ((), (_port("rendered", "RenderedEvaluation"),)),
    "EvaluationRunStore": ((_port("rendered", "RenderedEvaluation"),), ()),
}


INTERFACES = {
    interface_id: InterfaceDefinition(interface_id, "1.0", inputs, outputs)
    for interface_id, (inputs, outputs) in PORTS.items()
}


EDGES: tuple[DependencyEdge, ...] = (
    DependencyEdge("document_processor.main_parser", "primary", "document_processor.table_selector", "primary", active_when_all=(C1,)),
    DependencyEdge("document_processor.table_extractor", "report", "document_processor.table_selector", "reports", "ordered_collect", (C1,)),
    DependencyEdge("document_processor.main_parser", "primary", "document_processor.document_assembler", "primary"),
    DependencyEdge("document_processor.table_extractor", "report", "document_processor.document_assembler", "reports", "ordered_collect", (C1,)),
    DependencyEdge("document_processor.table_selector", "resolutions", "document_processor.document_assembler", "resolutions", active_when_all=(C1,)),
    DependencyEdge("table_content_preparation.header_detector", "decision", "table_content_preparation.table_serializer", "header", active_when_all=(C2,)),
    DependencyEdge("document_assembler.table_content_preparation", "prepared", "document_assembler.document_composer", "prepared_tables", active_when_all=(C2,)),
    DependencyEdge("document_processor.document_assembler", "document", "indexer.chunker", "document"),
    DependencyEdge("indexer.document_processor", "result", "system.artifact_builder", "processing"),
    DependencyEdge("indexer.chunker", "chunks", "indexer.embedder", "chunks"),
    DependencyEdge("indexer.chunker", "chunks", "system.artifact_builder", "chunks"),
    DependencyEdge("chatbot.retriever", "result", "chatbot.prompt_builder", "retrieval"),
    DependencyEdge("evaluator.evaluation_repository", "loaded", "evaluator.preflight_validator", "loaded"),
    DependencyEdge("evaluator.retriever", "result", "evaluator.metric_calculator", "case_retrieval"),
    DependencyEdge("evaluator.evaluation_reporter", "rendered", "evaluator.evaluation_run_store", "rendered"),
)


def validate_dependency_graph(edges: tuple[DependencyEdge, ...] = EDGES) -> None:
    adjacency: dict[str, set[str]] = defaultdict(set)
    degree = {slot_id: 0 for slot_id in SLOTS}
    seen = set()
    for edge in edges:
        signature = (edge.source_slot_id, edge.source_output_port_id,
                     edge.target_slot_id, edge.target_input_port_id)
        if signature in seen:
            raise ValueError(f"duplicate dependency edge: {signature}")
        seen.add(signature)
        source_slot, target_slot = SLOTS[edge.source_slot_id], SLOTS[edge.target_slot_id]
        source = {p.port_id: p for p in INTERFACES[source_slot.interface_id].output_ports}[edge.source_output_port_id]
        target = {p.port_id: p for p in INTERFACES[target_slot.interface_id].input_ports}[edge.target_input_port_id]
        expected = f"tuple[{source.model_id},...]" if edge.delivery == "ordered_collect" else source.model_id
        if target.model_id != expected:
            raise ValueError(f"dependency model mismatch: {signature}")
        if edge.delivery == "ordered_collect" and source_slot.cardinality != "multi":
            raise ValueError(f"ordered collect requires multi source: {signature}")
        if not set(source.active_when_all + target.active_when_all).issubset(edge.active_when_all):
            raise ValueError(f"conditional port without matching edge condition: {signature}")
        if edge.target_slot_id not in adjacency[edge.source_slot_id]:
            adjacency[edge.source_slot_id].add(edge.target_slot_id)
            degree[edge.target_slot_id] += 1
    queue = deque(slot for slot, count in degree.items() if count == 0)
    visited = 0
    while queue:
        slot = queue.popleft()
        visited += 1
        for target in adjacency[slot]:
            degree[target] -= 1
            if degree[target] == 0:
                queue.append(target)
    if visited != len(SLOTS):
        raise ValueError("dependency graph contains a cycle")
