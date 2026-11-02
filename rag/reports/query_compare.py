"""Query comparison page: `left configuration vs right configuration` on every combination.

The data every configuration shares (questions, plans, decomposition annotation, unit tables) is
embedded in the page; each configuration's per-question facts (rag/eval/query_compare.py) are in
facts/<config>.js and where its returned units came from in process/<config>.js, loaded when the
configuration is ticked. Both sides empty shows V3.13; otherwise any two different configurations
compare. Modules (docs/archive/changes/v3.14.md): better / worse questions (completion vectors by
dominance, independent of K), a heat map of the per-question verdict, the matrix and the curves at
K, and the query process of the right side. Without a merge there is no return order: the matrix,
the curves and the worst regressions are not shown.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from rag.jsonio import read_json, write_atomic
from rag.paths import Workspace
from rag.reports.html import esc, page, write_assets

ACCEPTANCE_KS = (3, 6, 9, 12)


def query_dir(workspace: Workspace) -> Path:
    return workspace.reports / "query"


def script_file(key: str) -> str:
    """facts/<file> and process/<file> of a configuration key ('' is V3.13)."""
    return (key or "none") + ".js"


SCRIPT = r"""
(function(){
var D=JSON.parse(document.getElementById('qc-data').textContent);
var PH=['P3.2.1','P3.2.2','P3.2.3','P3.2.4'];
var MNAME={};D.modules.forEach(function(m){MNAME[m.id]=m.name});
var KIND={'P3.2.1':'rewrite','P3.2.2':'translation','P3.2.3':'hyde','P3.2.4':'step_back'};
var KNAME={question:'原句',rewrite:'改写',translation:'翻译',hyde:'HyDE',step_back:'step-back'};
var MERGE={rr:'轮流取',rrf:'全局 RRF'};
var LANGM=['P3.2.1','P3.2.4'],LNAME={'':'问题语言',d:'文档语言',qd:'两种'};   // V3.14.1: the language a rewrite or step-back is written in
var N=D.cases.length,ACC=__ACC__,AVAIL=new Set(D.available);
var GROUPS=D.cases.reduce(function(a,q){return a+q.groups.length},0);
var CASE={};D.cases.forEach(function(q){CASE[q.case_id]=q});
var VN={better:'变好',worse:'变差',mixed:'各有优势',same:'不变'},VS={better:'▲',worse:'▼',mixed:'◆',same:'·'},
    VC={better:'good',worse:'badc',mixed:'mix',same:'flat'},VB={better:'ok',worse:'bad',mixed:'warn',same:''};
var ORDER=['better','worse','mixed','same'];
function blank(){return {d:false,merge:null,o:false,s:false,ph:new Set(),fuse:true,lang:{}}}   // fuse: the original text is one of the retrieval texts
var S={L:blank(),R:blank(),km:3,doc:'all',all:false,focus:null,cell:null,flip:null,
  pq:D.cases[0].case_id,pc:D.combos[0].label};
window.QC_FACTS=window.QC_FACTS||{};window.QC_PROCESS=window.QC_PROCESS||{};window.QC_PERTURB=window.QC_PERTURB||{};
var PT=D.perturbation||null,PAV=new Set(PT?PT.available:[]),GENS=[0].concat(PT?PT.generations:[]);
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}

/* ---------- configurations (key grammar: rag/eval/query_compare.py QueryConfig.key) ---------- */
function phs(c){return PH.filter(function(m){return c.ph.has(m)})}
function key(c){var p=[];
  if(c.d)p.push('P3.1'+(c.merge?'@'+c.merge:'')+(c.o?'-o':''));
  if(c.s)p.push('P3.2.0');
  if(c.ph.size)p.push(phs(c).map(function(m){return m+(c.lang[m]?'~'+c.lang[m]:'')}).join('+')+(c.fuse?'@rrf':''));
  return p.join('_')}
function parse(k){var c=blank();
  (k||'').split('_').filter(Boolean).forEach(function(part){
    var i=part.indexOf('@'),body=i<0?part:part.slice(0,i),opt=i<0?'':part.slice(i+1);
    if(body==='P3.1'){var m=opt.split('-');c.d=true;c.merge=MERGE[m[0]]?m[0]:null;c.o=!!c.merge&&m[1]==='o'}
    else if(body==='P3.2.0')c.s=true;
    else{body.split('+').forEach(function(x){var y=x.split('~');if(PH.indexOf(y[0])>=0){c.ph.add(y[0]);if(y[1]&&LANGM.indexOf(y[0])>=0)c.lang[y[0]]=y[1]}});c.fuse=opt==='rrf'||c.ph.size>1}
  });
  return c}
function merged(c){return !c.d||!!c.merge}
function decLabel(c){return c.merge?MERGE[c.merge]+(c.o?' + 原问题一路':''):'不合并'}
function phName(c,m){return MNAME[m]+(LANGM.indexOf(m)>=0?'（'+LNAME[c.lang[m]||'']+'）':'')}
function textsLabel(c){var t=(c.fuse?['原句']:[]).concat(phs(c).map(function(m){return phName(c,m)}));
  return t.join(' + ')+(t.length>1||(t.length===1&&c.lang[phs(c)[0]]==='qd')?'（RRF）':'')}
function label(c){var p=[];
  if(c.d)p.push('P3.1 分解（'+decLabel(c)+'）');
  if(c.s)p.push('去编码');
  if(c.ph.size)p.push(textsLabel(c));
  return p.length?p.join(' · '):'V3.13（无模块）'}
function diffs(L,R){
  var out=[];
  if(L.d!==R.d)out.push('分解：'+(L.d?'开（'+decLabel(L)+'）':'不开')+' → '+(R.d?'开（'+decLabel(R)+'）':'不开'));
  else if(L.d){
    if(L.merge!==R.merge)out.push('合并方式：'+(MERGE[L.merge]||'不合并')+' → '+(MERGE[R.merge]||'不合并'));
    if(L.o!==R.o)out.push('原问题一路：'+(L.o?'开':'关')+' → '+(R.o?'开':'关'));
  }
  if(L.s!==R.s)out.push('去编码：'+(L.s?'开':'关')+' → '+(R.s?'开':'关'));
  if(textsLabel(L)!==textsLabel(R))out.push('检索文本：'+textsLabel(L)+' → '+textsLabel(R));
  return out}

function readHash(){
  var h=new URLSearchParams(location.hash.slice(1));
  S.L=parse(h.get('L'));S.R=parse(h.get('R'));
  var k=+h.get('k');if(D.ks.indexOf(k)>=0)S.km=k;
  var cell=(h.get('cell')||'').split('@');
  if(CASE[cell[0]]&&D.combos.some(function(c){return c.label===cell[1]})){S.cell={q:cell[0],c:cell[1]};S.pq=cell[0];S.pc=cell[1]}
}
function writeHash(){history.replaceState(null,'','#L='+encodeURIComponent(key(S.L))+'&R='+encodeURIComponent(key(S.R))+'&k='+S.km+(S.cell?'&cell='+S.cell.q+'@'+S.cell.c:''))}
function load(kind,k){
  var store={facts:window.QC_FACTS,process:window.QC_PROCESS,perturb:window.QC_PERTURB}[kind];
  if(store[k]!==undefined||document.querySelector('script[data-'+kind+'="'+k+'"]'))return;
  var s=document.createElement('script');s.setAttribute('data-'+kind,k);
  s.src=kind+'/'+(k||'none')+'.js';
  s.onload=function(){render()};
  s.onerror=function(){store[k]=null;render()};
  document.head.appendChild(s);
}
function facts(k,combo){return window.QC_FACTS[k][combo]}
/* perturbation layer: generation 0 (frozen) and the independent generations */
function pert(){var a=key(S.L),b=key(S.R);return !!(PT&&window.QC_PERTURB[a]&&window.QC_PERTURB[b])}
function nOf(k,g,combo,id){return g===0?facts(k,combo)[id].n:window.QC_PERTURB[k][String(g)][combo][id]}
function gverdicts(lk,rk_,combo,id){return GENS.map(function(g){return verdict(vec(nOf(lk,g,combo,id)),vec(nOf(rk_,g,combo,id)))})}
function stable(vs){return vs.every(function(v){return v===vs[0]})}
function span(xs){var a=Math.min.apply(null,xs),b=Math.max.apply(null,xs);return a===b?''+a:a+'–'+b}
function flips(ds){return Math.min.apply(null,ds)<0&&Math.max.apply(null,ds)>0}
function perGen(fn){return GENS.map(function(g){return fn(g)})}
function view(k,g,combo){var out={};Object.keys(facts(k,combo)).forEach(function(id){out[id]={n:nOf(k,g,combo,id)}});return out}
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
function unmergedNote(){return '<div class="meta">'+(merged(S.L)?'右侧':merged(S.R)?'左侧':'两侧')+'为分解且不合并：没有统一的返回顺序，需要 K 的指标不计算。</div>'}

/* ---------- picker ---------- */
function opt(side,type,name,value,checked,enabled,text,why){
  return '<label class="'+(enabled?'':'off')+'"'+(why?' title="'+esc(why)+'"':'')+'><input type="'+type+'" data-side="'+side+'" data-f="'+name+'" value="'+value+'"'
    +(type==='radio'?' name="'+side+'-'+name+'"':'')+(checked?' checked':'')+(enabled?'':' disabled')+'> '+text+'</label>';
}
/* three steps along the data flow (proposal v3.14, "分层评估"): questions -> each question's
   retrieval texts -> merge across questions */
function sidePicker(side){
  var c=S[side],solo=!c.fuse;   // without the original text exactly one phrasing is retrieved
  var step1='<div class="cfg-step">① 问题 <span class="meta">检索用哪些问题</span></div><div class="cfg-row">'
    +opt(side,'radio','d','0',!c.d,true,'不分解（原问题）')+opt(side,'radio','d','1',c.d,true,'<b>P3.1</b> 分解（子问题）')+'</div>';
  var step2='<div class="cfg-step">② 每个问题的检索文本 <span class="meta">对 ① 的每个问题分别处理</span></div>'
    +'<div class="cfg-row">'+opt(side,'checkbox','s','1',c.s,true,'<b>P3.2.0</b> 去编码（作用于下面勾选的全部文本）')+'</div>'
    +'<div class="cfg-row">'+opt(side,'checkbox','orig','1',c.fuse,!c.fuse||c.ph.size===1,'原句（'+(c.d?'该子问题':'原问题')+'）',
        c.fuse&&c.ph.size!==1?'不含原句时须恰好勾一种写法':'')
    +PH.map(function(m){var en=!(solo&&!c.ph.has(m));
      return opt(side,'checkbox','ph',m,c.ph.has(m),en,'<b>'+m+'</b> '+esc(MNAME[m]),en?'':'不含原句时只能勾一种写法');
    }).join('')+'</div>'
    +LANGM.map(function(m){var on=c.ph.has(m),v=c.lang[m]||'';
      return '<div class="cfg-sub'+(on?'':' off')+'">'+esc(MNAME[m])+' 的语言：'+['','d','qd'].map(function(x){
        return '<label class="'+(on?'':'off')+'"'+(on?'':' title="勾选 '+m+' 后可选"')+'><input type="radio" name="'+side+'-lang-'+m+'" data-side="'+side+'" data-f="lang" data-m="'+m+'" value="'+x+'"'
          +(on&&v===x?' checked':'')+(on?'':' disabled')+'> '+LNAME[x]+(x==='qd'?'（RRF）':x===''?'（V3.14）':'')+'</label>'}).join('')+'</div>'}).join('')
    +'<div class="cfg-sub meta">只勾 1 种写法：直接用它的文本检索，文本多于一段时（如"两种"）各检索一次后 RRF；勾 2 条以上：全部文本各检索一次后 RRF 融合（须含原句）。当前：'+esc(textsLabel(c))+'</div>';
  var step3='<div class="cfg-step">③ 跨问题合并 <span class="meta">'+(c.d?'把各子问题的排名合成返回顺序':'仅分解时可用')+'</span></div><div class="cfg-row">'
    +opt(side,'radio','merge','',c.d&&!c.merge,c.d,'不合并（逐个子问题评估）',c.d?'':'先选 P3.1 分解')
    +opt(side,'radio','merge','rr',c.merge==='rr',c.d,'轮流取',c.d?'':'先选 P3.1 分解')
    +opt(side,'radio','merge','rrf',c.merge==='rrf',c.d,'全局 RRF',c.d?'':'先选 P3.1 分解')+'</div>'
    +'<div class="cfg-sub">'+opt(side,'checkbox','o','1',c.o,!!c.merge,'原问题一路（原问题原句，带编码，不经过 ②；'+(c.merge==='rrf'?'作为一条排名参与融合':'每圈最后')+'）',c.merge?'':'选了合并方式才可开')+'</div>';
  var rr='<div class="cfg-step">④ <label class="off" title="搁置，未评估"><input type="checkbox" disabled> <b>P3.3</b> 重排</label> <span class="badge warn">搁置，未评估</span></div>';
  return '<div class="cfg"><div class="cfg-side">'+(side==='L'?'左侧':'右侧')+'</div>'+step1+step2+step3+rr+'</div>';
}
function renderPicker(){
  document.getElementById('qc-sides').innerHTML=sidePicker('L')+sidePicker('R');
  var lk=key(S.L),rk_=key(S.R),msg=document.getElementById('qc-msg');
  var card=document.querySelector('.pick');card.classList.remove('bad','warn');
  var base=!lk&&!rk_;
  if(!base&&lk===rk_){card.classList.add('bad');msg.innerHTML='<span class="badge bad">两侧相同</span> 两侧配置不同才可比较';return 'bad'}
  var missing=[[lk,'左侧'],[rk_,'右侧']].filter(function(x){return !AVAIL.has(x[0])});
  if(missing.length){card.classList.add('warn');
    msg.innerHTML='<span class="badge warn">不可用</span> '+missing.map(function(x){var why=(D.unavailable||{})[x[0]];
      return esc(x[1]+' '+label(parse(x[0]))+'：'+(why?why.join('；'):'未评估'))}).join('<br>');return 'missing'}
  [lk,rk_].forEach(function(k){load('facts',k);if(PAV.has(k))load('perturb',k)});
  var waiting=[lk,rk_].some(function(k){return PAV.has(k)&&window.QC_PERTURB[k]===undefined});
  if(!window.QC_FACTS[lk]||!window.QC_FACTS[rk_]||waiting){
    var gone=[lk,rk_].filter(function(k){return window.QC_FACTS[k]===null});
    msg.innerHTML=gone.length?'<span class="badge bad">缺数据文件</span> facts/'+esc((gone[0]||'none')+'.js'):'正在加载逐题事实…';
    return gone.length?'bad':'loading'}
  var pnote=!PT?'':pert()?'<div class="meta">扰动标注：第 0 次（冻结）与第 '+PT.generations.join('、')+' 次独立生成，共 '+GENS.length+' 次。</div>'
    :'<div class="meta">扰动标注：'+esc([[lk,'左侧'],[rk_,'右侧']].filter(function(x){return !PAV.has(x[0])}).map(function(x){return x[1]}).join('、'))+'没有独立生成的数据，不显示。</div>';
  if(base){msg.innerHTML='<span class="badge">V3.13 基线</span>';return 'ok'}
  msg.innerHTML='<span class="badge ok">比较</span> '+esc(label(S.L))+' <b>vs</b> '+esc(label(S.R))
    +'<div class="meta">两侧差别：'+esc(diffs(S.L,S.R).join('；'))+'</div>'+pnote;
  return 'ok';
}

/* ---------- module 1: better / worse ---------- */
function renderClass(base){
  if(base)return '<div class="meta">两侧全空时为 V3.13 基线，没有比较。在右侧勾选配置，即为相对 V3.13 的效果；两侧只差一个因素时，看到的就是这个因素单独的效果。</div>';
  var lk=key(S.L),rk_=key(S.R),all=[],T=[],C=[],worst={},both=merged(S.L)&&merged(S.R),P=pert(),st={better:0,worse:0,mixed:0,same:0};
  var body=D.combos.map(function(c){
    var j=judge(lk,rk_,c.label),ids=Object.keys(j),n=counts(ids.map(function(id){return j[id]}));
    var sn={better:0,worse:0,mixed:0,same:0},gc=[];
    if(P){ids.forEach(function(id){var vs=gverdicts(lk,rk_,c.label,id);if(stable(vs)){sn[vs[0]]++;st[vs[0]]++}});
      gc=GENS.map(function(g){return counts(ids.map(function(id){return verdict(vec(nOf(lk,g,c.label,id)),vec(nOf(rk_,g,c.label,id)))}))})}
    var lf=facts(lk,c.label),rf=facts(rk_,c.label);
    var reg=both?ids.filter(function(id){return regress(lf[id],rf[id]).length}):[];
    reg.forEach(function(id){worst[id]=worst[id]||{n:0,c:c.label};worst[id].n++});
    ids.forEach(function(id){all.push(j[id]);(c.table_return_unit==='table'?T:C).push(j[id])});
    var btn=function(v){return n[v]&&v!=='same'?'<button type="button" class="lnk '+(v==='better'?'up':v==='worse'?'down':'')+'" data-act="focus" data-combo="'+c.label+'" data-v="'+v+'">'+n[v]+'</button>':''+n[v]};
    var cell=function(v){return '<td class="num"'+(P?' title="'+GENS.length+' 次生成的题数：'+span(gc.map(function(x){return x[v]}))+'"':'')+'>'+btn(v)
      +(P?'<div class="meta">稳定 '+sn[v]+'</div>':'')+'</td>'};
    return '<tr>'+comboHead(c)+ORDER.map(cell).join('')
      +(both?'<td class="num">'+(reg.length?'<button type="button" class="lnk down" data-act="focus" data-combo="'+c.label+'" data-v="regress">'+reg.length+'</button>':'0')+'</td>':'')+'</tr>';
  }).join('');
  var head='<tr><th>组合</th><th class="num">变好 ▲</th><th class="num">变差 ▼</th><th class="num">各有优势 ◆</th><th class="num">不变</th>'
    +(both?'<th class="num" title="验收 K（'+ACC.join('、')+'）中任一档由完整覆盖变为不完整的题">最坏退化</th>':'')+'</tr>';
  var wl=Object.keys(worst).sort();
  return '<div class="card"><b>13 种组合合计（题次）：</b>'+countText(counts(all))
    +(P?'<div class="meta">其中在 '+GENS.length+' 次生成下判定都相同（稳定）的：'+countText(st)+'。每格下方为稳定的题数，悬停看 '+GENS.length+' 次生成的题数范围。</div>':'')
    +'<div class="meta">返回整表（7 种）：'+countText(counts(T))+'。返回命中 chunk（6 种）：'+countText(counts(C))+'。</div>'
    +(both?'<div class="meta">最坏退化的题：'+(wl.length?wl.map(function(id){return '<button type="button" class="lnk down" data-act="goto" data-case="'+esc(id)+'" data-combo="'+worst[id].c+'">'+esc(id)+'</button>（'+worst[id].n+' 种组合）'}).join('，'):'无')+'</div>'
      :'<div class="meta">最坏退化不计算：'+unmergedNote().replace(/<[^>]+>/g,'')+'不合并时信息项的名次在它对应的子问题各路中计算（未对应的按全部子问题），对应关系见模块 __PROC__。</div>')
    +'<div class="meta">13 种组合共用同一批题，组合数只表示一致程度，不作投票；是否采用由助手按拍板问题 5 写明理由、你拍板（决策 015）。判定在冻结的规划（第 0 次生成）上得出'+(P?'，稳定性见每格下方。':'，待扰动测试核对。')+'</div></div>'
    +'<table>'+head+body+'</table>';
}

/* ---------- module 2: heat map ---------- */
function renderHeat(base){
  if(base)return '<div class="meta">两侧全空时没有比较。</div>';
  var lk=key(S.L),rk_=key(S.R),out=[],doc=null,dirs={},P=pert();
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
      var vs=P?gverdicts(lk,rk_,c.label,id):null,un=vs&&!stable(vs);
      return '<td class="hm center '+VC[v]+sel+(un?' unst':'')+'" title="'+esc(VN[v]+'：'+l.map(rk).join('·')+' → '+r.map(rk).join('·')
        +(vs?'；'+GENS.length+' 次生成：'+vs.map(function(x){return VS[x]}).join(' ')+(un?'（不稳定）':'（稳定）'):''))+'" data-act="cell" data-case="'+esc(id)+'" data-combo="'+c.label+'">'+VS[v]+(un?'<sup>?</sup>':'')+'</td>';
    }).join('');
    out.push('<tr><td><b>'+esc(id)+'</b><div class="meta">'+esc(q.question)+'</div></td>'+cells+'</tr>');
    if(picked)out.push('<tr><td colspan="'+(D.combos.length+1)+'">'+cellDetail(id,S.cell.c,lk,rk_)+'</td></tr>');
  });
  var bar=S.focus?'<div class="card meta">只显示 <b>'+esc(S.focus.combo)+'</b> 中'+(S.focus.v==='regress'?'最坏退化':VN[S.focus.v])+'的题。<button type="button" class="expand-btn" data-act="unfocus">显示全部</button></div>':'';
  return bar+(shown?'<table>'+head+out.join('')+'</table>':'<div class="meta">没有符合条件的题</div>');
}
function phr(i){var p=D.phrasing_labels[i].split(':');return (KNAME[p[0]]||p[0])+(p[1]?'（'+p[1]+'）':'')}
function bestPhrasing(ranks){
  var best=(ranks||[]).slice().sort(function(a,b){return a[1]-b[1]})[0];
  return best?esc(phr(best[0]))+' 第 '+best[1]+' 名':'';
}
function source(entry,c){   // entry of a merged configuration: [question id, round, [[phrasing, rank]...]]
  if(!entry)return '';
  var q=entry[0],r=entry[1],ranks=entry[2]||[];
  if(!q||!c.d)return '<span title="'+esc(ranks.map(function(x){return phr(x[0])+' 第 '+x[1]+' 名'}).join('；'))+'">原问题'+(ranks.length?' · '+bestPhrasing(ranks):'')+'</span>';
  var who=q==='original'?'原问题一路':q;
  var all=ranks.map(function(x){return phr(x[0])+' 第 '+x[1]+' 名'}).join('；');
  return '<span title="'+esc(all)+'">'+esc(who)+(c.merge==='rr'?' · 第 '+r+' 圈':'')+(ranks.length?' · '+bestPhrasing(ranks):'')+'</span>';
}
function cellDetail(id,combo,lk,rk_){
  var lf=facts(lk,combo)[id],rf=facts(rk_,combo)[id],units=D.units[combo],q=CASE[id];
  var held=(D.holds[combo]||{})[id]||{},xs={};q.excerpts.forEach(function(x){xs[x.excerpt_id]=x});
  var l=vec(lf.n),r=vec(rf.n),v=verdict(l,r),both=merged(S.L)&&merged(S.R);
  var items=q.groups.map(function(g,i){
    return '<tr><td>'+esc(g.group_id+' '+g.information_item)+'</td><td class="num">'+arrow(lf.f[i],rf.f[i])+'</td><td class="num">'+arrow(lf.n[i],rf.n[i])+'</td></tr>';
  }).join('');
  var head='<div class="card"><b>'+esc(combo)+'</b> <span class="badge '+VB[v]+'">'+VN[v]+'</span> 凑齐向量 '+vecHtml(l,r)
    +(v==='mixed'?'<div class="meta">'+leadText(l,r)+'</div>':'')+(pert()?genDetail(id,combo,lk,rk_):'');
  var itemTable='<div class="scroll"><table><tr><th>信息项</th><th class="num">首块名次 左 → 右</th><th class="num">凑齐名次 左 → 右</th></tr>'+items+'</table></div>';
  if(!both)return head+'</div>'+itemTable+unmergedNote()+'<div class="meta">不合并一侧的名次是信息项在它对应的子问题各路中的名次；各子问题的排名见模块 __PROC__（选这道题和这个组合）。</div>';
  var proc=window.QC_PROCESS[rk_];if(proc===undefined)load('process',rk_);
  var ids=Object.keys(lf.e).sort(function(a,b){return rf.e[a]-rf.e[b]});
  var rows=ids.map(function(i){
    var u=units[+i],a=lf.e[i],b=rf.e[i];
    var ex=(held[i]||[]).map(function(x){var e=xs[x];return '<span class="badge" title="'+esc(e?e.text:'')+'">'+esc(x)+(e&&e.groups.length?' · '+esc(e.groups.join('/')):'')+'</span>'}).join(' ');
    var src=b<=20?(proc?source(proc[combo][id][b-1],S.R):'加载中…'):'<span class="meta">第 20 名之后</span>';
    return '<tr><td class="nw" title="'+esc(u.text)+'">'+(u.table?'整表 ':'')+esc(short(u.key))+'<div class="meta">第 '+u.pages.join(',')+' 页</div></td>'
      +'<td>'+ex+'</td><td class="num">'+arrow(a,b)+'</td><td>'+src+'</td></tr>';
  }).join('');
  var flips=ACC.map(function(k){
    var a=complete(lf,k),b=complete(rf,k);
    return a===b?null:'K='+k+' '+(b?'<span class="up">由否变是</span>':'<span class="down">由是变否</span>');
  }).filter(Boolean);
  return head+'<div class="meta">验收 K 下完整覆盖：'+(flips.length?flips.join('，'):'没有翻转')+'</div></div>'+itemTable
    +'<div class="scroll"><table><tr><th>证据单元</th><th>承载的 excerpt · 信息项</th><th class="num">名次 左 → 右</th><th>右侧来源（'+(S.R.d?'子问题'+(S.R.merge==='rr'?' · 第几圈':'')+' · ':'')+'名次最好的写法；悬停看各写法）</th></tr>'+rows+'</table></div>'
    +'<div class="meta">绿色为右侧提前、红色为推后。悬停证据单元看正文开头，悬停 excerpt 看原文。</div>';
}

