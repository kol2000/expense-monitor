"use strict";
$('overview').insertAdjacentHTML('beforeend','<article class="panel" id="insightsPanel"></article>');
$('ruleForm').querySelector('button').insertAdjacentHTML('beforebegin','<label>Учёт следующих операций<select name="action"><option value="">Только категория</option><option value="review">Всегда отправлять на разбор</option><option value="expense">Считать расходом</option><option value="ignored">Не учитывать</option></select></label>');
$('ruleForm').insertAdjacentHTML('beforeend','<p class="muted">Правило применяется и к истории без индивидуальных решений. Собственные переводы всегда исключаются, непроведённые покупки ожидают банк.</p>');
document.body.insertAdjacentHTML('beforeend',`<dialog id="splitDialog"><form id="splitForm"><div class="panel-head"><h2>Разделить покупку</h2><button type="button" id="splitClose" class="quiet">Закрыть</button></div><p id="splitTitle"></p><p>Укажите точные суммы частей с копейками. Итог покупки сохраняется; округление распределяется между частями без увеличения расхода.</p><div id="splitParts"></div><p id="splitBalance" role="status"></p><button type="button" id="splitAdd" class="secondary">Добавить часть</button><button type="submit">Сохранить разделение</button><button type="button" id="splitRemove" class="quiet">Снять разделение</button></form></dialog>
<dialog id="mappingDialog"><form id="mappingForm"><div class="panel-head"><h2>Соответствие категории</h2><button type="button" id="mappingClose" class="quiet">Закрыть</button></div><p id="mappingTitle"></p><p>Изменятся все операции с этой категорией источника. Индивидуальные назначения и правила сохранят приоритет.</p><label>Категория в приложении<select id="mappingTarget"></select></label><button>Сохранить соответствие</button></form></dialog>`);
function updateOperationFilters(){
 for(const [id,values] of [['accountFilter',data.rows.map(r=>r.account)],['categoryFilter',data.categories],['payeeFilter',data.rows.map(r=>r.payee)]]){
  const old=$(id).value;$(id).innerHTML='<option value="">Все</option>'+[...new Set(values.filter(Boolean))].sort((a,b)=>a.localeCompare(b,'ru')).map(v=>`<option${v===old?' selected':''}>${esc(v)}</option>`).join('');
 }
}
for(const id of ['accountFilter','categoryFilter','payeeFilter','viewMode'])$(id).onchange=()=>{if(id==='viewMode')try{localStorage.setItem('expense-view-mode',$('viewMode').value);}catch(_){}renderRows();};
try{if(localStorage.getItem('expense-view-mode')==='compact')$('viewMode').value='compact';}catch(_){}
function renderSyncHealth(){
 if(!data)return;
 const last=data.last_sync?new Date(data.last_sync):null,stale=last&&Date.now()-last.getTime()>60*60*1000;
 const stats=data.sync_stats||{};
 $('syncHealth').className=data.zen_configured&&(data.sync_error||stale)?'sync-warning':'sync-summary';
 $('syncHealth').textContent=!data.zen_configured?'Дзен-мани не подключён':data.sync_error?'Ошибка синхронизации. Показаны ранее загруженные данные.':!last?'Ожидается первая успешная загрузка.':`${stale?'Данные не обновлялись более часа. Проверьте синхронизацию. ':''}Последняя загрузка: добавлено ${stats.added||0}, изменено ${stats.updated||0}, удалено ${stats.deleted||0}.`;
}
const displayDay=s=>s.split('-').reverse().join('.');
function renderInsights(){
 const d=data?.insights;if(!d)return;
 const delta=d.total-d.previous_total;
 $('insightsPanel').innerHTML=`<h2>Сравнение одинаковых отрезков месяца</h2><p>${displayDay(d.start)} — ${displayDay(d.end)} и ${displayDay(d.previous_start)} — ${displayDay(d.previous_end)}</p><div class="stats"><div><p>Выбранный месяц</p><h2>${money(d.total)}</h2></div><div><p>Предыдущий месяц</p><h2>${money(d.previous_total)}</h2></div><div><p>Изменение</p><h2>${delta>0?'+':''}${money(delta)}</h2><span>${d.previous_total?(delta/d.previous_total*100).toLocaleString('ru-RU',{maximumFractionDigits:1})+'%':'Нет базы для процента'}</span></div></div><h3>Изменения по категориям</h3>${d.categories.map(c=>`<div class="insight-line"><span>${esc(c.category)}</span><span>${money(c.previous)} → ${money(c.total)}</span><strong>${c.delta>0?'+':''}${money(c.delta)}</strong></div>`).join('')||'<p>Нет учтённых операций</p>'}<h3>Крупнейшие покупки за этот отрезок</h3>${d.largest.map(r=>`<div class="insight-line"><span>${displayDay(r.day)} · ${esc(r.payee||r.category)}</span><strong>${money(r.rubles)}</strong></div>`).join('')||'<p>Нет покупок</p>'}<p class="muted">Сравниваем одинаковое число дней. Для текущего месяца — по сегодняшний день, но не длиннее предыдущего месяца. Отсутствие записей не подтверждает полноту данных.</p>`;
}
let historySequence=0;
async function loadHistory(){
 const seq=++historySequence;const rows=await api('history');if(seq!==historySequence||currentPage!=='history')return;
 const fields={category:'Категория',custom_category:'Ручная категория',subcategory:'Подкатегория',decision:'Учёт',action:'Действие правила',amount:'Сумма, коп.',day:'Дата',deleted:'Удалено',name:'Название',value:'Значение',parts:'Части покупки'};
 $('historyList').innerHTML=rows.map(r=>`<article class="history-entry"><div class="panel-head"><div><h3>${esc(r.label)}</h3><p>${new Date(r.created).toLocaleString('ru-RU')} · ${r.undone?'Отменено':r.undoable?'Локальное изменение':'Системное событие'}</p></div>${r.undoable&&!r.undone?`<button class="secondary" data-undo-event="${r.id}">Отменить</button>`:''}</div>${r.details?.length?`<details><summary>Подробности (${r.count})</summary>${r.details.map(d=>`<p><strong>${esc(d.name)}</strong><br>${d.fields.map(f=>`${esc(fields[f.field]||f.field)}: ${esc(f.before??'—')} → ${esc(f.after??'—')}`).join('<br>')}</p>`).join('')}${r.count>30?'<p>Показаны первые 30 изменённых записей.</p>':''}</details>`:''}</article>`).join('')||'<p>Изменений пока нет. Журнал начнёт заполняться после установки этой версии.</p>';
}
$('historyRefresh').onclick=()=>busy($('historyRefresh'),loadHistory);
let splitRecord=null,mappingOriginal='';
function addSplitPart(part={}){
 const category=part.category||splitRecord.category;
 $('splitParts').insertAdjacentHTML('beforeend',`<div class="split-part"><label>Сумма, ₽<input class="split-amount" inputmode="decimal" required value="${part.amount===undefined?'':esc((part.amount/100).toFixed(2))}"></label><label>Категория<select class="split-category">${options(category)}</select></label><label>Подкатегория<select class="split-sub">${subOptions(category,part.subcategory)}</select></label><button type="button" class="quiet" data-remove-part>Убрать часть</button></div>`);splitBalance();
}
function splitBalance(){
 if(!splitRecord)return;
 const sum=[...$('splitParts').querySelectorAll('.split-amount')].reduce((n,e)=>n+Math.round(Number(e.value.replace(',','.'))*100||0),0);
 $('splitBalance').textContent=`Сумма частей: ${(sum/100).toFixed(2)} ₽ · Осталось: ${((splitRecord.amount-sum)/100).toFixed(2)} ₽`;
}
$('splitParts').addEventListener('input',splitBalance);
$('splitParts').addEventListener('change',event=>{if(event.target.matches('.split-category'))event.target.closest('.split-part').querySelector('.split-sub').innerHTML=subOptions(event.target.value);});
$('splitParts').addEventListener('click',event=>{if(event.target.closest('[data-remove-part]')){event.target.closest('.split-part').remove();splitBalance();}});
$('splitAdd').onclick=()=>{if($('splitParts').children.length<20)addSplitPart();};
$('splitClose').onclick=()=>$('splitDialog').close();
$('mappingClose').onclick=()=>$('mappingDialog').close();
$('splitForm').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{
 const parts=[...$('splitParts').querySelectorAll('.split-part')].map(p=>({amount:p.querySelector('.split-amount').value,category:p.querySelector('.split-category').value,subcategory:p.querySelector('.split-sub').value}));
 await api('split',{id:splitRecord.id,parts});$('splitDialog').close();await load();toast('Покупка разделена. Общая сумма сохранена.');
});};
$('splitRemove').onclick=()=>busy($('splitRemove'),async()=>{await api('split',{id:splitRecord.id,remove:true});$('splitDialog').close();await load();toast('Разделение снято');});
$('mappingForm').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{await api('mapping',{original:mappingOriginal,category:$('mappingTarget').value});$('mappingDialog').close();await load();toast('Соответствие обновлено');});};
document.addEventListener('click',event=>{
 const b=event.target.closest('button');if(!b)return;
 if(b.dataset.split){
  splitRecord=featureRecords.get(b.dataset.split);$('splitParts').innerHTML='';$('splitTitle').textContent=`${splitRecord.payee||splitRecord.category} · ${(splitRecord.amount/100).toFixed(2)} ₽`;
  if(splitRecord.parts?.length)splitRecord.parts.forEach(addSplitPart);else{addSplitPart();addSplitPart();}
  $('splitRemove').hidden=!splitRecord.parts?.length;$('splitDialog').showModal();
 }
 if(b.dataset.mapFrom){const r=featureRecords.get(b.dataset.mapFrom);mappingOriginal=r.original_category;$('mappingTitle').textContent='Категория источника: '+mappingOriginal;$('mappingTarget').innerHTML=options(r.category);$('mappingDialog').showModal();}
 if(b.dataset.undoEvent)busy(b,async()=>{await api('undo',{id:Number(b.dataset.undoEvent)});await load();await loadHistory();toast('Изменение отменено');});
});
load().catch(e=>{if(e.message!=='Войдите в приложение')toast(e.message);});

