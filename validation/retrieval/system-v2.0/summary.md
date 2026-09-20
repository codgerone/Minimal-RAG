# RAG v2.0 检索效果汇总

| Pipeline | Build fingerprint | K | Hit@K | Group Recall@K | Complete@K | Chunk Precision@K | MRR@K | Cross-doc contamination | Run |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| v1 | `87059c0fd057` | 3 | 4.17% | 4.17% | 4.17% | 5.56% | 9.03% | 54.17% | `20260920T052848Z-v1-87059c0f-k3` |
| v2 | `029ccb26ca98` | 3 | 20.83% | 13.89% | 8.33% | 11.11% | 15.97% | 65.28% | `20260920T052923Z-v2-029ccb26-k3` |

## v1 · K=3 · `20260920T052848Z-v1-87059c0f-k3`

| PDF（人工审核） | Questions | Hit@K | Group Recall@K | Complete@K | Chunk Precision@K |
|---|---:|---:|---:|---:|---:|
| [Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R].pdf](runs/pipeline-v1__cfg-87059c0fd057__k-3__20260920T052848Z-v1-87059c0f-k3/documents/Contrato-N-105-2025-LP-002-2024-FONAFE-Adquisición-de-me--d359c37e0bc3c87a.html) | 10 | 0.00% | 0.00% | 0.00% | 10.00% |
| [E001-602.pdf](runs/pipeline-v1__cfg-87059c0fd057__k-3__20260920T052848Z-v1-87059c0f-k3/documents/E001-602--817cfff2927c6c65.html) | 2 | 50.00% | 50.00% | 50.00% | 16.67% |
| [ORDER PERU 24-12 ANDET.pdf](runs/pipeline-v1__cfg-87059c0fd057__k-3__20260920T052848Z-v1-87059c0f-k3/documents/ORDER-PERU-24-12-ANDET--169a1f2133fe5643.html) | 6 | 0.00% | 0.00% | 0.00% | 0.00% |
| [ORDER PERU 25-15 ANDET VF.pdf](runs/pipeline-v1__cfg-87059c0fd057__k-3__20260920T052848Z-v1-87059c0f-k3/documents/ORDER-PERU-25-15-ANDET-VF--5883b02cd922a64f.html) | 2 | 0.00% | 0.00% | 0.00% | 0.00% |
| [签字-ORDER PERU 25-19 FONAFE.pdf](runs/pipeline-v1__cfg-87059c0fd057__k-3__20260920T052848Z-v1-87059c0f-k3/documents/签字-ORDER-PERU-25-19-FONAFE--5a4c36e0022f4917.html) | 4 | 0.00% | 0.00% | 0.00% | 0.00% |

## v2 · K=3 · `20260920T052923Z-v2-029ccb26-k3`

| PDF（人工审核） | Questions | Hit@K | Group Recall@K | Complete@K | Chunk Precision@K |
|---|---:|---:|---:|---:|---:|
| [Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R].pdf](runs/pipeline-v2__cfg-029ccb26ca98__k-3__20260920T052923Z-v2-029ccb26-k3/documents/Contrato-N-105-2025-LP-002-2024-FONAFE-Adquisición-de-me--d359c37e0bc3c87a.html) | 10 | 10.00% | 10.00% | 10.00% | 3.33% |
| [E001-602.pdf](runs/pipeline-v2__cfg-029ccb26ca98__k-3__20260920T052923Z-v2-029ccb26-k3/documents/E001-602--817cfff2927c6c65.html) | 2 | 50.00% | 50.00% | 50.00% | 16.67% |
| [ORDER PERU 24-12 ANDET.pdf](runs/pipeline-v2__cfg-029ccb26ca98__k-3__20260920T052923Z-v2-029ccb26-k3/documents/ORDER-PERU-24-12-ANDET--169a1f2133fe5643.html) | 6 | 0.00% | 0.00% | 0.00% | 0.00% |
| [ORDER PERU 25-15 ANDET VF.pdf](runs/pipeline-v2__cfg-029ccb26ca98__k-3__20260920T052923Z-v2-029ccb26-k3/documents/ORDER-PERU-25-15-ANDET-VF--5883b02cd922a64f.html) | 2 | 100.00% | 50.00% | 0.00% | 33.33% |
| [签字-ORDER PERU 25-19 FONAFE.pdf](runs/pipeline-v2__cfg-029ccb26ca98__k-3__20260920T052923Z-v2-029ccb26-k3/documents/签字-ORDER-PERU-25-19-FONAFE--5a4c36e0022f4917.html) | 4 | 25.00% | 8.33% | 0.00% | 33.33% |
