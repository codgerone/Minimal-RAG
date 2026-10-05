"""Validated plugin bindings assembled into lazy V3 command resources."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from rag.v3.adapters.artifact_store import LocalArtifactStore
from rag.v3.adapters.chroma_store import ChromaV3Store
from rag.v3.adapters.e5 import E5EmbeddingAdapter, E5PassageCounter
from rag.v3.adapters.evaluation_repository import LocalReviewedEvidenceRepository
from rag.v3.adapters.evaluation_run_store import LocalImmutableEvaluationRunStore
from rag.v3.adapters.local_registry import LocalPdfRegistry
from rag.v3.adapters.manifest_store import LocalManifestStore
from rag.v3.adapters.openrouter import OpenRouterLanguageModel
from rag.v3.adapters.pdf_words import PyMuPdfEvidenceReader
from rag.v3.adapters.processor_plugins import (
    DoclingLayoutMainParser, DoclingTableExtractor, PlainPageMainParser,
    camelot_extractor, pymupdf_extractor, unstructured_extractor,
)
from rag.v3.adapters.recovery_store import LocalRecoveryStore
from rag.v3.adapters.source_probe import PdfSourceProbe
from rag.v3.application.artifacts import SnapshotArtifactBuilder
from rag.v3.application.assembly import (
    build_projection, effective_parameters, index_identity, validate_configuration,
)
from rag.v3.application.chatbot import SingleTurnChatbot
from rag.v3.application.chunkers import CharacterChunker, StructuredChunker
from rag.v3.application.document_processor import DocumentProcessor
from rag.v3.application.evaluation_metrics import EvidenceMetricCalculator
from rag.v3.application.evaluation_preflight import ReviewedEvidencePreflight
from rag.v3.application.evaluation_reporter import JsonHtmlEvaluationReporter
from rag.v3.application.evaluator import FormalEvidenceEvaluator
from rag.v3.application.health import IndexHealth
from rag.v3.application.indexer import Indexer
from rag.v3.application.process_plugins import (
    DocumentAssemblerPlugin, DocumentComposerPlugin, HeaderDetectorPlugin,
    TableContentPreparationPlugin, TableSelectorPlugin, TableSerializerPlugin,
)
from rag.v3.application.prompt import GroundedPromptBuilder, GroundedPromptParameters
from rag.v3.application.publication import Publisher, Recovery
from rag.v3.application.read_gate import ManifestReadGate
from rag.v3.application.retriever import StableSemanticRetriever
from rag.v3.contracts.assembly import (
    PluginBinding, ResolvedPlugin, RuntimeBinding, SavedConfiguration,
)
from rag.v3.contracts.documents import SourceDocument
from rag.v3.contracts.retrieval import EmbeddingIdentity
from rag.v3.plugins.catalog import PLUGINS


PluginFactory = Callable[["RuntimeFactory", PluginBinding, dict[str, object]], object]


def _embedding_identity(params: dict[str, object]) -> EmbeddingIdentity:
    return EmbeddingIdentity(
        params["model_name"], params["revision"], params["vector_dimension"],
        params["normalize_embeddings"], params["passage_prefix"],
        params["query_prefix"], params["add_special_tokens"],
    )


class RuntimeFactory:
    def __init__(self, configuration: SavedConfiguration, workspace_root: Path,
                 sources: tuple[SourceDocument, ...] = (),
                 api_key_provider: Callable[[], str | None] | None = None,
                 auth_retry_provider: Callable[[], str | None] | None = None,
                 factories: dict[str, PluginFactory] | None = None):
        validate_configuration(configuration)
        self.configuration = configuration
        self.workspace_root = workspace_root.resolve()
        self.sources = sources
        self.api_key_provider = api_key_provider or (lambda: os.getenv("OPENROUTER_API_KEY"))
        self.auth_retry_provider = auth_retry_provider
        self.index = index_identity(configuration)
        self.projection = build_projection(configuration)
        self.factories = dict(BUILTIN_FACTORIES)
        if factories:
            self.factories.update(factories)
        self.bindings: dict[str, tuple[PluginBinding, ...]] = {}
        for binding in configuration.bindings:
            self.bindings.setdefault(binding.slot_id, ())
            self.bindings[binding.slot_id] += (binding,)
        self.bindings = {key: tuple(sorted(value, key=lambda item: item.order))
                         for key, value in self.bindings.items()}
        self._instances: dict[tuple[str, str], object] = {}
        self._resources: dict[str, object] = {}
        self._resolved: list[ResolvedPlugin] = []
        self._created: list[object] = []
        self._closed = False

    def resource(self, key: str, create: Callable[[], object]) -> object:
        if self._closed:
            raise RuntimeError("runtime binding is closed")
        if key not in self._resources:
            instance = create()
            self._resources[key] = instance
            self._created.append(instance)
        return self._resources[key]

    def many(self, slot_id: str) -> tuple[object, ...]:
        if self._closed:
            raise RuntimeError("runtime binding is closed")
        instances = []
        try:
            for binding in self.bindings.get(slot_id, ()):
                key = (slot_id, binding.binding_id)
                if key not in self._instances:
                    definition = PLUGINS[binding.plugin_id]
                    factory = self.factories.get(definition.factory_id)
                    if factory is None:
                        raise ValueError(f"no installed factory for {definition.factory_id}")
                    instance = factory(self, binding, effective_parameters(binding))
                    self._instances[key] = instance
                    self._created.append(instance)
                    self._resolved.append(ResolvedPlugin(slot_id, binding.binding_id,
                        binding.plugin_id, definition.interface_id, "1.0", instance))
                instances.append(self._instances[key])
        except Exception:
            try:
                self.close()
            except Exception:
                pass  # The construction error is the stage failure to report.
            raise
        return tuple(instances)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        seen: set[int] = set()
        first_error: Exception | None = None
        for instance in reversed(self._created):
            if id(instance) in seen:
                continue
            seen.add(id(instance))
            closer = getattr(instance, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        self._instances.clear()
        self._resources.clear()
        self._resolved.clear()
        self._created.clear()
        if first_error is not None:
            raise first_error

    def __enter__(self) -> "RuntimeFactory":
        if self._closed:
            raise RuntimeError("runtime binding is closed")
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        if _value is None:
            self.close()
        else:
            try:
                self.close()
            except Exception:
                pass  # Preserve the command's original failure.

    def one(self, slot_id: str) -> object:
        instances = self.many(slot_id)
        if len(instances) != 1:
            raise ValueError(f"slot {slot_id} requires one selected plugin")
        return instances[0]

    def optional(self, slot_id: str) -> object | None:
        instances = self.many(slot_id)
        if len(instances) > 1:
            raise ValueError(f"slot {slot_id} exceeds one plugin")
        return instances[0] if instances else None

    def runtime_binding(self) -> RuntimeBinding:
        return RuntimeBinding(self.configuration.name, self.index,
                              tuple(self._resolved), self.index.build_fingerprint)


def _vector(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    return ctx.resource("chroma", lambda: ChromaV3Store(ctx.workspace_root))


def _manifest(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    return ctx.resource("manifest", lambda: LocalManifestStore(ctx.workspace_root))


def _artifacts(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    return ctx.resource("artifacts", lambda: LocalArtifactStore(ctx.workspace_root))


def _recovery_store(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    return ctx.resource("recovery", lambda: LocalRecoveryStore(ctx.workspace_root))


def _embedder(ctx: RuntimeFactory, _binding: PluginBinding, params: dict[str, object]):
    identity = _embedding_identity(params)
    return ctx.resource("embedder:" + identity.model_name + ":" + identity.revision,
                        lambda: E5EmbeddingAdapter(identity))


def _structured_chunker(ctx: RuntimeFactory, _binding: PluginBinding,
                        params: dict[str, object]):
    identity = ctx.one("indexer.embedder").identity
    counter = ctx.resource("passage_counter:" + identity.model_name + ":" + identity.revision,
                           lambda: E5PassageCounter(identity))
    return StructuredChunker(params["maximum_input_tokens"],
                             params["text_overlap_tokens"], counter)


def _processor(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    return DocumentProcessor(ctx.one("document_processor.main_parser"),
        ctx.many("document_processor.table_extractor"),
        ctx.optional("document_processor.table_selector"),
        ctx.one("document_processor.document_assembler"))


def _assembler(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    return DocumentAssemblerPlugin(ctx.optional("document_assembler.table_content_preparation"),
                                   ctx.one("document_assembler.document_composer"))


def _content(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    return TableContentPreparationPlugin(
        ctx.one("table_content_preparation.header_detector"),
        ctx.one("table_content_preparation.table_serializer"))


def _publisher(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    return Publisher(ctx.one("publisher.manifest_store"), ctx.one("publisher.vector_store"),
                     ctx.one("publisher.artifact_store"), ctx.one("publisher.recovery_store"))


def _recovery(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    return Recovery(ctx.one("recovery.manifest_store"), ctx.one("recovery.vector_store"),
                    ctx.one("recovery.artifact_store"), ctx.one("recovery.recovery_store"))


def _health(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    identity = ctx.one("indexer.embedder").identity
    return IndexHealth(ctx.projection, identity, ctx.one("index_health.source_probe"),
                       ctx.one("index_health.manifest_store"),
                       ctx.one("index_health.vector_reader"),
                       ctx.one("index_health.artifact_store"),
                       ctx.one("index_health.recovery_store"))


def _indexer(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    chunker = ctx.one("indexer.chunker")
    embedder = ctx.one("indexer.embedder")
    return Indexer(ctx.configuration.name, ctx.index, ctx.projection,
                   chunker.context(ctx.index, "pending", embedder.identity),
                   bool(ctx.many("document_processor.table_extractor")),
                   ctx.one("indexer.document_processor"), chunker, embedder,
                   ctx.one("system.artifact_builder"), ctx.one("indexer.publisher"),
                   ctx.one("system.index_health"), ctx.one("system.recovery"))


def _retriever(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    manifest = ctx.one("index_health.manifest_store")
    recovery = ctx.one("index_health.recovery_store")
    embedder = ctx.one("retriever.embedder")
    return ctx.resource("retriever", lambda: StableSemanticRetriever(
        ctx.one("retriever.index_health"), embedder,
        ctx.one("retriever.vector_reader"), ctx.sources,
        ManifestReadGate(manifest, recovery), embedder.identity))


def _evaluator(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    return FormalEvidenceEvaluator(
        ctx.one("evaluator.evaluation_repository"),
        ctx.one("evaluator.preflight_validator"),
        ctx.one("evaluator.retriever"),
        ctx.one("evaluator.metric_calculator"),
        ctx.one("evaluator.evaluation_reporter"),
        ctx.one("evaluator.evaluation_run_store"),
        ctx.one("index_health.manifest_store"),
        ctx.one("index_health.vector_reader"),
        ctx.one("system.index_health"), ctx.sources)


def _chatbot(ctx: RuntimeFactory, _binding: PluginBinding, _params: dict[str, object]):
    llm_binding = ctx.bindings["chatbot.language_model"][0]
    model_name = effective_parameters(llm_binding)["model_name"]
    return SingleTurnChatbot(ctx.one("chatbot.retriever"),
                             ctx.one("chatbot.prompt_builder"),
                             ctx.one("chatbot.language_model"), model_name)


BUILTIN_FACTORIES: dict[str, PluginFactory] = {
    "registry.local_pdf": lambda ctx, binding, params: LocalPdfRegistry(),
    "indexer.incremental": _indexer,
    "artifacts.stage_snapshot_v3": lambda ctx, binding, params: SnapshotArtifactBuilder(ctx.workspace_root),
    "health.strict": _health,
    "retriever.semantic_stable_topk": _retriever,
    "chatbot.single_turn": _chatbot,
    "evaluator.formal": _evaluator,
    "eval.local_reviewed": lambda ctx, binding, params:
        LocalReviewedEvidenceRepository(ctx.workspace_root),
    "eval.preflight_v1": lambda ctx, binding, params:
        ReviewedEvidencePreflight(ctx.workspace_root),
    "eval.evidence_metrics": lambda ctx, binding, params: EvidenceMetricCalculator(),
    "eval.json_html": lambda ctx, binding, params: JsonHtmlEvaluationReporter(),
    "eval.immutable_local": lambda ctx, binding, params:
        LocalImmutableEvaluationRunStore(ctx.workspace_root),
    "recovery.journaled": _recovery,
    "processor.composed_v1": _processor,
    "chunker.page_characters": lambda ctx, binding, params: CharacterChunker(
        params["chunk_size_characters"], params["chunk_overlap_characters"]),
    "chunker.structured_tokens": _structured_chunker,
    "embedder.e5_small": _embedder,
    "publisher.journaled": _publisher,
    "parser.pymupdf_pages": lambda ctx, binding, params: PlainPageMainParser(),
    "parser.docling_layout": lambda ctx, binding, params: DoclingLayoutMainParser(),
    "extractor.pymupdf": lambda ctx, binding, params: pymupdf_extractor(),
    "extractor.camelot": lambda ctx, binding, params: camelot_extractor(),
    "extractor.docling": lambda ctx, binding, params: DoclingTableExtractor(),
    "extractor.unstructured": lambda ctx, binding, params: unstructured_extractor(
        ctx.workspace_root / "tmp" / "v3-unstructured-work"),
    "selector.table_v1": lambda ctx, binding, params: TableSelectorPlugin(
        ctx.one("table_selector.pdf_evidence_reader")),
    "pdf_evidence.pymupdf": lambda ctx, binding, params: PyMuPdfEvidenceReader(),
    "assembler.composed_v1": _assembler,
    "table_content.serial_v1": _content,
    "assembler.ordered": lambda ctx, binding, params: DocumentComposerPlugin(),
    "header.table_v1": lambda ctx, binding, params: HeaderDetectorPlugin(
        params["sample_row_budget"], params["minimum_independent_observations"]),
    "serializer.table_text_v1": lambda ctx, binding, params: TableSerializerPlugin(),
    "store.chroma_cosine": _vector,
    "store.json_manifest": _manifest,
    "store.local_recovery": _recovery_store,
    "store.local_artifacts": _artifacts,
    "probe.pymupdf": lambda ctx, binding, params: PdfSourceProbe(),
    "reader.chroma_cosine": _vector,
    "prompt.grounded": lambda ctx, binding, params: GroundedPromptBuilder(
        GroundedPromptParameters(params["source_label_style"])),
    "llm.openrouter": lambda ctx, binding, params: OpenRouterLanguageModel(
        ctx.api_key_provider, auth_retry_provider=ctx.auth_retry_provider),
}
