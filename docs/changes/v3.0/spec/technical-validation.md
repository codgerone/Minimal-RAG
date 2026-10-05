# V3.0 架构技术验证门

状态：编码前第三方可行性验证完成；实现一致性门须随 V3 组件交付执行。不是需求或接口字段的权威。返回[总架构](architecture.md)与[规格导航](README.md)。只读核对和最小隔离实验用于确定适配方式；若结果要求改变已批准的用户可见行为，先修订需求与架构，不让实验代码成为新规则。

| 门 | 待验证事实 | 通过证据 | 失败处理 |
| --- | --- | --- | --- |
| T-01 Docling 转换共享 | 主解析、原生表格候选和 raw 审核产物是否可由同一次转换无损提供 | 同一固定 PDF 的节点、候选 ID、来源与 raw 摘要对照历史基线 | 调整适配资源设计，不暗中运行两次或增加降级 |
| T-02 坐标/Schema | 四工具九策略的页单位、原点、跨度和缺失字段在锁定版本中的真实行为 | 每工具含合并格/跨页/缺失坐标样例及转换后公共候选回读 | 明确缺失事实或修订映射；用户可见变化先审需求 |
| T-03 E5 计数与编码 | 结构化 Chunker 内部计数与实际 Embedder 的 revision、前缀、special token、截断行为是否一致 | 512 边界、中文/英文、长字符块及模型输出维度/归一化测试 | 不猜模型行为，不以估算放行超限 chunk |
| T-04 统一模型等价 | 纯文本/结构化适配后正文、顺序、来源、稳定 ID 是否保留 | 固定 PDF 对照解析、chunk、metadata、命中和 Prompt 消息 | 调整公共模型/适配器，不改旧基线或按名称硬路由 |
| T-05 Chroma | 显式向量写入、metadata 类型、同分扩展、快照与恢复在本环境是否满足契约 | 独立临时 collection 的读写、故障注入和边界同分测试 | 调整端口适配或发布机制；不得降低可恢复承诺 |
| T-06 Windows 发布 | 同卷原子替换、flush/fsync、目录隔离和进程中断后恢复证据是否可验证 | 临时目录的逐步骤中断/回读试验 | 修改事务方案；不能以文件名时间推断生效 |
| T-07 配置身份 | canonical JSON、浮点参数和大小写名称在目标 Python/Windows 下是否稳定 | 等价配置同 fingerprint、构建参数变化不同 fingerprint、query 变化不重建 | 固定规范化编码与 schema 类型，拒绝不确定参数 |
| T-08 评估独立性 | V3 test set 重新映射时可否保留已审核标准证据且只使用同一 Retriever | chunk 正文/来源核对、映射审核、运行失败注入和不可变报告 | 不把旧 test set 或未完成运行放入正式基线 |
| T-09 PyMuPDF 原生快照 | 同次 `get_text("blocks", sort=True)` 返回的块字段、顺序和空白文本能否作为主解析原生证据保存 | 锁定 PyMuPDF 版本的真实 PDF 块长度/类型、含空白块与公共结果对照 | 固定原生 schema；不能为生成快照第二次提取或清洗原始块 |

以上验证不在现行 V2.0 collection 上写入，不运行付费模型或重建全部索引，除非后续实现阶段另有明确范围。每个门补充环境、命令、输入样例、实测输出、结论和对架构的影响后才能标记通过。

## 2026-09-22 隔离实测记录

环境：Windows，项目 `.venv`；Docling 2.121.0、PyMuPDF 1.28.0、Camelot 2.0.0、Unstructured 0.27.1、Chroma 1.5.9、sentence-transformers 5.6.1、transformers 5.14.1、torch 2.13.0。实验代码为 [`tmp/v3_gate_probe.py`](../../../../tmp/v3_gate_probe.py)，数据根由该脚本在项目 `tmp/` 下独立生成；未打开现行索引。受限沙箱中的 Chroma SQLite 打开报 `unable to open database file`，同一实验经授权在正常环境重跑成功，此差异不能当作产品缺陷或产品通过证据。

