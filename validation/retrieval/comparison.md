# 检索评估跨版本比较

生成时间：2026-09-29T09:29:26.898016+00:00

| 系统 | 运行 | 装配 | 构建指纹 | 汇总 |
| --- | --- | --- | --- | --- |
| 2.0 | 20260920T052848Z-v1-87059c0f-k3 | pipeline-v1 | `87059c0fd057e5f3c6fe26b9117b4580f57662791079c1afd0ff3c3e019c0750` | [validation/retrieval/system-v2.0/summary.md](validation/retrieval/system-v2.0/summary.md) |
| 2.0 | 20260920T052923Z-v2-029ccb26-k3 | pipeline-v2 | `029ccb26ca98d826cd54d5ba21b1156a086d0718cb7bda78775a232fba68ff93` | [validation/retrieval/system-v2.0/summary.md](validation/retrieval/system-v2.0/summary.md) |
| 3.0 | 20260929T092845Z-f337fe5ba148 | plain_text | `1ce9184dbbe79ece3e185e73885ca28f9709f959e441da58ded8514e44ea5d7b` | [validation/retrieval/system-v3.0/summary.md](validation/retrieval/system-v3.0/summary.md) |
| 3.0 | 20260929T092926Z-0d5a828b38f8 | structured | `1ab42635df0ae02480b0e6cec71454fcb27c473fba729429e18ba7a159875e9e` | [validation/retrieval/system-v3.0/summary.md](validation/retrieval/system-v3.0/summary.md) |

## 运行配置与整体指标

### 2.0 · pipeline-v1 · 20260920T052848Z-v1-87059c0f-k3

collection：`minimal_rag_documents_v1`；构建指纹：`87059c0fd057e5f3c6fe26b9117b4580f57662791079c1afd0ff3c3e019c0750`；K=3；数据集：`4612ed552d4a523a22af19989b8e2b40589d3dc174b891400b9133bb5f98ac23`。

主要构建字段：

```json
{"chunker":{"chunk_overlap_characters":50,"chunk_size_characters":300,"rule_version":"recursive_character_v1","separator_version":"legacy_default_v1"},"embedding":{"model_name":"intfloat/multilingual-e5-small","normalize_embeddings":true,"revision":"614241f622f53c4eeff9890bdc4f31cfecc418b3","vector_dimension":384},"parser":{"rule_version":"pymupdf_text_v1"}}
```

| 指标 | 整体值（分子/分母） |
| --- | ---: |
| macro_chunk_precision_at_k | 0.0556 (1.3333333333333333/24) |
| micro_chunk_precision_at_k | 0.0556 (4/72) |
| macro_evidence_group_recall_at_k | 0.0417 (1.0/24) |
| micro_evidence_group_recall_at_k | 0.0233 (1/43) |
| question_hit_rate_at_k | 0.0417 (1.0/24) |
| complete_coverage_rate_at_k | 0.0417 (1.0/24) |
| mean_reciprocal_rank_at_k | 0.0903 (2.1666666666666665/24) |
| evidence_group_mean_reciprocal_rank_at_k | 0.0078 (0.3333333333333333/43) |
| macro_cross_document_contamination_at_k | 0.5417 (13.0/24) |
| micro_cross_document_contamination_at_k | 0.5417 (39/72) |

### 2.0 · pipeline-v2 · 20260920T052923Z-v2-029ccb26-k3

collection：`minimal_rag_documents_v2`；构建指纹：`029ccb26ca98d826cd54d5ba21b1156a086d0718cb7bda78775a232fba68ff93`；K=3；数据集：`4612ed552d4a523a22af19989b8e2b40589d3dc174b891400b9133bb5f98ac23`。

主要构建字段：

```json
{"chunker":{"maximum_input_tokens":512,"rule_version":"structured_chunk_v1","separator_version":"recursive_boundaries_v1","text_overlap_tokens":32},"embedding":{"model_name":"intfloat/multilingual-e5-small","normalize_embeddings":true,"revision":"614241f622f53c4eeff9890bdc4f31cfecc418b3","vector_dimension":384},"parser":{"do_cell_matching":true,"do_ocr":false,"do_table_structure":true,"docling_version":"2.121.0","generate_page_images":false,"mapper_version":"docling_mapper_v2","rule_version":"docling_layout_v1","table_mode":"accurate"}}
```

