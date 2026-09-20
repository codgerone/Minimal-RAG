from pathlib import Path

from rag.bootstrap import build_config, create_runtime
from rag.config import SelectedPipelineSettings, Settings
from rag.pipeline_registry import PipelineRegistry, build_config_fingerprint


def _settings(tmp_path: Path, pipeline: str) -> SelectedPipelineSettings:
    base = Settings(tmp_path, tmp_path / "documents", tmp_path / ".rag/system-v2/chroma",
                    tmp_path / ".rag/system-v2/artifacts", "model", "revision",
                    700, 100, 512, 32, 4, False, None, "llm")
    return SelectedPipelineSettings(base, PipelineRegistry(tmp_path).get(pipeline))


def test_bootstrap_builds_isolated_consistent_v1_runtime(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "v1")
    runtime = create_runtime(settings, vector_dimension=7)
    assert runtime.pipeline_id == "v1"
    assert runtime.collection_name == "minimal_rag_documents_v1"
    assert runtime.build_config.embedding.vector_dimension == 7
    assert runtime.build_config_fingerprint == build_config_fingerprint(runtime.build_config)


def test_v2_runtime_uses_v2_limits_and_artifact_processor(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "v2")
    config = build_config(settings)
    runtime = create_runtime(settings)
    assert config.pipeline_id == runtime.pipeline_id == "v2"
    assert config.chunker.maximum_input_tokens == 512
    assert runtime.collection_name == "minimal_rag_documents_v2"
    assert runtime.document_processor.__class__.__name__ == "V2DocumentProcessor"
