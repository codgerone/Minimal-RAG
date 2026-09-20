"""Pipeline-specific document processors behind the shared runtime boundary."""

from __future__ import annotations

import shutil

from rag.chunker import chunk_pages, make_splitter
from rag.errors import ManifestError
from rag.models import DocumentBuildResult, DocumentBuildStats, SourceDocument
from rag.pdf_parser import parse_pdf


class V1DocumentProcessor:
    """Preserve the existing page-bounded PyMuPDF and character chunking path."""

    def __init__(self, chunk_size: int, chunk_overlap: int) -> None:
        self.splitter = make_splitter(chunk_size, chunk_overlap)

    def process(self, source: SourceDocument, build_id: str) -> DocumentBuildResult:
        pages = tuple(parse_pdf(source))
        chunks = tuple(chunk_pages(pages, source.file_hash, self.splitter))
        if not chunks:
            raise ManifestError(f"文档没有生成任何 chunk：{source.relative_path}")
        return DocumentBuildResult(
            source=source,
            pipeline_id="v1",
            build_id=build_id,
            chunks=chunks,
            stats=DocumentBuildStats(
                page_count=len(pages),
                character_count=sum(len(page.text) for page in pages),
                chunk_count=len(chunks),
            ),
            artifact_stage=None,
        )


class V2DocumentProcessor:
    """Execute the complete, independently persisted V2 document pipeline."""

    def __init__(
        self,
        *,
        build_config,
        build_config_fingerprint: str,
        artifacts_root,
        diagnostics_enabled: bool,
        token_counter=None,
        docling_converter=None,
        extra_adapters=None,
    ) -> None:
        from rag.embeddings import E5TokenCounter
        from rag.v2.docling_runner import convert_document

        self.build_config = build_config
        self.build_config_fingerprint = build_config_fingerprint
        self.artifacts_root = artifacts_root
        self.diagnostics_enabled = diagnostics_enabled
        self.token_counter = token_counter or E5TokenCounter(
            build_config.tokenizer.model_name, build_config.tokenizer.revision,
        )
        self.docling_converter = docling_converter or convert_document
        self.extra_adapters = extra_adapters

    def process(self, source: SourceDocument, build_id: str) -> DocumentBuildResult:
        import pymupdf

        from rag.v2.artifacts import stage_artifacts
        from rag.v2.chunking import chunk_parsed_document
        from rag.v2.docling_mapper import map_docling_document
        from rag.v2.extractors.camelot import make_adapters as camelot_adapters
        from rag.v2.extractors.pymupdf import make_adapters as pymupdf_adapters
        from rag.v2.extractors.unstructured import make_adapter as unstructured_adapter
        from rag.v2.parsed_document import assemble_parsed_document
        from rag.v2.table_extraction import StrategyAdapter, run_extraction
        from rag.v2.table_scoring import ScoringReport, build_slot_text_reference, score_group
        from rag.v2.table_selection import (
            GroupingReport, build_slot_groups, candidate_view,
            decide_candidate_admissions, evaluate_slot_matches,
        )

        from rag.document_registry import make_artifact_document_name

        artifact_document = make_artifact_document_name(source.relative_path, source.document_id)
        staging_root = self.artifacts_root / "v2" / "staging" / artifact_document / build_id
        work_root = staging_root / ".work"
        extraction_pdf = source.absolute_path
        try:
            work_root.mkdir(parents=True, exist_ok=True)
            with pymupdf.open(source.absolute_path) as pdf:
                if len(pdf) <= 0:
                    raise ValueError("PDF 没有页面。")
                page_sizes = {
                    index + 1: (float(page.rect.width), float(page.rect.height))
                    for index, page in enumerate(pdf)
                }
            raw_document = self.docling_converter(extraction_pdf)
            mapped = map_docling_document(raw_document, source, page_sizes)
            assigned_page = {}
            for candidate in mapped.docling_table_candidates:
                pages = [region.page_number for region in candidate.regions if region.page_number is not None]
                assigned_page[candidate.candidate_id] = pages[0] if pages else 1

            def docling_page(_path, page_number):
                return tuple(candidate for candidate in mapped.docling_table_candidates
                             if assigned_page[candidate.candidate_id] == page_number)

            adapters = {}
            adapters.update(pymupdf_adapters())
            adapters.update(camelot_adapters())
            adapters[("docling", "accurate")] = StrategyAdapter("docling", "accurate", docling_page)
            adapters[("unstructured", "hi_res")] = unstructured_adapter(work_root)
            if self.extra_adapters:
                adapters.update(self.extra_adapters)
            extraction = run_extraction(extraction_pdf, source.file_hash, len(page_sizes), adapters)
            views = tuple(candidate_view(item) for item in extraction.candidates)
            evaluations = evaluate_slot_matches(
                views, mapped.layout_document.table_slots,
                self.build_config.table_selection.candidate_coverage_minimum,
                self.build_config.table_selection.slot_coverage_minimum,
                self.build_config.table_selection.comparison_epsilon,
            )
            admissions = decide_candidate_admissions(views, evaluations)
            groups = build_slot_groups(mapped.layout_document.table_slots, views, admissions)
            grouping = GroupingReport(mapped.layout_document.table_slots, views, evaluations,
                                      admissions, groups, extraction.warnings)
            candidate_by_id = {item.candidate_id: item for item in extraction.candidates}
            scored = []
            for group in groups:
                if group.status != "ready_for_scoring":
                    continue
                if group.page_number is None or group.slot_bbox is None:
                    raise ValueError(f"ready group {group.group_id} 缺少定位。")
                reference = build_slot_text_reference(
                    source.absolute_path, group.slot_id, group.page_number, group.slot_bbox,
                )
                scored.append(score_group(
                    group.group_id, group.slot_id, reference,
                    tuple(candidate_by_id[item] for item in group.member_candidate_ids),
                ))
            scoring = ScoringReport(source.absolute_path.as_posix(), tuple(scored), ())
            parsed = assemble_parsed_document(source, mapped.layout_document, grouping,
                                              scoring, extraction.candidates)
            chunked = chunk_parsed_document(
                parsed, self.token_counter,
                max_input_tokens=self.build_config.chunker.maximum_input_tokens,
                overlap_tokens=self.build_config.chunker.text_overlap_tokens,
            )
            if chunked.warnings:
                parsed = type(parsed)(parsed.document_id, parsed.document_name, parsed.relative_path,
                                      parsed.file_hash, parsed.nodes, parsed.warnings + chunked.warnings)
            if work_root.exists():
                shutil.rmtree(work_root)
            artifact_stage = stage_artifacts(
                artifacts_root=self.artifacts_root, raw_document=raw_document, parsed=parsed,
                chunks=chunked.chunks, grouping=grouping, scoring=scoring, build_id=build_id,
                build_config_fingerprint=self.build_config_fingerprint,
                diagnostics_enabled=self.diagnostics_enabled,
            )
            return DocumentBuildResult(
                source, "v2", build_id, chunked.chunks,
                DocumentBuildStats(len(page_sizes), chunked.character_count, len(chunked.chunks)),
                artifact_stage,
            )
        except ManifestError:
            if staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)
            raise
        except Exception as exc:
            if staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)
            raise ManifestError(
                f"V2 文档处理失败：{source.relative_path}",
                f"{type(exc).__name__}: {exc}", cause=exc,
            ) from exc
