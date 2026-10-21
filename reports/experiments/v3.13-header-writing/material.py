"""Test material for the header writing experiment: 12 made-up tables, 4 questions each, and prose
paragraphs that compete with them.

Design (docs/decisions/013-llm-tables.md, header writing): header depth 2 / 3 × columns few / medium / many
× length short (one chunk) / long (three chunks or more); half Spanish, half English; questions in
Chinese. Question kinds: `group` asks for an upper header group, `cell` for one row under a leaf
column, `topic` for what the table is about, `rows` for the row entities it lists.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# A header column: (label, children); children None = a row label column spanning every header row.
Header = list[tuple[str, "list | None"]]


@dataclass(frozen=True)
class Question:
    kind: str                 # group / cell / topic / rows
    text: str
    row: str | None = None    # `cell`: the row label whose chunk holds the answer; else any chunk


@dataclass(frozen=True)
class Spec:
    key: str                  # depth-width-length
    language: str
    title: str                # shown to the reviewer only, not indexed
    header: Header
    rows: list[list[str]]     # full rows, row label columns first
    questions: tuple[Question, ...]


def _values(rng: random.Random, n: int, low: int, high: int, fmt: str = "{:,}") -> list[str]:
    return [fmt.format(rng.randint(low, high)) for _ in range(n)]


def _leaves(header: Header) -> int:
    def count(children) -> int:
        return 1 if children is None or isinstance(children, str) else sum(
            count(c[1] if isinstance(c, tuple) else None) for c in children)
    return sum(count(children) for _, children in header)


def specs() -> list[Spec]:
    rng = random.Random(1313)
    out: list[Spec] = []

    # 1. depth 2, few columns, short, English
    models = ["HX-120", "HX-130", "HX-330", "HX-340", "HX-500"]
    out.append(Spec("2-few-short", "english", "Unit prices and lead times by model",
        [("Model", None), ("Price (USD)", ["FOB", "CIF"]), ("Lead time (days)", ["Standard", "Express"])],
        [[m] + _values(rng, 2, 18, 95, "{}.50") + _values(rng, 2, 15, 90, "{}") for m in models],
        (Question("group", "各型号的交货周期（标准和加急）分别是多少天？"),
         Question("cell", "HX-330 的 CIF 单价是多少美元？", "HX-330"),
         Question("topic", "不同贸易术语下各型号的报价是怎样的？"),
         Question("rows", "报价单里一共列了哪些型号？"))))

    # 2. depth 2, few columns, long, Spanish
    codes = [f"MAT-{1000 + i * 7}" for i in range(70)]
    out.append(Spec("2-few-long", "spanish", "Inventario por almacén",
        [("Código", None), ("Existencias", ["Lima", "Arequipa"]), ("Pedidos pendientes", ["Lima", "Arequipa"])],
        [[c] + _values(rng, 4, 0, 900, "{}") for c in codes],
        (Question("group", "各物料在利马和阿雷基帕的待交订单数量分别是多少？"),
         Question("cell", "物料 MAT-1364 在阿雷基帕仓库的库存是多少？", "MAT-1364"),
         Question("topic", "各仓库的库存情况如何？"),
         Question("rows", "库存表中有哪些物料编码？"))))

    # 3. depth 2, medium columns, short, Spanish
    items = [("1", "Medidor monofásico 2 hilos"), ("2", "Medidor monofásico 3 hilos"),
             ("3", "Medidor trifásico 3 hilos"), ("4", "Medidor trifásico 4 hilos")]
    out.append(Spec("2-mid-short", "spanish", "Cronograma de entregas por ítem",
        [("Ítem", None), ("Descripción", None),
         ("Cantidad por entrega", ["1ra entrega", "2da entrega", "3ra entrega", "4ta entrega"]),
         ("Garantía", ["Meses", "Tipo"]), ("Total", None)],
        [[i, d] + _values(rng, 4, 500, 9000) + [str(rng.choice([24, 36])), rng.choice(["Comercial", "Técnica"]),
                                                f"{rng.randint(10, 30)},000"] for i, d in items],
        (Question("group", "每个品目每一批分别交付多少台？"),
         Question("cell", "三相三线电表第三批交付多少台？", "3"),
         Question("topic", "合同规定的分批交货安排是怎样的？"),
         Question("rows", "交货计划涉及哪几种电表？"))))

    # 4. depth 2, medium columns, long, English
    sites = [f"Substation {chr(65 + i // 10)}{i % 10 + 1:02d}" for i in range(55)]
    out.append(Spec("2-mid-long", "english", "Monthly installations by substation",
        [("Site", None), ("Q1 installations", ["Jan", "Feb", "Mar"]), ("Q2 installations", ["Apr", "May", "Jun"]),
         ("Total", None)],
        [[s] + _values(rng, 6, 0, 400, "{}") + [str(rng.randint(500, 2000))] for s in sites],
        (Question("group", "各变电站第二季度每个月安装了多少台？"),
         Question("cell", "Substation E04 二月份安装了多少台？", "Substation E04"),
         Question("topic", "上半年各站点的安装进度如何？"),
         Question("rows", "安装记录覆盖了哪些变电站？"))))

    # 5. depth 2, many columns, short, English
    meters = ["SN-240101", "SN-240102", "SN-240103", "SN-240104", "SN-240105"]
    loads = ["5% Ib", "10% Ib", "50% Ib", "100% Ib", "Imax", "PF 0.5"]
    insul = ["500 V", "1 kV", "2 kV", "4 kV", "6 kV", "Impulse"]
    out.append(Spec("2-many-short", "english", "Type test results",
        [("Meter ID", None), ("Accuracy error (%)", loads), ("Insulation test", insul), ("Result", None)],
        [[m] + [f"{rng.uniform(-0.4, 0.4):+.2f}" for _ in loads] + [rng.choice(["Pass", "Pass", "Fail"]) for _ in insul]
         + [rng.choice(["Approved", "Approved", "Rejected"])] for m in meters],
        (Question("group", "各电表在不同负载点的误差分别是多少？"),
         Question("cell", "SN-240103 在 4 kV 绝缘试验中是否通过？", "SN-240103"),
         Question("topic", "型式试验的结果怎么样？"),
         Question("rows", "参加型式试验的电表编号有哪些？"))))

    # 6. depth 2, many columns, long, Spanish
    supplies = [f"SUM-{40000 + i * 13}" for i in range(45)]
    months = ["Ene", "Feb", "Mar", "Abr", "May", "Jun"]
    out.append(Spec("2-many-long", "spanish", "Consumo mensual por suministro",
        [("Suministro", None), ("Consumo 2024 (kWh)", months), ("Consumo 2025 (kWh)", months), ("Estado", None)],
        [[s] + _values(rng, 12, 80, 900, "{}") + [rng.choice(["Activo", "Activo", "Suspendido"])] for s in supplies],
        (Question("group", "各用户 2025 年上半年每月的用电量是多少？"),
         Question("cell", "用户 SUM-40403 在 2024 年 3 月的用电量是多少千瓦时？", "SUM-40403"),
         Question("topic", "各用电户两年来的用电量变化如何？"),
         Question("rows", "用电量表中列出了哪些用户号？"))))

    # 7. depth 3, few columns, short, Spanish
    milestones = ["Firma del contrato", "Entrega de muestras", "Primera entrega", "Segunda entrega", "Conformidad final"]
    out.append(Spec("3-few-short", "spanish", "Pagos por hito",
        [("Hito", None), ("Condiciones de pago", [("Adelanto", ["%", "USD"]), ("Saldo", ["%", "USD"])])],
        [[m, f"{rng.choice([10, 20, 30])}", f"{rng.randint(10, 90)},000", f"{rng.choice([10, 20, 30])}",
          f"{rng.randint(10, 90)},000"] for m in milestones],
        (Question("group", "每个节点的尾款比例和金额是多少？"),
         Question("cell", "第一批交货节点的预付款金额是多少美元？", "Primera entrega"),
         Question("topic", "合同的付款节点是怎么安排的？"),
         Question("rows", "付款分为哪几个节点？"))))

    # 8. depth 3, few columns, long, English
    parts = [f"PN-{7000 + i * 11}" for i in range(75)]
    out.append(Spec("3-few-long", "english", "Warranty claims by part",
        [("Part number", None), ("Warranty claims", [("2024", ["Received", "Resolved"]), ("2025", ["Received", "Resolved"])])],
        [[p] + _values(rng, 4, 0, 60, "{}") for p in parts],
        (Question("group", "各零件 2025 年的保修索赔受理和解决数量是多少？"),
         Question("cell", "零件 PN-7539 在 2024 年收到了多少起保修索赔？", "PN-7539"),
         Question("topic", "各零件的保修索赔情况如何？"),
         Question("rows", "保修统计涉及哪些零件号？"))))

    # 9. depth 3, medium columns, short, English
    goods = ["Meter box single", "Meter box double", "Seal kit", "Current transformer", "Data concentrator"]
    out.append(Spec("3-mid-short", "english", "Price list by region and channel",
        [("Item", None), ("Price (USD)", [("North region", ["Retail", "Wholesale", "Distributor"]),
                                          ("South region", ["Retail", "Wholesale", "Distributor"])]), ("Notes", None)],
        [[g] + _values(rng, 6, 5, 400, "{}.00") + [rng.choice(["VAT included", "Ex works", "Min. 100 units"])]
         for g in goods],
        (Question("group", "南区各渠道的价格分别是多少？"),
         Question("cell", "Seal kit 在北区的批发价是多少？", "Seal kit"),
         Question("topic", "不同地区、不同渠道的价格体系是怎样的？"),
         Question("rows", "价目表中有哪些商品？"))))

    # 10. depth 3, medium columns, long, Spanish
    companies = [f"{name} – Zona {z}" for name in ("Electro Norte", "Electro Sur", "Hidrandina", "Electrocentro",
                                                    "Seal", "Electro Oriente", "Enosa", "Electro Puno", "Electro Ucayali")
                 for z in range(1, 7)]
    out.append(Spec("3-mid-long", "spanish", "Distribución de medidores por empresa",
        [("Empresa", None), ("Cantidad de medidores", [("Monofásicos", ["2 hilos", "3 hilos"]),
                                                       ("Trifásicos", ["3 hilos", "4 hilos", "Multifunción"])]),
         ("Total", None)],
        [[c] + _values(rng, 5, 0, 5000) + [f"{rng.randint(5, 20)},000"] for c in companies],
        (Question("group", "各配电公司分到的三相电表数量分别是多少？"),
         Question("cell", "Hidrandina – Zona 4 分到多少台单相三线电表？", "Hidrandina – Zona 4"),
         Question("topic", "电表在各配电公司之间是怎么分配的？"),
         Question("rows", "参与分配的配电公司及片区有哪些？"))))

    # 11. depth 3, many columns, short, Spanish
    lab = ["LAB-0091", "LAB-0092", "LAB-0093", "LAB-0094"]
    out.append(Spec("3-many-short", "spanish", "Ensayos de laboratorio",
        [("Medidor", None), ("Resultados de ensayo", [("Exactitud (%)", ["5% Ib", "10% Ib", "50% Ib", "100% Ib", "Imax", "FP 0,5"]),
                                                      ("Aislamiento", ["500 V", "1 kV", "2 kV", "4 kV", "6 kV", "Impulso"])]),
         ("Dictamen", None)],
        [[m] + [f"{rng.uniform(-0.4, 0.4):+.2f}" for _ in range(6)] + [rng.choice(["Cumple", "Cumple", "No cumple"]) for _ in range(6)]
         + [rng.choice(["Conforme", "Conforme", "No conforme"])] for m in lab],
        (Question("group", "各样表的绝缘试验结果如何？"),
         Question("cell", "LAB-0093 在额定电流 100% Ib 下的误差是多少？", "LAB-0093"),
         Question("topic", "实验室检测的结论是什么？"),
         Question("rows", "实验室检测了哪些样表？"))))

    # 12. depth 3, many columns, long, English
    lots = [f"LOT-{2025}-{i:03d}" for i in range(1, 46)]
    out.append(Spec("3-many-long", "english", "Delivery quantities by lot",
        [("Lot", None), ("Quantities by delivery", [("First half", [f"D{i}" for i in range(1, 7)]),
                                                    ("Second half", [f"D{i}" for i in range(7, 13)])]), ("Total", None)],
        [[lt] + _values(rng, 12, 0, 300, "{}") + [str(rng.randint(1000, 3000))] for lt in lots],
        (Question("group", "各批次下半年每次交货的数量是多少？"),
         Question("cell", "LOT-2025-033 第 5 次交货的数量是多少？", "LOT-2025-033"),
         Question("topic", "全年分批交货的数量安排是怎样的？"),
         Question("rows", "交货计划包含哪些批号？"))))

    for spec in out:
        assert all(len(row) == _leaves(spec.header) for row in spec.rows), spec.key
    return out


PROSE = [
    ("english", "Delivery shall be made DDP to the buyer's central warehouse. The seller bears all costs and risks until the goods are unloaded, including freight, insurance, import duties and customs clearance."),
    ("english", "Payment terms: thirty percent advance against the bank guarantee, sixty percent against shipping documents and the remaining ten percent after final acceptance of the goods."),
    ("english", "The warranty period is thirty-six months from the date of installation or forty-two months from delivery, whichever comes first. Defective units shall be replaced within fifteen days."),
    ("english", "All meters must pass routine tests at the factory, including accuracy at several load points, dielectric strength and starting current, before they are released for shipment."),
    ("english", "The supplier shall keep a safety stock of spare parts in Lima for the duration of the contract and report the inventory level every month."),
    ("english", "Prices are quoted in US dollars and remain fixed for the whole contract period. They include packing and marking according to the technical specifications."),
    ("english", "Installation progress is reported weekly by the contractor, with the number of meters installed at each site and the issues found during commissioning."),
    ("english", "A penalty of 0.5% of the value of the delayed lot applies for each day of delay, up to a maximum of ten percent of the contract value."),
    ("english", "Warranty claims shall be submitted through the customer portal with the serial number, a description of the failure and photographs of the unit."),
    ("english", "The buyer may increase or reduce the quantities by up to twenty-five percent at the same unit prices, giving written notice before the corresponding delivery."),
    ("english", "Lots are numbered consecutively in the order of production. Each lot is accompanied by a test report and a packing list showing the serial numbers it contains."),
    ("english", "Regional distributors receive the products at the wholesale price and may resell them only within their assigned area."),
    ("spanish", "El contratista entregará los bienes en los almacenes de cada empresa distribuidora, de acuerdo con el cronograma aprobado por la entidad."),
    ("spanish", "El pago se realizará en dólares americanos, dentro de los treinta días calendario siguientes a la conformidad de cada entrega parcial."),
    ("spanish", "La garantía comercial cubre defectos de fabricación por un período de treinta y seis meses contados desde la recepción de los bienes."),
    ("spanish", "Los ensayos de laboratorio se realizarán en un laboratorio acreditado por INACAL y comprenden pruebas de exactitud, aislamiento y compatibilidad electromagnética."),
    ("spanish", "Las cantidades asignadas a cada empresa podrán redistribuirse entre zonas previa autorización escrita del área usuaria."),
    ("spanish", "El inventario de materiales se actualiza diariamente en el sistema de gestión y se concilia con los almacenes al cierre de cada mes."),
    ("spanish", "La lectura del consumo mensual se efectúa entre el primer y el quinto día hábil de cada mes; los suministros suspendidos no generan facturación."),
    ("spanish", "Se aplicará una penalidad diaria por cada día de atraso en la entrega, conforme a la fórmula establecida en el Reglamento de la Ley de Contrataciones."),
    ("spanish", "El adelanto directo no podrá exceder del treinta por ciento del monto del contrato y se amortizará en cada pago a cuenta."),
    ("spanish", "Los medidores trifásicos multifunción deberán incluir puerto óptico y módem de comunicación compatible con el sistema de lectura remota."),
    ("spanish", "Las muestras deberán presentarse dentro de los quince días siguientes a la firma del contrato para su evaluación técnica."),
    ("spanish", "La conformidad final será otorgada por el área de mantenimiento luego de verificar la instalación y el funcionamiento de los equipos."),
]
