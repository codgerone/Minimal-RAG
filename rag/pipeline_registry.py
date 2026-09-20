"""Pipeline identity registry with fixed, isolated persistence targets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from rag.errors import ConfigurationError
from rag.pipeline_types import PipelineId



@dataclass(frozen=True)
class PipelineIdentity:
    pipeline_id: PipelineId
    collection_name: str
    manifest_path: Path


class BuildConfigIdentity(Protocol):
    pipeline_id: PipelineId


class DocumentProcessor(Protocol):
    def process(self, source: Any, build_id: str) -> Any: ...


@dataclass(frozen=True)
class PipelineRuntime:
    pipeline_id: PipelineId
    collection_name: str
    manifest_path: Path
    build_config: BuildConfigIdentity
    build_config_fingerprint: str
    document_processor: DocumentProcessor


def build_config_fingerprint(build_config: BuildConfigIdentity) -> str:
    """Hash a dataclass BuildConfig using canonical UTF-8 JSON."""
    if not is_dataclass(build_config) or isinstance(build_config, type):
        raise ConfigurationError("BuildConfig 必须是 dataclass 实例。")
    payload = json.dumps(
        asdict(build_config),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class PipelineRegistry:
    """Resolve a pipeline to its immutable storage identity.

    The registry deliberately contains no Chroma or model construction.  Callers
    must resolve and validate the selected identity before opening external
    resources.
    """

    _COLLECTIONS: dict[PipelineId, str] = {
        "v1": "minimal_rag_documents_v1",
        "v2": "minimal_rag_documents_v2",
    }
    _MANIFESTS: dict[PipelineId, Path] = {
        "v1": Path(".rag/system-v2/pipelines/v1/manifest.json"),
        "v2": Path(".rag/system-v2/pipelines/v2/manifest.json"),
    }

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def get(self, pipeline_id: str) -> PipelineIdentity:
        if pipeline_id not in self._COLLECTIONS:
            raise ConfigurationError(
                f"不支持的 pipeline：{pipeline_id!r}。",
                "请选择 v1 或 v2。",
            )
        selected = cast(PipelineId, pipeline_id)
        manifest_path = (self.project_root / self._MANIFESTS[selected]).resolve()
        if not manifest_path.is_relative_to(self.project_root):
            raise ConfigurationError(
                f"Pipeline {selected} 的 Manifest 路径超出项目目录。"
            )
        return PipelineIdentity(
            pipeline_id=selected,
            collection_name=self._COLLECTIONS[selected],
            manifest_path=manifest_path,
        )

    def create_runtime(
        self,
        pipeline_id: str,
        build_config: BuildConfigIdentity,
        document_processor: DocumentProcessor,
    ) -> PipelineRuntime:
        from rag.build_config import validate_build_config

        identity = self.get(pipeline_id)
        validate_build_config(build_config)  # type: ignore[arg-type]
        if build_config.pipeline_id != identity.pipeline_id:
            raise ConfigurationError(
                "Pipeline 与 BuildConfig 的 pipeline_id 不一致。"
            )
        runtime = PipelineRuntime(
            pipeline_id=identity.pipeline_id,
            collection_name=identity.collection_name,
            manifest_path=identity.manifest_path,
            build_config=build_config,
            build_config_fingerprint=build_config_fingerprint(build_config),
            document_processor=document_processor,
        )
        self.validate_runtime(runtime)
        return runtime

    def validate_runtime(self, runtime: PipelineRuntime) -> None:
        identity = self.get(runtime.pipeline_id)
        if runtime.build_config.pipeline_id != runtime.pipeline_id:
            raise ConfigurationError(
                "Runtime 与 BuildConfig 的 pipeline_id 不一致。"
            )
        if runtime.collection_name != identity.collection_name:
            raise ConfigurationError(
                f"Pipeline {runtime.pipeline_id} 的 Collection 身份不一致。"
            )
        if runtime.manifest_path.resolve() != identity.manifest_path:
            raise ConfigurationError(
                f"Pipeline {runtime.pipeline_id} 的 Manifest 身份不一致。"
            )
        expected_fingerprint = build_config_fingerprint(runtime.build_config)
        if runtime.build_config_fingerprint != expected_fingerprint:
            raise ConfigurationError("BuildConfig fingerprint 不一致。")
