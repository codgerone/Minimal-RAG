"""Shared page shell. Style and script live in reports/assets/ so they can change without re-ingesting."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Iterable

CSS = """
:root{--bg:#f6f7f9;--panel:#fff;--text:#1d2129;--muted:#5f6b7a;--line:#dde2e8;
--accent:#2f6fde;--ok:#1a7f43;--ok-bg:#e5f5ec;--bad:#c23b32;--bad-bg:#fdeceb;
--warn:#a35d00;--warn-bg:#fff3dc;--code:#f1f3f6;--hl:#fff7cc;color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px 60px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:22px;margin:4px 0 2px}h2{font-size:18px;margin:28px 0 10px}
h3{font-size:15px;margin:0 0 8px}
.crumbs{font-size:13px;color:var(--muted)}.sub{color:var(--muted);font-size:13px;margin-bottom:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin:12px 0}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.tile .v{font-size:24px;font-weight:600}.tile .k{font-size:13px;color:var(--muted)}
.tile .d{font-size:12px}
.up{color:var(--ok)}.down{color:var(--bad)}.same{color:var(--muted)}
.badge{display:inline-block;font-size:12px;line-height:18px;padding:0 7px;border-radius:9px;
border:1px solid var(--line);color:var(--muted);white-space:nowrap}
.badge.ok{background:var(--ok-bg);color:var(--ok);border-color:transparent}
.badge.bad{background:var(--bad-bg);color:var(--bad);border-color:transparent}
.badge.warn{background:var(--warn-bg);color:var(--warn);border-color:transparent}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:820px){.cols{grid-template-columns:1fr}}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
pre,.text{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--code);border-radius:6px;
padding:8px 10px;margin:6px 0;font:13px/1.55 ui-monospace,Consolas,"Microsoft YaHei",monospace}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid var(--line);padding:4px 7px;text-align:left;vertical-align:top}
th{background:var(--code);font-weight:600}td.hdr{background:#e3edff;font-weight:600;box-shadow:inset 0 0 0 1px #8fb0ee}td.num,th.num{text-align:right;white-space:nowrap}
.scroll{overflow-x:auto;max-width:100%}
details{margin:6px 0}summary{cursor:pointer;color:var(--muted);font-size:13px}
.item{border-top:1px solid var(--line);padding:8px 0}.item:first-child{border-top:0}
.meta{font-size:13px;color:var(--muted)}
.winner{outline:2px solid var(--ok);outline-offset:-1px}
.filters{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.filters button{font:inherit;font-size:13px;padding:3px 10px;border-radius:14px;cursor:pointer;
border:1px solid var(--line);background:var(--panel);color:var(--text)}
.filters button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.hidden{display:none}
.toggle{border:0;background:none;padding:0 2px;font:inherit;font-size:13px;color:var(--accent);cursor:pointer}
.toggle:hover{text-decoration:underline}
tr.winner-row td{background:var(--ok-bg)}tr.winner-row{outline:2px solid var(--ok);outline-offset:-2px}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0 4px;border-bottom:1px solid var(--line)}
.tabs a{padding:6px 12px;border:1px solid var(--line);border-bottom:0;border-radius:8px 8px 0 0;
background:var(--code);color:var(--text);font-size:14px}
.tabs a.on{background:var(--panel);font-weight:600;position:relative;top:1px}
.tabs a:hover{text-decoration:none}
.table-tools{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:4px 0 2px;
font-size:12px;color:var(--muted)}
.expand-btn{font:inherit;font-size:12px;padding:1px 9px;border-radius:12px;cursor:pointer;
border:1px solid var(--line);background:var(--panel);color:var(--accent);white-space:nowrap}
.expand-btn:hover{border-color:var(--accent)}
.overlay{position:fixed;inset:0;z-index:100;background:var(--bg);display:flex;flex-direction:column}
.overlay-head{display:flex;align-items:center;gap:12px;padding:10px 16px;background:var(--panel);
border-bottom:1px solid var(--line)}
.overlay-head .title{font-weight:600}.overlay-head .meta{flex:1}
.overlay-head button{font:inherit;font-size:13px;padding:3px 12px;border-radius:14px;cursor:pointer;
border:1px solid var(--line);background:var(--panel);color:var(--text)}
.overlay-body{flex:1;overflow:auto;padding:16px}
.overlay-body table{width:max-content;min-width:100%;font-size:14px;background:var(--panel)}
.overlay-body td{min-width:80px;max-width:420px}
body.no-scroll{overflow:hidden}
"""

SCRIPT = """
document.addEventListener('click',function(e){
  var t=e.target.closest('.toggle');if(!t)return;
  var box=t.closest('[data-expand]');if(!box)return;
  var open=box.classList.toggle('open');
  box.querySelectorAll('.rest').forEach(function(x){x.classList.toggle('hidden',!open)});
  box.querySelectorAll('.ellipsis').forEach(function(x){x.classList.toggle('hidden',open)});
  t.textContent=open?t.dataset.less:t.dataset.more;
});
(function(){
  var tabs=document.querySelectorAll('.tabs a[data-tab]');if(!tabs.length)return;
  function show(id){
    var found=false;tabs.forEach(function(a){if(a.dataset.tab===id)found=true});
    if(!found)id=tabs[0].dataset.tab;
    tabs.forEach(function(a){a.classList.toggle('on',a.dataset.tab===id)});
    document.querySelectorAll('[data-panel]').forEach(function(p){p.classList.toggle('hidden',p.dataset.panel!==id)});
  }
  window.addEventListener('hashchange',function(){show(location.hash.slice(1))});
  show(location.hash.slice(1));
})();
"""

FULLSCREEN_JS = """
(function(){
  var overlay=null;
  function close(){if(overlay){overlay.remove();overlay=null;document.body.classList.remove('no-scroll');}}
  function open(wrap){
    var table=wrap.querySelector('table');if(!table)return;
    var card=wrap.closest('.card,[data-panel]');
    var heading=card?card.querySelector('h3,h2'):null;
    var clone=table.cloneNode(true);
    clone.querySelectorAll('.hidden').forEach(function(x){x.classList.remove('hidden')});
    overlay=document.createElement('div');overlay.className='overlay';
    overlay.innerHTML='<div class="overlay-head"><span class="title"></span><span class="meta"></span>'+
      '<button type="button">关闭（Esc）</button></div><div class="overlay-body"></div>';
    overlay.querySelector('.title').textContent=heading?heading.textContent:document.title;
    overlay.querySelector('.meta').textContent='共 '+clone.rows.length+' 行，可上下左右滚动';
    overlay.querySelector('.overlay-body').appendChild(clone);
    overlay.querySelector('button').addEventListener('click',close);
    document.body.appendChild(overlay);document.body.classList.add('no-scroll');
  }
  document.addEventListener('keydown',function(e){if(e.key==='Escape')close();});
  document.querySelectorAll('.scroll[data-grid]').forEach(function(wrap){
    var bar=document.createElement('div');bar.className='table-tools';
    var wide=wrap.scrollWidth>wrap.clientWidth+2;
    bar.innerHTML='<span>'+(wide?'表格较宽，可左右滑动':'')+'</span>'+
      '<button type="button" class="expand-btn">⤢ 全屏查看</button>';
    bar.querySelector('button').addEventListener('click',function(){open(wrap)});
    wrap.parentNode.insertBefore(bar,wrap);
  });
})();
"""

FILTER_JS = """
document.querySelectorAll('.filters').forEach(function(bar){
  bar.addEventListener('click',function(e){
    var b=e.target.closest('button');if(!b)return;
    bar.querySelectorAll('button').forEach(function(x){x.classList.toggle('on',x===b)});
    var tag=b.dataset.tag;
    document.querySelectorAll('[data-tags]').forEach(function(el){
      el.classList.toggle('hidden',tag!=='all'&&el.dataset.tags.split(' ').indexOf(tag)<0);
    });
  });
});
"""


def esc(value: object) -> str:
    return escape(str(value), quote=True)


def write_assets(reports_dir: Path) -> None:
    """reports/assets/report.css + report.js, shared by every page."""
    from rag.jsonio import write_atomic
    write_atomic(reports_dir / "assets" / "report.css", CSS.strip() + "\n")
    write_atomic(reports_dir / "assets" / "report.js",
                 (SCRIPT + FULLSCREEN_JS + FILTER_JS).strip() + "\n")


def page(title: str, body: str, *, root: str, crumbs: Iterable[tuple[str, str]] = ()) -> str:
    """`root` is the relative path from this page back to reports/ (e.g. "../../")."""
    trail = " / ".join(f'<a href="{esc(href)}">{esc(label)}</a>' for label, href in crumbs)
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><link rel="stylesheet" href="{root}assets/report.css"></head>'
            f'<body><main><div class="crumbs">{trail}</div>{body}</main>'
            f'<script src="{root}assets/report.js"></script></body></html>')


def badge(text: str, kind: str = "") -> str:
    return f'<span class="badge {kind}">{esc(text)}</span>'


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def text_block(text: str, limit: int | None = None) -> str:
    """Full text; long text shows a preview whose continuation expands in place."""
    if limit is None or len(text) <= limit:
        return f'<div class="text">{esc(text)}</div>'
    more = f"展开全文（共 {len(text)} 字符）"
    return (f'<div class="text" data-expand>{esc(text[:limit])}<span class="ellipsis">… </span>'
            f'<span class="rest hidden">{esc(text[limit:])} </span>'
            f'<button class="toggle" data-more="{more}" data-less="收起">{more}</button></div>')


def filters(options: Iterable[tuple[str, str, int]]) -> str:
    buttons = "".join(f'<button data-tag="{esc(tag)}"{" class=on" if tag == "all" else ""}>'
                      f'{esc(label)}（{count}）</button>' for tag, label, count in options)
    return f'<div class="filters">{buttons}</div>'
