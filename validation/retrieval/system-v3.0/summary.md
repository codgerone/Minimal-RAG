# V3.0 检索评估汇总

数据集：2.0.0 / `4612ed552d4a523a22af19989b8e2b40589d3dc174b891400b9133bb5f98ac23`；PDF 5 份；题目 24 道。

正式基线：正式基线未选定

| 装配 | run ID | 索引 | K | 状态 | 整体 Question Hit | 组召回 | 完整覆盖 | Chunk Precision | MRR | 跨文档污染 |
| --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| plain_text | 20260929T092845Z-f337fe5ba148 | `1ce9184dbbe79ece3e185e73885ca28f9709f959e441da58ded8514e44ea5d7b` | 3 | completed | 0.0417 | 0.0417 | 0.0417 | 0.0556 | 0.0903 | 0.5417 |
| structured | 20260929T092926Z-0d5a828b38f8 | `1ab42635df0ae02480b0e6cec71454fcb27c473fba729429e18ba7a159875e9e` | 3 | completed | 0.2083 | 0.1389 | 0.0833 | 0.1111 | 0.1597 | 0.6528 |

## 运行配置与逐 PDF 指标

### plain_text · 20260929T092845Z-f337fe5ba148

collection：`rag_v3_1ce9184dbbe79ece3e185e73885ca28f`；构建指纹：`1ce9184dbbe79ece3e185e73885ca28f9709f959e441da58ded8514e44ea5d7b`；K=3；查询前缀：`query: `；状态：completed；失败数：0。

主要构建字段：

```json
{"document_processor.main_parser":{"parameters":{"rule_version":"pymupdf_text_v1"},"plugin_id":"parser.pymupdf_pages"},"indexer.chunker":{"parameters":{"chunk_overlap_characters":50,"chunk_size_characters":300,"rule_version":"recursive_character_v1","separator_version":"legacy_default_v1"},"plugin_id":"chunker.page_characters"},"indexer.embedder":{"parameters":{"add_special_tokens":true,"model_name":"intfloat/multilingual-e5-small","normalize_embeddings":true,"passage_prefix":"passage: ","query_prefix":"query: ","revision":"614241f622f53c4eeff9890bdc4f31cfecc418b3","vector_dimension":384},"plugin_id":"embedder.e5_small"}}
```

完整配置：[run.json](runs/assembly-plain_text__cfg-1ce9184dbbe7__k-3__20260929T092845Z-f337fe5ba148/run.json)

| PDF / 汇总 | 可回答 | 不可回答 | unmappable | macro_chunk_precision_at_k | micro_chunk_precision_at_k | macro_evidence_group_recall_at_k | micro_evidence_group_recall_at_k | question_hit_rate_at_k | complete_coverage_rate_at_k | mean_reciprocal_rank_at_k | evidence_group_mean_reciprocal_rank_at_k | macro_cross_document_contamination_at_k | micro_cross_document_contamination_at_k |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 169a1f2133fe5643 | 6 | 0 | 0 | 0.0000 (0.0/6) | 0.0000 (0.0/18) | 0.0000 (0.0/6) | 0.0000 (0.0/6) | 0.0000 (0.0/6) | 0.0000 (0.0/6) | 0.0000 (0.0/6) | 0.0000 (0.0/6) | 1.0000 (6.0/6) | 1.0000 (18.0/18) |
| 5883b02cd922a64f | 2 | 0 | 0 | 0.0000 (0.0/2) | 0.0000 (0.0/6) | 0.0000 (0.0/2) | 0.0000 (0.0/4) | 0.0000 (0.0/2) | 0.0000 (0.0/2) | 0.0000 (0.0/2) | 0.0000 (0.0/4) | 0.6667 (1.3333333333333333/2) | 0.6667 (4.0/6) |
| 5a4c36e0022f4917 | 4 | 0 | 0 | 0.0000 (0.0/4) | 0.0000 (0.0/12) | 0.0000 (0.0/4) | 0.0000 (0.0/10) | 0.0000 (0.0/4) | 0.0000 (0.0/4) | 0.0000 (0.0/4) | 0.0000 (0.0/10) | 0.3333 (1.3333333333333333/4) | 0.3333 (4.0/12) |
| 817cfff2927c6c65 | 2 | 0 | 0 | 0.1667 (0.3333333333333333/2) | 0.1667 (1.0/6) | 0.5000 (1.0/2) | 0.3333 (1.0/3) | 0.5000 (1.0/2) | 0.5000 (1.0/2) | 0.1667 (0.3333333333333333/2) | 0.1111 (0.3333333333333333/3) | 0.8333 (1.6666666666666665/2) | 0.8333 (5.0/6) |
| d359c37e0bc3c87a | 10 | 0 | 0 | 0.1000 (1.0/10) | 0.1000 (3.0/30) | 0.0000 (0.0/10) | 0.0000 (0.0/20) | 0.0000 (0.0/10) | 0.0000 (0.0/10) | 0.1833 (1.8333333333333333/10) | 0.0000 (0.0/20) | 0.2667 (2.6666666666666665/10) | 0.2667 (8.0/30) |
| 整体 | 24 | 0 | 0 | 0.0556 (1.3333333333333333/24) | 0.0556 (4.0/72) | 0.0417 (1.0/24) | 0.0233 (1.0/43) | 0.0417 (1.0/24) | 0.0417 (1.0/24) | 0.0903 (2.1666666666666665/24) | 0.0078 (0.3333333333333333/43) | 0.5417 (13.0/24) | 0.5417 (39.0/72) |

