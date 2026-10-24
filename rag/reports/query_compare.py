"""Query comparison page: `left modules vs right modules` on every combination.

The facts (rag/eval/query_compare.py) are embedded in the page; the page script derives every
metric at K from them, for the module configurations the reader ticks. Both sides empty shows
V3.13; otherwise the left side must be a proper subset of the right side.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag.jsonio import read_json, write_atomic, write_json
from rag.paths import Workspace
from rag.reports.html import esc, page, write_assets


def query_dir(workspace: Workspace) -> Path:
    return workspace.reports / "query"


SCRIPT = r"""
(function(){
var D=JSON.parse(document.getElementById('qc-data').textContent);
var MODS=D.modules.map(function(m){return m.id});
var NAME={};D.modules.forEach(function(m){NAME[m.id]=m.id+' '+m.name});
var S={L:new Set(),R:new Set(),km:3,kc:3,doc:'all',changed:false,open:{}};
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function key(set){return MODS.filter(function(m){return set.has(m)}).join('+')}
function readHash(){
  var h=new URLSearchParams(location.hash.slice(1));
  ['L','R'].forEach(function(side){S[side]=new Set((h.get(side)||'').split('+').filter(function(m){return MODS.indexOf(m)>=0}))});
}
function writeHash(){history.replaceState(null,'','#L='+key(S.L)+'&R='+key(S.R))}
function error(){
  var L=S.L,R=S.R;
  if(!L.size&&!R.size)return null;
  if(!R.size)return '右侧为空时左侧也必须为空';
  var extra=[...L].filter(function(m){return !R.has(m)});
  if(extra.length)return '左侧的 '+extra.join('、')+' 不在右侧';
  if(L.size>=R.size)return '右侧必须比左侧至少多选一个模块';
  return null;
}
function facts(cfg,combo){var c=D.configs[cfg];return c?c[combo]:undefined}
function status(f,k){
  var cov=f.needed.filter(function(x){return x!==null&&x<=k}).length;
  return cov===f.needed.length?'complete':cov?'partial':'miss';
}
function metrics(fs,k){
  var ids=Object.keys(fs),n=ids.length,m={hit:0,complete:0,mrr:0,tokens:0,retrievals:0,ms:0,llm:0};
  ids.forEach(function(id){var f=fs[id],s=status(f,k);
    m.hit+=s!=='miss';m.complete+=s==='complete';
    if(f.first_relevant!==null&&f.first_relevant<=k)m.mrr+=1/f.first_relevant;
    m.tokens+=f.tokens.slice(0,k).reduce(function(a,b){return a+b},0);
    m.retrievals+=f.cost.retrievals;m.ms+=f.cost.retrieval_seconds*1000;m.llm+=f.cost.llm_calls;});
  ['mrr','tokens','retrievals','ms','llm'].forEach(function(x){m[x]=n?m[x]/n:0});
  return m;
}
function moves(lf,rf,k){
  var better=0,worse=0;
  Object.keys(rf).forEach(function(id){
    var a=status(lf[id],k)==='complete',b=status(rf[id],k)==='complete';
    better+=!a&&b;worse+=a&&!b;});
  return [better,worse];
}
var FMT={hit:function(v){return v.toFixed(0)},complete:function(v){return v.toFixed(0)},
  mrr:function(v){return (v*100).toFixed(1)+'%'},tokens:function(v){return v.toFixed(0)},
  retrievals:function(v){return v.toFixed(1)},ms:function(v){return v.toFixed(0)},llm:function(v){return v.toFixed(1)}};
var LOWER={tokens:1,retrievals:1,ms:1,llm:1};   // smaller is better
function cell(kind,l,r){
  var f=FMT[kind];
  if(l===undefined)return '<td class="num">'+f(r)+'</td>';
  var d=r-l,cls=Math.abs(d)<1e-9?'same':((d>0)!==!!LOWER[kind]?'up':'down');
  var ds=Math.abs(d)<1e-9?'±0':(d>0?'+':'−')+f(Math.abs(d));
  return '<td class="num"><div class="meta">'+f(l)+' → '+f(r)+'</div><div class="'+cls+'">'+ds+'</div></td>';
}
function ingest(c){
  var x=c.ingest_cost;
  if(!x)return '入库未调用 LLM';
  return '入库 LLM 表格处理：'+x.tables+' 张表、'+x.calls+' 次调用、$'+x.cost_usd+'、'+x.seconds+' 秒';
}
function comboHead(c){return '<td class="nw" title="'+esc(c.config+' · '+(c.table_return_unit==='table'?'返回整表':'返回命中 chunk')+' · '+ingest(c))+'"><b>'+esc(c.label)+'</b></td>'}
function renderPicker(){
  ['L','R'].forEach(function(side){
    document.querySelectorAll('[data-side="'+side+'"] input').forEach(function(box){box.checked=S[side].has(box.value)});
  });
  var e=error(),msg=document.getElementById('qc-msg'),lk=key(S.L),rk=key(S.R);
  var label=function(set){return set.size?[...set].sort(function(a,b){return MODS.indexOf(a)-MODS.indexOf(b)}).join(' + '):'V3.13（无模块）'};
  if(e){msg.innerHTML='<span class="badge bad">标签不合法</span> '+esc(e);return 'bad'}
  var missing=[];
  if(!D.configs[lk])missing.push('左侧 '+label(S.L));
  if(!D.configs[rk]&&rk!==lk)missing.push('右侧 '+label(S.R));
  if(missing.length){msg.innerHTML='<span class="badge warn">尚未评估</span> '+esc(missing.join('；'));return 'missing'}
  msg.innerHTML=(!S.L.size&&!S.R.size)?'<span class="badge">V3.13 基线</span> 两侧全空，每格只显示 V3.13 的数值'
    :'<span class="badge ok">比较</span> '+esc(label(S.L))+' <b>vs</b> '+esc(label(S.R))+'：每格依次为左侧 → 右侧与变化';
  return 'ok';
}
function renderMatrix(base){
  var k=S.km,lk=key(S.L),rk=key(S.R);
  var cols=[['hit','命中题数'],['complete','完整覆盖题数'],['mrr','MRR'],['tokens','每题返回 token']];
  var cost=[['retrievals','每题检索次数'],['ms','每题检索耗时（ms）'],['llm','每题 LLM 调用']];
  var head='<tr><th>组合</th>'+cols.map(function(c){return '<th class="num">'+c[1]+'</th>'}).join('')
    +(base?'':'<th class="num">变好</th><th class="num">变差</th>')+cost.map(function(c){return '<th class="num">'+c[1]+'</th>'}).join('')+'</tr>';
  var rows=D.combos.map(function(c){
    var rf=facts(rk,c.label),r=metrics(rf,k),l=base?undefined:metrics(facts(lk,c.label),k);
    var mv=base?null:moves(facts(lk,c.label),rf,k);
    return '<tr>'+comboHead(c)+cols.map(function(x){return cell(x[0],l&&l[x[0]],r[x[0]])}).join('')
      +(base?'':'<td class="num up">'+mv[0]+'</td><td class="num down">'+mv[1]+'</td>')
      +cost.map(function(x){return cell(x[0],l&&l[x[0]],r[x[0]])}).join('')+'</tr>';
  }).join('');
  return '<table>'+head+rows+'</table>';
}
function renderCurve(base){
  var lk=key(S.L),rk=key(S.R);
  var head='<tr><th>组合</th>'+D.ks.map(function(k){return '<th class="num">K='+k+'</th>'}).join('')+'</tr>';
  var rows=D.combos.map(function(c){
    return '<tr>'+comboHead(c)+D.ks.map(function(k){
      var r=metrics(facts(rk,c.label),k).complete;
      return cell('complete',base?undefined:metrics(facts(lk,c.label),k).complete,r)}).join('')+'</tr>';
  }).join('');
  return '<table>'+head+rows+'</table>';
}
var MARK={complete:['✓','ok'],partial:['◐','warn'],miss:['✗','bad']};
function mark(s){return '<span class="badge '+MARK[s][1]+'">'+MARK[s][0]+'</span>'}
function units(combo,f,k){
  return f.units.slice(0,k).map(function(u,i){
    var info=(D.units[combo]||{})[u]||{};
    return '<span class="badge'+(f.relevant[i]?' ok':'')+'" title="'+esc((info.table?'整表 · ':'')+'第 '+(info.pages||[]).join(',')+' 页\n'+(info.text||''))+'">#'+(i+1)+' '+esc(u.split('-').pop())+'</span>';
  }).join(' ');
}
function renderCases(base){
  var k=S.kc,lk=key(S.L),rk=key(S.R),out=[],doc=null;
  var head='<tr><th>题目</th>'+D.combos.map(function(c){return '<th class="nw">'+esc(c.label)+'</th>'}).join('')+'</tr>';
  D.cases.forEach(function(q){
    if(S.doc!=='all'&&q.document_id!==S.doc)return;
    var changed=false;
    var cells=D.combos.map(function(c){
      var r=status(facts(rk,c.label)[q.case_id],k);
      if(base)return '<td class="nw">'+mark(r)+'</td>';
      var l=status(facts(lk,c.label)[q.case_id],k),flip=(l==='complete')!==(r==='complete');
      changed=changed||flip;
      return '<td class="nw">'+mark(l)+' → '+mark(r)+(flip?(r==='complete'?' <span class="up">↑</span>':' <span class="down">↓</span>'):'')+'</td>';
    });
    if(S.changed&&!changed)return;
    if(q.document_id!==doc){doc=q.document_id;out.push('<tr><td colspan="'+(D.combos.length+1)+'"><b>'+esc(q.document_name)+'</b></td></tr>')}
    out.push('<tr><td><button type="button" class="toggle" data-case="'+esc(q.case_id)+'">'+(S.open[q.case_id]?'▾ ':'▸ ')+esc(q.case_id)+'</button>'
      +'<div class="meta">'+esc(q.question)+'</div></td>'+cells.join('')+'</tr>');
    if(S.open[q.case_id])out.push('<tr><td colspan="'+(D.combos.length+1)+'">'+caseDetail(q,base,k)+'</td></tr>');
  });
  return out.length?'<table>'+head+out.join('')+'</table>':'<div class="meta">没有符合筛选的题</div>';
}
function caseDetail(q,base,k){
  var lk=key(S.L),rk=key(S.R);
  var groups=q.groups.map(function(g){return esc(g.group_id)+' '+esc(g.information_item)}).join('；');
  var rows=D.combos.map(function(c){
    var rf=facts(rk,c.label)[q.case_id],lf=base?null:facts(lk,c.label)[q.case_id];
    var need=function(f){return f.needed.map(function(x){return x===null?'无':x}).join(' / ')};
    return '<tr>'+comboHead(c)+(base?'':'<td class="nw">'+need(lf)+'</td><td>'+units(c.label,lf,k)+'</td>')
      +'<td class="nw">'+need(rf)+'</td><td>'+units(c.label,rf,k)+'</td></tr>';
  }).join('');
  var side=function(t){return '<th class="nw">'+t+' 所需单元数</th><th>'+t+' 前 '+k+' 个返回单元</th>'};
  return '<div class="meta">证据组：'+groups+'。所需单元数按证据组顺序列出（无＝返回顺序中无法补全）；绿色为含证据的单元，悬停看原文。</div>'
    +'<div class="scroll"><table><tr><th>组合</th>'+(base?side('V3.13'):side('左侧')+side('右侧'))+'</tr>'+rows+'</table></div>';
}
function render(){
  writeHash();
  var state=renderPicker(),base=!S.L.size&&!S.R.size;
  document.querySelectorAll('[data-module]').forEach(function(sec){
    var body=sec.querySelector('.qc-body');
    if(state!=='ok'){body.innerHTML='<div class="meta">无结果</div>';return}
    var kind=sec.dataset.module;
    body.innerHTML=kind==='matrix'?renderMatrix(base):kind==='curve'?renderCurve(base):renderCases(base);
  });
  document.querySelectorAll('.seg[data-k]').forEach(function(bar){
    var v=bar.dataset.k==='km'?S.km:S.kc;
    bar.querySelectorAll('button').forEach(function(b){b.classList.toggle('on',+b.dataset.v===v)});
  });
}
document.querySelectorAll('[data-side] input').forEach(function(box){
  box.addEventListener('change',function(){var set=S[box.closest('[data-side]').dataset.side];
    box.checked?set.add(box.value):set.delete(box.value);render();});
});
document.addEventListener('click',function(e){
  var b=e.target.closest('.seg[data-k] button');
  if(b){S[b.closest('.seg').dataset.k]=+b.dataset.v;render();return}
  var t=e.target.closest('button[data-case]');
  if(t){S.open[t.dataset.case]=!S.open[t.dataset.case];render();return}
  var d=e.target.closest('.seg[data-doc] button');
  if(d){S.doc=d.dataset.v;d.parentNode.querySelectorAll('button').forEach(function(x){x.classList.toggle('on',x===d)});render();return}
  var c=e.target.closest('#qc-changed');
  if(c){S.changed=c.checked;render()}
});
document.getElementById('qc-reset').addEventListener('click',function(){S.L=new Set();S.R=new Set();render()});
window.addEventListener('hashchange',function(){readHash();render()});
readHash();render();
})();
"""


def render(data: dict[str, Any]) -> str:
    modules = data["modules"]
    boxes = lambda side: "".join(
        f'<label class="nw"><input type="checkbox" value="{esc(m["id"])}"> {esc(m["id"])} {esc(m["name"])}</label>'
        for m in modules)
    docs = {c["document_id"]: c["document_name"] for c in data["cases"]}
    doc_bar = ('<div class="seg" data-doc>文档 <button type="button" data-v="all" class="on">全部</button>'
               + "".join(f'<button type="button" data-v="{esc(d)}">{esc(n)}</button>' for d, n in docs.items())
               + "</div>")
    evaluated = [k or "（无模块）" for k in data["configs"]]
    k_bar = lambda name: (f'<div class="seg" data-k="{name}">K = '
                          + "".join(f'<button type="button" data-v="{k}">{k}</button>' for k in data["ks"])
                          + "</div>")
    body = (
        f'<h1>查询侧对比 · {esc(data["name"])}</h1>'
        f'<div class="sub">{esc(data["created_at"])} · 数据集 {esc(data["dataset"]["version"])}，'
        f'{data["dataset"]["answerable"]} 道可回答题 · {len(data["combos"])} 种组合（混合检索、文档过滤开启）'
        f' · 已评估的模块配置：{esc("、".join(evaluated))}</div>'
        f'<div class="card pick"><div class="pick-side" data-side="L"><b>左侧</b>{boxes("L")}</div>'
        f'<div class="pick-vs">vs</div><div class="pick-side" data-side="R"><b>右侧</b>{boxes("R")}</div>'
        f'<div class="pick-foot"><span id="qc-msg"></span> <button type="button" id="qc-reset" class="expand-btn">清空两侧</button></div>'
        f'<div class="meta">两侧全空显示 V3.13；否则左侧必须是右侧的真子集，变化即右侧多出的模块带来的效果。'
        f'每种组合只与它自己比较。</div></div>'
        f'<section data-module="matrix"><h2>整体指标矩阵</h2>{k_bar("km")}'
        f'<div class="meta">命中：至少一个证据组补全；完整覆盖：全部证据组补全；变好 / 变差：左侧未完整覆盖、右侧完整覆盖的题数 / 反之。'
        f'MRR 与查询成本按 {data["dataset"]["answerable"]} 道可回答题平均。悬停组合名看入库成本（左右两侧相同）。</div>'
        f'<div class="scroll" data-grid><div class="qc-body"></div></div></section>'
        f'<section data-module="curve"><h2>K–完整覆盖曲线</h2>'
        f'<div class="meta">每格为该 K 下完整覆盖的题数。</div>'
        f'<div class="scroll" data-grid><div class="qc-body"></div></div></section>'
        f'<section data-module="cases"><h2>逐题变化</h2>{k_bar("kc")}{doc_bar}'
        f'<label class="meta"><input type="checkbox" id="qc-changed"> 只看完整覆盖有变化的题</label>'
        f'<div class="meta">✓ 完整覆盖、◐ 部分覆盖、✗ 未命中；↑ / ↓ 为完整覆盖由否变是 / 由是变否。点题号展开各组合的所需单元数与返回单元。</div>'
        f'<div class="scroll" data-grid><div class="qc-body"></div></div></section>'
        f'<h2>查询过程</h2><div class="card meta">右侧配置下逐题的子问题、各写法、每个返回单元的来源与分解对照，第 4 步填入。</div>'
        f'<h2>扰动标注</h2><div class="card meta">变化落在 3 次独立生成扰动范围内的单元格标灰，第 4 步填入。</div>'
        f'<script type="application/json" id="qc-data">{_embed(data)}</script>'
        f'<script>{SCRIPT}</script>')
    return page(f"查询侧对比 · {data['name']}", body, root="../../", crumbs=[("报告首页", "../../index.html")])


def _embed(data: dict[str, Any]) -> str:
    """JSON safe inside a <script> element."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def write_query_page(workspace: Workspace, data: dict[str, Any]) -> Path:
    folder = query_dir(workspace) / data["name"]
    write_json(folder / "data.json", data)
    write_assets(workspace.reports)
    write_atomic(folder / "index.html", render(data))
    return folder


def rerender_query_pages(workspace: Workspace) -> int:
    folder = query_dir(workspace)
    count = 0
    for path in sorted(folder.glob("*/data.json")) if folder.is_dir() else []:
        write_atomic(path.parent / "index.html", render(read_json(path)))
        count += 1
    return count
