'use strict';
(() => {
  const key='expense-monitor-theme', skinKey='expense-monitor-skin';
  const skins={forest:'Лес',ocean:'Океан',graphite:'Графит',sand:'Песок'};
  let theme='light',skin='forest';
  try {theme=localStorage.getItem(key)==='dark'?'dark':'light';const saved=localStorage.getItem(skinKey);if(Object.hasOwn(skins,saved))skin=saved;}catch(_){}
  function apply(){
    document.documentElement.dataset.theme=theme;
    document.documentElement.dataset.skin=skin;
    const colors={forest:['#153f35','#101c18'],ocean:['#eaf0f8','#111925'],graphite:['#eeeeef','#18191c'],sand:['#f6f1e8','#211c17']};
    document.querySelector('meta[name="theme-color"]').content=colors[skin][theme==='dark'?1:0];
    document.querySelectorAll('[data-theme-toggle]').forEach(b=>{b.textContent=theme==='dark'?'☀ Светлая тема':'☾ Тёмная тема';b.setAttribute('aria-label',theme==='dark'?'Включить светлую тему':'Включить тёмную тему');});
    document.querySelectorAll('[data-theme-choice]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.themeChoice===theme)));
    document.querySelectorAll('[data-skin-choice]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.skinChoice===skin)));
    const status=document.getElementById('appearanceStatus');if(status)status.textContent=`${skins[skin]} · ${theme==='dark'?'тёмный':'светлый'} режим`;
  }
  apply();document.addEventListener('DOMContentLoaded',apply);
  document.addEventListener('click',event=>{
    const b=event.target.closest('button');if(!b)return;
    if(b.hasAttribute('data-theme-toggle'))theme=theme==='dark'?'light':'dark';
    else if(['light','dark'].includes(b.dataset.themeChoice))theme=b.dataset.themeChoice;
    else if(Object.hasOwn(skins,b.dataset.skinChoice))skin=b.dataset.skinChoice;
    else return;
    try{localStorage.setItem(key,theme);localStorage.setItem(skinKey,skin);}catch(_){}
    apply();
  });
  window.addEventListener('storage',event=>{
    if(event.key===key)theme=event.newValue==='dark'?'dark':'light';
    else if(event.key===skinKey)skin=Object.hasOwn(skins,event.newValue)?event.newValue:'forest';
    else return;
    apply();
  });
})();
