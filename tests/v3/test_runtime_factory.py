"""The saved assembly selects real nested plugin instances and shared resources."""

import pytest
from dataclasses import asdict

from rag.v3.adapters.e5 import default_identity
from rag.v3.adapters.manifest_store import decode_manifest
from rag.v3.application.assembly import build_projection, builtin_configuration, canonical_json_bytes, index_identity
from rag.v3.application.health import IndexHealth
from rag.v3.contracts.storage import Manifest
from rag.v3.plugins.catalog import PLUGINS
from rag.v3.plugins.runtime_factory import RuntimeFactory


@pytest.mark.parametrize("name", ("plain_text", "structured"))
def test_builtin_runtime_resolves_selected_graph_without_loading_model(tmp_path, name):
    config = builtin_configuration(name)
    factory = RuntimeFactory(config, tmp_path)
    indexer = factory.one("system.indexer")
    retriever = factory.one("system.retriever")
    chatbot = factory.one("system.chatbot")
    evaluator = factory.one("system.evaluator")

    assert indexer.embedder is retriever.embedder
    assert indexer.publisher.vector is factory.one("publisher.vector_store")
    assert indexer.publisher.vector is factory.one("retriever.vector_reader")
    assert chatbot.retriever.embedder is retriever.embedder
    assert chatbot.retriever.vectors is retriever.vectors
    assert chatbot.retriever is retriever
    assert evaluator.retriever is retriever
    assert factory.runtime_binding().index_identity == factory.index
    assert {item.plugin_id for item in factory.runtime_binding().resolved_plugins}.issubset(
        {item.plugin_id for item in config.bindings})

    if name == "structured":
        processor = factory.one("indexer.document_processor")
        selector = processor.selector
        assert selector.evidence is factory.one("table_selector.pdf_evidence_reader")
        assert len(processor.extractors) == 4
    else:
        assert not factory.many("document_processor.table_extractor")
        assert factory.optional("document_processor.table_selector") is None


def test_selected_plugin_without_installed_factory_fails_before_execution(tmp_path):
    config = builtin_configuration("plain_text")
    factory = RuntimeFactory(config, tmp_path)
    binding = factory.bindings["system.registry"][0]
    factory.factories.pop(PLUGINS[binding.plugin_id].factory_id)
    with pytest.raises(ValueError, match="no installed factory"):
        factory.one("system.registry")


def test_runtime_scope_closes_created_plugins_in_reverse_order_on_failure(tmp_path):
    config = builtin_configuration("plain_text")
    events = []

    class Resource:
        def __init__(self, name):
            self.name = name

        def close(self):
            events.append(self.name)

    registry_id = PLUGINS[next(item.plugin_id for item in config.bindings
        if item.slot_id == "system.registry")].factory_id
    probe_id = PLUGINS[next(item.plugin_id for item in config.bindings
        if item.slot_id == "index_health.source_probe")].factory_id
    factory = RuntimeFactory(config, tmp_path, factories={
        registry_id: lambda *_: Resource("registry"),
        probe_id: lambda *_: Resource("probe"),
    })
    factory.one("system.registry")
    factory.one("index_health.source_probe")
    factory.close()
    factory.close()
    assert events == ["probe", "registry"]

    events.clear()
    failed = RuntimeFactory(config, tmp_path, factories={
        registry_id: lambda *_: Resource("registry"),
        probe_id: lambda *_: (_ for _ in ()).throw(ValueError("construction failed")),
    })
    failed.one("system.registry")
    with pytest.raises(ValueError, match="construction failed"):
        failed.one("index_health.source_probe")
    assert events == ["registry"]


def test_structured_manifest_json_roundtrip_passes_health_projection_check():
    config = builtin_configuration("structured")
    index = index_identity(config)
    projection = build_projection(config)
    original = Manifest("index_manifest_v3", index, projection, default_identity(), {},
                        "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
    decoded = decode_manifest(canonical_json_bytes(asdict(original)), index)

    class ManifestReader:
        def load(self, _index):
            return decoded

    class VectorReader:
        def collection_exists(self, _index):
            return True
        def list_records(self, _index):
            return ()

    class RecoveryReader:
        def list_pending(self, _index):
            return ()

    report = IndexHealth(projection, default_identity(), object(), ManifestReader(),
                         VectorReader(), object(), RecoveryReader()).check(index, ())
    assert report.usable
    assert report.issues == ()