| 指标 | 整体值（分子/分母） |
| --- | ---: |
| macro_chunk_precision_at_k | 0.1111 (2.6666666666666665/24) |
| micro_chunk_precision_at_k | 0.1111 (8/72) |
| macro_evidence_group_recall_at_k | 0.1389 (3.3333333333333335/24) |
| micro_evidence_group_recall_at_k | 0.1163 (5/43) |
| question_hit_rate_at_k | 0.2083 (5.0/24) |
| complete_coverage_rate_at_k | 0.0833 (2.0/24) |
| mean_reciprocal_rank_at_k | 0.1597 (3.833333333333333/24) |
| evidence_group_mean_reciprocal_rank_at_k | 0.0659 (2.833333333333333/43) |
| macro_cross_document_contamination_at_k | 0.6528 (15.666666666666668/24) |
| micro_cross_document_contamination_at_k | 0.6528 (47/72) |

### 3.0 · plain_text · 20260929T092845Z-f337fe5ba148

collection：`rag_v3_1ce9184dbbe79ece3e185e73885ca28f`；构建指纹：`1ce9184dbbe79ece3e185e73885ca28f9709f959e441da58ded8514e44ea5d7b`；K=3；数据集：`4612ed552d4a523a22af19989b8e2b40589d3dc174b891400b9133bb5f98ac23`。

主要构建字段：

```json
{"document_processor.main_parser":{"parameters":{"rule_version":"pymupdf_text_v1"},"plugin_id":"parser.pymupdf_pages"},"indexer.chunker":{"parameters":{"chunk_overlap_characters":50,"chunk_size_characters":300,"rule_version":"recursive_character_v1","separator_version":"legacy_default_v1"},"plugin_id":"chunker.page_characters"},"indexer.embedder":{"parameters":{"add_special_tokens":true,"model_name":"intfloat/multilingual-e5-small","normalize_embeddings":true,"passage_prefix":"passage: ","query_prefix":"query: ","revision":"614241f622f53c4eeff9890bdc4f31cfecc418b3","vector_dimension":384},"plugin_id":"embedder.e5_small"}}
```

| 指标 | 整体值（分子/分母） |
| --- | ---: |
| macro_chunk_precision_at_k | 0.0556 (1.3333333333333333/24) |
| micro_chunk_precision_at_k | 0.0556 (4.0/72) |
| macro_evidence_group_recall_at_k | 0.0417 (1.0/24) |
| micro_evidence_group_recall_at_k | 0.0233 (1.0/43) |
| question_hit_rate_at_k | 0.0417 (1.0/24) |
| complete_coverage_rate_at_k | 0.0417 (1.0/24) |
| mean_reciprocal_rank_at_k | 0.0903 (2.1666666666666665/24) |
| evidence_group_mean_reciprocal_rank_at_k | 0.0078 (0.3333333333333333/43) |
| macro_cross_document_contamination_at_k | 0.5417 (13.0/24) |
| micro_cross_document_contamination_at_k | 0.5417 (39.0/72) |

### 3.0 · structured · 20260929T092926Z-0d5a828b38f8

collection：`rag_v3_1ab42635df0ae02480b0e6cec71454fc`；构建指纹：`1ab42635df0ae02480b0e6cec71454fcb27c473fba729429e18ba7a159875e9e`；K=3；数据集：`4612ed552d4a523a22af19989b8e2b40589d3dc174b891400b9133bb5f98ac23`。

主要构建字段：

