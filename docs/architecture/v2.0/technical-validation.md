# V2.0 编码前技术验证记录

本文记录[当前架构第 12 节](../../architecture.md)要求的第三方 SDK、模型、Chroma 和 Windows 文件系统验证。它只证明适配方式在当前锁定环境可行，不替代需求、字段契约或后续实现测试。

验证日期：2026-09-16。环境：Windows、Python 3.11、项目 `.venv`。可复现探针为 `scripts/validate_v2_technical_gates.py`，结构化结果保存在 `tmp/v2-technical-validation/`；`tmp/` 结果可能包含业务 PDF raw 内容，只作为本地证据，不提交、不作为运行时依赖或现行规则来源。

## 1. 版本基线

| 组件 | 实测版本 |
| --- | --- |
| docling | 2.121.0 |
| docling-core | 2.92.0 |
| chromadb | 1.5.9 |
| transformers | 5.14.1 |
| sentence-transformers | 5.6.1 |
| PyMuPDF | 1.28.0 |
| camelot-py | 2.0.0 |
| unstructured | 0.27.1 |

## 2. Docling SDK 与真实转换

执行：

```text
.venv/Scripts/python.exe scripts/validate_v2_technical_gates.py docling-sdk
.venv/Scripts/python.exe scripts/validate_v2_technical_gates.py docling-conversion --pdf documents/E001-602.pdf
```

结果：通过。

- `iterate_items` 实际支持 `with_groups`、`traverse_pictures` 和 `page_no`。
- DoclingDocument 实际包含 body、furniture、groups、texts、pictures、tables、key_value_items、form_items 和 pages。
- TextItem/ListItem/TableItem 实际提供规格所需的 text/orig/marker/prov/captions/footnotes/references 字段。
- BoundingBox 实际字段为 l/t/r/b/coord_origin，同时支持 TOPLEFT 与 BOTTOMLEFT。
- SDK 公共导入位置为 `docling_core.types.doc`；`docling_core.types.doc.items` 不导出这些模型，mapper 不得从后者导入。
- 用 V2 配置成功转换一页数字型 PDF：77 个遍历项、73 条 provenance、1 个 table、2 个 key_value_area 和 1 个 form_area；raw schema 为 `DoclingDocument 1.10.0`。
- `do_ocr=false`、table mode accurate、cell matching 开启时能够完成转换。
- `generate_page_images=true` 会把整页 PNG data URI 写入 raw JSON；关闭后上述结构计数保持一致，且本次 raw JSON 为 95,359 bytes。当前流程不消费页图，因此 V2ParserConfig 固定为 false。

转换时 Hugging Face 缓存尝试更新 refs 文件遇到 PermissionError，但已缓存模型仍成功加载并完成转换。这是环境诊断，不改变输出语义；实现不得把 refs 写入失败误报为 PDF 解析失败。

本验证确认 SDK 适配方式，不等于 mapper 已实现。普通段落、页眉页脚、多 provenance、嵌套 List、caption/footnote、picture caption、未知 label 和缺 provenance 仍须在 mapper 实现时固化为 RAG 自有、脱敏 fixture 并执行契约测试。

## 3. Chroma snapshot 与恢复

执行：

```text
.venv/Scripts/python.exe scripts/validate_v2_technical_gates.py chroma
```

结果：通过。

- `collection.get(where=..., include=[documents, embeddings, metadatas])` 能完整读取文档级 snapshot。
- 无匹配文档返回合法空 snapshot。
- 无 where 的 get 能读取完整 collection snapshot。
- 删除一份文档后可用原 IDs、documents、embeddings 和 metadatas 恢复。
- PersistentClient 重启后仍能读取两条记录，embedding 维度保持一致。
- 先前记录的最小 PersistentClient 阻塞本次未复现。

结论：V2.0 可以采用当前“持久化 snapshot+journal 后原 collection 替换”的架构，不需要改成临时 collection 交换。实现测试仍须对 vector replace、artifact publish、manifest publish 和 restore 各步骤注入异常，验证契约规定的最终状态。

## 4. E5 tokenizer 与硬上限

执行：

```text
.venv/Scripts/python.exe scripts/validate_v2_technical_gates.py tokenizer
```

结果：通过。

- 本地实际 tokenizer 为 XLMRobertaTokenizer，model_max_length=512。
- 空字符串加 special tokens 为 2 tokens；`passage: ` 加 special tokens 为 4 tokens。
- `销售订单 12345` 的完整 passage 输入为 9 tokens。
- 关闭 truncation 后，探针输入返回 704 tokens 并明确报告超限，没有静默截断。
- 同一输入重复计数一致。
- 本机缓存实际 snapshot revision 为 `614241f622f53c4eeff9890bdc4f31cfecc418b3`。

结论：TokenCounter 必须调用同一 tokenizer，固定 add_special_tokens=true、truncation=false，并对带 `passage: ` 的完整输入计数。模型名和不可变 revision 同时进入 TokenizerConfig、EmbeddingConfig 和 fingerprint；默认不得跟随可变 main。

## 5. Windows 文件系统

执行：

```text
.venv/Scripts/python.exe scripts/validate_v2_technical_gates.py windows
```

结果：核心发布原语通过，平台限制已确认。

