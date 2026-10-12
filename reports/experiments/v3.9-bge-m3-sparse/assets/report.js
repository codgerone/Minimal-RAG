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