```json
{"document_processor.main_parser":{"parameters":{"do_cell_matching":true,"do_ocr":false,"do_table_structure":true,"docling_version":"2.121.0","generate_page_images":false,"mapper_version":"docling_mapper_v2","rule_version":"docling_layout_v1","table_mode":"accurate"},"plugin_id":"parser.docling_layout"},"indexer.chunker":{"parameters":{"document_text_rule_version":"document_text_v1","maximum_input_tokens":512,"rule_version":"structured_chunk_v1","separator_version":"recursive_boundaries_v1","text_overlap_tokens":32},"plugin_id":"chunker.structured_tokens"},"indexer.embedder":{"parameters":{"add_special_tokens":true,"model_name":"intfloat/multilingual-e5-small","normalize_embeddings":true,"passage_prefix":"passage: ","query_prefix":"query: ","revision":"614241f622f53c4eeff9890bdc4f31cfecc418b3","vector_dimension":384},"plugin_id":"embedder.e5_small"}}
```

| 指标 | 整体值（分子/分母） |
| --- | ---: |
| macro_chunk_precision_at_k | 0.1111 (2.6666666666666665/24) |
| micro_chunk_precision_at_k | 0.1111 (8.0/72) |
| macro_evidence_group_recall_at_k | 0.1389 (3.3333333333333335/24) |
| micro_evidence_group_recall_at_k | 0.1163 (5.0/43) |
| question_hit_rate_at_k | 0.2083 (5.0/24) |
| complete_coverage_rate_at_k | 0.0833 (2.0/24) |
| mean_reciprocal_rank_at_k | 0.1597 (3.833333333333333/24) |
| evidence_group_mean_reciprocal_rank_at_k | 0.0659 (2.833333333333333/43) |
| macro_cross_document_contamination_at_k | 0.6528 (15.666666666666668/24) |
| micro_cross_document_contamination_at_k | 0.6528 (47.0/72) |


| 左 | 右 | 可比性 | 差异原因 |
| --- | --- | --- | --- |
| 2.0/20260920T052848Z-v1-87059c0f-k3 | 2.0/20260920T052923Z-v2-029ccb26-k3 | strictly_comparable | 无 |

| 指标 | 左 | 右 | 右减左 |
| --- | ---: | ---: | ---: |
| question_hit_rate_at_k | 0.0417 | 0.2083 | +0.1667 |
| macro_evidence_group_recall_at_k | 0.0417 | 0.1389 | +0.0972 |
| complete_coverage_rate_at_k | 0.0417 | 0.0833 | +0.0417 |
| macro_chunk_precision_at_k | 0.0556 | 0.1111 | +0.0556 |
| mean_reciprocal_rank_at_k | 0.0903 | 0.1597 | +0.0694 |
| macro_cross_document_contamination_at_k | 0.5417 | 0.6528 | +0.1111 |
| 2.0/20260920T052848Z-v1-87059c0f-k3 | 3.0/20260929T092845Z-f337fe5ba148 | protocol_changed | annotation_rule_version |
| 2.0/20260920T052848Z-v1-87059c0f-k3 | 3.0/20260929T092926Z-0d5a828b38f8 | protocol_changed | annotation_rule_version |
| 2.0/20260920T052923Z-v2-029ccb26-k3 | 3.0/20260929T092845Z-f337fe5ba148 | protocol_changed | annotation_rule_version |
| 2.0/20260920T052923Z-v2-029ccb26-k3 | 3.0/20260929T092926Z-0d5a828b38f8 | protocol_changed | annotation_rule_version |
| 3.0/20260929T092845Z-f337fe5ba148 | 3.0/20260929T092926Z-0d5a828b38f8 | strictly_comparable | 无 |

| 指标 | 左 | 右 | 右减左 |
| --- | ---: | ---: | ---: |
| question_hit_rate_at_k | 0.0417 | 0.2083 | +0.1667 |
| macro_evidence_group_recall_at_k | 0.0417 | 0.1389 | +0.0972 |
| complete_coverage_rate_at_k | 0.0417 | 0.0833 | +0.0417 |
| macro_chunk_precision_at_k | 0.0556 | 0.1111 | +0.0556 |
| mean_reciprocal_rank_at_k | 0.0903 | 0.1597 | +0.0694 |
| macro_cross_document_contamination_at_k | 0.5417 | 0.6528 | +0.1111 |
