"""Query comparison page: `left modules vs right modules` on every combination.

The facts (rag/eval/query_compare.py) are embedded in the page; the page script derives every
figure from them, for the module configurations the reader ticks. Both sides empty shows V3.13;
otherwise the left side must be a proper subset of the right side. Modules (docs/changes/v3.14.md):
better / worse questions (completion vectors by dominance, independent of K), a heat map of the
per-question verdict, the matrix and the curves at K, the query process of the right side, and the
P3.1 layers (retrieval layer, ideal bound, merged) against V3.13.
Where each returned unit came from is kept per configuration in process/<config>.js and loaded
when the query process is shown.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag.jsonio import read_json, write_atomic, write_json
from rag.paths import Workspace
from rag.reports.html import esc, page, write_assets

ACCEPTANCE_KS = (3, 6, 9, 12)


def query_dir(workspace: Workspace) -> Path:
    return workspace.reports / "query"


def process_file(key: str) -> str:
    """process/<file>.js of a configuration key ('' is V3.13)."""
    return (key.replace("+", "_") or "none") + ".js"


SCRIPT = r"""
(function(){
var D=JSON.parse(document.getElementById('qc-data').textContent);
var MODS=D.modules.map(function(m){return m.id});
var KIND={'P3.2.1':'rewrite','P3.2.2':'translation','P3.2.3':'hyde','P3.2.4':'step_back'};
var KNAME={question:'原句',rewrite:'改写',translation:'翻译',hyde:'HyDE',step_back:'step-back'};
var N=D.cases.length,ACC=__ACC__;
var GROUPS=D.cases.reduce(function(a,q){return a+q.groups.length},0);
var CASE={};D.cases.forEach(function(q){CASE[q.case_id]=q});
var VN={better:'变好',worse:'变差',mixed:'各有优势',same:'不变'},VS={better:'▲',worse:'▼',mixed:'◆',same:'·'},
    VC={better:'good',worse:'badc',mixed:'mix',same:'flat'},VB={better:'ok',worse:'bad',mixed:'warn',same:''};
var ORDER=['better','worse','mixed','same'];
var S={L:new Set(),R:new Set(),km:3,doc:'all',all:false,focus:null,cell:null,flip:null,lcell:null,lall:false,
  pq:D.cases[0].case_id,pc:D.combos[0].label};
window.QC_PROCESS=window.QC_PROCESS||{};
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function key(set){return MODS.filter(function(m){return set.has(m)}).join('+')}
function label(set){return set.size?MODS.filter(function(m){return set.has(m)}).join(' + '):'V3.13（无模块）'}
function readHash(){
  var h=new URLSearchParams(location.hash.slice(1));
  ['L','R'].forEach(function(side){S[side]=new Set((h.get(side)||'').split(/[+ ]/).filter(function(m){return MODS.indexOf(m)>=0}))});
  var k=+h.get('k');if(D.ks.indexOf(k)>=0)S.km=k;
  var cell=(h.get('cell')||'').split('@');
  if(CASE[cell[0]]&&D.combos.some(function(c){return c.label===cell[1]})){S.cell={q:cell[0],c:cell[1]};S.pq=cell[0];S.pc=cell[1]}
  var lc=(h.get('layer')||'').split('@');
  if(CASE[lc[0]]&&D.combos.some(function(c){return c.label===lc[1]}))S.lcell={q:lc[0],c:lc[1]};
}
function writeHash(){history.replaceState(null,'','#L='+key(S.L)+'&R='+key(S.R)+'&k='+S.km+(S.cell?'&cell='+S.cell.q+'@'+S.cell.c:'')+(S.lcell?'&layer='+S.lcell.q+'@'+S.lcell.c:''))}
function error(){
  var L=S.L,R=S.R;
  if(!L.size&&!R.size)return null;
  if(!R.size)return '右侧为空时左侧也必须为空';
  var extra=[...L].filter(function(m){return !R.has(m)});
  if(extra.length)return '左侧的 '+extra.join('、')+' 不在右侧';
  if(L.size>=R.size)return '右侧必须比左侧至少多选一个模块';
  return null;
}
function facts(cfg,combo){return D.configs[cfg][combo]}
function complete(f,k){return f.n.every(function(x){return x!==null&&x<=k})}
function hit(f,k){return f.n.some(function(x){return x!==null&&x<=k})}
/* the rule (proposal v3.14, question 3): completion vectors compared place by place */
function vec(n){return n.map(function(x){return x===null?Infinity:x}).sort(function(a,b){return a-b})}
function verdict(l,r){var e=false,t=false;for(var i=0;i<l.length;i++){if(r[i]<l[i])e=true;if(r[i]>l[i])t=true}
  return e&&t?'mixed':e?'better':t?'worse':'same'}
function leads(l,r){
  var fin=l.concat(r).filter(function(x){return x!==Infinity}),top=fin.length?Math.max.apply(null,fin):1,out={left:[],right:[]};
  for(var k=1;k<=top;k++){
    var a=l.filter(function(x){return x<=k}).length,b=r.filter(function(x){return x<=k}).length,side=a>b?'left':b>a?'right':null;
    if(!side)continue;var rs=out[side];
    if(rs.length&&rs[rs.length-1][1]===k-1)rs[rs.length-1][1]=k;else rs.push([k,k]);
  }
  return out;
}
function rangeText(rs){return rs.map(function(x){return x[0]===x[1]?'K='+x[0]:'K='+x[0]+'–'+x[1]}).join('、')}
function leadText(l,r){var x=leads(l,r),p=[];if(x.right.length)p.push('右侧领先 '+rangeText(x.right));if(x.left.length)p.push('左侧领先 '+rangeText(x.left));return p.join('；')}
function rk(x){return x===null||x===Infinity?'∞':x}
function vecHtml(l,r){   // the right vector, each place coloured against the left one
  return l.map(rk).join('·')+' → '+r.map(function(x,i){var c=x<l[i]?'up':x>l[i]?'down':'';return '<span class="'+c+'">'+rk(x)+'</span>'}).join('·');
}
function arrow(a,b){var c=b===a?'same':(b===null?Infinity:b)<(a===null?Infinity:a)?'up':'down';return rk(a)+' → <span class="'+c+'">'+rk(b)+'</span>'}
function judge(lk,rk_,combo){var lf=facts(lk,combo),rf=facts(rk_,combo),out={};
  Object.keys(rf).forEach(function(id){out[id]=verdict(vec(lf[id].n),vec(rf[id].n))});return out}
function regress(lf,rf){return ACC.filter(function(k){return complete(lf,k)&&!complete(rf,k)})}
function fmt(v,d){return v.toFixed(d)}
function sign(v,d){return Math.abs(v)<1e-9?'±0':(v>0?'+':'−')+Math.abs(v).toFixed(d)}
function ingest(c){
  var x=c.ingest_cost;
  if(!x)return '入库未调用 LLM';
  return '入库 LLM 表格处理：'+x.tables+' 张表、'+x.calls+' 次调用、$'+x.cost_usd+'、'+x.seconds+' 秒';
}
function comboHead(c){return '<td class="nw" title="'+esc(c.config+' · '+(c.table_return_unit==='table'?'返回整表':'返回命中 chunk')+' · '+ingest(c))+'"><b>'+esc(c.label)+'</b></td>'}
function short(k){return k.split('-').pop()}
function cases(){return D.cases.filter(function(q){return S.doc==='all'||q.document_id===S.doc})}
function counts(vs){var c={better:0,worse:0,mixed:0,same:0};vs.forEach(function(v){c[v]++});return c}
function countText(c){return ORDER.map(function(v){return VN[v]+' '+c[v]}).join(' · ')}

/* ---------- picker ---------- */
function renderPicker(){
  ['L','R'].forEach(function(side){
    document.querySelectorAll('[data-side="'+side+'"] input').forEach(function(box){box.checked=S[side].has(box.value)});
  });
  var e=error(),msg=document.getElementById('qc-msg'),lk=key(S.L),rk_=key(S.R);
  var card=document.querySelector('.pick');card.classList.remove('bad','warn');
  if(e){card.classList.add('bad');msg.innerHTML='<span class="badge bad">配置不合法</span> '+esc(e);return 'bad'}
  var missing=[];
  if(!D.configs[lk])missing.push('左侧 '+label(S.L));
  if(!D.configs[rk_]&&rk_!==lk)missing.push('右侧 '+label(S.R));
  if(missing.length){card.classList.add('warn');msg.innerHTML='<span class="badge warn">未评估</span> '+esc(missing.join('；'))+'（P3.3 重排已搁置，含它的配置未评估）';return 'missing'}
  msg.innerHTML=(!S.L.size&&!S.R.size)?'<span class="badge">V3.13 基线</span>'
    :'<span class="badge ok">比较</span> '+esc(label(S.L))+' <b>vs</b> '+esc(label(S.R));
  return 'ok';
}

/* ---------- module 1: better / worse ---------- */
function renderClass(base){
  if(base)return '<div class="meta">两侧全空时为 V3.13 基线，没有比较。在右侧勾选模块，即为相对 V3.13 的效果；左侧再勾选右侧的一部分，即为右侧多出的模块的边际效果。</div>';
  var lk=key(S.L),rk_=key(S.R),all=[],T=[],C=[],worst={};
  var body=D.combos.map(function(c){
    var j=judge(lk,rk_,c.label),ids=Object.keys(j),n=counts(ids.map(function(id){return j[id]}));
    var lf=facts(lk,c.label),rf=facts(rk_,c.label);
    var reg=ids.filter(function(id){return regress(lf[id],rf[id]).length});
    reg.forEach(function(id){worst[id]=worst[id]||{n:0,c:c.label};worst[id].n++});
    ids.forEach(function(id){all.push(j[id]);(c.table_return_unit==='table'?T:C).push(j[id])});
    var btn=function(v){return n[v]&&v!=='same'?'<button type="button" class="lnk '+(v==='better'?'up':v==='worse'?'down':'')+'" data-act="focus" data-combo="'+c.label+'" data-v="'+v+'">'+n[v]+'</button>':''+n[v]};
    return '<tr>'+comboHead(c)+ORDER.map(function(v){return '<td class="num">'+btn(v)+'</td>'}).join('')
      +'<td class="num">'+(reg.length?'<button type="button" class="lnk down" data-act="focus" data-combo="'+c.label+'" data-v="regress">'+reg.length+'</button>':'0')+'</td></tr>';
  }).join('');
  var head='<tr><th>组合</th><th class="num">变好 ▲</th><th class="num">变差 ▼</th><th class="num">各有优势 ◆</th><th class="num">不变</th><th class="num" title="验收 K（'+ACC.join('、')+'）中任一档由完整覆盖变为不完整的题">最坏退化</th></tr>';
  var wl=Object.keys(worst).sort();
  return '<div class="card"><b>13 种组合合计（题次）：</b>'+countText(counts(all))
    +'<div class="meta">返回整表（7 种）：'+countText(counts(T))+'。返回命中 chunk（6 种）：'+countText(counts(C))+'。</div>'
    +'<div class="meta">最坏退化的题：'+(wl.length?wl.map(function(id){return '<button type="button" class="lnk down" data-act="goto" data-case="'+esc(id)+'" data-combo="'+worst[id].c+'">'+esc(id)+'</button>（'+worst[id].n+' 种组合）'}).join('，'):'无')+'</div>'
    +'<div class="meta">13 种组合共用同一批题，组合数只表示一致程度，不作投票；是否采用由助手按拍板问题 5 写明理由、你拍板（决策 015）。判定在冻结的规划（第 0 次生成）上得出，待扰动测试核对。</div></div>'
    +'<table>'+head+body+'</table>';
}

/* ---------- module 2: heat map ---------- */
function renderHeat(base){
  if(base)return '<div class="meta">两侧全空时没有比较。</div>';
  var lk=key(S.L),rk_=key(S.R),out=[],doc=null,dirs={};
  D.combos.forEach(function(c){dirs[c.label]=judge(lk,rk_,c.label)});
  var head='<tr><th>题目</th>'+D.combos.map(function(c){return '<th class="num">'+esc(c.label)+'</th>'}).join('')+'</tr>';
  var shown=0;
  cases().forEach(function(q){
    var id=q.case_id,any=D.combos.some(function(c){return dirs[c.label][id]!=='same'});
    var picked=S.cell&&S.cell.q===id;
    if(!picked){
      if(S.focus){var f=S.focus;
        if(f.v==='regress'){if(!regress(facts(lk,f.combo)[id],facts(rk_,f.combo)[id]).length)return}
        else if(dirs[f.combo][id]!==f.v)return}
      else if(!S.all&&!any)return;
    }
    shown++;
    if(q.document_id!==doc){doc=q.document_id;out.push('<tr><td colspan="'+(D.combos.length+1)+'"><b>'+esc(q.document_name)+'</b></td></tr>')}
    var cells=D.combos.map(function(c){
      var v=dirs[c.label][id],sel=S.cell&&S.cell.q===id&&S.cell.c===c.label?' sel':'';
      var l=vec(facts(lk,c.label)[id].n),r=vec(facts(rk_,c.label)[id].n);
      return '<td class="hm center '+VC[v]+sel+'" title="'+esc(VN[v]+'：'+l.map(rk).join('·')+' → '+r.map(rk).join('·'))+'" data-act="cell" data-case="'+esc(id)+'" data-combo="'+c.label+'">'+VS[v]+'</td>';
    }).join('');
    out.push('<tr><td><b>'+esc(id)+'</b><div class="meta">'+esc(q.question)+'</div></td>'+cells+'</tr>');
    if(picked)out.push('<tr><td colspan="'+(D.combos.length+1)+'">'+cellDetail(id,S.cell.c,lk,rk_)+'</td></tr>');
  });
  var bar=S.focus?'<div class="card meta">只显示 <b>'+esc(S.focus.combo)+'</b> 中'+(S.focus.v==='regress'?'最坏退化':VN[S.focus.v])+'的题。<button type="button" class="expand-btn" data-act="unfocus">显示全部</button></div>':'';
  return bar+(shown?'<table>'+head+out.join('')+'</table>':'<div class="meta">没有符合条件的题</div>');
}
function source(entry){
  if(!entry)return '';
  var q=entry[0],r=entry[1],ranks=entry[2]||[];
  if(!q)return '原问题';
  var best=ranks.slice().sort(function(a,b){return a[1]-b[1]})[0];
  var who=q==='original'?'原问题一路':q;
  var all=ranks.map(function(x){return phr(x[0])+' 第 '+x[1]+' 名'}).join('；');
  return '<span title="'+esc(all)+'">'+esc(who)+' · 第 '+r+' 轮'+(best?' · '+esc(phr(best[0]))+' 第 '+best[1]+' 名':'')+'</span>';
}
function phr(lbl){var p=lbl.split(':');return (KNAME[p[0]]||p[0])+(p[1]?'（'+p[1]+'）':'')}
function cellDetail(id,combo,lk,rk_){
  var lf=facts(lk,combo)[id],rf=facts(rk_,combo)[id],units=D.units[combo],q=CASE[id];
  var held=(D.holds[combo]||{})[id]||{},xs={};q.excerpts.forEach(function(x){xs[x.excerpt_id]=x});
  var proc=window.QC_PROCESS[rk_];if(!proc)loadProcess(rk_);
  var l=vec(lf.n),r=vec(rf.n),v=verdict(l,r);
  var items=q.groups.map(function(g,i){
    return '<tr><td>'+esc(g.group_id+' '+g.information_item)+'</td><td class="num">'+arrow(lf.f[i],rf.f[i])+'</td><td class="num">'+arrow(lf.n[i],rf.n[i])+'</td></tr>';
  }).join('');
  var ids=Object.keys(lf.e).sort(function(a,b){return rf.e[a]-rf.e[b]});
  var rows=ids.map(function(i){
    var u=units[+i],a=lf.e[i],b=rf.e[i];
    var ex=(held[i]||[]).map(function(x){var e=xs[x];return '<span class="badge" title="'+esc(e?e.text:'')+'">'+esc(x)+(e&&e.groups.length?' · '+esc(e.groups.join('/')):'')+'</span>'}).join(' ');
    var src=b<=20?(proc?source(proc[combo][id][b-1]):'加载中…'):'<span class="meta">第 20 名之后</span>';
    return '<tr><td class="nw" title="'+esc(u.text)+'">'+(u.table?'整表 ':'')+esc(short(u.key))+'<div class="meta">第 '+u.pages.join(',')+' 页</div></td>'
      +'<td>'+ex+'</td><td class="num">'+arrow(a,b)+'</td><td>'+src+'</td></tr>';
  }).join('');
  var flips=ACC.map(function(k){
    var a=complete(lf,k),b=complete(rf,k);
    return a===b?null:'K='+k+' '+(b?'<span class="up">由否变是</span>':'<span class="down">由是变否</span>');
  }).filter(Boolean);
  return '<div class="card"><b>'+esc(combo)+'</b> <span class="badge '+VB[v]+'">'+VN[v]+'</span> 凑齐向量 '+vecHtml(l,r)
    +(v==='mixed'?'<div class="meta">'+leadText(l,r)+'</div>':'')
    +'<div class="meta">验收 K 下完整覆盖：'+(flips.length?flips.join('，'):'没有翻转')+'</div></div>'
    +'<div class="scroll"><table><tr><th>信息项</th><th class="num">首块名次 左 → 右</th><th class="num">凑齐名次 左 → 右</th></tr>'+items+'</table></div>'
    +'<div class="scroll"><table><tr><th>证据单元</th><th>承载的 excerpt · 信息项</th><th class="num">名次 左 → 右</th><th>右侧来源（子问题 · 轮次 · 名次最好的写法；悬停看各写法）</th></tr>'+rows+'</table></div>'
    +'<div class="meta">绿色为右侧提前、红色为推后。悬停证据单元看正文开头，悬停 excerpt 看原文。</div>';
}

/* ---------- module 3: matrix ---------- */
function totals(fs,k,units){
  var ids=Object.keys(fs),n=ids.length,m={hit:0,complete:0,tokens:0,retrievals:0,ms:0,usd:0};
  ids.forEach(function(id){var f=fs[id];
    m.hit+=hit(f,k);m.complete+=complete(f,k);
    m.tokens+=f.u.slice(0,k).reduce(function(a,i){return a+units[i].tokens},0);
    m.retrievals+=f.c[0];m.ms+=(f.c[1]+f.c[3])*1000;m.usd+=f.c[4];});
  ['tokens','retrievals','ms','usd'].forEach(function(x){m[x]=n?m[x]/n:0});
  return m;
}
function flipped(lf,rf,k,test){
  var b=[],w=[];
  Object.keys(rf).forEach(function(id){var a=test(lf[id],k),c=test(rf[id],k);if(!a&&c)b.push(id);if(a&&!c)w.push(id)});
  return [b,w];
}
var TEST={complete:complete,hit:hit};
function renderMatrix(base){
  var k=S.km,lk=key(S.L),rk_=key(S.R);
  var cost=[['retrievals','每题检索次数',1,'检索器调用次数：问题数 × 启用的写法数（含原句），开分解时加原问题一路'],
    ['ms','每题查询耗时（ms）',0,'规划（LLM）+ 检索的耗时，不含作答；命中缓存的取当次记录的耗时'],
    ['usd','每题 LLM 费用（$）',4,'规划的费用，只计该配置用到的步骤（分解计拆解调用，开任一写法计写法调用），不含作答']];
  var head='<tr><th>组合</th><th class="num">完整覆盖（共 '+N+' 题）</th><th class="num">命中（共 '+N+' 题）</th><th class="num">每题返回 token</th>'
    +cost.map(function(c){return '<th class="num" title="'+c[3]+'">'+c[1]+'</th>'}).join('')+'</tr>';
  var out=[];
  D.combos.forEach(function(c){
    var units=D.units[c.label],rf=facts(rk_,c.label),r=totals(rf,k,units);
    if(base){
      out.push('<tr>'+comboHead(c)+'<td class="num">'+r.complete+'</td><td class="num">'+r.hit+'</td><td class="num">'+fmt(r.tokens,0)+'</td>'
        +cost.map(function(x){return '<td class="num">'+fmt(r[x[0]],x[2])+'</td>'}).join('')+'</tr>');
      return;
    }
    var lf=facts(lk,c.label),l=totals(lf,k,units);
    var count=function(metric){
      var f=flipped(lf,rf,k,TEST[metric]);
      var btn=function(ids,d,cls,sym){return ids.length?'<button type="button" class="lnk '+cls+'" data-act="flip" data-combo="'+c.label+'" data-metric="'+metric+'" data-dir="'+d+'">'+sym+ids.length+'</button>':'<span class="same">'+sym+'0</span>'};
      return '<td class="num">'+l[metric]+' → '+r[metric]+'<div>（'+btn(f[0],1,'up','+')+' / '+btn(f[1],-1,'down','−')+'）</div></td>';
    };
    var diff=function(name,d,lower){var v=r[name]-l[name],cls=Math.abs(v)<1e-9?'same':(v>0)!==lower?'up':'down';
      return '<td class="num"><div class="meta">'+fmt(l[name],d)+' → '+fmt(r[name],d)+'</div><div class="'+cls+'">'+sign(v,d)+'</div></td>'};
    out.push('<tr>'+comboHead(c)+count('complete')+count('hit')+diff('tokens',0,true)
      +cost.map(function(x){return diff(x[0],x[2],true)}).join('')+'</tr>');
    if(S.flip&&S.flip.combo===c.label){
      var f=flipped(lf,rf,k,TEST[S.flip.metric])[S.flip.dir>0?0:1];
      out.push('<tr><td colspan="'+(4+cost.length)+'"><span class="meta">K='+k+' 下'+(S.flip.metric==='complete'?'完整覆盖':'命中')
        +(S.flip.dir>0?'由否变是':'由是变否')+'的题（点题号到逐题变化看证据单元）：</span> '
        +f.map(function(id){return '<button type="button" class="lnk" data-act="goto" data-case="'+esc(id)+'" data-combo="'+c.label+'">'+esc(id)+'</button>'}).join(' ')+'</td></tr>');
    }
  });
  return '<table>'+head+out.join('')+'</table>';
}

/* ---------- module 4: curves ---------- */
function curve(base,count,total){
  var lk=key(S.L),rk_=key(S.R);
  var head='<tr><th>组合</th>'+D.ks.map(function(k){return '<th class="num'+(ACC.indexOf(k)>=0?' acc':'')+'">K='+k+'</th>'}).join('')+'</tr>';
  var rows=D.combos.map(function(c){
    return '<tr>'+comboHead(c)+D.ks.map(function(k){
      var r=count(facts(rk_,c.label),k);
      if(base)return '<td class="num">'+r+'</td>';
      var l=count(facts(lk,c.label),k),v=r-l;
      return '<td class="num"><div class="meta">'+l+' → '+r+'</div><div class="'+(v>0?'up':v<0?'down':'same')+'">'+sign(v,0)+'</div></td>';
    }).join('')+'</tr>';
  }).join('');
  return '<table>'+head+rows+'</table>';
}
function renderCurve(base){
  var byCase=function(fs,k){return Object.values(fs).filter(function(f){return complete(f,k)}).length};
  var byItem=function(fs,k){return Object.values(fs).reduce(function(a,f){return a+f.n.filter(function(x){return x!==null&&x<=k}).length},0)};
  return '<h3>完整覆盖题数（共 '+N+' 题）</h3>'+curve(base,byCase)
    +'<h3 style="margin-top:12px">已凑齐的信息项（共 '+GROUPS+' 项）</h3>'+curve(base,byItem);
}

/* ---------- module 5: query process ---------- */
function loadProcess(k){
  if(window.QC_PROCESS[k]||document.querySelector('script[data-proc="'+k+'"]'))return;
  var s=document.createElement('script');s.dataset.proc=k;
  s.src='process/'+((k.replace(/\+/g,'_'))||'none')+'.js';
  s.onload=function(){render()};
  s.onerror=function(){window.QC_PROCESS[k]=null;var b=document.querySelector('[data-module="process"] .qc-body');if(b)b.innerHTML='<div class="meta">没有找到 '+esc(s.src)+'</div>'};
  document.head.appendChild(s);
}
function mappingHtml(id){
  var m=(D.decomposition||{})[id],q=CASE[id],plan=D.plans[id].decomposed;
  if(!m)return '<div class="meta">没有对应标注。</div>';
  var text={};plan.questions.forEach(function(x){text[x.id]=x.text});
  var rows=q.groups.map(function(g){var qs=m.mapping[g.group_id]||[];
    return '<tr><td>'+esc(g.group_id+' '+g.information_item)+'</td><td>'+(qs.length?qs.map(function(x){return '<div><b>'+x+'</b> '+esc(text[x]||'')+'</div>'}).join(''):'<span class="down">分解遗漏</span>')+'</td></tr>'}).join('');
  var notes=[];
  if(m.extra.length)notes.push('多余子问题：'+m.extra.map(function(x){return x+' '+text[x]}).join('；'));
  if(m.lost_qualifier.length)notes.push('丢失限定条件：'+m.lost_qualifier.join('、'));
  if(m.note)notes.push(m.note);
  return '<div class="scroll"><table><tr><th>信息项</th><th>对应的子问题</th></tr>'+rows+'</table></div>'
    +notes.map(function(n){return '<div class="meta">'+esc(n)+'</div>'}).join('');
}
function renderProcess(){
  var rk_=key(S.R),q=CASE[S.pq],combo=S.pc,plan=D.plans[q.case_id],cfg=S.R;
  var pick='<div class="seg">题 <select data-act="pq">'+D.cases.map(function(x){return '<option value="'+esc(x.case_id)+'"'+(x.case_id===S.pq?' selected':'')+'>'+esc(x.case_id)+'</option>'}).join('')+'</select>'
    +' 组合 <select data-act="pc">'+D.combos.map(function(c){return '<option'+(c.label===combo?' selected':'')+'>'+esc(c.label)+'</option>'}).join('')+'</select>'
    +' <span class="meta">右侧配置：'+esc(label(cfg))+'</span></div>';
  var scope=plan.scope;
  var head='<div class="card"><b>'+esc(q.case_id)+'</b> '+esc(q.question)+'<div class="meta">检索范围：'+esc(scope.documents.length?q.document_name:'全库')
    +' · 文档语言：'+esc(scope.languages.join('、'))+' · 标识编码：'+esc(scope.codes.join('、')||'无')+'</div></div>';
  var planHtml;
  if(!cfg.size)planHtml='<div class="meta">V3.13：原问题检索一次，没有规划。</div>';
  else{
    var p=cfg.has('P3.1')?plan.decomposed:plan.plain,kinds={question:1};
    Object.keys(KIND).forEach(function(m){if(cfg.has(m))kinds[KIND[m]]=1});
    planHtml=(cfg.has('P3.1')?'<div class="meta">分解为 '+p.questions.length+' 个子问题，另有原问题一路（每圈最后）。</div>':'<div class="meta">不分解：原问题是唯一的问题。</div>')
      +(p.degraded.length?'<div class="meta down">规划降级：'+esc(p.degraded.join('；'))+'</div>':'')
      +p.questions.map(function(x){
        return '<div class="item"><b>'+esc(x.id)+'</b> '+esc(x.text)+x.phrasings.filter(function(f){return kinds[f.kind]&&f.kind!=='question'}).map(function(f){
          return '<div class="meta">'+esc(KNAME[f.kind]+(f.language?'（'+f.language+'）':''))+'：'+esc(f.text)+'</div>'}).join('')+'</div>';
      }).join('');
  }
  var proc=window.QC_PROCESS[rk_];
  if(proc===undefined){loadProcess(rk_);return pick+head+planHtml+'<div class="meta">正在加载来源明细…</div>'}
  var f=facts(rk_,combo)[q.case_id],units=D.units[combo],held=(D.holds[combo]||{})[q.case_id]||{};
  var pos={};Object.keys(f.e).forEach(function(i){pos[f.e[i]]=i});
  var rows=f.u.map(function(i,n){
    var u=units[i],ev=pos[n+1]!==undefined,src=proc?source(proc[combo][q.case_id][n]):'';
    return '<tr><td class="num">'+(n+1)+'</td><td class="nw">'+(u.table?'整表 ':'')+esc(short(u.key))+'<div class="meta">第 '+u.pages.join(',')+' 页 · '+u.tokens+' token</div></td>'
      +'<td>'+(ev?'<span class="badge ok">'+esc((held[i]||[]).join('、')||'证据')+'</span> ':'')+'<span class="meta">'+esc(u.text.slice(0,120))+'</span></td><td>'+src+'</td></tr>';
  }).join('');
  var c=f.c;
  return pick+head+'<h3>规划</h3>'+planHtml
    +(cfg.has('P3.1')?'<h3 style="margin-top:12px">子问题与信息项的对应</h3>'+mappingHtml(q.case_id):'')
    +'<h3 style="margin-top:12px">返回顺序（前 '+f.u.length+' 个）</h3><div class="scroll"><table><tr><th class="num">#</th><th>单元</th><th>内容（绿色为证据单元及其 excerpt）</th><th>来源（悬停看各写法名次）</th></tr>'+rows+'</table></div>'
    +'<div class="meta">本题成本：检索 '+c[0]+' 次 · 检索 '+fmt(c[1]*1000,0)+' ms · 规划 LLM '+c[2]+' 次、'+fmt(c[3],1)+' 秒、$'+fmt(c[4],4)+'</div>';
}

/* ---------- module 6: P3.1 layers (independent of the picker) ---------- */
function layerVerdicts(combo,id){
  var x=D.layers[combo][id],o=vec(x.o),m=vec(x.m),i=vec(x.i),g=vec(facts('P3.1',combo)[id].n);
  return {mapped:verdict(o,m),ideal:verdict(o,i),merged:verdict(o,g),o:o,m:m,i:i,g:g};
}
function renderLayers(){
  if(!D.layers||!Object.keys(D.layers[D.combos[0].label]||{}).length)return '<div class="meta">没有子问题与信息项的对应标注，或标注与冻结的规划不一致。</div>';
  var ids=Object.keys(D.layers[D.combos[0].label]);
  var tot={mapped:[],ideal:[],merged:[]};
  var summary=D.combos.map(function(c){
    var vs={mapped:[],ideal:[],merged:[]};
    ids.forEach(function(id){var v=layerVerdicts(c.label,id);['mapped','ideal','merged'].forEach(function(s){vs[s].push(v[s]);tot[s].push(v[s])})});
    return '<tr>'+comboHead(c)+['mapped','ideal','merged'].map(function(s){var n=counts(vs[s]);
      return '<td class="num"><span class="up">'+n.better+'</span> / <span class="down">'+n.worse+'</span> / '+n.mixed+' / <span class="same">'+n.same+'</span></td>'}).join('')+'</tr>';
  }).join('');
  var foot='<tr><td><b>合计</b></td>'+['mapped','ideal','merged'].map(function(s){var n=counts(tot[s]);return '<td class="num"><b>'+n.better+' / '+n.worse+' / '+n.mixed+' / '+n.same+'</b></td>'}).join('')+'</tr>';
  var table='<table><tr><th>组合</th><th class="num" title="各信息项在对应子问题一路（只用原句）中的凑齐名次">检索层 vs 原问题</th><th class="num" title="各信息项在全部子问题各路中的凑齐名次；假设事先知道哪一路有用">理想上限 vs 原问题</th><th class="num" title="只开 P3.1 的返回顺序（轮流取，原问题一路每圈最后）">合并后 vs 原问题</th></tr>'+summary+foot+'</table>'
    +'<div class="meta">每格为 变好 / 变差 / 各有优势 / 不变 的题数。</div>';
  var head='<tr><th>题目</th>'+D.combos.map(function(c){return '<th class="num">'+esc(c.label)+'</th>'}).join('')+'</tr>',out=[],doc=null,shown=0;
  cases().forEach(function(q){
    var id=q.case_id;if(!D.layers[D.combos[0].label][id])return;
    var vs={};D.combos.forEach(function(c){vs[c.label]=layerVerdicts(c.label,id)});
    var any=D.combos.some(function(c){return vs[c.label].mapped!=='same'||vs[c.label].merged!=='same'});
    var picked=S.lcell&&S.lcell.q===id;
    if(!picked&&!S.lall&&!any)return;
    shown++;
    if(q.document_id!==doc){doc=q.document_id;out.push('<tr><td colspan="'+(D.combos.length+1)+'"><b>'+esc(q.document_name)+'</b></td></tr>')}
    out.push('<tr><td><b>'+esc(id)+'</b><div class="meta">'+esc(q.question)+'</div></td>'+D.combos.map(function(c){
      var v=vs[c.label],sel=picked&&S.lcell.c===c.label?' sel':'';
      return '<td class="hm center '+VC[v.mapped]+sel+'" title="'+esc('检索层'+VN[v.mapped]+'；合并后'+VN[v.merged])+'" data-act="lcell" data-case="'+esc(id)+'" data-combo="'+c.label+'">'+VS[v.mapped]+'<div class="'+({better:'up',worse:'down',mixed:'',same:'same'})[v.merged]+'" style="font-size:12px">'+VS[v.merged]+'</div></td>';
    }).join('')+'</tr>');
    if(picked)out.push('<tr><td colspan="'+(D.combos.length+1)+'">'+layerDetail(id,S.lcell.c)+'</td></tr>');
  });
  return table+'<h3 style="margin-top:12px">逐题</h3><label class="meta"><input type="checkbox" id="qc-lall"'+(S.lall?' checked':'')+'> 显示全部题（默认只显示检索层或合并后有变化的题）</label>'
    +'<div class="meta">每格上方为检索层相对原问题的判定，下方小字为合并后的判定（▲ 变好、▼ 变差、◆ 各有优势、· 不变）。点一格展开。</div>'
    +(shown?'<table>'+head+out.join('')+'</table>':'<div class="meta">没有符合条件的题</div>');
}
function layerDetail(id,combo){
  var q=CASE[id],x=D.layers[combo][id],v=layerVerdicts(combo,id),g=facts('P3.1',combo)[id].n;
  var m=(D.decomposition||{})[id],text={};D.plans[id].decomposed.questions.forEach(function(s){text[s.id]=s.text});
  var rows=q.groups.map(function(gr,i){var qs=m?m.mapping[gr.group_id]||[]:[];
    return '<tr><td>'+esc(gr.group_id+' '+gr.information_item)+'</td><td>'+(qs.length?qs.map(function(s){return '<span title="'+esc(text[s]||'')+'"><b>'+s+'</b></span>'}).join('、'):'<span class="down">分解遗漏</span>')+'</td>'
      +'<td class="num">'+rk(x.o[i])+'</td><td class="num">'+arrow(x.o[i],x.m[i]).split(' → ')[1]+'</td><td class="num">'+arrow(x.o[i],x.i[i]).split(' → ')[1]+'</td><td class="num">'+arrow(x.o[i],g[i]).split(' → ')[1]+'</td></tr>'}).join('');
  var line=function(name,r,vv){return '<div>'+name+' <span class="badge '+VB[vv]+'">'+VN[vv]+'</span> '+vecHtml(v.o,r)+(vv==='mixed'?'<span class="meta">（'+leadText(v.o,r)+'）</span>':'')+'</div>'};
  return '<div class="card"><b>'+esc(combo)+'</b> · 凑齐向量（原问题 → 各层）'+line('检索层',v.m,v.mapped)+line('理想上限',v.i,v.ideal)+line('合并后',v.g,v.merged)+'</div>'
    +'<div class="scroll"><table><tr><th>信息项</th><th>对应的子问题（悬停看原文）</th><th class="num">原问题</th><th class="num">检索层</th><th class="num">理想上限</th><th class="num">合并后</th></tr>'+rows+'</table></div>'
    +'<h3 style="margin-top:8px">子问题</h3>'+D.plans[id].decomposed.questions.map(function(s){return '<div class="item"><b>'+s.id+'</b> '+esc(s.text)+'</div>'}).join('')
    +(m?mappingNotes(m,text):'')
    +'<div class="meta">凑齐名次：绿色比原问题提前、红色推后。检索层允许同一信息项的几段证据来自不同子问题；合成一个返回顺序后能否保持靠前，看“合并后”。</div>';
}
function mappingNotes(m,text){
  var notes=[];
  if(m.extra.length)notes.push('多余子问题：'+m.extra.join('、'));
  if(m.lost_qualifier.length)notes.push('丢失限定条件：'+m.lost_qualifier.join('、'));
  if(m.note)notes.push(m.note);
  return notes.map(function(n){return '<div class="meta">'+esc(n)+'</div>'}).join('');
}

/* ---------- wiring ---------- */
function render(){
  writeHash();
  var state=renderPicker(),base=!S.L.size&&!S.R.size;
  var R={class:renderClass,heat:renderHeat,matrix:renderMatrix,curve:renderCurve,process:renderProcess,layers:renderLayers};
  document.querySelectorAll('[data-module]').forEach(function(sec){
    var body=sec.querySelector('.qc-body'),m=sec.dataset.module;
    body.innerHTML=m==='layers'?renderLayers():state!=='ok'?'<div class="meta">无结果</div>':R[m](base);
  });
  document.querySelectorAll('.seg[data-k] button').forEach(function(b){b.classList.toggle('on',+b.dataset.v===S.km)});
  document.querySelectorAll('.seg[data-doc] button').forEach(function(b){b.classList.toggle('on',b.dataset.v===S.doc)});
  var all=document.getElementById('qc-all');if(all)all.checked=S.all;
}
function scrollTo(id){var el=document.getElementById(id);if(el)el.scrollIntoView({behavior:'smooth',block:'start'})}
document.querySelectorAll('[data-side] input').forEach(function(box){
  box.addEventListener('change',function(){var set=S[box.closest('[data-side]').dataset.side];
    box.checked?set.add(box.value):set.delete(box.value);S.cell=null;S.flip=null;S.focus=null;render();});
});
document.addEventListener('change',function(e){
  var t=e.target;
  if(t.id==='qc-all'){S.all=t.checked;render()}
  if(t.id==='qc-lall'){S.lall=t.checked;render()}
  if(t.dataset&&t.dataset.act==='pq'){S.pq=t.value;render()}
  if(t.dataset&&t.dataset.act==='pc'){S.pc=t.value;render()}
});
document.addEventListener('click',function(e){
  var b=e.target.closest('.seg[data-k] button');
  if(b){S.km=+b.dataset.v;S.flip=null;render();return}
  var d=e.target.closest('.seg[data-doc] button');
  if(d){S.doc=d.dataset.v;render();return}
  var a=e.target.closest('[data-act]');if(!a||a.tagName==='SELECT')return;
  var act=a.dataset.act;
  if(act==='focus'){S.focus={combo:a.dataset.combo,v:a.dataset.v};S.cell=null;render();scrollTo('m-heat')}
  else if(act==='unfocus'){S.focus=null;render()}
  else if(act==='cell'){var same=S.cell&&S.cell.q===a.dataset.case&&S.cell.c===a.dataset.combo;
    S.cell=same?null:{q:a.dataset.case,c:a.dataset.combo};S.pq=a.dataset.case;S.pc=a.dataset.combo;render()}
  else if(act==='lcell'){var s2=S.lcell&&S.lcell.q===a.dataset.case&&S.lcell.c===a.dataset.combo;
    S.lcell=s2?null:{q:a.dataset.case,c:a.dataset.combo};render()}
  else if(act==='flip'){var f={combo:a.dataset.combo,metric:a.dataset.metric,dir:+a.dataset.dir};
    S.flip=S.flip&&S.flip.combo===f.combo&&S.flip.metric===f.metric&&S.flip.dir===f.dir?null:f;render()}
  else if(act==='goto'){var c=a.dataset.combo||S.pc;S.focus=null;S.all=true;S.cell={q:a.dataset.case,c:c};S.pq=a.dataset.case;S.pc=c;render();scrollTo('m-heat')}
});
document.getElementById('qc-reset').addEventListener('click',function(){S.L=new Set();S.R=new Set();S.cell=null;S.flip=null;S.focus=null;render()});
window.addEventListener('hashchange',function(){readHash();render()});
readHash();render();
})();
"""


def render(data: dict[str, Any]) -> str:
    modules = data["modules"]
    boxes = lambda side: "".join(
        f'<label><input type="checkbox" value="{esc(m["id"])}"> {esc(m["id"])} {esc(m["name"])}</label>'
        for m in modules)
    docs = {c["document_id"]: c["document_name"] for c in data["cases"]}
    doc_bar = ('<div class="seg" data-doc>文档 <button type="button" data-v="all" class="on">全部</button>'
               + "".join(f'<button type="button" data-v="{esc(d)}">{esc(n)}</button>' for d, n in docs.items())
               + "</div>")
    k_bar = ('<div class="seg" data-k>K = '
             + "".join(f'<button type="button" data-v="{k}">{k}</button>' for k in data["ks"]) + "</div>")
    n = data["dataset"]["answerable"]
    section = lambda mid, title, extra, note: (
        f'<section id="m-{mid}" data-module="{mid}"><h2>{title}</h2>{extra}<div class="meta">{note}</div>'
        f'<div class="scroll" data-grid><div class="qc-body"></div></div></section>')
    nav = ('<div class="card meta">模块：<a href="#m-class">1 变好、变差</a> · <a href="#m-heat">2 逐题变化</a> · '
           '<a href="#m-matrix">3 指标矩阵</a> · <a href="#m-curve">4 K 曲线</a> · <a href="#m-process">5 查询过程</a> · '
           '<a href="#m-layers"><b>6 P3.1 分层评估</b></a>（不随顶部勾选变化）</div>')
    body = (
        f'<h1>查询侧对比 · {esc(data["name"])}</h1>'
        f'<div class="sub">{esc(data["created_at"])} · 数据集 {esc(data["dataset"]["version"])}，{n} 道可回答题 · '
        f'{len(data["combos"])} 种组合（混合检索、文档过滤开启）· 已评估 {len(data["configs"])} 种模块配置'
        f'（{esc("、".join(data["evaluated"]))} 的全部组合；P3.3 搁置）· 规划：{esc(data["plan_model"])}，'
        f'拆解提示词 v9、写法提示词 v7，冻结的第 0 次生成</div>'
        + nav +
        f'<div class="card pick"><div class="pick-row" data-side="L"><b>左侧</b>{boxes("L")}</div>'
        f'<div class="pick-vs">vs</div><div class="pick-row" data-side="R"><b>右侧</b>{boxes("R")}</div>'
        f'<div class="pick-foot"><span id="qc-msg"></span> <button type="button" id="qc-reset" class="expand-btn">清空两侧</button></div>'
        f'<div class="meta">两侧全空显示 V3.13；否则左侧必须是右侧的真子集，比较的是右侧多出的模块带来的效果。'
        f'每种组合只与它自己比较。开 P3.1 时目前都带原问题一路、轮流取（决策 014 重新评估中，合并层做完后加开关）。</div></div>'
        + section("class", "1. 变好、变差与最坏退化", "",
                  '一题的判定（提案 v3.14 拍板问题 3）：各信息项的凑齐名次（某个方案全部证据到位时的名次）升序排成凑齐向量，两侧逐位比较。'
                  '右侧每一位都不晚于左侧且至少一位更早为变好，反之为变差，有早有晚为各有优势，完全相同为不变；与 K 无关，不设阈值。'
                  '最坏退化：验收 K 中任一档由完整覆盖变为不完整。点题数到逐题变化看是哪些题。')
        + section("heat", "2. 逐题变化", f'{doc_bar}<label class="meta"><input type="checkbox" id="qc-all"> 显示全部题（默认只显示在任一组合中有变化的题）</label>',
                  '每格为该题在该组合下的判定：▲ 变好（绿）、▼ 变差（红）、◆ 各有优势（黄）、· 不变；悬停看两侧凑齐向量。'
                  '点一格展开：凑齐向量、各有优势时各自领先的 K 区间、每个信息项的首块名次与凑齐名次、每个证据单元的名次与右侧来源。')
        + section("matrix", "3. 整体指标矩阵", k_bar,
                  f'完整覆盖：全部信息项凑齐；命中：至少一个信息项凑齐。每格为左侧 → 右侧，括号内为选定 K 下由否变是（+）/ 由是变否（−）的题数，点击列出这些题。'
                  f'token 与成本按 {n} 道可回答题平均；悬停列名看成本的定义，悬停组合名看入库成本（左右两侧相同）。')
        + section("curve", "4. K 曲线", "",
                  f'上表为该 K 下完整覆盖的题数，下表为该 K 下已凑齐的信息项总数；蓝色表头为验收用的 K。')
        + section("process", "5. 查询过程", "",
                  '右侧配置下一道题的检索范围、规划（只列该配置启用的写法）、子问题与信息项的对应（开 P3.1 时）与前 20 个返回单元的来源。来源明细按配置单独存放，切换时加载。')
        + section("layers", "6. P3.1 分层评估", "",
                  '只开 P3.1、冻结的 v9 规划，与原问题检索一次（V3.13）比较，判定同模块 1。'
                  '检索层：各信息项在它对应的子问题一路（只用原句，不融合、不选取）中的凑齐名次，一段证据取它在对应各路中最好的名次，方案取各段中最差的；'
                  '理想上限：同样计算，但对应全部子问题；合并后：只开 P3.1 的实际返回顺序（轮流取，原问题一路每圈最后）。'
                  '合并层（其他合成方式、原问题一路的开关）做完后加入。')
        + '<div class="card meta">扰动：各模块的效果测完后做 3 次独立生成，作为模块 1–4 上的标注层加入。</div>'
        + f'<script type="application/json" id="qc-data">{_embed(data)}</script>'
        + f'<script>{SCRIPT.replace("__ACC__", json.dumps(list(ACCEPTANCE_KS)))}</script>')
    return page(f"查询侧对比 · {data['name']}", body, root="../../", crumbs=[("报告首页", "../../index.html")])


def _embed(data: dict[str, Any]) -> str:
    """JSON safe inside a <script> element."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def write_query_page(workspace: Workspace, data: dict[str, Any]) -> Path:
    """index.html with the main data embedded, data.json (the same data) and process/<config>.js."""
    folder = query_dir(workspace) / data["name"]
    process = data.get("process", {})
    main = {k: v for k, v in data.items() if k != "process"}
    write_json(folder / "data.json", main)
    for key, detail in process.items():
        write_atomic(folder / "process" / process_file(key),
                     f"window.QC_PROCESS=window.QC_PROCESS||{{}};window.QC_PROCESS[{json.dumps(key)}]="
                     + _embed(detail) + ";\n")
    write_assets(workspace.reports)
    write_atomic(folder / "index.html", render(main))
    return folder


def rerender_query_pages(workspace: Workspace) -> int:
    folder = query_dir(workspace)
    count = 0
    for path in sorted(folder.glob("*/data.json")) if folder.is_dir() else []:
        write_atomic(path.parent / "index.html", render(read_json(path)))
        count += 1
    return count
