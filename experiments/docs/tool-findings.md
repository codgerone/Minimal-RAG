# 四工具原始输出与适配依据

本文记录工具事实及证据，不另建一套需求。公共模型、变换公式和恢复算法的唯一详细定义位于[架构](architecture.md)。字段核查日期：2026-09-09；2026-09-10 已针对 E001-602.pdf 重跑四工具九策略，详见[重构验收](history/refactor-validation.md)。这不等于对全部历史 PDF 重新提取。

## 1. 版本与证据边界

| 包 | 本地安装版本 | 本次重点核查 |
| --- | --- | --- |
| PyMuPDF | 1.28.0 | Table.cells/rows、页面坐标与旋转 |
| camelot-py | 2.0.0 | Cell 四边、hspan/vspan、copy_text/shift_text |
| docling | 2.121.0 | TableStructureOptions、TableCell 构建 |
| docling-core | 2.92.0 | TableCell span/offset/bbox schema |
| unstructured | 0.27.1 | Element metadata、HTML 与 PixelSpace |

这些版本是本次环境事实，不代表所有历史产物都由这些版本生成。重现某次结果应读取该次 manifest 的版本和配置；缺记录只能记为未知。官方 latest/main 链接会更新，下面同时保留本地源码路径和符号，升级时需重新核查，不能把在线文档变化自动视为项目需求变化。

## 2. 能力对照

| 工具 | 原生数值跨度 | 原生 cell bbox | bbox 的含义 | 本管道使用方式 |
| --- | --- | --- | --- | --- |
| PyMuPDF | 当前快照接口没有直接数值 span | 有；cell 位置也可能为空 | 工具识别/推断的格区域；text 策略不要求有真实绘制线 | 从几何边界推算逻辑覆盖 |
| Camelot | 无数值 span；有四边与 hspan/vspan 布尔值 | 有，原子格矩形 | 解析所得网格区域，区别于内部文本范围 | 用缺共享边恢复矩形合并区域 |
| Docling | 有 row_span、col_span、起终点 offsets | 可选 | 预测及匹配后处理结果；可能对齐文本，不保证是物理格边界 | 原生逻辑跨度优先；bbox 仅作可追溯几何 |
| Unstructured | Table HTML 可表达 rowspan/colspan | 现有实验 raw 未提供可用 cell bbox | metadata.coordinates 为整个 Element 区域 | HTML 恢复逻辑网格，cell bbox=null |

“真实单元格边界”应理解为足以描述格区域的几何证据，而非工具准确率承诺。网格框、文本框与缺失框不能互相冒充；同一工具也可能因策略、版本、匹配配置不同而改变 bbox 语义。

## 3. PyMuPDF