- 同目录临时文件 flush/fsync/close 后 `Path.replace()` 成功替换旧文件。
- 同卷 staging directory rename 成功。
- Windows 不能按当前 Python API 对目录执行 fsync，实际返回 PermissionError；实现记录平台事实，不把它伪报成发布失败。
- winner HTML 文件仍被打开时，删除 build 目录实际返回 WinError 32；因此 manifest 提交后的旧 artifact 清理必须是非阻断 warning，并允许以后重试。
- Windows 上对只读文件描述符调用 fsync 返回 Bad file descriptor；实现必须用可写临时文件完成 flush/fsync。

## 6. 实现后真实双链路冒烟验证

2026-09-16 使用隔离输出目录执行：

```text
.venv/Scripts/python.exe scripts/validate_v2_runtime.py --pipeline v2 --pdf documents/E001-602.pdf --output tmp/v2-runtime-validation-0916a
.venv/Scripts/python.exe scripts/validate_v2_runtime.py --pipeline v1 --pdf documents/E001-602.pdf --output tmp/v1-runtime-validation-0917b
```

结果：通过。RAG 正式 V2 入口独立完成真实 PDF 的 Docling 映射、九策略提取、准入/分组/评分、winner 融合、结构化分块、E5 embedding、Chroma 发布、manifest 发布、健康检查和查询；生成 1 个 slot、1 个 group、1 个 winner、4 个最终 chunk（2 个 text、2 个 table），最终最大输入为 425 tokens，collection count 为 4，健康检查无问题，查询返回 3 条结果。输出保存在可再生的 `tmp/v2-runtime-validation-0916a/`，不读取 `experiments/`，也不改写项目正式 `.rag` 数据。

运行中 tokenizer 对分块前的 537-token 探针给出超过模型 512 上限的提示；chunker 随后继续拆分，最终入库输入均低于硬上限。这是“关闭静默截断并以最终 passage 重算”的预期证据，不是超限入库。Hugging Face refs 缓存仍因沙箱权限无法更新时间戳，但缓存模型加载、构建和查询均成功。

同一 PDF 的 V1 隔离验证也通过：独立发布到 `minimal_rag_documents_v1` 和 `.rag/manifest.v1.json`，生成 4 个旧式页内 chunk，健康检查无问题并返回 3 条查询结果。两次验证使用不同临时根目录，证明两条链路无需共享或改写索引即可分别运行；该结果属于功能贯通证据，不代表 V2 效果优于 V1。

2026-09-17 又使用 `scripts/compare_v1_v2.py` 在同一隔离根连续构建现有 5 份 PDF。初次运行发现第三方提取器在 Windows 下延迟持有 staging 内输入副本，造成后续 artifact 发布及自动恢复无法删除该文件。实现随后改为让提取器读取权威源文件，Docling 使用 `DocumentStream(BytesIO)` 避免 Unicode 路径和文件句柄问题，且 `.work` 必须清理成功后才进入发布。修复后的批量重跑中 V1/V2 均构建 5/5 文档、健康检查 usable、无 recovery journal 或 staging 残留。正式检索效果见[检索效果汇总](../../../validation/retrieval/system-v2.0/summary.md)。

## 6. 正式检索评估：同距离 Top-K 边界（已关闭）

2026-09-18 使用 `scripts/validate_chroma_tie_boundary.py` 对 Chroma 1.5.9 建立隔离 collection，写入 8 条不同 ID、完全相同 embedding 的记录，以同一向量分别查询 K=1、3、5、8。验证包含同进程 10 轮和独立 Python 进程 4 轮，不读取或修改 `.rag`。

结果：14 轮产生 5 组不同结果；K=1 曾分别返回 `chunk-00`、`chunk-02`、`chunk-04`、`chunk-05`，K=3/5 的候选集合也变化；只有取全部 8 条时集合稳定。不同 K 的结果不保证嵌套。因此，在 Chroma 已经严格截取 K 条之后再按 `(distance, chunk_id)` 排序，只能稳定已返回顺序，不能恢复被排除的同距离候选。

用户确认采用严格 K 的确定性适配：初始请求 K+1，若第 K 条与候选池末条距离完全相同则倍增候选数，直到越过同分边界或覆盖全部作用域，最后按 `(distance, chunk_id)` 排序并严格取 K。脚本按该算法再次执行同进程 10 轮和独立进程 4 轮；虽然原始 Chroma 仍产生 5 组结果，适配后的 K=1、3、5、8 全部稳定且互为前缀，验证通过。

结论：技术门已关闭。VectorStore 负责按指定候选数查询，Retriever 负责自适应扩展和最终稳定排序；search、ask、chat 和正式 eval 共用该行为。极端全同分时允许读取当前检索作用域全部候选，但最终仍严格返回 K 条。

## 7. 结论与剩余实施验证

四项编码前技术门均已得到足以确定适配方式的证据：Docling 字段/转换、Chroma snapshot、tokenizer 计数和 Windows 原子文件操作均可按当前架构实现。验证引起的架构修订为：关闭 Docling page image 生成、增加 docling-core 版本、固定 embedding revision、保留 Windows cleanup warning 和不支持目录 fsync的事实。

以下属于实现契约测试，不再是未决定的架构规则：

- mapper 的完整脱敏 fixture 集；
- 发布各步骤故障注入和进程中断恢复；
- TokenCounter 与最终 SentenceTransformer adapter 的逐输入一致性；
- Windows 打开 HTML 后的延迟清理重试。
