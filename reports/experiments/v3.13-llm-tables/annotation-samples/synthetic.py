"""人造样表：仿照真实表格，覆盖现有 12 张表没有的结构（嵌套分节、多行节标题、表头之上的全表标题、
合计与注释行、隐式合并、本来就没有值的空格、解析出的纵向合并）。不进评估集。

网格写法：`<` 表示被左侧单元格横向覆盖，`^` 表示被上方单元格纵向覆盖，空字符串是空单元格。
`expected` 是造表时预期的标注，只供审核页对照，不作为校验依据。
"""

from __future__ import annotations

from rag.ingest.tables.models import StructuredTable, TableCell
from rag.models import BoundingBox


def build(table_id: str, rows: list[list[str]]) -> StructuredTable:
    height, width = len(rows), len(rows[0])
    assert all(len(r) == width for r in rows), table_id
    cells = []
    for r in range(height):
        for c in range(width):
            if rows[r][c] in ("<", "^"):
                continue
            c1 = c + 1
            while c1 < width and rows[r][c1] == "<":
                c1 += 1
            r1 = r + 1
            while r1 < height and all(rows[r1][k] == "^" for k in range(c, c1)):
                r1 += 1
            cells.append(TableCell(f"{table_id}_r{r:04d}c{c:04d}", rows[r][c], r, r1, c, c1, r1 - r,
                                   c1 - c, BoundingBox(c * 60, r * 12, c1 * 60 - 4, r1 * 12 - 2),
                                   ("body",), "native", ()))
    return StructuredTable(table_id, "pymupdf", "lines", height, width, tuple(cells), (), None, (), ())


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _budget_rows() -> list[list[str]]:
    full = lambda text: [text] + ["<"] * 5
    rows = [full("PRESUPUESTO DE OBRA: AMPLIACIÓN DE LA SUBESTACIÓN DE DISTRIBUCIÓN SED-114 Y REDES ASOCIADAS"),
            ["Partida", "Descripción", "Und.", "Metrado", "Precio (S/)", "<"],
            ["^", "^", "^", "^", "Unitario", "Parcial"]]
    groups = [
        ("01", "OBRAS CIVILES", [
            ("01.01", "Movimiento de tierras", None, [
                ("Excavación manual de zanjas en terreno normal para cables subterráneos", "m3", 45.60, 38.50),
                ("Excavación de hoyos para cimentación de postes de concreto armado", "m3", 18.20, 52.00),
                ("Relleno compactado con material propio seleccionado en capas de 0.20 m", "m3", 39.80, 24.75),
                ("Eliminación de material excedente con volquete a distancia promedio de 5 km", "m3", 24.00, 31.40),
                ("Nivelación y compactación del terreno para la losa de la subestación", "m2", 64.00, 9.80),
            ]),
            ("01.02", "Concreto simple y armado", None, [
                ("Solado de concreto f'c = 100 kg/cm2, espesor 0.10 m", "m2", 64.00, 42.30),
                ("Concreto f'c = 210 kg/cm2 para losa de la subestación", "m3", 12.80, 415.00),
                ("Acero de refuerzo fy = 4200 kg/cm2, habilitado y colocado", "kg", 1350.00, 6.85),
                ("Encofrado y desencofrado de muros perimetrales de la caseta", "m2", 86.40, 48.20),
                ("Concreto f'c = 175 kg/cm2 para buzones de inspección de cables", "m3", 6.40, 380.00),
                ("Tapa de concreto armado para buzón, con marco de fierro fundido", "und", 8.00, 265.00),
            ]),
        ]),
        ("02", "INSTALACIONES ELECTROMECÁNICAS", [
            ("02.01", "Tableros y celdas de media tensión",
             "(incluye suministro, transporte, montaje, pruebas y puesta en servicio)", [
                ("Celda de llegada con seccionador bajo carga 24 kV, 630 A", "und", 2.00, 38500.00),
                ("Celda de protección del transformador con interruptor de vacío 24 kV", "und", 1.00, 52300.00),
                ("Celda de medición con transformadores de tensión y de corriente", "und", 1.00, 41750.00),
                ("Tablero general de baja tensión 400/230 V con barras de cobre", "und", 1.00, 28900.00),
                ("Tablero de servicios auxiliares con rectificador y banco de baterías", "und", 1.00, 17450.00),
                ("Sistema de puesta a tierra con resistencia menor de 10 ohmios", "glb", 1.00, 9850.00),
            ]),
            ("02.02", "Cables y accesorios", None, [
                ("Cable N2XSY 18/30 kV, 1 x 70 mm2, tendido en ducto", "m", 640.00, 96.40),
                ("Cable NYY 0.6/1 kV, 3 x 120 mm2 + 1 x 70 mm2, tendido en zanja", "m", 380.00, 118.70),
                ("Terminación unipolar exterior para cable de 18/30 kV", "jgo", 6.00, 2150.00),
                ("Empalme recto termocontraíble para cable de 18/30 kV", "jgo", 4.00, 1890.00),
                ("Ducto de PVC SAP de 6 pulgadas para cruce de vías", "m", 120.00, 58.30),
                ("Cinta señalizadora de peligro y ladrillo de protección", "m", 640.00, 7.20),
                ("Pruebas de aislamiento y de continuidad de cables instalados", "glb", 1.00, 4200.00),
            ]),
        ]),
    ]
    grand = 0.0
    for code, name, subgroups in groups:
        rows.append([code, name, "<", "<", "<", "<"])
        subtotal = 0.0
        for sub_code, sub_name, sub_note, items in subgroups:
            rows.append([sub_code, sub_name, "", "", "", ""])
            if sub_note:
                rows.append(["", sub_note, "", "", "", ""])
            for k, (description, unit, quantity, price) in enumerate(items, 1):
                partial = round(quantity * price, 2)
                subtotal += partial
                rows.append([f"{sub_code}.{k:02d}", description, unit, f"{quantity:,.2f}",
                             _money(price), _money(partial)])
        rows.append(["", f"Subtotal {code} {name.capitalize()}", "", "", "", _money(subtotal)])
        grand += subtotal
    rows.append(["", "COSTO DIRECTO", "", "", "", _money(grand)])
    rows.append(full("Nota: Los precios unitarios no incluyen el Impuesto General a las Ventas (IGV)."))
    return rows


