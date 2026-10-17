'use strict';
const $ = id => document.getElementById(id);
const money = n => new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(n)+' ₽';
const esc = s => String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const localDate = () => new Date().toLocaleDateString('sv-SE');
let data, currentPage='overview', toastTimer;
$('month').value=localDate().slice(0,7);
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6500);}
async function api(path,body){
  const response=await fetch('/api/'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'expense-monitor'},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok){if(response.status===401){$('workspace').hidden=true;$('login').hidden=false;}throw new Error(result.error||'Ошибка запроса');}
  return result;
}
async function busy(button,fn){button.disabled=true;try{await fn();}catch(e){toast(e.message);}finally{button.disabled=false;}}
function options(selected){return data.categories.map(c=>`<option${c===selected?' selected':''}>${esc(c)}</option>`).join('');}
function sourceLabel(source){return {easyfinance:'EasyFinance',zenmoney:'Дзен-мани',manual:'Вручную'}[source]||source;}
function rowHTML(r,editable=false){
 const statuses={expense:'Расход',review:r.pending?'Ожидает списания':'Нужно разобрать',ignored:r.own_transfer?'Перевод между счетами':'Исключено',income:'Поступление'};
 const exact=((r.display_amount??r.amount)/100).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2});
 const receipt=r.kind==='income';
 const headline=receipt?`+${exact} ₽`:money(r.rubles);
 const title=r.own_transfer?'Перевод между своими счетами':r.payee||r.category;
 const account=r.own_transfer?`${r.account} → ${r.account_to}`:r.account||'Не указан';
 const date=r.day.split('-').reverse().join('.');
 const accounting=r.own_transfer?'Не входит в расходы':!r.primary?'Только для сверки':r.included?'Входит в расходы':r.status==='income'?'Не уменьшает расходы':'Не входит в расходы';
 const reason=r.status==='review'?(r.pending?'Банк обрабатывает покупку. После проведения она обновится автоматически.':r.review_reason):'';
 return `<article class="transaction transaction-${esc(r.status)}">
 <div class="tx-main"><div class="tx-status"><span class="badge ${r.status==='review'?'review':''}">${esc(statuses[r.status]||r.status)}</span><span class="tx-source">${esc(sourceLabel(r.source))}</span></div><h3 class="tx-title">${esc(title)}</h3></div>
 <div class="amount"><strong>${headline}</strong><span class="tx-accounting">${accounting}</span>${!receipt&&r.amount!==r.rubles*100?`<span class="tx-exact">До округления: ${exact} ₽</span>`:''}</div>
 <dl class="tx-meta"><div><dt>Дата</dt><dd>${esc(date)}</dd></div>${!r.own_transfer?`<div><dt>Категория</dt><dd>${esc(r.category)}</dd></div>`:''}<div class="tx-account"><dt>${r.own_transfer?'Откуда → куда':'Счёт'}</dt><dd>${esc(account)}</dd></div></dl>
 ${reason?`<p class="tx-notice">${esc(reason)}</p>`:''}${r.note?`<p class="tx-note"><span>Комментарий</span>${esc(r.note)}</p>`:''}
 ${editable?`<details class="record-editor"><summary>Изменить операцию</summary><p class="tx-original">Категория источника: ${esc(r.original_category||'Без категории')}</p><div class="record-actions"><label>Категория<select data-category="${esc(r.id)}" aria-label="Категория операции">${options(r.category)}</select></label><label>Учёт<select data-decision="${esc(r.id)}" aria-label="Учёт операции"><option value="">Как в источнике</option><option value="expense"${r.decision==='expense'?' selected':''}${r.pending||r.own_transfer?' disabled':''}>Считать расходом</option><option value="ignored"${r.decision==='ignored'?' selected':''}>Исключить</option><option value="review"${r.decision==='review'?' selected':''}>Разобрать позже</option></select></label><button data-save="${esc(r.id)}">Сохранить</button></div></details>`:''}</article>`;
}

