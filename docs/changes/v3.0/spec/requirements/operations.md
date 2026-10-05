# 命令、健康检查与引导恢复需求

状态：V3.0 需求已审核通过。本文件完整定义[总需求第 8—9 节](../requirements.md#8-索引健康与查询入口)的操作行为；发布事务见[索引生命周期需求](index-lifecycle.md)，正式指标见[检索评估需求](retrieval-evaluation.md)。

## 1. 命令范围和配置选择

保留 `documents`、`ingest`、`chunks`、`browse`、`search`、`ask`、`chat` 和 `eval` 命令及除装配选择外的既有参数含义；所有命令均不再接受 `--pipeline v1|v2`。省略选择参数时使用当前最佳装配配置；`--config NAME` 直接选择已保存配置，`--configure` 进入 CLI 引导，列出已有配置的适配器组成与索引状态，并提供创建新配置选项。两者互斥，不能同时使用。每条命令显示最终所选配置；chat 启动后配置固定。配置不存在、配置尚无索引、索引损坏是不同事实；索引不可用时只引导处理所选配置，不自动切换到另一配置。CLI 帮助应写明默认值、配置入口和冲突规则。

`--config NAME` 中的 NAME 来自系统固定命名的两个内置预设，或用户在 `--configure` 向导完成校验后保存的名称。向导和配置列表须展示名称与适配器组成；未知名称应报告可用名称，不得猜测或自动创建。名称格式、唯一性、禁止覆盖和与构建身份的区别由[能力装配需求](assembly.md)统一定义。

新配置引导按[能力装配需求](assembly.md)逐接口筛选候选并在完整校验后保存。若系统尚无可用索引及其配置，交互命令先询问是否采用最佳装配；同意则选择该配置，拒绝则进入创建配置引导。选择本身不悄悄建索引：入库命令在用户确认后可继续构建；依赖索引的只读/查询命令应引导先构建所选配置，构建完成并复查后才继续原请求。非交互环境不等待输入、不隐式创建配置或索引，报告缺失事实及可执行的下一步。用户取消引导不修改配置或存储。

`ingest --all-configs` 对所有已保存、已校验且可入库的配置建立或更新索引，不包含尚未保存的向导草稿；与 `--config`、`--configure` 互斥。各目标独立执行和发布，一个失败保留该目标旧索引并继续其他目标，最终列出每个配置的成功、跳过或失败及原因；任一目标失败则命令非零退出。批量入口不得扩展单个配置内的 force、prune 或恢复作用域，不得改写其他配置或 V2.0 历史产物；目标配置内部已被新 manifest 替代的 V3.0 构建按[索引生命周期](index-lifecycle.md)清理。

`ingest --file` 与 `--prune` 互斥；`--force` 强制重建选定范围。`chunks --document` 使用相对路径或唯一文件名；`--page` 是正的一基物理页码，命中来源页集合包含该页的 chunk。`browse` 只读 ID、正文和 metadata，按相对路径、chunk 序号及 ID 稳定排序后执行非负 offset 与正 limit；默认隐藏体积大的来源 JSON，只有 `--full-metadata` 展开。browse 不能加载或显示 embedding。`search` 的 K 为正数；`--document` 精确限定一份文档。默认 eval 是正式检索评估，`--live` 单独进行 LLM 冒烟，不能纳入正式指标。

`documents`、`ingest`、`browse`、`chunks`、`search` 和默认 `eval` 不要求 OpenRouter API Key。`ask`、`chat`、`eval --live` 在索引就绪后检查 key。只读浏览无需解析器或模型；查询只需 query embedder；问答才加载 LLM。装配校验应在昂贵模型加载前发现可静态判断的错误。

## 2. 健康状态

DocumentState 封闭为 `current|new|changed|missing|invalid|unprocessable|unassessed`。`current` 表示源、manifest、向量和必需 active 产物一致；`new` 是源存在而 manifest 无记录；`changed` 是同 document_id 的源 file hash 改变；`missing` 是 manifest 有记录而源不存在；`invalid` 是已登记记录的向量或 active 产物不一致；`unprocessable` 仅是 new/changed 源文档的只读预检不能生成可用内容，不能覆盖已有 current/invalid 事实；`unassessed` 是组合级阻断使逐文档判断尚未进行。

组合级 manifest schema、身份或构建配置不匹配，collection/manifest 结构问题和未完成恢复日志属于阻断问题；发生时源目录与 manifest 的文档并集一律为 `unassessed`，不得同时对它们报告伪造的 `current` 或 `invalid`。这是 D-03 已确认纠偏。只有无组合级阻断时，才按源、记录、向量与必需产物生成逐文档状态。问题码、作用域、优先级与错误阶段必须是封闭机器值；人类 message 和拼接了 document ID 的字符串不能承担程序分支。字段全集在架构中定义，不得在本需求另造别名。

索引 usable 当且仅当没有阻断问题且所有应检查文档为 current。new、changed、missing、invalid、unprocessable、unassessed 均不能被误报成可用。正常尚未建立的空 manifest/空 collection 是可引导构建的未构建状态，不能当作数据损坏。

## 3. 问题优先级与动作

结构性组合级问题优先于逐文档问题。manifest 缺失而 collection 有记录、未知文档记录、总数不一致或构建配置不兼容，建议目标组合全量 force，交互默认拒绝。manifest 有记录而 collection 缺失，建议普通增量恢复，交互默认接受，但不得部分发布。new、changed、invalid 建议目标文档普通增量，invalid 完成后复查；unprocessable 仅显示人工修复建议，不进入 Indexer；missing 仅在明确确认永久删除后 prune，默认拒绝；unassessed 不直接对文档操作，先解决组合级阻断。

存在 journal 时最高优先级是依据恢复证据判定已提交或未提交，不能 force 覆盖证据。交互只读命令经用户确认可调用恢复器，完成后完整复查并继续原命令；非交互只读命令只输出修复命令并失败返回，不写入。显式 ingest 在文档发现/构建前处理 pending journal，恢复失败立即停止。除 ingest 外，交互修复成功后重查再继续原 `search/ask/chat/eval/documents` 请求；ingest 不通过恢复协调器重试自身。

非交互环境不等待输入、不自动修复、不修改配置；取消交互确认不执行写操作并停止原命令。每项动作的作用域固定为所选配置。已发布的其他配置和 V2.0 历史证据不可被本次修复触碰。

## 4. LLM 凭据与输出

交互缺 key 时，可以仅在当前进程使用输入值；只有明确确认后才原子更新项目 `.env` 的 `OPENROUTER_API_KEY`，不得打印秘密。认证失败允许新 key 后重试一次原 LLM 请求；限流、超时与上游失败不自动循环重试。模型配置缺失或其他配置错误引导人工改 `.env`，不自动改写。
两份内置装配保留既有 `OPENROUTER_MODEL` 环境覆盖：显式设置时用于本次问答模型，未设置时使用插件默认值。覆盖不改写已保存装配，也不改变索引构建身份；自定义装配使用自己保存的 LanguageModel 插件参数。最终回答记录实际模型名。

Prompt 只用 Retriever 实际命中的 chunk 作为业务事实，要求证据不足时拒答、数字/金额/币种/日期/型号/单位忠实保留，并引用真实文档、全部来源页和 chunk ID。文档内容中的指令只作为待分析数据。`ask` 一次检索和生成；chat 每轮独立检索，无跨轮记忆，支持 `/help`、`/exit`、`/quit`、`/debug on`、`/debug off`。

默认日志不保存文档全文、Prompt 全文、embedding 或 API Key；显式 `ask --debug` / chat debug 可仅向当前终端显示检索命中和最终消息，不打印 key。异常调试摘要不得额外输出完整 Prompt。这明确区分此前总需求与运行契约对“debug”的不同措辞，保留显式终端展示行为。

## 5. 错误和验收

预期错误输出简洁原因和下一步，不默认打印 traceback。参数/配置、文档选择、PDF/Docling/表格、分块、embedding、存储、发布、健康、评估、LLM 错误必须能按阶段和作用域区分；关闭的状态、原因与阶段在架构定义。正常零搜索结果、成功零表、表头 `undetermined` 和证据不足拒答不是异常。单文档失败批量继续时汇总失败并以失败码退出；进入发布事务后的错误按发布恢复规则处理。`.env`、`.rag/`、业务 PDF、产物与日志按用途忽略版本控制，示例环境文件不得含秘密。

验收至少覆盖：组合级阻断时全部文档 `unassessed`；修复后逐文档重新判断；已登记 invalid 文档即使预检失败也不被覆盖为 unprocessable；机器 issue code 不拼接文档 ID；非交互只读命令不执行写入；恢复日志先于 force；缺 key 与认证单次重试；debug 显示最终消息但不泄露 key；`browse` 无 embedding；显式文档和页筛选的边界。

配置入口还须验收：无选择参数准确落到当前最佳装配；旧 `--pipeline` 被明确拒绝；`--config` 与 `--configure` 冲突在接触模型和索引前报错；向导只展示可形成完整有效装配的兼容候选，取消不落盘；无可用索引时交互询问最佳装配，拒绝后进入逐接口引导；非交互查询不弹出向导或隐式建索引；新建配置未有索引时明确提示建库。`ingest --all-configs` 只处理已保存、已校验且可入库的配置，一个配置失败不影响其他配置的成功提交，失败者保留旧 active 索引，最终非零退出并展示逐配置结果；与单配置选择参数冲突时不得开始入库。

名称验收须覆盖：两个内置预设可直接按固定名称选取；新配置完成校验前不能命名落盘；大小写差异视为重名；非法字符和空名称被拒绝并允许重新输入；重名不得覆盖旧配置及其索引；同名配置的构建参数变化不能凭名称通过索引兼容校验。