### structured · 20260929T092926Z-0d5a828b38f8

collection：`rag_v3_1ab42635df0ae02480b0e6cec71454fc`；构建指纹：`1ab42635df0ae02480b0e6cec71454fcb27c473fba729429e18ba7a159875e9e`；K=3；查询前缀：`query: `；状态：completed；失败数：0。

主要构建字段：

```json
{"document_processor.main_parser":{"parameters":{"do_cell_matching":true,"do_ocr":false,"do_table_structure":true,"docling_version":"2.121.0","generate_page_images":false,"mapper_version":"docling_mapper_v2","rule_version":"docling_layout_v1","table_mode":"accurate"},"plugin_id":"parser.docling_layout"},"indexer.chunker":{"parameters":{"document_text_rule_version":"document_text_v1","maximum_input_tokens":512,"rule_version":"structured_chunk_v1","separator_version":"recursive_boundaries_v1","text_overlap_tokens":32},"plugin_id":"chunker.structured_tokens"},"indexer.embedder":{"parameters":{"add_special_tokens":true,"model_name":"intfloat/multilingual-e5-small","normalize_embeddings":true,"passage_prefix":"passage: ","query_prefix":"query: ","revision":"614241f622f53c4eeff9890bdc4f31cfecc418b3","vector_dimension":384},"plugin_id":"embedder.e5_small"}}
```

完整配置：[run.json](runs/assembly-structured__cfg-1ab42635df0a__k-3__20260929T092926Z-0d5a828b38f8/run.json)

| PDF / 汇总 | 可回答 | 不可回答 | unmappable | macro_chunk_precision_at_k | micro_chunk_precision_at_k | macro_evidence_group_recall_at_k | micro_evidence_group_recall_at_k | question_hit_rate_at_k | complete_coverage_rate_at_k | mean_reciprocal_rank_at_k | evidence_group_mean_reciprocal_rank_at_k | macro_cross_document_contamination_at_k | micro_cross_document_contamination_at_k |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 169a1f2133fe5643 | 6 | 0 | 0 | 0.0000 (0.0/6) | 0.0000 (0.0/18) | 0.0000 (0.0/6) | 0.0000 (0.0/6) | 0.0000 (0.0/6) | 0.0000 (0.0/6) | 0.0000 (0.0/6) | 0.0000 (0.0/6) | 1.0000 (6.0/6) | 1.0000 (18.0/18) |
| 5883b02cd922a64f | 2 | 0 | 0 | 0.3333 (0.6666666666666666/2) | 0.3333 (2.0/6) | 0.5000 (1.0/2) | 0.5000 (2.0/4) | 1.0000 (2.0/2) | 0.0000 (0.0/2) | 0.5000 (1.0/2) | 0.2500 (1.0/4) | 0.6667 (1.3333333333333333/2) | 0.6667 (4.0/6) |
| 5a4c36e0022f4917 | 4 | 0 | 0 | 0.3333 (1.3333333333333333/4) | 0.3333 (4.0/12) | 0.0833 (0.3333333333333333/4) | 0.1000 (1.0/10) | 0.2500 (1.0/4) | 0.0000 (0.0/4) | 0.3750 (1.5/4) | 0.0500 (0.5/10) | 0.1667 (0.6666666666666666/4) | 0.1667 (2.0/12) |
| 817cfff2927c6c65 | 2 | 0 | 0 | 0.1667 (0.3333333333333333/2) | 0.1667 (1.0/6) | 0.5000 (1.0/2) | 0.3333 (1.0/3) | 0.5000 (1.0/2) | 0.5000 (1.0/2) | 0.1667 (0.3333333333333333/2) | 0.1111 (0.3333333333333333/3) | 0.8333 (1.6666666666666665/2) | 0.8333 (5.0/6) |
| d359c37e0bc3c87a | 10 | 0 | 0 | 0.0333 (0.3333333333333333/10) | 0.0333 (1.0/30) | 0.1000 (1.0/10) | 0.0500 (1.0/20) | 0.1000 (1.0/10) | 0.1000 (1.0/10) | 0.1000 (1.0/10) | 0.0500 (1.0/20) | 0.6000 (6.0/10) | 0.6000 (18.0/30) |
| 整体 | 24 | 0 | 0 | 0.1111 (2.6666666666666665/24) | 0.1111 (8.0/72) | 0.1389 (3.3333333333333335/24) | 0.1163 (5.0/43) | 0.2083 (5.0/24) | 0.0833 (2.0/24) | 0.1597 (3.833333333333333/24) | 0.0659 (2.833333333333333/43) | 0.6528 (15.666666666666668/24) | 0.6528 (47.0/72) |