function showPage(page){currentPage=page;document.querySelectorAll('.page').forEach(el=>el.hidden=el.id!==page);document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===page));$('pageTitle').textContent={overview:'Обзор расходов',transactions:'Все операции',reconcile:'Сверка с EasyFinance',settings:'Категории и данные'}[page];}
function renderRows(){
 const term=$('search').value.toLocaleLowerCase(), status=$('statusFilter').value;
 const rows=data.rows.filter(r=>(status==='all'||status==='included'&&r.included||status==='review'&&r.primary&&r.status==='review'||status==='ignored'&&r.status==='ignored')&&[r.payee,r.note,r.category,r.account,r.account_to].join(' ').toLocaleLowerCase().includes(term));
 $('rows').innerHTML=rows.length?rows.map(r=>rowHTML(r,true)).join(''):'<p class="empty">За этот период подходящих операций нет.</p>';
}
function render(){
 $('total').textContent=money(data.total);$('count').textContent=`${data.count} операций в отчёте`;$('reviewCount').textContent=data.review;
 $('syncStatus').textContent=data.sync_error|| (data.last_sync?`Дзен-мани обновлён: ${new Date(data.last_sync).toLocaleString('ru-RU')}`:data.zen_configured?'Дзен-мани подключён. Ожидается первая синхронизация.':'Дзен-мани пока не подключён. Можно импортировать историю и добавлять расходы.');
 $('chart').innerHTML=Object.entries(data.totals).map(([c,n])=>`<div class="bar-row"><div class="bar-label"><span>${esc(c)}</span><strong>${money(n)}</strong></div><progress max="${data.total||1}" value="${n}"></progress></div>`).join('')||'<p class="empty">Здесь появится распределение расходов.</p>';
 $('recent').innerHTML=data.rows.filter(r=>r.included).slice(0,6).map(r=>rowHTML(r)).join('')||'<p class="empty">Добавьте первый расход или загрузите историю.</p>';
 $('manualCategory').innerHTML=options();$('cutover').value=data.cutover;
 $('mapping').innerHTML=data.mapping.map(m=>`<div class="mapping-row"><span>${esc(m.original||'Без категории')}</span><select data-map="${esc(m.original)}" aria-label="Соответствие категории ${esc(m.original)}">${options(m.category)}</select>${m.suggested&&m.suggested!=='Прочее'&&m.suggested!==m.category?`<button class="secondary" data-suggest="${esc(m.original)}" data-value="${esc(m.suggested)}">Применить: ${esc(m.suggested)}</button>`:''}</div>`).join('')||'<p class="empty">Соответствия появятся после импорта.</p>';
 $('categoryList').innerHTML=data.categories.map(c=>`<div class="category-row"><input data-rename-input="${esc(c)}" aria-label="Название категории ${esc(c)}" value="${esc(c)}" maxlength="60"><button data-rename="${esc(c)}" class="secondary">Сохранить</button></div>`).join('');
 renderRows();
}
async function load(){data=await api('dashboard?month='+encodeURIComponent($('month').value));$('login').hidden=true;$('workspace').hidden=false;render();}
document.addEventListener('click',async event=>{
 const button=event.target.closest('button');if(!button)return;
 if(button.dataset.page)showPage(button.dataset.page);
 if(button.hasAttribute('data-suggest'))await busy(button,async()=>{await api('mapping',{original:button.dataset.suggest,category:button.dataset.value});await load();toast('Соответствие сохранено');});
 if(button.dataset.save){await busy(button,async()=>{const id=button.dataset.save;const select=[...document.querySelectorAll('[data-category]')].find(e=>e.dataset.category===id);const decision=[...document.querySelectorAll('[data-decision]')].find(e=>e.dataset.decision===id);await api('edit',{id,category:select.value,decision:decision.value||null});await load();toast('Операция сохранена');});}
 if(button.dataset.rename){await busy(button,async()=>{const old=button.dataset.rename;const input=[...document.querySelectorAll('[data-rename-input]')].find(e=>e.dataset.renameInput===old);await api('categories',{old,name:input.value});await load();toast('Категории обновлены');});}
});
document.addEventListener('change',event=>{if(event.target.matches('[data-map]'))busy(event.target,async()=>{await api('mapping',{original:event.target.dataset.map,category:event.target.value});await load();toast('Соответствие сохранено');});});
$('loginForm').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{await api('login',{password:new FormData(event.target).get('password')});event.target.reset();await load();});};
$('logout').onclick=()=>busy($('logout'),async()=>{await api('logout',{});data=null;$('workspace').hidden=true;$('login').hidden=false;});
$('month').onchange=()=>load().catch(e=>toast(e.message));
$('search').oninput=renderRows;$('statusFilter').onchange=renderRows;
$('reviewOpen').onclick=()=>{$('statusFilter').value='review';showPage('transactions');renderRows();};
$('add').onclick=()=>{$('expenseForm').reset();$('expenseForm').elements.day.value=localDate();$('expenseDialog').showModal();};
$('closeDialog').onclick=()=>$('expenseDialog').close();
$('expenseForm').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{await api('manual',Object.fromEntries(new FormData(event.target)));$('expenseDialog').close();await load();toast('Расход добавлен');});};
$('categoryForm').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{await api('categories',Object.fromEntries(new FormData(event.target)));event.target.reset();await load();});};
$('saveCutover').onclick=()=>busy($('saveCutover'),async()=>{await api('settings',{cutover:$('cutover').value});await load();toast('Дата перехода сохранена');});
$('sync').onclick=()=>busy($('sync'),async()=>{const result=await api('sync',{});await load();toast(`Обработано изменений: ${result.received}`);});
$('import').onclick=()=>busy($('import'),async()=>{
 const file=$('csvFile').files[0];if(!file)throw new Error('Выберите CSV-файл');if(file.size>8*1024*1024)throw new Error('Файл больше 8 МБ');
 const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
 const result=await api('import',{content:btoa(binary)});await load();toast(`Добавлено: ${result.inserted}. Уже были в базе: ${result.already_present}.`);
});
$('reconcileRun').onclick=()=>busy($('reconcileRun'),async()=>{
 const r=await api('reconcile?month='+encodeURIComponent($('month').value));
 $('comparison').innerHTML=`<hr><div class="stats"><article><p>EasyFinance</p><h2>${money(r.easyfinance_total)}</h2></article><article><p>Новый учёт</p><h2>${money(r.app_total)}</h2></article><article><p>Разница</p><h2>${money(r.app_total-r.easyfinance_total)}</h2></article></div><p>Предварительно совпало: ${r.matched}. ${esc(r.note)}</p><h2>Только в EasyFinance (${r.easyfinance_only.length})</h2>${r.easyfinance_only.map(x=>rowHTML(x)).join('')||'<p class="muted">Нет расхождений</p>'}<h2>Только в новом учёте (${r.app_only.length})</h2>${r.app_only.map(x=>rowHTML(x)).join('')||'<p class="muted">Нет расхождений</p>'}`;
});
load().catch(e=>{if(e.message!=='Войдите в приложение')toast(e.message);});
