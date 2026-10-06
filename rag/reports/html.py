"""Shared page shell for every report: one light stylesheet, small interaction helpers."""

from __future__ import annotations

from html import escape
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
th{background:var(--code);font-weight:600}td.num,th.num{text-align:right;white-space:nowrap}
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


def page(title: str, body: str, *, crumbs: Iterable[tuple[str, str]] = (),
         scripts: bool = False) -> str:
    trail = " / ".join(f'<a href="{esc(href)}">{esc(label)}</a>' for label, href in crumbs)
    script = f"<script>{SCRIPT}{FILTER_JS if scripts else ''}</script>"
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><style>{CSS}</style></head><body><main>'
            f'<div class="crumbs">{trail}</div>{body}</main>{script}</body></html>')


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