## 可比性

| 左 | 右 | 状态 | 差异原因 |
| --- | --- | --- | --- |
| 2.0/20260920T052848Z-v1-87059c0f-k3 | 2.0/20260920T052923Z-v2-029ccb26-k3 | strictly_comparable | 无 |
| 2.0/20260920T052848Z-v1-87059c0f-k3 | 3.0/20260929T092845Z-f337fe5ba148 | protocol_changed | annotation_rule_version |
| 2.0/20260920T052848Z-v1-87059c0f-k3 | 3.0/20260929T092926Z-0d5a828b38f8 | protocol_changed | annotation_rule_version |
| 2.0/20260920T052923Z-v2-029ccb26-k3 | 3.0/20260929T092845Z-f337fe5ba148 | protocol_changed | annotation_rule_version |
| 2.0/20260920T052923Z-v2-029ccb26-k3 | 3.0/20260929T092926Z-0d5a828b38f8 | protocol_changed | annotation_rule_version |
| 3.0/20260929T092845Z-f337fe5ba148 | 3.0/20260929T092926Z-0d5a828b38f8 | strictly_comparable | 无 |

## 本次 PDF 结果

- Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R].pdf · SHA `d8700a61b3d7e8d82619bfce60c339844936fbd9c7a9c5055846a82795adc82c` · [JSON](runs/assembly-structured__cfg-1ab42635df0a__k-3__20260929T092926Z-0d5a828b38f8/documents/Contrato-N-105-2025-LP-002-2024-FONAFE-Adquisición-de-me--d359c37e0bc3c87a.json) · [HTML](runs/assembly-structured__cfg-1ab42635df0a__k-3__20260929T092926Z-0d5a828b38f8/documents/Contrato-N-105-2025-LP-002-2024-FONAFE-Adquisición-de-me--d359c37e0bc3c87a.html)
- E001-602.pdf · SHA `22e75e312a2e2111326412927d76053ba8d74c95d458203623afce956e663239` · [JSON](runs/assembly-structured__cfg-1ab42635df0a__k-3__20260929T092926Z-0d5a828b38f8/documents/E001-602--817cfff2927c6c65.json) · [HTML](runs/assembly-structured__cfg-1ab42635df0a__k-3__20260929T092926Z-0d5a828b38f8/documents/E001-602--817cfff2927c6c65.html)
- ORDER PERU 24-12 ANDET.pdf · SHA `27b3e11879c4adef8bd5cd1b91d1f29322179959196c0412815fc767563ad4d3` · [JSON](runs/assembly-structured__cfg-1ab42635df0a__k-3__20260929T092926Z-0d5a828b38f8/documents/ORDER-PERU-24-12-ANDET--169a1f2133fe5643.json) · [HTML](runs/assembly-structured__cfg-1ab42635df0a__k-3__20260929T092926Z-0d5a828b38f8/documents/ORDER-PERU-24-12-ANDET--169a1f2133fe5643.html)
- ORDER PERU 25-15 ANDET VF.pdf · SHA `af1613f91c9aa298133e5d1328db4040c5dc156968102b41bc4340e456fce7a6` · [JSON](runs/assembly-structured__cfg-1ab42635df0a__k-3__20260929T092926Z-0d5a828b38f8/documents/ORDER-PERU-25-15-ANDET-VF--5883b02cd922a64f.json) · [HTML](runs/assembly-structured__cfg-1ab42635df0a__k-3__20260929T092926Z-0d5a828b38f8/documents/ORDER-PERU-25-15-ANDET-VF--5883b02cd922a64f.html)
- 签字-ORDER PERU 25-19 FONAFE.pdf · SHA `04d82628210338b261fbb0809d53bf5b2c85ca1f524940a0e3c9fcf712329e89` · [JSON](runs/assembly-structured__cfg-1ab42635df0a__k-3__20260929T092926Z-0d5a828b38f8/documents/签字-ORDER-PERU-25-19-FONAFE--5a4c36e0022f4917.json) · [HTML](runs/assembly-structured__cfg-1ab42635df0a__k-3__20260929T092926Z-0d5a828b38f8/documents/签字-ORDER-PERU-25-19-FONAFE--5a4c36e0022f4917.html)
