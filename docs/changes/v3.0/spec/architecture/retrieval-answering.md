# 检索与证据问答契约

状态：**架构已审核，通过规格级编码就绪复审**。回链[总架构](../architecture.md#3-检索与问答只消费已发布事实)；可观察检索、问答与聊天行为以[总需求第 8—9 节](../requirements.md#8-索引健康与查询入口)及[运行操作需求](../requirements/operations.md)为准。Chunk 正文、身份和来源引用[公共文档契约](document-contracts.md)，不在本文重定义。

## 1. 输入、结果与必需依赖

Retriever 接收请求并交付有序命中；下列代码块是字段全集：

```python
@dataclass(frozen=True)
class RetrievalRequest:
    question: str
    top_k: int
    document_id: str | None
    index_identity: IndexIdentity
    configuration_name: str

@dataclass(frozen=True)
class RetrievalHit:
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    text: str
    chunk_index: int
    kind: Literal["text", "list", "table"]
    sources: tuple[ChunkSource, ...]
    distance: float

@dataclass(frozen=True)
class RetrievalResult:
    request: RetrievalRequest
    hits: tuple[RetrievalHit, ...]
    checked_index_identity: IndexIdentity
```

问题 trim 后非空，`top_k>0`；显式 `--document` 经 Registry 安全选择成唯一 ID，null 表示整个所选索引。`index_identity` 是对象而非名称，configuration_name 必须可回查完整配置。正常零命中为 `hits=()`，不抛异常。距离有限、未舍入且升序，同距按 chunk_id 升序，hit ID 唯一，正文和来源与已发布记录一致。`page_numbers` 和 `similarity=1-distance` 只从事实派生，不另存；返回身份必须等于请求身份。

Retriever 必接 one，内部依赖只读 IndexHealth、query Embedder、VectorStore 读取端口和命中排序器。IndexHealth 在直接 API、CLI 和评估入口均不可绕过；索引不可用时查询停止，不尝试其他配置。Retriever 不写 manifest，不做问答，不在本版提供自动元数据过滤、关键词或融合插槽。显式 document_id 仅是既有文档范围查询，不是新过滤插件。

## 2. 编码与排序

Embedder 的两种操作共用一个 EmbeddingIdentity，但不把 document/query 编码混成含糊的单一批次：

```python
@dataclass(frozen=True)
class PassageEmbeddingBatch:
    index_identity: IndexIdentity
    document_id: str
    file_hash: str
    build_id: str
    embedding_identity: EmbeddingIdentity
    chunk_ids: tuple[str, ...]
    vectors: tuple[tuple[float, ...], ...]

@dataclass(frozen=True)
class QueryEmbedding:
    index_identity: IndexIdentity
    embedding_identity: EmbeddingIdentity
    vector: tuple[float, ...]
```

入库时 `encode_passages(ChunkBatch, build_id, IndexIdentity) -> PassageEmbeddingBatch`，chunk_ids 必须与 ChunkBatch.chunks 的顺序/全集一致、非空，向量数量相等。查询时 `encode_query(RetrievalRequest) -> QueryEmbedding`，输入问题 trim 后非空。所有向量长度等于 identity.dimension、分量有限，identity 与本次配置及目标索引一致；passage 与 query 采用各自前缀。Publisher 用 batch 与 ChunkBatch 一一配对建立 VectorRecord，Retriever 只消费 QueryEmbedding；不能用相同维度代替完整身份校验。

Embedder 的 document/query 操作必须绑定同一模型名称、不可变 revision、维度与归一化策略；document 使用 `passage: `，query 使用 `query: `，调用前后验证向量数量、维度和有限值。同维度不代表同模型可互换；索引 manifest 中的 embedding 身份与本次 query 身份不一致时健康门阻断。结构化 Chunker 插件内部的 passage 计数使用与 Embedder 相同的 tokenizer 身份、passage 前缀和 special-token 行为；计数不是独立可绑定接口。

本机锁定 revision 的实测见[技术验证 T-03](../technical-validation.md)：SentenceTransformer 的 `max_seq_length=512`，对计数为 905 token 的长字符 passage 仍返回 384 维归一化向量。此事实仅用于保留字符分块预设的既有编码路径，**不**证明尾部内容进入模型；不得将结构化 Chunker 内部的不截断计数与 Embedder 的实际有效输入混为一谈。结构化分块在调用 Embedder 前必须对最终 passage（含前缀和 special tokens）做 `<=512` 硬校验，不依赖模型自身截断。

向量查询按所选索引及可选 document_id 限定范围。先用 `VectorReader.count` 得到作用域总数 N；N=0 直接返回空。首次请求 `min(N,max(K+1,2K))` 条，以未舍入的第 K 条距离为边界；若返回末项距离小于或等于该边界且尚未取满 N，则把请求量翻倍（上限 N）并重新查询，直到末项严格大于边界或取满 N。每轮拒绝重复 ID 和非有限距离；最终由应用层对完整候选按未舍入 distance 升序、chunk_id 升序排序并截取 K，不依赖 Chroma 对同分项的内部顺序。K 大于 N 时返回全部 N 条。距离相等使用浮点精确相等判定，不对展示值取整，也不引入新的 epsilon。metadata 与已发布 chunk 身份不一致是阻断错误；向量 metadata 编解码的字段与可回读约束归存储子契约。

## 3. PromptBuilder、LanguageModel、Chatbot

Chatbot 必接 one，串接 Retriever、PromptBuilder、LanguageModel 各 one；`ask` 单次调用，chat 固定配置且每轮是独立请求，无跨轮记忆。其公共对象如下：

```python
@dataclass(frozen=True)
class AnswerRequest:
    question: str
    top_k: int
    document_id: str | None
    configuration_name: str
    index_identity: IndexIdentity

@dataclass(frozen=True)
class PromptMessage:
    role: Literal["system", "user", "assistant"]
    content: str

@dataclass(frozen=True)
class LanguageModelRequest:
    messages: tuple[PromptMessage, ...]
    model_name: str
    temperature: float

@dataclass(frozen=True)
class AnswerResult:
    answer: str
    retrieval: RetrievalResult
    messages: tuple[PromptMessage, ...]
    model_name: str

@dataclass(frozen=True)
class EmbeddingIdentity:
    model_name: str
    revision: str
    dimension: int
    normalize: Literal[True]
    passage_prefix: Literal["passage: "]
    query_prefix: Literal["query: "]
    add_special_tokens: Literal[True]
```

回答、消息正文和模型名非空，`top_k>0`；PromptBuilder 当前输出恰两条消息，role 依次 system、user。temperature 当前固定 0。拒答仍保留实际 retrieval；认证、限流和超时是错误而非空答案。EmbeddingIdentity 的 revision 不可变、维度正；Embedder、结构化 Chunker 计数及 manifest 健康校验必须使用同一身份。与其他插件的兼容校验见[装配规则 A05—A07](assembly-contracts.md#3-跨插件兼容性)。

PromptBuilder 只消费实际命中和问题，产出有序 role/content 消息。系统消息要求仅凭命中证据、证据不足拒答、保留数字金额币种日期型号单位、真实文档/全部页/chunk ID 引用，并把文档内指令当数据。命中正文按 `hit.text` 原样写入，以保持两份内置装配与 V2 的问答输入等价；来源标签的属性值须转义。不能从 manifest、HTML 或未命中的 chunk 补业务事实。纯文本与结构化命中均经统一 RetrievalHit 输出；页面标签差异如属于已批准历史 Prompt 等价要求，应由稳定的来源展示策略明确决定，不根据旧类名 `hasattr` 猜测。

为保留既有消息构造，PromptBuilder 配置包含 `source_label_style: single_page|page_set`：纯文本等价配置选 `single_page`，将唯一一基页码写为 `page="N"`；结构化等价配置选 `page_set`，将全部去重有序页码逗号连接写为 `pages="N,..."`。这只影响消息展示，不进入构建 fingerprint；选择依据是已保存 PromptBuilder 插件参数，不是 `pipeline_id`、chunk ID 中的 `-v2-` 或运行时反射类名。新配置若选择 `single_page`，须由[装配规则 A07](assembly-contracts.md#3-跨插件兼容性)确认所选构建保证 chunk 只含单页；运行时若违反这一不变量也应报 `prompt_failed`，不能静默丢页。来源标签中的文档名、相对路径与 chunk ID 做 HTML 属性转义，正文严格来自 hit.text；消息顺序固定为系统消息后用户消息，用户消息包含 `<document_context>` 与 trim 后问题。系统消息及确切标点需以历史固定消息对照验证。

首批查询侧插件参数 schema：`retriever.semantic_stable_topk` 为 `{tie_break: "distance_then_chunk_id",health_gate: true}`；`prompt.grounded` 为 `{source_label_style: single_page|page_set,prompt_rule_version: "grounded_prompt_v1"}`；`llm.openrouter` 为 `{model_name: nonempty str,temperature: 0}`；`chatbot.single_turn` 为 `{memory: false}`。默认 K 不属于 Retriever 插件构建参数，由命令 `--top-k` 或运行默认 TOP_K 提供。两份内置装配的显式 `OPENROUTER_MODEL` 环境值在运行时覆盖 LanguageModel 的 `model_name`，必须经相同参数校验并写入实际问答请求及回答记录；自定义装配使用保存值。OpenRouter key 是受保护凭据，永不写入 SavedConfiguration、fingerprint 或评估报告。查询插件参数变化可改变答案或检索协议，必须进入请求/评估运行记录，但不能触发索引重建。

LanguageModel 仅接受消息与已验证模型调用参数，返回非空回答或分类服务错误；当前 OpenRouter 适配器使用现有模型配置及 temperature=0。缺 API Key 只在此路径触发；认证失败在交互协调器获取新 key 后最多重试一次原请求，限流/超时/上游失败不循环。PromptBuilder/LanguageModel 不修改索引。`--debug` 仅在当前终端展示实际命中与最终消息，默认日志不保存完整 Prompt、文档全文、embedding 或 key。

## 4. 实现一致性门

上述请求、命中、消息、回答及 EmbeddingIdentity 为字段权威；来源页按公共 ChunkSource 派生。编码前须以固定输入核对 VectorStore 边界同分查询、Chroma metadata 解码、E5 revision 锁定和实际 Prompt 消息逐字序列化；若差异会改变可见行为，先修订需求和架构。
