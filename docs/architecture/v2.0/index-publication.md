# V2.0 索引、产物发布与恢复契约

本文是[当前架构第 9 节](../../architecture.md)的发布子文档，定义 collection、artifact 和 manifest 之间的一致性。manifest 是 active build 的唯一权威指针；目录存在不代表已发布。恢复文件字段与路径以[运行与持久化模型契约第 6 节](runtime-persistence-models.md)为唯一权威。

## 1. 发布状态

| 状态 | 含义 | 可查询 |
| --- | --- | --- |
| staged | 新产物位于 `.staging`，manifest 未引用 | 否 |
| vector_replaced | 新向量已写入但 manifest 未更新 | 否；仅事务内部短暂存在 |
| published | manifest 原子保存并引用相同 build_id | 是 |
| orphan | build 目录存在但无 manifest 引用 | 否 |
| inconsistent | manifest、向量或必需产物身份不一致 | 否 |

读取入口只从所选 manifest 取得 artifact_path/build_id，禁止扫描目录选择“最新”文件。

## 2. 文档级 VectorSnapshot

字段模型统一使用[运行与持久化模型契约第 6 节](runtime-persistence-models.md)的 VectorSnapshot 和 CollectionSnapshot；本文只规定发布行为。

Chroma 适配器使用 `collection.get(where={document_id}, include=[documents, embeddings, metadatas])`。四个数组长度必须相同、ID 唯一、embedding 维度一致，否则 snapshot 失败且不得删除旧向量。旧文档不存在时使用合法空 snapshot。

## 3. 恢复准备与通用规则

任何会改变旧向量、active artifact 或 manifest 的操作都必须先建立唯一恢复目录 `.rag/recovery/<pipeline>/<build_id>/`，写入并验证 snapshot、old manifest 和 `journal.json`。journal 在首次破坏性写入前从 prepared 原子更新为 mutating；存在任何 journal 时，同 pipeline 不得开始第二个写操作。

启动 ingest 或只读健康检查发现 journal 时，不猜测最新目录，也不继续原业务请求。只读命令只报告阻断问题；交互恢复经确认后或显式 ingest 才能调用恢复器，非交互只读命令不得写入。另一 pipeline 的恢复目录不得读取或修改。

恢复器先判定事务是否已经跨过 manifest 生效点：replace/force 的 manifest 已引用新 build，且新向量和必需 artifact 回读一致；或 prune 的 manifest 已移除记录、对应向量为空且 artifact 位于 quarantine。满足时完成提交收尾，删除 journal 后清理恢复目录，不得回滚已生效事务。否则按 journal 的 operation/current_step 使用持久化 snapshot、old manifest 和 artifact quarantine 回滚，逐项回读验证后删除整个恢复目录。完成后重新执行完整健康检查。无法证明已提交或无法完整回滚时写 `state=recovery_failed` 并保留全部证据，pipeline 不可查询。

恢复目录创建失败、snapshot 不完整或 old manifest hash 不匹配时，操作必须在改变旧状态前终止。恢复文件只是事务证据，不属于阶段缓存，不得被 DocumentProcessor 当作输入复用。

## 4. 单文档发布顺序

1. DocumentProcessor 完成 DocumentBuildResult 和 staging 校验。
2. Embedder 完成全部新 chunks，验证数量、维度和 token 上限。
3. 读取并验证旧 VectorSnapshot、旧 manifest bytes 和旧 ManifestDocument，持久化恢复目录及 prepared journal。
4. 原子更新 journal 为 mutating/vector_replace；删除该 document_id 旧向量，写入全部新向量。
5. 回读验证 count、chunk IDs、build_id、file_hash、config_fingerprint，并更新 current_step=vector_verify。
6. 更新 current_step=artifact_publish，将 staging 同卷重命名到不可变 build 目录。
7. 更新 current_step=manifest_publish，原子保存包含新 ManifestDocument 的 manifest；此刻新 build 生效。
8. 删除恢复目录，然后清理旧 artifact build；清理失败只记 warning。

步骤 4—7 任一步失败：把 journal 更新为 restoring，删除新向量并从持久化 snapshot 恢复，恢复 old manifest bytes，删除本次 staging/build，再回读验证。成功后删除恢复目录；失败则保留 recovery_failed journal 和全部恢复证据并抛 IndexPublicationError，健康检查阻止查询。

