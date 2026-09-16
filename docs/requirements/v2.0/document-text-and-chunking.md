# 普通文本、List 文本化与 V2 分块需求

本文完整定义[当前系统需求第 6 节](../../requirements.md)的非表格节点正文和 V2 结构分块算法。表格正文仍由[结构表格文本化需求](table-text-serialization.md)定义。规则版本固定为 `document_text_v1` 和 `structured_chunk_v1`。

## 1. 共同文本规则

- 输入原文只把 `\r\n`、`\r` 统一为 `\n`，去除整个节点首尾空白；内部文字、标点、大小写和换行不得改写。
- 规范化后为空的节点不生成正文，但保留 `empty_text_node` warning 和来源；一份文档最终没有任何非空正文时入库失败。
- 不向正文添加“标题”“段落”“页眉”“页脚”等类型标签。节点类型只进入 metadata。
- 两个完整 TextNode 以两个换行 `\n\n` 连接。跨页或跨章节不额外插入页码、分隔线或提示语。
- embedding 和检索后交给 LLM 的正文完全相同；E5 `passage: ` 只由 embedding adapter 添加。

## 2. List 的完整文本

一个 ListNode 按 items 原顺序输出，每个 item 至少一行：

```text
<每层两个空格><标记><一个空格><item 原文第一行>
<延续缩进><item 原文后续行>
```

规则如下：

- level 从 0 开始，每层前置两个 ASCII 空格。
- ordinal 非空时，标记原样使用 ordinal；ordinal 为空时使用 `-`。
- item 内部换行保留；第二行起在层级缩进后再增加两个空格，不重复项目标记。
- 不自行生成序号，不改写原始 ordinal，不把父项文字复制到每个子项。
- item 之间使用一个换行；List 末尾不追加多余空行。

例如：

```text
1. 交付要求
  - 第一批次
    说明保持原换行
  - 第二批次
```

## 3. chunk 组合顺序

- TableNode 和 ListNode 分别使用独立 chunk 流，不能与相邻 TextNode 混装。
- 连续 TextNode 在实际 token 上限内按阅读顺序合并；加入下一完整节点超限时先结束当前 chunk。
- 遇到 ListNode 或 TableNode 时结束当前普通文本 chunk；其后重新开始。
- 完整单元能够单独容纳时不得为填满前块而拆分。

## 4. 超长普通文本的递归边界

单个 TextNode 超限时，按以下有序层级递归拆分：

1. 两个及以上连续换行；
2. 单个换行；
3. 句末符号 `。！？.!?` 后的位置；
4. 分号 `；;` 后的位置；
5. 冒号 `：:` 后的位置；
6. 逗号 `，,` 后的位置；
7. Unicode 空白后的位置；
8. Unicode code point 边界。

每一级从左到右选择在 token 上限内可容纳的最长前缀；分隔符保留在前一片段，分隔符后的空白保留原文。当前级找不到有效切点才进入下一级。不得在 Unicode code point 内切割。

## 5. 普通文本 overlap

- title 的 overlap 固定为 0。
- paragraph、header、footer、footnote、caption、table_note、other_text 仅在同一个超长节点内部允许 overlap。
- 生成下一片段时，从上一片段末尾按第 4 节相反优先级寻找不超过 32 tokens 的最长完整后缀；找不到自然边界时按 code point 取得不超过 32 tokens 的最长后缀。
- overlap 可以少于 32 tokens；正文、overlap、前缀和特殊 token 合计仍必须不超过模型上限。
- overlap 不能跨 TextNode，且在 ChunkSource 中标记 `repeated_context=true`。

## 6. 超长 List

1. 先按完整 ListItem 分片，item 之间没有 overlap。
2. 某个 item 加上理解它所需的祖先链仍可容纳时，片段开头按完整 List 格式重复从根到直接父项的祖先行。
3. 祖先链过长时，从最远祖先开始移除正文，替换为 `〔上级项：第N项〕`；N 是该父项在其同级兄弟中的一基位置。仍超限则继续向直接父项退化，直至只保留位置链。
4. 单个 item 正文仍超限时，按第 4 节拆 item 内部文字，并按第 5 节设置最多 32 tokens overlap。
5. 重复祖先标记为 repeated_context；item 自身正文至少在一个非重复来源范围中完整覆盖。

## 7. 超长表格

表格按[结构表格文本化需求第 8 节](table-text-serialization.md)拆分。所谓“尽量让纵向合并覆盖行同块”具体指：若整个合并覆盖范围及必要表头能在一个 chunk 内容纳，则不得在范围内部切分；否则按数据行边界切分，并在各片段重复合并格事实和原始/当前覆盖范围。

重复表头放不下时使用 `〔表格 <table_id>，字段路径见完整表格〕` 加本片段实际涉及的字段路径；列表父项位置链放不下时使用 `〔List <node_id>，父项路径见完整列表〕`。这些兜底文字计入 token 预算并标记 repeated_context。

## 8. token 预算与终止保证

- TokenCounter 必须和实际 embedding adapter 使用同一 tokenizer、`passage: ` 前缀及 special tokens。
- 每次选择切点后立即重算完整 passage；超限则继续缩短，不能依赖估算或模型截断。
- code point 级单字符加最短定位上下文仍超限时，抛出 `ChunkingError`，不得丢弃字符。
- 每轮必须消费至少一个此前未消费的 code point、ListItem、数据行或 cell；测试必须证明算法不会死循环。

## 9. 来源和验收

每个 chunk 保存 node、页面集合、原文范围、父单元、fragment_index、fragment_count，以及重复上下文范围。原始正文的每个字符必须至少出现在一个非重复范围；允许 overlap 导致重复，不允许缺失或乱序。

验收至少覆盖：空节点、内部换行、多语言标点、无空白长串、标题超限、跨页 TextNode、多节点合并、嵌套列表、ordinal 缺失、超长祖先、单项超限、32-token 边界、单字符兜底、表格纵向合并切分和重复上下文退化。