def _schedule_rows() -> list[list[str]]:
    rows = [["Lot", "Destination warehouse", "Item", "Description", "Qty", "Delivery date",
             "Inspection witness", "Remarks"]]
    lots = [
        ("Lot 1", [
            ("Callao Central Warehouse, Av. Argentina 2850", [
                ("1.1", "Single phase meter 2 wires, 5(60) A, class 1", "12,000", "15/03/2027", "Client QA"),
                ("1.2", "Single phase meter 3 wires, 5(80) A, class 1", "4,500", "15/03/2027", "Client QA"),
                ("1.3", "Meter box in polycarbonate with sealing screws", "16,500", "15/03/2027", ""),
            ]),
            ("Arequipa Regional Warehouse, Parque Industrial Mz. F", [
                ("1.4", "Single phase meter 2 wires, 5(60) A, class 1", "6,000", "30/03/2027", "Client QA"),
                ("1.5", "Meter box in polycarbonate with sealing screws", "6,000", "30/03/2027", ""),
            ]),
        ]),
        ("Lot 2", [
            ("Callao Central Warehouse, Av. Argentina 2850", [
                ("2.1", "Three phase meter 4 wires, 5(100) A, class 1, with RS-485 port", "1,200", "20/05/2027", "Third-party inspector"),
                ("2.2", "Three phase meter 3 wires, 5(100) A, class 1", "", "", ""),
                ("2.3", "Current transformer 200/5 A, class 0.5S", "3,600", "20/05/2027", "Third-party inspector"),
                ("2.4", "Test terminal block for indirect metering", "1,200", "20/05/2027", ""),
            ]),
            ("Trujillo Regional Warehouse, Av. Nicolás de Piérola 1210", [
                ("2.5", "Three phase meter 4 wires, 5(100) A, class 1, with RS-485 port", "800", "05/06/2027", "Third-party inspector"),
                ("2.6", "Current transformer 200/5 A, class 0.5S", "2,400", "05/06/2027", "Third-party inspector"),
            ]),
        ]),
        ("Lot 3", [
            ("Callao Central Warehouse, Av. Argentina 2850", [
                ("3.1", "Data concentrator with PLC and 4G modem", "150", "^", "Client QA"),
                ("3.2", "Head-end software licence, per 1,000 meters", "30", "^", ""),
                ("3.3", "Installation and commissioning kit for concentrator", "150", "^", ""),
                ("3.4", "Training of client staff, 5 days", "1", "18/08/2027", ""),
            ]),
        ]),
    ]
    remarks = {"2.2": "Cancelled by Addendum No. 2", "3.1": "Includes 2 years of firmware updates",
               "3.4": "At the client's premises"}
    for lot, sites in lots:
        first_in_lot = True
        for site, items in sites:
            for k, (item, description, qty, date, witness) in enumerate(items):
                rows.append([lot if first_in_lot else "", site if k == 0 else "", item, description,
                             qty, date, witness, remarks.get(item, "")])
                first_in_lot = False
    # Lot 3's first three items share one delivery date, merged by the parser.
    first = next(i for i, r in enumerate(rows) if r[2] == "3.1")
    rows[first][5] = "10/08/2027"
    return rows