Table 的 cells/rows 提供格区域，extract 提供文本矩阵；本管道没有取得直接的数值行列跨度，因此需要把物理格边缘映射到聚类后的行列边界。`None` 位置不能单独证明它是合并格的延续。lines、lines_strict、text 的识别证据不同，尤其 text 可以依赖文本对齐推断边界。[官方 Page/Table 接口](https://pymupdf.readthedocs.io/en/latest/page.html)

项目证据：[pymupdf_runner.py](../table_extraction/infrastructure/extractors/pymupdf/runner.py)、[pymupdf_normalizer.py](../table_extraction/infrastructure/extractors/pymupdf/normalizer.py)。后者实现边界聚类和 span 映射；边界聚类、原始矩阵一致性告警和源引用已随重构修正，验证范围见[历史重构验收](history/refactor-validation.md)。

坐标不能仅写“左上角，所以无需转换”：PyMuPDF 的页面操作坐标与旋转后的 page.rect 需要区分；rotation_matrix/derotation_matrix 用于相应空间映射。当前公共空间采用有效页面基准，旋转与裁剪样本必须验证后才能宣称适配正确。[官方页面坐标说明](https://pymupdf.readthedocs.io/en/latest/page.html)

## 4. Camelot

本地 `camelot/core.py::Cell` 的 x1/y1 为左下、x2/y2 为右上，表示格矩形；hspan/vspan 由左右/上下边状态推导，是布尔值，不提供跨度数量。项目根据相邻原子格双侧缺边恢复连通区域，只有矩形分量才形成合法合并格。[官方 Cell API](https://camelot-py.readthedocs.io/en/latest/api.html)

`copy_text` 控制在跨度格中复制文字，默认 None；默认 `shift_text=['l','t']` 与复制不是同一动作。本地 `core.py::copy_spanning_text` 及其水平/垂直辅助方法可查到复制逻辑。项目 [camelot_runner.py](../table_extraction/infrastructure/extractors/camelot/runner.py) 对 lattice/hybrid 显式使用 copy_text=None、shift_text=["l","t"]；stream/network 不传这两个不适用的参数。因此实际调用未开启 copy_text，因此不能声称每个合并区域原始格都会重复出现 xyz。[官方跨度文本说明](https://camelot-py.readthedocs.io/en/stable/user/advanced.html)

项目 [camelot_normalizer.py](../table_extraction/infrastructure/extractors/camelot/normalizer.py) 的 `_component_text` 当前对分量内相同完整文本去重。用户确认的规格限定为已确认同一合并格内的重复表示；独立格同值、一个源格自身的重复词不删除。已补 source_refs 和限定去重用例，不能用评分 token 归一化扩大去重范围。

## 5. Docling

本地 `docling_core/types/doc/items/table/table_data.py::TableCell` 定义可选 bbox、数值 span、零基排他 offsets、文本和 header/section 标记。逻辑结构来自工具原生结果，适配器不应从 bbox 重新推算合法原生跨度。[官方文档模型](https://docling-project.github.io/docling/reference/docling_document/)

bbox 需要沿处理链判断，而不能只看字段名：

1. `docling/datamodel/pipeline_options.py::TableStructureOptions` 默认 do_cell_matching=True；当前 runner 设置 accurate 等选项，没有关闭匹配。
2. `docling/models/stages/table_structure/table_structure_model.py` 将 TableFormer 响应构成 TableCell，并还原图像缩放。
3. `docling_ibm_models/tableformer/data_management/tf_predictor.py::_generate_tf_response` 从匹配后的 table_cell 复制 bbox；`_merge_tf_output` 同时保留另外的 text_cell_bboxes，不可把两个字段混称。
4. `matching_post_processor.py::_align_table_cells_to_pdf` 会把格框对齐到匹配 PDF 文本区域，多块时取联合；process 的相应步骤在 PDF cell 数量超过 300 时跳过。其他后处理还可能调整区域。

因此当前匹配路径的 cell bbox **可能是文本对齐及后处理区域，不保证真实格边界**，也不能断言每个 bbox 都恰好等于文本框。模型预测路径、匹配开关及版本差异都需要保留。来源：[TableFormer predictor](https://github.com/docling-project/docling-ibm-models/blob/main/docling_ibm_models/tableformer/data_management/tf_predictor.py)、[匹配后处理](https://github.com/docling-project/docling-ibm-models/blob/main/docling_ibm_models/tableformer/data_management/matching_post_processor.py)。

TableItem.prov 是整表区域与页面来源；TableCell.bbox 是格级几何，两者不同。bbox 的 coord_origin 可以为 TOPLEFT/BOTTOMLEFT，必须逐对象检查；跨页表若没有 cell-to-page 关联，不根据坐标猜页码。项目实现见 [docling_normalizer.py](../table_extraction/infrastructure/extractors/docling/normalizer.py)，转换限制和原生异常保留要求见架构。

## 6. Unstructured

当前实验用 hi_res + infer_table_structure=True，Table Element 的 metadata.text_as_html 提供表格 HTML，metadata.coordinates 提供 Element 区域。原始 Element.text 与结构模型 HTML 是不同表示，不能假定两者文本始终完全一致。[官方 Element 元数据](https://docs.unstructured.io/concepts/document-elements)、[PDF 分区源码](https://github.com/Unstructured-IO/unstructured/blob/main/unstructured/partition/pdf.py)

本地 `unstructured/documents/coordinates.py::PixelSpace` 使用左上像素空间；转换需读取 layout_width/layout_height，分别按 x/y 比例缩放到 PDF pt，不固定假设 DPI。项目只适配已验证的 PixelSpace；未知空间应保留原始事实并诊断。

本地 ElementMetadata 另有可选 `table_as_cells` 字段，但当前实验保存的 Unstructured raw 中未检出该字段，适配器也未使用它。因此这里的结论是“现有实验输入没有可用 cell bbox”，不能扩大为“所有 Unstructured 版本/API 永远不提供 cell 级结果”。升级或启用其他输出后需另核其 schema、坐标与 bbox 语义。项目实现见 [unstructured_normalizer.py](../table_extraction/infrastructure/extractors/unstructured/normalizer.py)。

## 7. 复核方法与尚未验证部分

升级工具时，记录实际版本和有效配置，保存最小 raw 样本，对照原 PDF、原始格事实和 normalized，分别检查：数值跨度来源、bbox 含义、坐标转换、空格与合并文本、零表、异常页继续执行。跨页、旋转/裁剪及部分返回需要针对性样本，不能由正常单页样本推断全部支持。

本轮完成字段与源码依据梳理；未重新验证每张历史表格的真实边界、逐页成功状态或 winner 质量。逐页失败恢复已有自动测试，但全部表格人工质量复核不在本次重构验收范围；相关边界见 [当前限制](limitations.md) 和 [验收](validation.md)。