function renderAccounts(){
 const summary=data.accounts||{rows:[],total:0,missing:0,included_count:0};
 const exact=n=>n===null?'Нет данных':new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}).format(n/100)+' ₽';
 const types={cash:'Наличные',ccard:'Карта',checking:'Банковский счёт',loan:'Кредит',deposit:'Вклад',emoney:'Электронный кошелёк',debt:'Долги'};
 const cards=rows=>rows.map(a=>`<article class="account-card"><div><h3>${esc(a.title)}</h3><p class="muted">${esc(types[a.type]||'Счёт')}${a.savings?' · Накопления':''}</p></div><strong class="account-balance">${exact(a.balance)}</strong><span class="muted">${a.archived?'Архив · не входит в итог':a.included?'Входит в итог':'Не входит в итог'}</span></article>`).join('');
 $('accountsContent').innerHTML=summary.rows.length?`<article class="stat primary account-total"><p>${summary.missing?'Известный остаток · неполная сумма':'Общий остаток'}</p><strong>${exact(summary.total)}</strong><span>Счетов в итоге: ${summary.included_count}</span></article><p>В итог входят активные счета с настройкой «В балансе» в Дзен-мани. Отрицательные остатки уменьшают сумму. Кредитный лимит не прибавляется. Системный счёт долгов исключён.</p>${summary.missing?`<p class="tx-notice">У ${summary.missing} счетов нет остатка. Итог неполный — обновите данные в Дзен-мани.</p>`:''}<div class="account-grid">${cards(summary.rows.filter(a=>!a.archived))}</div>${summary.rows.some(a=>a.archived)?`<details class="account-archive"><summary>Архивные счета</summary><div class="account-grid">${cards(summary.rows.filter(a=>a.archived))}</div></details>`:''}`:'<p class="empty">Счета появятся после синхронизации с Дзен-мани.</p>';
 $('accountsSync').disabled=!data.zen_configured;
}
$('accountsSync').onclick=()=>busy($('accountsSync'),async()=>{await api('sync',{});await load();toast('Данные Дзен-мани обновлены');});