def _payment_rows() -> list[list[str]]:
    rows = [["期次", "付款节点", "付款比例", "金额（元）", "对应交货内容", "数量", "计划日期"]]
    batches = [
        ("第一批：单相智能电能表及表箱", [
            ("1", "合同生效后预付款", "10%", "1,286,400.00", "—", "", "2027年1月15日"),
            ("2", "到货验收合格后", "40%", "5,145,600.00", "单相智能电能表 2 级", "60,000 只", "2027年3月31日"),
            ("", "", "", "", "单相表箱（透明聚碳酸酯）", "60,000 个", "2027年3月31日"),
            ("", "", "", "", "封印及安装附件", "60,000 套", "2027年3月31日"),
            ("3", "安装调试完成并经买方签认后", "20%", "2,572,800.00", "现场安装指导", "", "2027年5月31日"),
        ]),
        ("第二批：三相智能电能表及互感器", [
            ("4", "到货验收合格后", "15%", "1,929,600.00", "三相四线智能电能表 0.5S 级", "3,000 只", "2027年6月30日"),
            ("", "", "", "", "低压电流互感器 200/5A", "9,000 只", "2027年6月30日"),
            ("", "", "", "", "联合接线盒", "3,000 个", "2027年6月30日"),
            ("5", "系统联调完成后", "10%", "1,286,400.00", "采集终端及主站软件接入", "150 套", "2027年8月31日"),
        ]),
    ]
    for title, items in batches:
        rows.append([title] + ["<"] * 6)
        for item in items:
            rows.append(list(item))
    rows.append(["6", "质保期满且无质量问题后", "5%", "643,200.00", "质量保证金", "", "2029年8月31日"])
    rows.append(["合计", "<", "100%", "12,864,000.00", "", "", ""])
    return rows


TABLES = [  # key, 显示名, 语言, 上下文节点 (kind, text), 网格, 预期标注
    ("syn-budget", "人造：西文工程预算表（嵌套分节、多行节标题、全表标题、合计与注释）", "spanish",
     [("title", "5. PRESUPUESTO REFERENCIAL"),
      ("paragraph", "El presupuesto referencial se ha elaborado con precios vigentes al mes de marzo de 2026 "
                    "y comprende las partidas de obras civiles e instalaciones electromecánicas necesarias "
                    "para la ampliación de la subestación.")],
     _budget_rows,
     "表头 1–2；节：0（全表标题，到末行）；3 起的 01 节（到 Subtotal 01）、其下 01.01、01.02；"
     "02 节、其下 02.01（标题 2 行）、02.02；Subtotal、COSTO DIRECTO、Nota 不是节标题；无隐式合并"),
    ("syn-schedule", "人造：英文分批交货与检验计划（隐式合并、没有值的空格、解析出的纵向合并）", "english",
     [("paragraph", "Page 7 of 15 - Contract No. 045-2026-ELN"),
      ("title", "Annex B. Delivery and inspection schedule")],
     _schedule_rows,
     "表头 0；无分节；隐式合并：Lot 列各批次、Destination warehouse 列各仓库；"
     "Qty/Delivery date/Inspection witness/Remarks 的空格是没有值，不是同上；Lot 3 的交货日期是解析出的合并"),
    ("syn-payment", "人造：中文付款与交货计划（分节、隐式合并、没有值的空格、合计）", "chinese",
     [("title", "第四条 付款与交货"),
      ("paragraph", "买方按下表所列节点向卖方支付合同价款，卖方按对应批次交付货物；每期付款前，卖方应提交等额增值税专用发票。")],
     _payment_rows,
     "表头 0；节：第一批（到期次 3 的行）、第二批（到期次 5 的行）；期次 6 与合计不属于任何一批；"
     "隐式合并：期次、付款节点、付款比例、金额四列在同一期的后续交货行；数量的空格是没有值"),
]
