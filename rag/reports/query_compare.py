"""Query comparison page: `left modules vs right modules` on every combination.

The facts (rag/eval/query_compare.py) are embedded in the page; the page script derives every
figure from them, for the module configurations the reader ticks. Both sides empty shows V3.13;
otherwise the left side must be a proper subset of the right side. Modules (docs/changes/v3.14.md):
classification by better / worse questions (evidence-unit positions, independent of K), a heat map
of the per-question change, the matrix and the curve at K, and the query process of the right side.
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
var TH=D.threshold,N=D.cases.length,ACC=__ACC__;
var CASE={};D.cases.forEach(function(q){CASE[q.case_id]=q});
var S={L:new Set(),R:new Set(),km:3,doc:'all',all:false,focus:null,cell:null,flip:null,
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
}
function writeHash(){history.replaceState(null,'','#L='+key(S.L)+'&R='+key(S.R)+'&k='+S.km+(S.cell?'&cell='+S.cell.q+'@'+S.cell.c:''))}
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
function change(lf,rf){var s=0;for(var i in lf.e){if(i in rf.e)s+=Math.log(lf.e[i]/rf.e[i])}return s}
function dir(v){return v>TH?1:v<-TH?-1:0}
function classify(b,w){return w<=1&&b>w?'固定启用':b>=1&&w>=2?'V3.15 补救候选':'不采用'}
var CLS={'固定启用':'ok','V3.15 补救候选':'warn','不采用':'bad'};
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

/* ---------- picker ---------- */
function renderPicker(){
  ['L','R'].forEach(function(side){
    document.querySelectorAll('[data-side="'+side+'"] input').forEach(function(box){box.checked=S[side].has(box.value)});
  });
  var e=error(),msg=document.getElementById('qc-msg'),lk=key(S.L),rk=key(S.R);
  var card=document.querySelector('.pick');card.classList.remove('bad','warn');
  if(e){card.classList.add('bad');msg.innerHTML='<span class="badge bad">配置不合法</span> '+esc(e);return 'bad'}
  var missing=[];
  if(!D.configs[lk])missing.push('左侧 '+label(S.L));
  if(!D.configs[rk]&&rk!==lk)missing.push('右侧 '+label(S.R));
  if(missing.length){card.classList.add('warn');msg.innerHTML='<span class="badge warn">未评估</span> '+esc(missing.join('；'))+'（P3.3 重排已搁置，含它的配置未评估）';return 'missing'}
  msg.innerHTML=(!S.L.size&&!S.R.size)?'<span class="badge">V3.13 基线</span>'
    :'<span class="badge ok">比较</span> '+esc(label(S.L))+' <b>vs</b> '+esc(label(S.R));
  return 'ok';
}

/* ---------- module 1: classification ---------- */
function directions(lk,rk,combo){
  var lf=facts(lk,combo),rf=facts(rk,combo),out={};
  Object.keys(rf).forEach(function(id){var v=change(lf[id],rf[id]);out[id]={v:v,d:dir(v)}});
  return out;
}
function majority(rows){
  var votes={};rows.forEach(function(r){votes[r.cls]=(votes[r.cls]||0)+1});
  var top=Object.keys(votes).sort(function(a,b){return votes[b]-votes[a]});
  return {votes:votes,top:top[0],count:votes[top[0]],tie:top.length>1&&votes[top[1]]===votes[top[0]]};
}
function voteText(v){return Object.keys(v.votes).sort(function(a,b){return v.votes[b]-v.votes[a]}).map(function(c){return c+' '+v.votes[c]}).join('，')}
function renderClass(base){
  if(base)return '<div class="meta">两侧全空时为 V3.13 基线，没有比较。在右侧勾选模块，即为相对 V3.13 的效果；左侧再勾选右侧的一部分，即为右侧多出的模块的边际效果。</div>';
  var lk=key(S.L),rk=key(S.R);
  var rows=D.combos.map(function(c){
    var ds=directions(lk,rk,c.label),b=0,w=0;
    Object.keys(ds).forEach(function(id){b+=ds[id].d>0;w+=ds[id].d<0});
    return {combo:c,b:b,w:w,cls:classify(b,w)};
  });
  var head='<tr><th>组合</th><th class="num">变好题数</th><th class="num">变差题数</th><th>归类</th></tr>';
  var body=rows.map(function(r){
    var btn=function(n,d,cls){return n?'<button type="button" class="lnk '+cls+'" data-act="focus" data-combo="'+r.combo.label+'" data-dir="'+d+'">'+n+'</button>':'0'};
    return '<tr>'+comboHead(r.combo)+'<td class="num">'+btn(r.b,1,'up')+'</td><td class="num">'+btn(r.w,-1,'down')+'</td>'
      +'<td><span class="badge '+CLS[r.cls]+'">'+r.cls+'</span></td></tr>';
  }).join('');
  var all=majority(rows),T=majority(rows.filter(function(r){return r.combo.table_return_unit==='table'})),
      C=majority(rows.filter(function(r){return r.combo.table_return_unit!=='table'}));
  var verdict=all.count>=7?'<span class="badge '+CLS[all.top]+'">'+all.top+'</span>（'+all.count+' / '+rows.length+' 种组合）'
    :'<span class="badge">没有类别达到 7 种组合</span>';
  var notes=[];
  if(all.count>=6&&all.count<=7)notes.push('结论卡在 7 种组合的投票线上：返回命中 chunk（-C）的组合把表头等 excerpt 的每个备选 chunk 都算作证据单元，其挪动会计入变化量（决策 014），请检查 -C 各行是否由此造成。');
  notes.push('归类在冻结的规划（第 0 次生成）上得出，待扰动测试核对。');
  return '<div class="card"><b>结论：</b>'+verdict+'<div class="meta">全部组合：'+voteText(all)+'。返回整表（7 种）：'+voteText(T)+(T.tie?'（并列）':'')
    +'。返回命中 chunk（6 种）：'+voteText(C)+(C.tie?'（并列）':'')+'。</div>'
    +notes.map(function(n){return '<div class="meta">'+esc(n)+'</div>'}).join('')+'</div>'
    +'<table>'+head+body+'</table>';
}

/* ---------- module 2: heat map ---------- */
function renderHeat(base){
  if(base)return '<div class="meta">两侧全空时没有比较。</div>';
  var lk=key(S.L),rk=key(S.R),out=[],doc=null;
  var dirs={};D.combos.forEach(function(c){dirs[c.label]=directions(lk,rk,c.label)});
  var head='<tr><th>题目</th>'+D.combos.map(function(c){return '<th class="num">'+esc(c.label)+'</th>'}).join('')+'</tr>';
  var shown=0;
  cases().forEach(function(q){
    var id=q.case_id,any=D.combos.some(function(c){return dirs[c.label][id].d!==0});
    var picked=S.cell&&S.cell.q===id;   // the opened question always shows
    if(!picked){if(S.focus){if(dirs[S.focus.combo][id].d!==S.focus.dir)return}
      else if(!S.all&&!any)return;}
    shown++;
    if(q.document_id!==doc){doc=q.document_id;out.push('<tr><td colspan="'+(D.combos.length+1)+'"><b>'+esc(q.document_name)+'</b></td></tr>')}
    var cells=D.combos.map(function(c){
      var x=dirs[c.label][id],cls=x.d>0?'good':x.d<0?'badc':'flat';
      var sel=S.cell&&S.cell.q===id&&S.cell.c===c.label?' sel':'';
      return '<td class="hm '+cls+sel+'" data-act="cell" data-case="'+esc(id)+'" data-combo="'+c.label+'">'+sign(x.v,2)+'</td>';
    }).join('');
    out.push('<tr><td><b>'+esc(id)+'</b><div class="meta">'+esc(q.question)+'</div></td>'+cells+'</tr>');
    if(S.cell&&S.cell.q===id)out.push('<tr><td colspan="'+(D.combos.length+1)+'">'+cellDetail(id,S.cell.c,lk,rk)+'</td></tr>');
  });
  var bar=S.focus?'<div class="card meta">只显示 <b>'+esc(S.focus.combo)+'</b> 中'+(S.focus.dir>0?'变好':'变差')+'的题。<button type="button" class="expand-btn" data-act="unfocus">显示全部</button></div>':'';
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
function cellDetail(id,combo,lk,rk){
  var lf=facts(lk,combo)[id],rf=facts(rk,combo)[id],units=D.units[combo],q=CASE[id];
  var held=(D.holds[combo]||{})[id]||{},xs={};q.excerpts.forEach(function(x){xs[x.excerpt_id]=x});
  var proc=window.QC_PROCESS[rk];if(!proc)loadProcess(rk);
  var ids=Object.keys(lf.e).sort(function(a,b){return rf.e[a]-rf.e[b]});
  var rows=ids.map(function(i){
    var u=units[+i],l=lf.e[i],r=rf.e[i],v=Math.log(l/r);
    var ex=(held[i]||[]).map(function(x){var e=xs[x];return '<span class="badge" title="'+esc(e?e.text:'')+'">'+esc(x)+(e&&e.groups.length?' · '+esc(e.groups.join('/')):'')+'</span>'}).join(' ');
    var src=r<=20?(proc?source(proc[combo][id][r-1]):'加载中…'):'<span class="meta">第 20 名之后</span>';
    return '<tr><td class="nw" title="'+esc(u.text)+'">'+(u.table?'整表 ':'')+esc(short(u.key))+'<div class="meta">第 '+u.pages.join(',')+' 页</div></td>'
      +'<td>'+ex+'</td><td class="num">'+l+'</td><td class="num">'+r+'</td>'
      +'<td class="num '+(v>0?'up':v<0?'down':'same')+'">'+sign(v,2)+'</td><td>'+src+'</td></tr>';
  }).join('');
  var flips=ACC.map(function(k){
    var a=complete(lf,k),b=complete(rf,k);
    return a===b?null:'K='+k+' '+(b?'<span class="up">由否变是</span>':'<span class="down">由是变否</span>');
  }).filter(Boolean);
  var total=change(lf,rf);
  return '<div class="meta"><b>'+esc(combo)+'</b> · 变化量 '+sign(total,2)+'（阈值 ±'+TH.toFixed(2)+'）· 证据组：'
    +q.groups.map(function(g){return esc(g.group_id+' '+g.information_item)}).join('；')+'</div>'
    +'<div class="scroll"><table><tr><th>证据单元</th><th>承载的 excerpt · 证据组</th><th class="num">左侧名次</th><th class="num">右侧名次</th><th class="num">ln(左/右)</th><th>右侧来源（子问题 · 轮次 · 名次最好的写法；悬停看各写法）</th></tr>'+rows+'</table></div>'
    +'<div class="meta">验收用的 K 下完整覆盖：'+(flips.length?flips.join('，'):'没有翻转')+'。悬停证据单元看正文开头，悬停 excerpt 看原文。</div>';
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
  var k=S.km,lk=key(S.L),rk=key(S.R);
  var cost=[['retrievals','每题检索次数',1,'检索器调用次数：问题数 × 启用的写法数（含原句），开分解时加原问题一路'],
    ['ms','每题查询耗时（ms）',0,'规划（LLM）+ 检索的耗时，不含作答；命中缓存的取当次记录的耗时'],
    ['usd','每题 LLM 费用（$）',4,'规划的费用，只计该配置用到的步骤（分解计拆解调用，开任一写法计写法调用），不含作答']];
  var head='<tr><th>组合</th><th class="num">完整覆盖（共 '+N+' 题）</th><th class="num">命中（共 '+N+' 题）</th><th class="num">每题返回 token</th>'
    +cost.map(function(c){return '<th class="num" title="'+c[3]+'">'+c[1]+'</th>'}).join('')+'</tr>';
  var out=[];
  D.combos.forEach(function(c){
    var units=D.units[c.label],rf=facts(rk,c.label),r=totals(rf,k,units);
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

/* ---------- module 4: curve ---------- */
function renderCurve(base){
  var lk=key(S.L),rk=key(S.R);
  var head='<tr><th>组合</th>'+D.ks.map(function(k){return '<th class="num'+(ACC.indexOf(k)>=0?' acc':'')+'">K='+k+'</th>'}).join('')+'</tr>';
  var rows=D.combos.map(function(c){
    return '<tr>'+comboHead(c)+D.ks.map(function(k){
      var r=Object.values(facts(rk,c.label)).filter(function(f){return complete(f,k)}).length;
      if(base)return '<td class="num">'+r+'</td>';
      var l=Object.values(facts(lk,c.label)).filter(function(f){return complete(f,k)}).length,v=r-l;
      return '<td class="num"><div class="meta">'+l+' → '+r+'</div><div class="'+(v>0?'up':v<0?'down':'same')+'">'+sign(v,0)+'</div></td>';
    }).join('')+'</tr>';
  }).join('');
  return '<table>'+head+rows+'</table>';
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
function renderProcess(){
  var rk=key(S.R),q=CASE[S.pq],combo=S.pc,plan=D.plans[q.case_id],cfg=S.R;
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
  var proc=window.QC_PROCESS[rk];
  if(proc===undefined){loadProcess(rk);return pick+head+planHtml+'<div class="meta">正在加载来源明细…</div>'}
  var f=facts(rk,combo)[q.case_id],units=D.units[combo],held=(D.holds[combo]||{})[q.case_id]||{};
  var pos={};Object.keys(f.e).forEach(function(i){pos[f.e[i]]=i});
  var rows=f.u.map(function(i,n){
    var u=units[i],ev=pos[n+1]!==undefined,src=proc?source(proc[combo][q.case_id][n]):'';
    return '<tr><td class="num">'+(n+1)+'</td><td class="nw">'+(u.table?'整表 ':'')+esc(short(u.key))+'<div class="meta">第 '+u.pages.join(',')+' 页 · '+u.tokens+' token</div></td>'
      +'<td>'+(ev?'<span class="badge ok">'+esc((held[i]||[]).join('、')||'证据')+'</span> ':'')+'<span class="meta">'+esc(u.text.slice(0,120))+'</span></td><td>'+src+'</td></tr>';
  }).join('');
  var c=f.c;
  return pick+head+'<h3>规划</h3>'+planHtml
    +'<h3 style="margin-top:12px">返回顺序（前 '+f.u.length+' 个）</h3><div class="scroll"><table><tr><th class="num">#</th><th>单元</th><th>内容（绿色为证据单元及其 excerpt）</th><th>来源（悬停看各写法名次）</th></tr>'+rows+'</table></div>'
    +'<div class="meta">本题成本：检索 '+c[0]+' 次 · 检索 '+fmt(c[1]*1000,0)+' ms · 规划 LLM '+c[2]+' 次、'+fmt(c[3],1)+' 秒、$'+fmt(c[4],4)+'</div>'
    +'<div class="meta">分解对照（子问题与标注信息项的对应）：待助手逐题标注后填入。</div>';
}

/* ---------- wiring ---------- */
function render(){
  writeHash();
  var state=renderPicker(),base=!S.L.size&&!S.R.size;
  var R={class:renderClass,heat:renderHeat,matrix:renderMatrix,curve:renderCurve,process:renderProcess};
  document.querySelectorAll('[data-module]').forEach(function(sec){
    var body=sec.querySelector('.qc-body');
    body.innerHTML=state!=='ok'?'<div class="meta">无结果</div>':R[sec.dataset.module](base);
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
  if(act==='focus'){S.focus={combo:a.dataset.combo,dir:+a.dataset.dir};S.cell=null;render();scrollTo('m-heat')}
  else if(act==='unfocus'){S.focus=null;render()}
  else if(act==='cell'){var same=S.cell&&S.cell.q===a.dataset.case&&S.cell.c===a.dataset.combo;
    S.cell=same?null:{q:a.dataset.case,c:a.dataset.combo};S.pq=a.dataset.case;S.pc=a.dataset.combo;render()}
  else if(act==='flip'){var f={combo:a.dataset.combo,metric:a.dataset.metric,dir:+a.dataset.dir};
    S.flip=S.flip&&S.flip.combo===f.combo&&S.flip.metric===f.metric&&S.flip.dir===f.dir?null:f;render()}
  else if(act==='goto'){S.focus=null;S.all=true;S.cell={q:a.dataset.case,c:a.dataset.combo};S.pq=a.dataset.case;S.pc=a.dataset.combo;render();scrollTo('m-heat')}
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
    threshold = f'{data["threshold"]:.2f}'
    section = lambda mid, title, extra, note: (
        f'<section id="m-{mid}" data-module="{mid}"><h2>{title}</h2>{extra}<div class="meta">{note}</div>'
        f'<div class="scroll" data-grid><div class="qc-body"></div></div></section>')
    body = (
        f'<h1>查询侧对比 · {esc(data["name"])}</h1>'
        f'<div class="sub">{esc(data["created_at"])} · 数据集 {esc(data["dataset"]["version"])}，{n} 道可回答题 · '
        f'{len(data["combos"])} 种组合（混合检索、文档过滤开启）· 已评估 {len(data["configs"])} 种模块配置'
        f'（{esc("、".join(data["evaluated"]))} 的全部组合；P3.3 搁置）· 规划：{esc(data["plan_model"])}，冻结的第 0 次生成</div>'
        f'<div class="card pick"><div class="pick-row" data-side="L"><b>左侧</b>{boxes("L")}</div>'
        f'<div class="pick-vs">vs</div><div class="pick-row" data-side="R"><b>右侧</b>{boxes("R")}</div>'
        f'<div class="pick-foot"><span id="qc-msg"></span> <button type="button" id="qc-reset" class="expand-btn">清空两侧</button></div>'
        f'<div class="meta">两侧全空显示 V3.13；否则左侧必须是右侧的真子集，比较的是右侧多出的模块带来的效果。'
        f'每种组合只与它自己比较。开 P3.1 时都带原问题一路（决策 014）。</div></div>'
        + section("class", "1. 归类与变好、变差", "",
                  f'一题的变化量 = Σ ln(左侧名次 / 右侧名次)，对该题每个证据单元（返回顺序中含任一证据 chunk 的单元）计一次；'
                  f'&gt; {threshold}（ln 1.5）为变好，&lt; −{threshold} 为变差，与 K 无关。每种组合：变差 ≤ 1 且变好 &gt; 变差为固定启用；'
                  f'变好 ≥ 1 且变差 ≥ 2 为 V3.15 补救候选；其余不采用；13 种组合中 ≥ 7 种给出的类别为结论（提案 v3.14 拍板问题 3、5）。'
                  f'点题数到逐题变化看是哪些题。')
        + section("heat", "2. 逐题变化", f'{doc_bar}<label class="meta"><input type="checkbox" id="qc-all"> 显示全部题（默认只显示在任一组合中超过阈值的题）</label>',
                  '每格为该题在该组合下的变化量：绿色变好、红色变差、灰色在阈值以内。点一格展开该组合下每个证据单元的名次变化、承载的 excerpt 与右侧来源。')
        + section("matrix", "3. 整体指标矩阵", k_bar,
                  f'完整覆盖：全部证据组补全；命中：至少一个证据组补全。每格为左侧 → 右侧，括号内为选定 K 下由否变是（+）/ 由是变否（−）的题数，点击列出这些题。'
                  f'token 与成本按 {n} 道可回答题平均；悬停列名看成本的定义，悬停组合名看入库成本（左右两侧相同）。')
        + section("curve", "4. K–完整覆盖曲线", "",
                  f'每格为该 K 下完整覆盖的题数（共 {n} 题）；蓝色表头为验收用的 K。')
        + section("process", "5. 查询过程", "",
                  '右侧配置下一道题的检索范围、规划（只列该配置启用的写法）与前 20 个返回单元的来源。来源明细按配置单独存放，切换时加载。')
        + '<h2>6. 选取方式对照</h2><div class="card meta">最终配置定下后填入：轮流取、每个子问题固定取前 k 个、全局 RRF 的比较。</div>'
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