function genDetail(id,combo,lk,rk_){
  var rows=GENS.map(function(g){
    var l=vec(nOf(lk,g,combo,id)),r=vec(nOf(rk_,g,combo,id)),v=verdict(l,r);
    var sub=g&&(S.L.d||S.R.d)?(PT.plans[String(g)][id]||{}).decomposed:null;
    return '<tr><td class="nw">第 '+g+' 次'+(g?'':'（冻结）')+'</td><td><span class="badge '+VB[v]+'">'+VN[v]+'</span> '+vecHtml(l,r)+'</td><td class="meta">'
      +(sub?esc(sub.join(' ｜ ')):g?'':'子问题见模块 __PROC__')+'</td></tr>'}).join('');
  return '<div class="scroll"><table><tr><th>生成</th><th>判定 · 凑齐向量 左 → 右</th><th>子问题（开 P3.1 时，第 1–'+PT.generations.length+' 次）</th></tr>'+rows+'</table></div>';
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
  if(!merged(S.L)||!merged(S.R))return unmergedNote();
  var k=S.km,lk=key(S.L),rk_=key(S.R);
  var cost=[['retrievals','每题检索次数',1,'检索器调用次数：问题数 × 每个问题的检索文字数（RRF 融合时为原句加启用的写法，不融合时为 1），开原问题一路时加 1'],
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
    var lf=facts(lk,c.label),l=totals(lf,k,units),P=pert();
    var count=function(metric){
      var f=flipped(lf,rf,k,TEST[metric]);
      var ds=P?perGen(function(g){var a=view(lk,g,c.label),b=view(rk_,g,c.label),t=TEST[metric];
        return Object.keys(b).filter(function(id){return t(b[id],k)}).length-Object.keys(a).filter(function(id){return t(a[id],k)}).length}):null;
      var pr=ds?'<div class="meta" title="'+GENS.length+' 次生成的差值">Δ '+span(ds)+'</div>':'';
      var btn=function(ids,d,cls,sym){return ids.length?'<button type="button" class="lnk '+cls+'" data-act="flip" data-combo="'+c.label+'" data-metric="'+metric+'" data-dir="'+d+'">'+sym+ids.length+'</button>':'<span class="same">'+sym+'0</span>'};
      return '<td class="num'+(ds&&flips(ds)?' pgray':'')+'">'+l[metric]+' → '+r[metric]+'<div>（'+btn(f[0],1,'up','+')+' / '+btn(f[1],-1,'down','−')+'）</div>'+pr+'</td>';
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
function curve(base,count){
  var lk=key(S.L),rk_=key(S.R);
  var head='<tr><th>组合</th>'+D.ks.map(function(k){return '<th class="num'+(ACC.indexOf(k)>=0?' acc':'')+'">K='+k+'</th>'}).join('')+'</tr>';
  var rows=D.combos.map(function(c){
    return '<tr>'+comboHead(c)+D.ks.map(function(k){
      var r=count(facts(rk_,c.label),k);
      if(base)return '<td class="num">'+r+'</td>';
      var l=count(facts(lk,c.label),k),v=r-l;
      var ds=pert()?perGen(function(g){return count(view(rk_,g,c.label),k)-count(view(lk,g,c.label),k)}):null,gr=ds&&flips(ds);
      return '<td class="num'+(gr?' pgray':'')+'"'+(ds?' title="'+GENS.length+' 次生成的差值：'+span(ds)+'"':'')+'><div class="meta">'+l+' → '+r+'</div><div class="'+(v>0?'up':v<0?'down':'same')+'">'+sign(v,0)+'</div>'
        +(ds?'<div class="meta">'+span(ds)+'</div>':'')+'</td>';
    }).join('')+'</tr>';
  }).join('');
  return '<table>'+head+rows+'</table>';
}
function renderCurve(base){
  if(!merged(S.L)||!merged(S.R))return unmergedNote();
  var byCase=function(fs,k){return Object.values(fs).filter(function(f){return complete(f,k)}).length};
  var byItem=function(fs,k){return Object.values(fs).reduce(function(a,f){return a+f.n.filter(function(x){return x!==null&&x<=k}).length},0)};
  return '<h3>完整覆盖题数（共 '+N+' 题）</h3>'+curve(base,byCase)
    +'<h3 style="margin-top:12px">已凑齐的信息项（共 '+GROUPS+' 项）</h3>'+curve(base,byItem);
}

/* ---------- module 5: query process ---------- */
function mappingHtml(id){
  var m=(D.decomposition||{})[id],q=CASE[id],plan=D.plans[id].decomposed;
  if(!m)return '<div class="meta">没有对应标注。</div>';
  var text={};plan.questions.forEach(function(x){text[x.id]=x.text});
  var rows=q.groups.map(function(g){var qs=m.mapping[g.group_id]||[];
    return '<tr><td>'+esc(g.group_id+' '+g.information_item)+'</td><td>'+(qs.length?qs.map(function(x){return '<div><b>'+x+'</b> '+esc(text[x]||'')+'</div>'}).join(''):'<span class="down">分解遗漏</span>（按全部子问题计算）')+'</td></tr>'}).join('');
  var notes=[];
  if(m.extra.length)notes.push('多余子问题：'+m.extra.map(function(x){return x+' '+text[x]}).join('；'));
  if(m.multi.length)notes.push('一问多项：'+m.multi.join('、'));
  if(m.lost_qualifier.length)notes.push('丢失限定条件：'+m.lost_qualifier.join('、'));
  if(m.note)notes.push(m.note);
  return '<div class="scroll"><table><tr><th>信息项</th><th>对应的子问题</th></tr>'+rows+'</table></div>'
    +notes.map(function(n){return '<div class="meta">'+esc(n)+'</div>'}).join('');
}
function unitRow(n,i,units,held,src){
  var u=units[i],ev=held[i];
  return '<tr><td class="num">'+n+'</td><td class="nw">'+(u.table?'整表 ':'')+esc(short(u.key))+'<div class="meta">第 '+u.pages.join(',')+' 页 · '+u.tokens+' token</div></td>'
    +'<td>'+(ev?'<span class="badge ok">'+esc(ev.join('、'))+'</span> ':'')+'<span class="meta">'+esc(u.text.slice(0,120))+'</span></td><td>'+src+'</td></tr>';
}
function renderProcess(){
  var cfg=S.R,rk_=key(cfg),q=CASE[S.pq],combo=S.pc,plan=D.plans[q.case_id];
  var pick='<div class="seg">题 <select data-act="pq">'+D.cases.map(function(x){return '<option value="'+esc(x.case_id)+'"'+(x.case_id===S.pq?' selected':'')+'>'+esc(x.case_id)+'</option>'}).join('')+'</select>'
    +' 组合 <select data-act="pc">'+D.combos.map(function(c){return '<option'+(c.label===combo?' selected':'')+'>'+esc(c.label)+'</option>'}).join('')+'</select>'
    +' <span class="meta">右侧配置：'+esc(label(cfg))+'</span></div>';
  var scope=plan.scope;
  var head='<div class="card"><b>'+esc(q.case_id)+'</b> '+esc(q.question)+'<div class="meta">检索范围：'+esc(scope.documents.length?q.document_name:'全库')
    +' · 文档语言：'+esc(scope.languages.join('、'))+' · 标识编码：'+esc(scope.codes.join('、')||'无')+'</div></div>';
  var planHtml;
  if(!rk_)planHtml='<div class="meta">V3.13：原问题检索一次，没有规划。</div>';
  else{
    var p=cfg.d?plan.decomposed:plan.plain,kinds={};
    if(cfg.fuse)kinds.question=1;
    cfg.ph.forEach(function(m){kinds[KIND[m]]=cfg.lang[m]||'q'});
    var shown=function(f){var v=kinds[f.kind];if(!v||f.kind==='question')return false;
      if(f.kind!=='rewrite'&&f.kind!=='step_back')return true;
      return v==='qd'||(v==='q')===!f.language};
    planHtml=(cfg.d?'<div class="meta">分解为 '+p.questions.length+' 个子问题；'+(cfg.merge?'合并方式：'+MERGE[cfg.merge]+(cfg.o?'，另有原问题一路（'+(cfg.merge==='rr'?'每圈最后':'作为一条排名参与融合')+'）':'')
        :'不合并：各子问题的排名分别列出，没有返回顺序')+'。</div>':'<div class="meta">不分解：原问题是唯一的问题。</div>')
      +(cfg.ph.size?'<div class="meta">'+(cfg.fuse?'每个问题的原句与启用的写法各检索一次，按 RRF 融合。':'每个问题只用 '+esc(phName(cfg,phs(cfg)[0]))+' 检索，代替原句；多于一段文字时各检索一次后 RRF 融合。')+'</div>':'')
      +(cfg.s?'<div class="meta">去编码：下列文字在检索前删去标识编码 '+esc(scope.codes.join('、')||'（无）')+'（原问题一路不删）。</div>':'')
      +(p.degraded.length?'<div class="meta down">规划降级：'+esc(p.degraded.join('；'))+'</div>':'')
      +p.questions.map(function(x){
        return '<div class="item"><b>'+esc(x.id)+'</b> '+esc(x.text)+(kinds.question?'':' <span class="meta">（原句不检索）</span>')+x.phrasings.filter(shown).map(function(f){
          return '<div class="meta">'+esc(KNAME[f.kind]+(f.language?'（'+f.language+'）':''))+'：'+esc(f.text)+'</div>'}).join('')+'</div>';
      }).join('');
  }
  var proc=window.QC_PROCESS[rk_];
  if(proc===undefined){load('process',rk_);return pick+head+planHtml+'<div class="meta">正在加载来源明细…</div>'}
  if(proc===null)return pick+head+planHtml+'<div class="meta">没有找到 process/'+esc((rk_||'none')+'.js')+'</div>';
  var f=facts(rk_,combo)[q.case_id],units=D.units[combo],held=(D.holds[combo]||{})[q.case_id]||{};
  var c=f.c,body;
  if(!merged(cfg)){
    var text={};plan.decomposed.questions.forEach(function(x){text[x.id]=x.text});
    var m=(D.decomposition||{})[q.case_id],of={};
    if(m)q.groups.forEach(function(g){(m.mapping[g.group_id]||[]).forEach(function(x){(of[x]=of[x]||[]).push(g.group_id)})});
    var items=q.groups.map(function(g,i){return '<tr><td>'+esc(g.group_id+' '+g.information_item)+'</td><td class="num">'+rk(f.f[i])+'</td><td class="num">'+rk(f.n[i])+'</td></tr>'}).join('');
    body='<h3 style="margin-top:12px">信息项的名次（在对应的子问题各路中）</h3><div class="scroll"><table><tr><th>信息项</th><th class="num">首块名次</th><th class="num">凑齐名次</th></tr>'+items+'</table></div>'
      +proc[combo][q.case_id].routes.map(function(r){
        var rows=r[1].map(function(x,n){return unitRow(n+1,x[0],units,held,'<span title="'+esc(x[1].map(function(y){return phr(y[0])+' 第 '+y[1]+' 名'}).join('；'))+'">'+bestPhrasing(x[1])+'</span>')}).join('');
        return '<h3 style="margin-top:12px">'+esc(r[0])+' 的排名（前 '+r[1].length+' 个）</h3><div class="meta">'+esc(text[r[0]]||'')+' · 对应信息项：'+esc((of[r[0]]||[]).join('、')||'无')+'</div>'
          +'<div class="scroll"><table><tr><th class="num">#</th><th>单元</th><th>内容（绿色为证据单元及其 excerpt）</th><th>名次最好的写法（悬停看各写法）</th></tr>'+rows+'</table></div>';
      }).join('');
  }else{
    var rows=f.u.map(function(i,n){return unitRow(n+1,i,units,held,source(proc[combo][q.case_id][n],cfg))}).join('');
    body='<h3 style="margin-top:12px">返回顺序（前 '+f.u.length+' 个）</h3><div class="scroll"><table><tr><th class="num">#</th><th>单元</th><th>内容（绿色为证据单元及其 excerpt）</th><th>来源（'
      +(cfg.d?'子问题'+(cfg.merge==='rr'?' · 第几圈':'')+' · ':'')+'名次最好的写法；悬停看各写法名次）</th></tr>'+rows+'</table></div>';
  }
  return pick+head+'<h3>规划</h3>'+planHtml
    +(cfg.d?'<h3 style="margin-top:12px">子问题与信息项的对应</h3>'+mappingHtml(q.case_id):'')
    +body
    +'<div class="meta">本题成本：检索 '+c[0]+' 次 · 检索 '+fmt(c[1]*1000,0)+' ms · 规划 LLM '+c[2]+' 次、'+fmt(c[3],1)+' 秒、$'+fmt(c[4],4)+'</div>';
}

/* ---------- wiring ---------- */
function render(){
  writeHash();
  var state=renderPicker(),base=!key(S.L)&&!key(S.R);
  var R={class:renderClass,heat:renderHeat,matrix:renderMatrix,curve:renderCurve,process:renderProcess};
  document.querySelectorAll('[data-module]').forEach(function(sec){
    var body=sec.querySelector('.qc-body'),m=sec.dataset.module;
    body.innerHTML=state==='loading'?'<div class="meta">加载中…</div>':state!=='ok'?'<div class="meta">无结果</div>':R[m](base);
  });
  document.querySelectorAll('.seg[data-k] button').forEach(function(b){b.classList.toggle('on',+b.dataset.v===S.km)});
  document.querySelectorAll('.seg[data-doc] button').forEach(function(b){b.classList.toggle('on',b.dataset.v===S.doc)});
  var all=document.getElementById('qc-all');if(all)all.checked=S.all;
}
function scrollTo(id){var el=document.getElementById(id);if(el)el.scrollIntoView({behavior:'smooth',block:'start'})}
function setOption(box){
  var c=S[box.dataset.side],f=box.dataset.f;
  if(f==='d'){c.d=box.value==='1';if(!c.d){c.merge=null;c.o=false}}
  else if(f==='s')c.s=box.checked;
  else if(f==='orig')c.fuse=box.checked;
  else if(f==='merge'){c.merge=box.value||null;if(!c.merge)c.o=false}
  else if(f==='o')c.o=box.checked;
  else if(f==='ph'){if(box.checked)c.ph.add(box.value);else{c.ph.delete(box.value);delete c.lang[box.value]}if(c.ph.size!==1)c.fuse=true}
  else if(f==='lang'){if(box.value)c.lang[box.dataset.m]=box.value;else delete c.lang[box.dataset.m]}
}
document.addEventListener('change',function(e){
  var t=e.target;
  if(t.dataset&&t.dataset.side&&t.dataset.f){setOption(t);S.cell=null;S.flip=null;S.focus=null;render();return}
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
  if(act==='focus'){S.focus={combo:a.dataset.combo,v:a.dataset.v};S.cell=null;render();scrollTo('m-heat')}
  else if(act==='unfocus'){S.focus=null;render()}
  else if(act==='cell'){var same=S.cell&&S.cell.q===a.dataset.case&&S.cell.c===a.dataset.combo;
    S.cell=same?null:{q:a.dataset.case,c:a.dataset.combo};S.pq=a.dataset.case;S.pc=a.dataset.combo;render()}
  else if(act==='flip'){var f={combo:a.dataset.combo,metric:a.dataset.metric,dir:+a.dataset.dir};
    S.flip=S.flip&&S.flip.combo===f.combo&&S.flip.metric===f.metric&&S.flip.dir===f.dir?null:f;render()}
  else if(act==='goto'){var c=a.dataset.combo||S.pc;S.focus=null;S.all=true;S.cell={q:a.dataset.case,c:c};S.pq=a.dataset.case;S.pc=c;render();scrollTo('m-heat')}
});
document.getElementById('qc-reset').addEventListener('click',function(){S.L=blank();S.R=blank();S.cell=null;S.flip=null;S.focus=null;render()});
window.addEventListener('hashchange',function(){readHash();render()});
readHash();render();
})();
"""


def render(data: dict[str, Any], analysis: str | None = None) -> str:
    """`analysis`: an HTML fragment shown as module 1 (the written analysis of a comparison, made by an
    experiment script next to the data), or None."""
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
    first = 2 if analysis else 1
    nav = ('<div class="card meta">模块：' + ('<a href="#m-analysis">1 各措施的单独效果（分析结论）</a> · ' if analysis else '')
           + ' · '.join(f'<a href="#m-{mid}">{first + i} {name}</a>' for i, (mid, name) in enumerate(
               (("class", "变好、变差"), ("heat", "逐题变化"), ("matrix", "指标矩阵"), ("curve", "K 曲线"), ("process", "查询过程")))) + '</div>')
    num = lambda i: first + i
    body = (
        f'<h1>查询侧对比 · {esc(data["name"])}</h1>'
        f'<div class="sub">{esc(data["created_at"])} · 数据集 {esc(data["dataset"]["version"])}，{n} 道可回答题 · '
        f'{len(data["combos"])} 种组合（混合检索、文档过滤开启）· 已评估 {len(data["available"])} 种配置'
        f'（检索文本 32 种状态：V3.14 的 20 种，加 V3.14.1 改写、step-back 用文档语言或两种的 12 种；× 去编码 2 × 分解 6 种状态；P3.3 搁置）'
        f'· 规划：{esc(data["plan_model"])}，拆解提示词 v9、写法提示词 v8、文档语言写法提示词 lang v1，冻结的第 0 次生成</div>'
        + nav
        + (f'<section id="m-analysis"><h2>1. 各措施的单独效果（分析结论）</h2>{analysis}</section>'
           '<h2>对比工具</h2>' if analysis else '')
        + '<div class="card pick"><div id="qc-sides" class="cfg-grid"></div>'
        '<div class="pick-foot"><span id="qc-msg"></span> <button type="button" id="qc-reset" class="expand-btn">清空两侧</button></div>'
        '<div class="meta">两侧全空显示 V3.13；否则两侧配置不同即可比较，两侧只差一个因素时看到的就是它单独的效果。'
        '每种组合只与它自己比较。分解不选合并方式（不合并）时没有返回顺序，只用于评估：信息项的名次在它对应的子问题各路中计算。</div></div>'
        + section("class", f"{num(0)}. 变好、变差与最坏退化", "",
                  '一题的判定（提案 v3.14 拍板问题 3）：各信息项的凑齐名次（某个方案全部证据到位时的名次）升序排成凑齐向量，两侧逐位比较。'
                  '右侧每一位都不晚于左侧且至少一位更早为变好，反之为变差，有早有晚为各有优势，完全相同为不变；与 K 无关，不设阈值。'
                  '最坏退化：验收 K 中任一档由完整覆盖变为不完整（任一侧不合并时不计算）。点题数到逐题变化看是哪些题。')
        + section("heat", f"{num(1)}. 逐题变化", f'{doc_bar}<label class="meta"><input type="checkbox" id="qc-all"> 显示全部题（默认只显示在任一组合中有变化的题）</label>',
                  '每格为该题在该组合下的判定：▲ 变好（绿）、▼ 变差（红）、◆ 各有优势（黄）、· 不变；悬停看两侧凑齐向量。'
                  '点一格展开：凑齐向量、各有优势时各自领先的 K 区间、每个信息项的首块名次与凑齐名次、每个证据单元的名次与右侧来源（两侧都有返回顺序时）。')
        + section("matrix", f"{num(2)}. 整体指标矩阵", k_bar,
                  f'完整覆盖：全部信息项凑齐；命中：至少一个信息项凑齐。每格为左侧 → 右侧，括号内为选定 K 下由否变是（+）/ 由是变否（−）的题数，点击列出这些题。'
                  f'token 与成本按 {n} 道可回答题平均；悬停列名看成本的定义，悬停组合名看入库成本（左右两侧相同）。任一侧不合并时不显示。')
        + section("curve", f"{num(3)}. K 曲线", "",
                  '上表为该 K 下完整覆盖的题数，下表为该 K 下已凑齐的信息项总数；蓝色表头为验收用的 K。任一侧不合并时不显示。')
        + section("process", f"{num(4)}. 查询过程", "",
                  '右侧配置下一道题的检索范围、规划（只列该配置启用的写法）、子问题与信息项的对应（开 P3.1 时）与前 20 个返回单元的来源；'
                  '不合并时分别列出各子问题的排名。来源明细按配置单独存放，切换时加载。')
        + (f'<div class="card meta">扰动标注（模块 {num(0)}–{num(3)}）：两侧都有独立生成的数据时，每题在第 0 次（冻结）与各次独立生成下分别判定，都相同为稳定；'
           f'模块 {num(0)} 每格下方为稳定的题数，模块 {num(1)} 不稳定的格标 ?，模块 {num(2)}、{num(3)} 写出各次生成的差值范围，范围内可能反号的格标灰。'
           f'每次生成的判定与子问题在模块 {num(1)} 的展开中列出。</div>' if data.get("perturbation") else
           f'<div class="card meta">扰动：3 次独立生成作为模块 {num(0)}–{num(3)} 上的标注层，扰动测试完成后加入。</div>')
        + f'<script type="application/json" id="qc-data">{_embed(data)}</script>'
        + f'<script>{SCRIPT.replace("__ACC__", json.dumps(list(ACCEPTANCE_KS))).replace("__PROC__", str(num(4)))}</script>')
    return page(f"查询侧对比 · {data['name']}", body, root="../../", crumbs=[("报告首页", "../../index.html")])


def _embed(data: dict[str, Any]) -> str:
    """JSON safe inside a <script> element."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _script(name: str, key: str, value: Any) -> str:
    return f"window.{name}=window.{name}||{{}};window.{name}[{json.dumps(key)}]=" + _embed(value) + ";\n"


def _write_facts(folder: Path, data: dict[str, Any]) -> None:
    """facts/<config>.js, and perturb/<config>.js with the independent generations' completion ranks."""
    for sub, name, configs in (("facts", "QC_FACTS", data["configs"]),
                               ("perturb", "QC_PERTURB", data.get("perturbation", {}).get("configs", {}))):
        shutil.rmtree(folder / sub, ignore_errors=True)
        for key, facts in configs.items():
            write_atomic(folder / sub / script_file(key), _script(name, key, facts))


def _shared(data: dict[str, Any]) -> dict[str, Any]:
    """The page's embedded data: the configurations' keys instead of their facts."""
    shared = {k: v for k, v in data.items() if k not in ("configs", "perturbation")}
    shared["available"] = sorted(data["configs"])
    if "perturbation" in data:
        shared["perturbation"] = {**{k: v for k, v in data["perturbation"].items() if k != "configs"},
                                  "available": sorted(data["perturbation"]["configs"])}
    return shared


def write_query_page(workspace: Workspace, data: dict[str, Any]) -> Path:
    """data.json (machine data: everything but the process detail), index.html with the data every
    configuration shares embedded, facts/<config>.js and process/<config>.js."""
    folder = query_dir(workspace) / data["name"]
    main = {k: v for k, v in data.items() if k != "process"}
    write_atomic(folder / "data.json", _embed(main) + "\n")
    _write_facts(folder, main)
    if "process" in data:   # without it the process files already written are kept
        shutil.rmtree(folder / "process", ignore_errors=True)
    for key, detail in data.get("process", {}).items():
        write_atomic(folder / "process" / script_file(key), _script("QC_PROCESS", key, detail))
    write_assets(workspace.reports)
    write_atomic(folder / "index.html", render(_shared(main), _analysis(folder)))
    return folder


def _analysis(folder: Path) -> str | None:
    path = folder / "analysis.html"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def rerender_query_pages(workspace: Workspace) -> int:
    folder = query_dir(workspace)
    count = 0
    for path in sorted(folder.glob("*/data.json")) if folder.is_dir() else []:
        data = read_json(path)
        _write_facts(path.parent, data)
        write_atomic(path.parent / "index.html", render(_shared(data), _analysis(path.parent)))
        count += 1
    return count