| 门 | 本次实际输入与结果 | 结论、架构影响及仍需证据 |
| --- | --- | --- |
| T-01 | `HF_HUB_OFFLINE=1` 下调用 `rag.v2.docling_runner.convert_document(Path('documents/E001-602.pdf'))`：一次转换产出 1 页、1 表、`iterate_items()` 遍历 73 项、raw JSON 51,322 字节、表引用 `#/tables/0`。既有隔离样例记录 77 个含其他类别的 item、1 表、73 个 provenance；两种 item 计法不同。 | **第三方可行性通过**：同一个 Document 可提供主节点、原生表和 raw。V3 共享资源组件验收必须证明仅调用一次转换并对照 ID、来源和 raw；不得用第二次转换补齐。 |
| T-02 | 对 `documents/E001-602.pdf` 第 1 页调用现有九策略并回读公共候选：PyMuPDF `lines/lines_strict/text` 分别 1/1/1 个候选、11/11/896 个 cell；Camelot `lattice/stream/network/hybrid` 分别 0/3/1/1 个候选（stream 三候选 63/108/70 cell，network 125，hybrid 1045）；Docling accurate 1 个候选、35 cell、原始 `bottom_left` 转换且 bbox 可用；Unstructured hi_res 1 个候选、168 cell、bbox 可用。每个非空候选均有页号 1 和公共 bbox。 | **锁定版本正常路径通过**，且 lattice 零结果确认不是失败。合并格、跨页、旋转/CropBox 与缺失坐标按表格契约已有明确拒绝/缺失表达；各适配器完成前必须用合成 fixture 做契约测试，不阻止先编码公共契约和适配器骨架。 |
| T-03 | `HF_HUB_OFFLINE=1` 下调用现有 `E5TokenCounter`：`你好世界` 和 `hello world` 各 7 token，`订单编号` 重复 300 次为 905。`a ` 重复 508/509/700 次分别计 512/513/704；同一锁定 revision 的 SentenceTransformer `tokenize` 有效输入分别为 512/512/512，明确证实超过 512 会截断。现有 Embedder 对短文本和 905-token 长文本均返回 384 维、L2 范数约 1.0 的向量；模型与 tokenizer 上限均 512，revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`。 | **本环境通过第三方行为门**。字符路径保留既有模型截断事实，不声称尾部参与编码；结构化路径在调用前以完整 passage 计数拒绝 >512。V3 实现仍需契约测试防止引入不同 tokenizer/参数。 |
| T-04 | V3 统一模型尚未实现，无法生成与旧实现成对的解析、chunk、命中、Prompt。 | **实现验收门，非架构编码前门**。允许按已闭合契约编码；两份预设在各自端到端适配完成前不得标记交付或切换默认运行入口。 |
| T-05 | 运行 `.venv/Scripts/python.exe tmp/v3_gate_probe.py`：独立 Chroma collection 用显式二维向量写入两条记录，数值/字符串/布尔 metadata 回读成功；等距查询返回 `['a','b']`；删除 `a` 后 count=1，按快照内容回填后 count=2。又运行 `tmp/v3_publication_recovery_probe.py`，跨进程在向量替换后强退并用显式旧向量回填，三类事实恢复为 old。 | **第三方可行性通过**。架构不依赖 Chroma 同分内部顺序，而使用扩大候选池后的应用层排序；生产实现仍须对完整 V3 metadata、向量逐值快照及文档/全量/prune 三种作用域运行同一故障矩阵。 |
| T-06 | `v3_gate_probe.py` 验证同目录 flush+fsync+`os.replace` 前/后强退状态和 Windows `msvcrt.locking` 跨进程排他锁。`v3_publication_recovery_probe.py` 对 Chroma+artifact+manifest 原型分别在 `vector_replace|artifact_publish|manifest_publish|artifact_cleanup` 后以 `os._exit(40)` 中断，新进程恢复结果依次为 `rolled_back|rolled_back|committed|committed`，并逐项回读三类事实一致。 | **机制可行性通过**。这证明架构选定的 journal、生效点和新进程判定可实现；不承诺断电目录项耐久性。V3 Publisher 实现完成前必须用其真实 schema 和全部故障注入点重复测试，作为组件完成标准而非重新发明事务规则。 |
| T-07 | 同脚本测试 `sort_keys=True`、UTF-8、紧凑 JSON：不同键顺序同摘要、`Case`/`case` 字节不同、NaN 被拒。架构进一步固定小数由十进制词法规范化，不经过二进制 float。 | **编码前规则闭合**。实现必须用 0、负零、尾零、不同键序、NAME 大小写、build/query 参数变化的表驱动测试确认，不得自行选择浮点格式。 |
| T-08 | V3 数据集/运行服务尚未实现，未做独立映射及失败注入。 | **实现验收门，非核心架构编码前门**。Evaluator 可依契约编码；未完成重新映射、审核和失败注入前不得产生 V3 正式基线。 |
| T-09 | 在 PyMuPDF 1.28.0 上读取 `documents/E001-602.pdf`，运行 `p.get_text('blocks',sort=True)`：1 页、44 块，每块 7 项，`text` 为 `str`、`block_no`/`block_type` 为 `int`；首块正文为 `' \n \n'`，证明空白块确实存在且不能被原生快照提前清洗。 | **本机正常路径可行**。V3 主解析适配器须从同一次块遍历一边保留原生块、一边按现有纯文本规则生成公共页事实；空白页、非文本块、跨块正文及坐标有限性仍须用 fixture 和实现后的成对快照测试。 |

隔离目录保留供复核，不是系统运行数据。T-01—T-03、T-05—T-07 已给出开始实现所需的第三方行为或机制证据；它们不替代真实 V3 组件的契约测试。T-04/T-08 天然需要实现产物，作为分阶段完成门而不是循环阻止编码。任何后续结果若改变用户可见语义，先修订已批准需求和架构。