## 5. Manifest 原子保存

在 manifest 同目录创建临时文件，写入、flush、fsync、关闭后使用同卷 replace。临时文件名包含 build_id。序列化或 fsync 失败不得触碰旧文件。replace 成功后再 fsync 父目录；Windows 不支持父目录 fsync 时记录平台事实，不伪报失败。

## 6. 全量 force

全量 force 对目标 pipeline 执行全有或全无发布：

1. 所有文档依次完成 processor、artifact staging 和 embedding；任一失败清理所有本次 staging，目标索引不变。
2. 对目标 collection 全量读取 CollectionSnapshot，验证数组和维度；读取旧 manifest bytes。
3. 把 CollectionSnapshot、old manifest 和 prepared journal 写入恢复目录并验证，随后更新为 mutating。
4. 重新创建目标 collection，写入全部新记录并逐文档验证，更新 journal current_step。
5. 发布所有 artifact build，更新 journal current_step。
6. 原子保存一次新 manifest；这是新全量 build 的生效点。
7. 删除恢复目录，成功后清理旧 artifacts。

步骤 4—6 失败时进入 restoring，重建目标 collection 并从持久化 CollectionSnapshot 恢复，恢复旧 manifest bytes，删除本次 build并回读验证；成功后删除恢复目录，失败则保留 recovery_failed journal。另一 pipeline 始终不打开、不快照、不修改。

CollectionSnapshot 可能占用大量内存，但本版是本地教学项目，优先保证一致性；不在 V2.0 引入临时 collection 交换或检查点。若真实数据量证明不可接受，再作为后续架构升级。

## 7. Artifact 生命周期与 prune

- staging 超过 24 小时且不属于当前进程 build_id 时可在 ingest 启动时清理。
- 每个文档始终保留 manifest 引用的 active build；成功发布后默认只保留该 build。
- orphan 只在健康检查报告，不自动删除；下次成功 ingest 可在明确日志后删除同文档 orphan。
- `--prune` 逐文档事务执行：先持久化 VectorSnapshot、old manifest 和 prepared journal；再将该文档全部 artifact builds 同卷原子移动到恢复目录的 `artifact-quarantine/`；随后删除向量并原子发布移除记录后的 manifest。manifest 保存成功是 prune 生效点。提交后先原子删除 journal，再删除其余恢复目录及 quarantine；后续清理失败只产生 `artifact_cleanup_failed` warning 和无 journal 的清理残留，不阻止查询。移动 artifact、删除向量或 manifest 发布失败时，按 journal 恢复向量、old manifest，并把 quarantine 原子移回原路径；三者回读一致后才能删除 journal。不允许 manifest 引用已删除的 active artifact。
- HTML 没有额外“latest”复制文件；CLI 通过 manifest 输出 active HTML 路径。

## 8. 健康检查

v2 每个 ManifestDocument 必须满足：

- collection 中 count、build_id、file_hash、config_fingerprint 与记录一致；
- artifact_path 位于 `.rag/artifacts/v2/documents/<document_id>/<build_id>`；
- 五个必需文件存在且公共身份字段一致；
- chunks.json 的 chunk IDs/count 与向量一致；
- winner HTML 存在；selection summary 的 winner_count 与 HTML 声明一致。

任一不满足产生 `artifact_index_mismatch` 或 `collection_manifest_mismatch`，pipeline unusable，需重建目标文档或 pipeline。orphan 单独产生非阻断 `orphan_artifact_build`；stale staging 产生非阻断 `stale_artifact_staging`。

v1 没有 artifact_path，不执行 artifact 检查。

## 9. 技术验证门

Chroma 1.5.9 已完成 get(include embeddings) 文档 round-trip、空 snapshot、collection 全量 snapshot、删除后恢复和进程重启读取，先前的最小 PersistentClient 阻塞未复现。证据见[编码前技术验证记录第 3 节](technical-validation.md)。实现时仍须对步骤 4—7 分别注入异常和进程中断，验证 journal 恢复，而不是用适配器探针替代事务测试。

Windows 同卷文件 replace 和目录 rename 已通过；目录 fsync 不受支持，打开 HTML 时删除旧 build 返回 WinError 32。实现必须记录目录 fsync 平台事实，并把提交后的旧 build 清理失败作为非阻断 warning 和后续重试项。证据见[编码前技术验证记录第 5 节](technical-validation.md)。
