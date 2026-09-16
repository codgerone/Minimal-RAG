# Docling 2.121.0 到 LayoutDocument 的映射规格

本文是[当前架构第 2.2、3.1 节](../../architecture.md)的外部适配子文档，定义锁定版本 `docling==2.121.0`、`docling-core==2.92.0` 和 raw schema `DoclingDocument 1.10.0` 的映射契约。mapper 读取 DoclingDocument SDK 对象；raw `document.json` 只用于持久化和契约 fixture，不在 application/domain 中解析。SDK 类型从 `docling_core.types.doc` 公共入口导入，不依赖未导出的内部 `items` 路径。

## 1. 输入与输出

输入必须包含成功的 DoclingDocument、源 PDF 页面尺寸和 SourceDocument。输出 LayoutParseResult：

```python
class LayoutParseResult:
    raw_document: DoclingRawArtifact
    layout_document: LayoutDocument
    docling_table_candidates: tuple[TableCandidate, ...]
    page_executions: tuple[PageExecution, ...]
```

DoclingRawArtifact 只保存 staging 中 raw JSON 的相对路径、Docling schema/version 和文档统计，不把 SDK 对象传出 infrastructure。

## 2. 遍历与去重

1. 使用 `DoclingDocument.iterate_items(with_groups=True, traverse_pictures=True)` 遍历 body 主树，保存 SDK 给出的主阅读顺序和 group 层级。
2. 建立 handled_refs。一个 self_ref 一旦映射为节点、List item、table placeholder 或附属文字，后续引用不得再次输出。
3. table 的 captions 在 TableNode 位置前输出为 LayoutText(caption)；footnotes 在 placeholder 后输出为 LayoutText(table_note)。
4. picture 本身跳过；其 captions 在图片出现位置输出为 LayoutText(caption)。无文字 caption 不输出。
5. body 遍历后，收集未处理的 page_header/page_footer。按第 5 节插入，不因它们未挂在 body children 而丢失。
6. 其他未处理且带非空 text 的 TextItem 按 provenance 位置插入为 other_text，并产生 `detached_text_recovered` warning；无 provenance 的放到末尾并产生 `source_location_unavailable`。

同一 self_ref 在 caption/footnote 引用和 body 主树同时出现时，以第一次规定位置为准并加入 handled_refs，不重复正文。

## 3. label 映射

| Docling label | Layout 结果 |
| --- | --- |
| `section_header`、`title` | LayoutText.kind=`title` |
| `text`、`paragraph` | `paragraph` |
| `page_header` | `header` |
| `page_footer` | `footer` |
| `footnote` | `footnote`；若被 table.footnotes 引用则 `table_note` |
| `caption` | `caption`；若被 table.captions 引用仍为 `caption` |
| `formula` | `other_text`，保留可用 text |
| `list_item` 且属于 list group | LayoutListItem |
| `list_item` 但不属于 list group | LayoutText.kind=`other_text`，保留 orig；warning=`detached_list_item` |
| `table` | LayoutTablePlaceholder |
| `picture` | 不创建节点，只处理 captions |
| 其他含 text 的类型 | `other_text`，warning=`unknown_docling_text_label` |
| 其他无 text 的类型 | 不输出，warning=`unsupported_nontext_item` |

文字正文优先使用 `item.text`；仅 detached list item 使用 `orig` 以避免丢失 marker。`orig` 与 `text` 都保存在 mapper 契约测试输入中，但 ParsedDocument 正文只保存上述选定文本。

## 4. group 与 List

- label/name 均为 `list` 的 GroupItem 建立一个 LayoutList。
- 按 group.children 顺序递归展开。直接 ListItem level=0；嵌套 list group 每深入一层 level+1。
- item.ordinal 取 `marker.strip()`；marker 缺失或空串时为 None。`enumerated` 只保留为来源诊断，不自行生成 marker。
- item.text 使用 Docling `text`，不能把 marker 再拼进正文；最终序列化器按 ordinal 输出。
- parent_item_id 是当前嵌套 list group 之前最近的上级 ListItem。若出现嵌套 group 但没有父项，将其中 items 降为当前 level，parent=None，并产生 `orphan_nested_list_group`。
- `key_value_area`、`form_area` 和未知 group 不形成 List；按 children 原顺序透明展开。
- 相邻两个 list group 不合并，即使页面和缩进相同。

## 5. 来源与阅读顺序

每个 provenance 生成一个 PageSpan。page_no 必须为正；bbox 按 coord_origin 转换到 `pymupdf_page_top_left_pt_v1`，并使用 PDF 对应页尺寸验证。多个 provenance 按原数组顺序保存，charspan 只用于契约验证，不拆分跨页节点。

body 主树顺序优先。未在主树中的家具元素按页插入：

- 某页所有 header 按转换后 bbox `(y0,x0,self_ref)` 排序，插入该页第一个主树元素之前。
- footer 同序排序，插入该页最后一个主树元素之后。
- 一个不可切分主树节点跨多页时，其涉及页面的 headers 全部放在该节点之前，footers 全部放在该节点之后；不得为插入家具元素切开节点。
- 页面没有主树元素时，该页 headers、其他脱离文字、footers 按 `(y0,x0,self_ref)` 输出。
- 无 bbox 但有页码时按 self_ref 排在该类别最后；无页码元素统一放文末。

跨页节点的这种家具顺序是结构完整性优先的确定性选择，审核和 metadata 仍保存每个 PageSpan。

## 6. table 与附属文字

每个 Docling TableItem 产生一个 TableSlot 和一个 LayoutTablePlaceholder，两者共享 slot_id/docling_ref。slot 按 table placeholder 在主阅读顺序中的先后编号；不按 `document.tables` 数组位置另排一次。

TableItem 同时交给 Docling table normalizer 生成候选。fallback 必须引用这一候选，不能在 Assembler 中再次从 SDK 转换。

captions 先于 placeholder，footnotes 后于 placeholder。references 只保存为 TableSlot source_refs，不输出额外正文。caption/footnote 原本也在 body 树时由 handled_refs 防止重复。

## 7. 完整性检查与失败

mapper 完成后验证：

- element_id、item_id、slot_id 唯一；
- 每个 table self_ref 恰有一个 slot 和一个 placeholder；
- 每个 placeholder 能找到同 slot；
- 所有 List parent_item_id 指向同 List 中更早的 item；
- 非空、受支持文字 self_ref 恰好承载一次；
- PageSpan 页码不超过 PDF 页数，bbox 合法或明确为 None；
- 图片正文没有进入 LayoutDocument。

身份、slot 或重复承载错误抛 `LayoutMappingError`。单个文字缺来源保留节点并 warning，不失败。未知非文字类型保留 warning，不伪造文本。

## 8. 契约 fixtures

至少固定以下 Docling 2.121.0 fixture：普通段落、section header、页眉页脚、跨页 text 的多个 provenance、单层/嵌套/无 marker List、key_value/form group、table caption/footnote、picture caption、formula、未知 label、脱离 body 的文字、缺 provenance、BOTTOMLEFT/TOPLEFT bbox。

升级 Docling 或 docling-core 后必须重跑全部 fixture；映射结果变化时更新 mapper version 和 BuildConfig fingerprint。
