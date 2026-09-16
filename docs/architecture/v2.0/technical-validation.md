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

## 6. 结论与剩余实施验证

四项编码前技术门均已得到足以确定适配方式的证据：Docling 字段/转换、Chroma snapshot、tokenizer 计数和 Windows 原子文件操作均可按当前架构实现。验证引起的架构修订为：关闭 Docling page image 生成、增加 docling-core 版本、固定 embedding revision、保留 Windows cleanup warning 和不支持目录 fsync的事实。

以下属于实现契约测试，不再是未决定的架构规则：

- mapper 的完整脱敏 fixture 集；
- 发布各步骤故障注入和进程中断恢复；
- TokenCounter 与最终 SentenceTransformer adapter 的逐输入一致性；
- Windows 打开 HTML 后的延迟清理重试。
