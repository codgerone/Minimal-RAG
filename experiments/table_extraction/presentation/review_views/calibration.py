from __future__ import annotations
from pathlib import Path
from typing import Any
import json
import math
from experiments.table_extraction.domain.calibration import Box, CandidateGeometry, SlotLabel, CandidateLabel, CoverageMetrics, CalibrationRow, coverage_metrics, calculate_rows
from experiments.table_extraction.domain.admission_config import MIN_CANDIDATE_COVERAGE, MIN_SLOT_COVERAGE
def _chart_payload(rows: list[CalibrationRow]) -> str:
    """把校准结果序列化为图表使用的紧凑 JSON。"""
    payload = [
        {
            "sequence": row.label.sequence,
            "pdf": row.label.pdf_name,
            "group": row.label.review_group_id,
            "candidate": row.label.candidate_id,
            "label": row.label.admission_label,
            "slot": row.comparison_slot_id,
            "candidateCoverage": row.metrics.candidate_coverage if row.metrics else None,
            "slotCoverage": row.metrics.slot_coverage if row.metrics else None,
            "iou": row.metrics.iou if row.metrics else None,
        }
        for row in rows
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

def render_chart(rows: list[CalibrationRow]) -> str:
    """生成无需外部依赖的交互式双向覆盖率散点图。"""
    data = _chart_payload(rows)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>表格候选双向覆盖率校准图</title>
<style>
body{{font-family:Arial,"Microsoft YaHei",sans-serif;margin:24px;color:#1f2937;background:#f8fafc}}
h1{{margin:0 0 6px}} .hint{{color:#475569;margin:0 0 18px}}
.controls{{display:flex;gap:18px;align-items:center;flex-wrap:wrap;background:white;padding:12px 16px;border:1px solid #dbe3ec;border-radius:8px}}
.metric-note{{margin:10px 2px 0;color:#475569;line-height:1.6;font-size:13px}}
label{{font-weight:600}} input[type=number]{{width:76px;padding:5px}}
.summary{{font-weight:700}} .layout{{display:grid;grid-template-columns:minmax(680px,1fr) 360px;gap:18px;margin-top:18px}}
.card{{background:white;border:1px solid #dbe3ec;border-radius:8px;padding:14px}}
svg{{width:100%;height:auto}} .axis{{stroke:#475569;stroke-width:1}} .grid{{stroke:#dbe3ec;stroke-width:1}}
.threshold{{stroke:#7c3aed;stroke-width:2;stroke-dasharray:7 5}} .point{{cursor:pointer;stroke:white;stroke-width:1.5;opacity:.86}}
.point:hover,.point.active{{stroke:#111827;stroke-width:3;opacity:1}} .admit{{fill:#15803d}} .reject{{fill:#dc2626}}
.point-label{{font-size:8px;fill:#111827;pointer-events:none}} #detail{{white-space:pre-wrap;line-height:1.55;min-height:190px}}
#search{{box-sizing:border-box;width:100%;padding:8px;margin-bottom:10px}} table{{border-collapse:collapse;width:100%;font-size:12px}}
th,td{{border-bottom:1px solid #e2e8f0;padding:6px;text-align:left}} tbody tr{{cursor:pointer}} tbody tr:hover{{background:#f1f5f9}}
.scroll{{max-height:420px;overflow:auto}} .legend span{{margin-right:16px}} .dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}}
.no-slot{{margin-top:18px}} .no-slot-list{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:6px 18px;font-size:12px}}
@media(max-width:1050px){{.layout{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<h1>表格候选双向覆盖率校准图</h1>
<p class="hint">横轴为 candidate_coverage，纵轴为 slot_coverage。点上的数字对应人工标注表序号；悬停或点击查看候选详情。</p>
<div class="controls">
  <label>candidate 阈值 <input id="tc" type="number" min="0" max="1" step="0.01" value="{MIN_CANDIDATE_COVERAGE:.2f}"></label>
  <label>slot 阈值 <input id="ts" type="number" min="0" max="1" step="0.01" value="{MIN_SLOT_COVERAGE:.2f}"></label>
  <span class="legend"><span><i class="dot" style="background:#15803d"></i>admit</span><span><i class="dot" style="background:#dc2626"></i>reject</span></span>
  <span id="summary" class="summary"></span>
</div>
<p class="metric-note">TP：人工标注为 admit，且阈值判断为准入；FP：人工标注为 reject，但阈值错误准入；FN：人工标注为 admit，但阈值错误拒绝；TN：人工标注为 reject，且阈值判断为拒绝。覆盖率为 N/A 的候选没有同页 Docling Table Slot，不参与这四项统计。</p>
<div class="layout">
  <div class="card"><svg id="plot" viewBox="0 0 820 650" role="img" aria-label="双向覆盖率散点图"></svg></div>
  <div class="card">
    <input id="search" placeholder="搜索序号、PDF 或 candidate_id">
    <div id="detail">点击图中的点或下方候选行查看详情。</div>
    <div class="scroll"><table><thead><tr><th>#</th><th>标签</th><th>candidate_id</th></tr></thead><tbody id="rows"></tbody></table></div>
  </div>
</div>
<div class="card no-slot"><h2>无同页 Docling slot 的候选（<span id="noSlotCount"></span>）</h2><div id="noSlotList" class="no-slot-list"></div></div>
<script>
const data={data};
const comparable=data.filter(d=>d.candidateCoverage!==null);
const noSlot=data.filter(d=>d.candidateCoverage===null);
const svg=document.getElementById('plot'), NS='http://www.w3.org/2000/svg';
const box={{left:70,top:30,width:700,height:540}}, sx=x=>box.left+x*box.width, sy=y=>box.top+(1-y)*box.height;
const add=(tag,attrs,parent=svg)=>{{const e=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));parent.appendChild(e);return e}};
for(let i=0;i<=10;i++){{const v=i/10;add('line',{{x1:sx(v),y1:box.top,x2:sx(v),y2:box.top+box.height,class:'grid'}});add('line',{{x1:box.left,y1:sy(v),x2:box.left+box.width,y2:sy(v),class:'grid'}});let tx=add('text',{{x:sx(v),y:box.top+box.height+22,'text-anchor':'middle','font-size':'11'}});tx.textContent=v.toFixed(1);let ty=add('text',{{x:box.left-12,y:sy(v)+4,'text-anchor':'end','font-size':'11'}});ty.textContent=v.toFixed(1)}}
add('line',{{x1:box.left,y1:box.top+box.height,x2:box.left+box.width,y2:box.top+box.height,class:'axis'}});add('line',{{x1:box.left,y1:box.top,x2:box.left,y2:box.top+box.height,class:'axis'}});
let xl=add('text',{{x:box.left+box.width/2,y:630,'text-anchor':'middle','font-size':'14','font-weight':'bold'}});xl.textContent='candidate_coverage';let yl=add('text',{{x:18,y:box.top+box.height/2,transform:`rotate(-90 18 ${{box.top+box.height/2}})`,'text-anchor':'middle','font-size':'14','font-weight':'bold'}});yl.textContent='slot_coverage';
const vline=add('line',{{class:'threshold'}}), hline=add('line',{{class:'threshold'}});
const detail=document.getElementById('detail');
function show(d){{detail.textContent=`序号：${{d.sequence}}\nPDF：${{d.pdf}}\n旧审核组：${{d.group}}\ncandidate：${{d.candidate}}\n人工标签：${{d.label}}\n比较/目标 slot：${{d.slot}}\ncandidate_coverage：${{d.candidateCoverage===null?'N/A':d.candidateCoverage.toFixed(6)}}\nslot_coverage：${{d.slotCoverage===null?'N/A':d.slotCoverage.toFixed(6)}}\nIoU：${{d.iou===null?'N/A':d.iou.toFixed(6)}}`;document.querySelectorAll('.point').forEach(p=>p.classList.toggle('active',Number(p.dataset.seq)===d.sequence))}}
for(const d of comparable){{const g=add('g',{{}});const c=add('circle',{{cx:sx(d.candidateCoverage),cy:sy(d.slotCoverage),r:7,class:`point ${{d.label}}`,'data-seq':d.sequence}},g);let title=add('title',{{}},c);title.textContent=`#${{d.sequence}} ${{d.candidate}} | ${{d.label}} | C=${{d.candidateCoverage.toFixed(4)}} S=${{d.slotCoverage.toFixed(4)}}`;const t=add('text',{{x:sx(d.candidateCoverage),y:sy(d.slotCoverage)+3,'text-anchor':'middle',class:'point-label'}},g);t.textContent=d.sequence;c.addEventListener('click',()=>show(d));g.addEventListener('mouseenter',()=>show(d))}}
function update(){{const tc=Number(document.getElementById('tc').value),ts=Number(document.getElementById('ts').value);Object.assign(vline,{{}});vline.setAttribute('x1',sx(tc));vline.setAttribute('x2',sx(tc));vline.setAttribute('y1',box.top);vline.setAttribute('y2',box.top+box.height);hline.setAttribute('x1',box.left);hline.setAttribute('x2',box.left+box.width);hline.setAttribute('y1',sy(ts));hline.setAttribute('y2',sy(ts));let tp=0,fp=0,fn=0,tn=0;for(const d of comparable){{const pass=d.candidateCoverage>=tc&&d.slotCoverage>=ts;if(d.label==='admit'&&pass)tp++;else if(d.label==='admit')fn++;else if(pass)fp++;else tn++}}document.getElementById('summary').textContent=`可比较：${{comparable.length}} | TP ${{tp}} / FP ${{fp}} / FN ${{fn}} / TN ${{tn}} | 无同页 slot：${{noSlot.length}}`}}
document.getElementById('tc').addEventListener('input',update);document.getElementById('ts').addEventListener('input',update);update();
const tbody=document.getElementById('rows');function renderRows(q=''){{tbody.innerHTML='';for(const d of data.filter(d=>`${{d.sequence}} ${{d.pdf}} ${{d.candidate}}`.toLowerCase().includes(q.toLowerCase()))){{const tr=document.createElement('tr');tr.innerHTML=`<td>${{d.sequence}}</td><td>${{d.label}}</td><td>${{d.candidate}}</td>`;tr.addEventListener('click',()=>show(d));tbody.appendChild(tr)}}}}renderRows();document.getElementById('search').addEventListener('input',e=>renderRows(e.target.value));
document.getElementById('noSlotCount').textContent=noSlot.length;const noSlotList=document.getElementById('noSlotList');for(const d of noSlot){{const item=document.createElement('div');item.textContent=`#${{d.sequence}} · ${{d.pdf}} · ${{d.candidate}}`;item.addEventListener('click',()=>show(d));noSlotList.appendChild(item)}}
</script>
</body></html>"""

