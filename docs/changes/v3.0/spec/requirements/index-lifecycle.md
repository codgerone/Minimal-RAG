# 索引构建、发布与恢复需求

状态：V3.0 需求已审核通过。本文件完整定义[总需求第 7 节](../requirements.md#7-向量化与索引发布)的可观察构建与发布行为；健康状态、交互修复及命令见[运行操作需求](operations.md)，存储字段及事务实现属于架构。

## 1. 目标、输入、输出与范围

Indexer 接收目标能力组合、源 PDF 集合或一份被选中文档、构建配置及 `force/prune` 请求。它调用文档处理、分块、必需产物构建、向量化和发布，输出逐文档新增、更新、跳过、失败、删除的结果与目标 collection 的最终数量。只有已完成完整构建且满足发布校验的文档才可使新索引生效。每个能力组合有独立 active collection、manifest、产物及恢复作用域；一个组合的操作不得读写另一组合的 active 状态。

Manifest 记录构建身份、collection、完整构建配置及 fingerprint，以及每份已发布文档的 file hash、build 身份、产物引用、页数、字符数、chunk 数和索引时间。`current/new/changed/missing/invalid/unprocessable/unassessed` 是健康检查派生状态，不写成源事实。不同源文件可有相同文件名，但必须以路径身份和 document_id 区分。

## 2. 文档编码与记录

使用 `intfloat/multilingual-e5-small` 的锁定 revision 作为等价预设默认模型。Passage 前缀固定 `passage: `，query 前缀固定 `query: `；向量批量编码并归一化。空 passage/query 拒绝，编码数量、维度和有限数值必须验证；结构化分块的实际 passage 包含正文、前缀与 special tokens 不得超过 512 tokens，不依赖截断。向量存储每个 chunk 一条记录，保存完整正文和可还原的文档、页码、节点及来源信息；应用显式写向量，不调用存储默认 embedding。

文档向量与查询向量的模型身份及 revision 必须兼容。字符分块结果按已有规则编码，不以 512-token 约束为理由悄悄改写正文或页边界；若现有编码对超长字符块的实际行为存在不明处，必须在编码前验证并按既有结果处理，不自行发明截断政策。

## 3. 增量、force 与 prune

- 新文档或相同 document_id 下 file hash 改变：在目标组合中从解析到编码全阶段重建该文档，不从中间产物续跑；其他组合不受影响。
- 源文件、已发布构建配置与全部必需记录和产物一致：正常跳过。只检查 hash 和向量数量不足以判定可跳过，active 产物损坏或不一致的 `invalid` 文档必须重建并复查。
- 影响正文、来源、选择、chunk、向量或持久化结果的配置变化：目标组合的现有索引不兼容，明确说明差异并引导该组合全量 force；查询 K、Prompt、日志、诊断、展示变化不触发重建。
- `ingest --file` 只处理一份 PDF，不能同时 `--prune`。`--force` 重新构建选定作用范围；全量 force 在所有目标文档完成新 payload、必需产物和向量验证前，不清空目标 collection 或改写 manifest。
- 全量 force 若发现旧 manifest 仍登记但本次源集合缺失的文档，不能通过新 manifest 默默移除该文档；须先由用户明确执行 prune，或在文档级 `--file --force` 作用域重建现存源文件。collection 已缺失且无法保留该缺失文档旧向量时，普通恢复同样停在该阻断事实，不发布部分 collection。
- 旧 manifest 存在但不可解析时只允许显式全量 force；先保留原始字节与旧 collection、会被触碰的产物目录的回滚证据，再发布可完整验证的新状态。旧登记文档集合无法可信确定，逐文档 `added|updated` 依据事务前可回读旧向量中是否存在该 document_id 判定；旧向量无法完整快照时不得发布。
- `--prune` 仅对所选组合中源文件已不存在、且用户明确确认永久删除的文档执行，逐文档保留恢复能力；不能因为一次扫描失败而当作永久删除。没有 `--prune` 时报告 missing，但不自动删除。

某文档在进入发布事务前失败，清理本次暂存，保留其旧 active 索引、manifest 和审核产物，批量增量可继续其他文档；命令汇总须标出该失败并使用失败退出码。全量 force 任意目标文档预构建失败时，清理本次所有 staging、目标 collection 和 manifest 不变，不部分发布；这属于已确认 D-04 限定纠偏。collection 缺失而 manifest 有记录时，普通恢复必须对所有仍有源文件的目标文档形成完整可发布集合；任一失败不发布部分 collection，保留旧 manifest，missing 文档仍待明确 prune。

## 4. 单文档发布与全量生效点

发布协调器先验证新 payload，并持久化旧向量快照、旧 manifest 与恢复日志；任何破坏旧状态的写入都在恢复证据可回读之后。单文档依次替换并验证该文档向量、发布必需暂存产物、原子保存新 manifest；manifest 保存成功才是新 build 生效点。进入该点前失败，恢复旧向量、旧 manifest 和旧 active 产物并验证；恢复失败保留全部证据并阻断该组合作用域的查询。

全量 force 先准备所有文档，再读取完整旧 collection 快照及 manifest，并建立可回读恢复证据；重建目标 collection 与发布全部必需产物后，原子保存一次新 manifest 才整体生效。任一发布步骤失败应恢复整个旧目标状态。另一组合不参与该事务。

prune 在持久化恢复证据后隔离对应产物、删除目标向量，并原子保存移除记录的新 manifest 才生效；此前失败恢复旧向量、manifest 与产物。读取 active 产物只能通过 manifest 指针，不能扫描“最新目录”。同一构建配置的每份 PDF 在一次成功发布或恢复完成后，`artifacts/documents/` 下仅保留 manifest 指向的最新一份构建结果；被替换或 prune 的 V3 旧构建在新 manifest、向量和产物完整验证后清理。失败回滚时旧构建保持或由恢复快照还原；清理失败时保留事务证据、报告失败并阻断该索引，恢复可幂等重试。V2 产物不参与此清理。

## 5. 中断恢复与健康

存在未完成 journal 时，不猜测操作是否完成，不直接继续原查询或开始第二个写操作。先检查该事务是否已跨过 manifest 生效点，且新向量与必需产物回读一致；若已提交，只完成提交后清理。否则按持久化快照与旧 manifest 回滚，逐项验证后再清理证据。无法证明已提交或无法完整回滚时保留失败证据，目标组合不可查询。只读命令不得擅自执行写恢复；交互确认或显式 ingest 才能启动恢复。

健康检查与恢复可用性规则见[运行操作需求](operations.md)。任一组合的 active 快照缺少其实际接入阶段的必需文件、身份/哈希不一致，或 chunks 与向量正文及来源不一致，均是阻断问题；不受 manifest 引用的 orphan 与陈旧 staging 仅报告，不自动在只读查询中清理。

## 6. 纠偏验收

对 D-04 至少注入：某一目标 PDF 在全量 force 预构建失败、collection 缺失恢复时第二份 PDF 构建失败、已发布文档的必需 artifact 损坏而 file hash/count 未变。预期分别是：旧目标完全不变且本次 staging 清理；无部分新 collection 被发布且旧 manifest 保留；该文档不被跳过，重建后重新检查。还需注入向量替换、产物发布、manifest 保存、prune 隔离及清理后失败，分别验证提交判定、完整回滚或非阻断 warning；禁止报告成功却留下 manifest 指向缺失产物。
