'use strict';
const $ = id => document.getElementById(id);
const money = n => new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(n)+' ₽';
const esc = s => String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const localDate = () => new Date().toLocaleDateString('sv-SE');
const featureRecords=new Map();
let data, currentPage='overview', toastTimer, loadSequence=0, activeActions=0;
$('month').value=localDate().slice(0,7);
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6500);}
async function api(path,body){
  const response=await fetch('/api/'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'expense-monitor'},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok){if(response.status===401){$('workspace').hidden=true;$('login').hidden=false;}throw new Error(result.error||'Ошибка запроса');}
  return result;
}
async function busy(button,fn){button.disabled=true;activeActions++;try{await fn();}catch(e){toast(e.message);}finally{button.disabled=false;activeActions--;}}
function options(selected){return [...data.categories].sort((a,b)=>a.localeCompare(b,'ru',{sensitivity:'base'})).map(c=>`<option${c===selected?' selected':''}>${esc(c)}</option>`).join('');}
function sourceLabel(source){return {easyfinance:'EasyFinance',zenmoney:'Дзен-мани',manual:'Вручную'}[source]||source;}
function rowHTML(r,editable=false){
 if(!r.allocation)featureRecords.set(r.id,r);
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
 <div class="tx-main"><h3 class="tx-title">${esc(title)}</h3>${r.status!=='expense'? `<span class="badge ${r.status==='review'?'review':''}">${esc(statuses[r.status]||r.status)}</span>`:''}</div>
 <div class="amount"><strong>${headline}</strong>${!r.included?`<span class="tx-accounting">${accounting}</span>`:''}</div>
 <div class="tx-category">${!r.own_transfer?(r.parts?.length?'Несколько категорий':esc(r.category)+(!r.parts?.length&&r.subcategory?' / '+esc(r.subcategory):'')):''}</div>
 <div class="tx-context"><time datetime="${esc(r.day)}">${esc(date)}</time><span aria-label="Счёт">${esc(account)}</span></div>
 ${r.parts?.length?`<div class="tx-note"><strong>${r.split_stale?'Разделение требует проверки':'Покупка разделена'}</strong>${r.parts.map(p=>`<div>${esc(p.category)}${p.subcategory?' / '+esc(p.subcategory):''} · ${(p.amount/100).toLocaleString('ru-RU')} ₽</div>`).join('')}</div>`:''}
 ${reason?`<p class="tx-notice">${esc(reason)}</p>`:''}${r.note?`<p class="tx-note"><span>Комментарий</span>${esc(r.note)}</p>`:''}
 ${editable?`<details class="record-editor"><summary>Подробнее / изменить</summary><div class="record-actions"><label>Категория<select data-category="${esc(r.id)}" aria-label="Категория операции">${options(r.category)}</select></label><label>Подкатегория<select data-subcategory="${esc(r.id)}">${subOptions(r.category,r.subcategory)}</select></label><label class="operation-note">Примечание<textarea data-local-note maxlength="2000" rows="3" placeholder="Ваше примечание к операции">${esc(r.local_note||'')}</textarea></label><button data-save="${esc(r.id)}">Сохранить</button><details class="advanced-editor"><summary>Дополнительно: учёт, правила и источник</summary><p class="tx-original">Источник: ${esc(sourceLabel(r.source))}</p><p class="tx-original">Категория источника: ${esc(r.original_category||'Без категории')}</p><p class="tx-original">${esc(r.category_reason||'')} · ${esc(r.decision_reason||'')}</p><label>Учёт этой операции<select data-decision="${esc(r.id)}" aria-label="Учёт операции"><option value="">Как в источнике</option><option value="expense"${r.decision==='expense'?' selected':''}${r.pending||r.own_transfer?' disabled':''}>Считать расходом</option><option value="ignored"${r.decision==='ignored'?' selected':''}>Исключить</option><option value="review"${r.decision==='review'?' selected':''}>Разобрать позже</option></select></label>${!r.own_transfer&&r.kind!=='income'?`<button class="secondary" data-split="${esc(r.id)}">Разделить покупку</button>`:''}${r.original_category?`<button class="secondary" data-map-from="${esc(r.id)}">Исправить соответствие источника</button>`:''}${r.source==='manual'?`<button class="secondary" data-manual-edit="${esc(r.id)}">Сумма, дата и описание</button><button class="quiet" data-manual-delete="${esc(r.id)}">В корзину</button>`:''}${r.source==='zenmoney'&&r.payee?`<button class="secondary" data-rule-from="${esc(r.id)}">Правило для получателя</button><button class="quiet" data-auto-category="${esc(r.id)}">Вернуть автокатегорию</button>`:''}</details></div></details>`:''}</article>`;
}

function showPage(page){if($('operationDialog')?.open)$('operationDialog').close();currentPage=page;document.querySelectorAll('.page').forEach(el=>el.hidden=el.id!==page);document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===page));$('pageTitle').textContent={accounts:'Счета',overview:'Обзор расходов',transactions:'Все операции',reconcile:'Сверка с EasyFinance',settings:'Категории и данные',appearance:'Настройки',history:'История изменений',reports:'Отчёты',attention:'Требует внимания'}[page];document.querySelector('.month-navigation').hidden=page==='accounts';if(page==='accounts'){renderAccounts();autoRefresh();}updatePeriodLabels();if(page==='history')loadHistory().catch(e=>toast(e.message));if(page==='attention')loadAttention().catch(e=>toast(e.message));if(page==='reports')loadReports().catch(e=>toast(e.message));}
function renderRows(){
 updateOperationFilters();
 const term=$('search').value.toLocaleLowerCase(),status=$('statusFilter').value;
 const rows=data.rows.filter(r=>(status==='all'||status==='included'&&r.included||status==='review'&&r.primary&&['review','category'].includes(r.attention)&&!r.pending||status==='pending'&&r.pending||status==='ignored'&&r.status==='ignored')&&
  (!$('accountFilter').value||r.account===$('accountFilter').value)&&(!$('categoryFilter').value||r.category===$('categoryFilter').value||r.parts?.some(p=>p.category===$('categoryFilter').value))&&
  (!$('payeeFilter').value||r.payee===$('payeeFilter').value)&&[r.payee,r.note,r.local_note,r.category,r.subcategory,r.account,r.account_to,...(r.parts||[]).map(p=>p.category)].join(' ').toLocaleLowerCase().includes(term));
 $('rows').innerHTML=operationTable(rows);
}

function render(){
 renderOverview();renderAccounts();
 $('manualCategory').innerHTML=options();$('cutover').value=data.cutover;
 $('mapping').innerHTML=data.mapping.map(m=>`<div class="mapping-row"><span>${esc(m.original||'Без категории')}</span><select data-map="${esc(m.original)}" aria-label="Соответствие категории ${esc(m.original)}">${options(m.category)}</select>${m.suggested&&m.suggested!=='Прочее'&&m.suggested!==m.category?`<button class="secondary" data-suggest="${esc(m.original)}" data-value="${esc(m.suggested)}">Применить: ${esc(m.suggested)}</button>`:''}</div>`).join('')||'<p class="empty">Соответствия появятся после импорта.</p>';
 $('categoryList').innerHTML=data.categories.map(c=>`<div class="category-row"><input data-rename-input="${esc(c)}" aria-label="Название категории ${esc(c)}" value="${esc(c)}" maxlength="60"><button data-rename="${esc(c)}" class="secondary">Сохранить</button></div>`).join('');
 renderRows();
 renderFeatures();
 if(currentPage==='attention')loadAttention().catch(e=>toast(e.message));
 if(currentPage==='reports')loadReports().catch(e=>toast(e.message));
}
function renderOverview(){
 renderSyncHealth();renderInsights();
 $('yearTotal').textContent=money(data.year_total);$('yearRange').textContent=`${data.year_start.split('-').reverse().join('.')} — ${data.year_end.split('-').reverse().join('.')}`;
 $('total').textContent=money(data.total);$('count').textContent=`${data.count} операций в отчёте`;$('reviewCount').textContent=data.review;$('pendingCount').textContent=data.pending_count;$('pendingTotal').textContent=money(data.pending_total||0);$('overviewSync').disabled=!data.zen_configured;
 $('syncStatus').textContent=data.sync_error|| (data.last_sync?`Дзен-мани обновлён: ${new Date(data.last_sync).toLocaleString('ru-RU')}`:data.zen_configured?'Дзен-мани подключён. Ожидается первая синхронизация.':'Дзен-мани пока не подключён. Можно импортировать историю и добавлять расходы.');
 $('chart').innerHTML=Object.entries(data.totals).map(([c,n])=>`<div class="bar-row"><div class="bar-label"><span>${esc(c)}</span><strong>${money(n)}</strong></div><progress max="${data.total||1}" value="${n}"></progress></div>`).join('')||'<p class="empty">Здесь появится распределение расходов.</p>';
 $('recent').innerHTML=operationTable(data.rows.filter(r=>r.primary&&(r.included||r.attention)).slice(0,20));
}
async function load(){const sequence=++loadSequence;const result=await api('dashboard?month='+encodeURIComponent($('month').value));if(sequence!==loadSequence)return;data=result;$('login').hidden=true;$('workspace').hidden=false;render();}
document.addEventListener('click',async event=>{
 const button=event.target.closest('button');if(!button)return;
 if(button.dataset.page)showPage(button.dataset.page);
 if(button.hasAttribute('data-suggest'))await busy(button,async()=>{await api('mapping',{original:button.dataset.suggest,category:button.dataset.value});await load();toast('Соответствие сохранено');});
 if(button.dataset.save){await busy(button,async()=>{const id=button.dataset.save;const editor=button.closest('.record-actions');const select=editor.querySelector('[data-category]');const decision=editor.querySelector('[data-decision]');const original=featureRecords.get(id);const patch={id,local_note:editor.querySelector('[data-local-note]').value};if(select.value!==original.category||editor.querySelector('[data-subcategory]').value!==(original.subcategory||'')||decision.value!==(original.decision||'')){Object.assign(patch,{category:select.value,decision:decision.value||null,subcategory:editor.querySelector('[data-subcategory]').value});}await api('edit',patch);$('operationDialog').close();await load();toast('Операция сохранена');});}
 if(button.dataset.rename){await busy(button,async()=>{const old=button.dataset.rename;const input=[...document.querySelectorAll('[data-rename-input]')].find(e=>e.dataset.renameInput===old);await api('categories',{old,name:input.value});await load();toast('Категории обновлены');});}
});
document.addEventListener('change',event=>{if(event.target.matches('[data-map]'))busy(event.target,async()=>{await api('mapping',{original:event.target.dataset.map,category:event.target.value});await load();toast('Соответствие сохранено');});});
$('loginForm').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{await api('login',{password:new FormData(event.target).get('password')});event.target.reset();await load();});};
$('logout').onclick=()=>busy($('logout'),async()=>{await api('logout',{});data=null;$('workspace').hidden=true;$('login').hidden=false;});
function changeMonth(){ $('comparison').innerHTML=''; load().catch(e=>toast(e.message)); }
function stepMonth(delta){
 const [year,month]=($('month').value||localDate().slice(0,7)).split('-').map(Number);
 const index=year*12+month-1+delta*(currentPage==='reports'?({year:12,quarter:3}[$('reportPeriod').value]||1):1);
 const nextYear=Math.floor(index/12);
 if(nextYear<1||nextYear>9999)return;
 $('month').value=String(nextYear).padStart(4,'0')+'-'+String(index%12+1).padStart(2,'0');
 changeMonth();
}
$('month').onchange=changeMonth;
$('previousMonth').onclick=()=>stepMonth(-1);
$('nextMonth').onclick=()=>stepMonth(1);
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
let reportData, reportCategory=null, reportDetail=null, reportSequence=0, reportGroups=[];
const reportColors=['#5488dc','#e68d49','#51a58f','#ac7bcd','#d56c83','#8fa844','#41a7bd','#c69b45','#7f91ac','#b58062','#698c65','#bf75a8','#739ac1','#a99b70'];
function detailOf(r){if(r.subcategory)return r.subcategory;return !r.original_category||r.original_category===r.category?'Без уточнения':r.original_category;}
function updatePeriodLabels(){
 const year=currentPage==='reports'&&$('reportPeriod').value==='year';
 for(const [id,prefix] of [['previousMonth','Предыдущий'],['nextMonth','Следующий']]){const quarter=currentPage==='reports'&&$('reportPeriod').value==='quarter';$(id).disabled=currentPage==='reports'&&$('reportPeriod').value==='range';const label=prefix+(year?' год':quarter?' квартал':' месяц');$(id).title=label;$(id).setAttribute('aria-label',label);}
}
async function loadReports(){
 updatePeriodLabels();
 const sequence=++reportSequence;
 $('reportContent').innerHTML='<p class="empty">Загружаем отчёт…</p>';
 try{
  const result=await api('reports?'+reportQuery());
  if(sequence!==reportSequence||currentPage!=='reports')return;
  reportData=result;reportCategory=$('reportCategoryFilter').value||null;reportDetail=null;renderReport();
 }catch(e){if(sequence===reportSequence)$('reportContent').innerHTML='<p class="empty">Не удалось загрузить отчёт. Попробуйте выбрать период ещё раз.</p>';throw e;}
}
function renderReport(){
 const d=reportData;
 const date=new Date(d.month+'-01T12:00:00');
 $('reportPeriodTitle').textContent=d.start.split('-').reverse().join('.')+' — '+d.end.split('-').reverse().join('.');
 const rows=d.rows.filter(r=>(reportCategory===null||r.category===reportCategory)&&(reportDetail===null||detailOf(r)===reportDetail));
 const total=rows.reduce((sum,r)=>sum+r.rubles,0);
 const groups=new Map();
 for(const r of rows){const name=reportCategory===null?r.category:detailOf(r);const g=groups.get(name)||{name,total:0,count:0,ids:new Set()};g.total+=r.rubles;g.ids.add(r.id);g.count=g.ids.size;groups.set(name,g);}
 reportGroups=[...groups.values()].sort((a,b)=>b.total-a.total||a.name.localeCompare(b.name,'ru'));
 let offset=0;
 const segments=reportGroups.filter(g=>g.total>0).map(g=>{
  const i=reportGroups.indexOf(g), share=g.total/(total||1)*100;
  const circle=`<circle cx="100" cy="100" r="74" fill="none" stroke="${categoryColor((reportCategory||'')+'/'+g.name)}" stroke-width="28" pathLength="100" stroke-dasharray="${share} ${100-share}" stroke-dashoffset="${-offset}" transform="rotate(-90 100 100)" data-report-group="${i}"><title>${esc(g.name)}: ${money(g.total)}</title></circle>`;
  offset+=share;return circle;
 }).join('');
 const legend=reportGroups.map((g,i)=>`<button class="report-legend-row" data-report-group="${i}"><span class="report-dot" data-color="${i}"></span><span class="report-name">${esc(g.name)}<small>Операций: ${g.count}</small></span><span><strong>${money(g.total)}</strong><small>${(total?g.total/total*100:0).toLocaleString('ru-RU',{maximumFractionDigits:1})}%</small></span><span aria-hidden="true">›</span></button>`).join('');
 const buckets=d.buckets.map(key=>({key,rows:rows.filter(r=>r.day.startsWith(key))}));
 const amounts=buckets.map(b=>b.rows.reduce((sum,r)=>sum+r.rubles,0));
 const max=Math.max(1,...amounts);
 const bars=buckets.map((b,i)=>{
  const label=!d.daily?new Date(b.key+'-01T12:00:00').toLocaleDateString('ru-RU',{month:'short',year:'2-digit'}):b.key.slice(5).split('-').reverse().join('.');
  const hint=b.rows.length?money(amounts[i]):'Нет учтённых операций';
  return `<div class="report-column" tabindex="0" aria-label="${esc(b.key+': '+hint)}"><div class="report-column-track"><div class="report-column-fill" data-height="${amounts[i]/max*100}"></div><span class="report-tooltip">${esc(label)}: ${hint}</span></div><span>${esc(label)}</span></div>`;
 }).join('');
 $('reportContent').innerHTML=`<div class="report-breadcrumb"><button class="secondary" data-report-back="all">Все расходы</button>${reportCategory!==null?`<span>›</span><button class="secondary" data-report-back="category">${esc(reportCategory)}</button>`:''}${reportDetail!==null?`<span>› ${esc(reportDetail)}</span>`:''}</div>
 ${comparisonHTML(d,rows)}<article class="panel"><div class="panel-head"><h2>${esc(reportDetail??reportCategory??'Распределение расходов')}</h2><span>Операций: ${new Set(rows.map(r=>r.id)).size}</span></div>
 ${rows.length?`<div class="report-distribution"><div class="report-donut"><svg viewBox="0 0 200 200" aria-hidden="true"><circle cx="100" cy="100" r="74" fill="none" stroke="#89988b33" stroke-width="28"/>${segments}</svg><div class="report-donut-total"><small>Всего за период</small><strong>${money(total)}</strong></div></div><div class="report-legend">${legend}</div></div>`:'<p class="empty">За этот период учтённых расходов нет.</p>'}
 <p class="muted">${reportCategory===null?'Нажмите на сектор или категорию, чтобы увидеть детализацию.':'Ваши подкатегории и сохранённая детализация источника. Нажмите на строку, чтобы увидеть операции. «Без уточнения» — записи без дополнительной категории.'}</p></article>
 <article class="panel"><div class="panel-head"><h2>${!d.daily?'Расходы по месяцам':'Расходы по дням'}</h2><span class="muted">Максимум: ${money(Math.max(0,...amounts))}</span></div><div class="report-timeline ${d.daily?'daily':'monthly'}">${bars}</div><p class="muted">Сумма доступна при наведении или фокусе. Пустой столбец означает отсутствие учтённых операций, а не подтверждение полноты данных. Текущий период может быть неполным.</p></article>
 ${reportDetail!==null?`<article class="panel"><h2>Операции: ${esc(reportDetail)}</h2>${rows.map(r=>rowHTML(r,!r.allocation)).join('')}</article>`:''}`;
 // CSSOM assignments work with the strict style-src policy; HTML style attributes do not.
 $('reportContent').querySelector('.report-timeline').style.setProperty('--bucket-count',buckets.length);
 $('reportContent').querySelectorAll('[data-height]').forEach(el=>el.style.height=el.dataset.height+'%');
 $('reportContent').querySelectorAll('[data-color]').forEach(el=>el.style.backgroundColor=categoryColor((reportCategory||'')+'/'+reportGroups[Number(el.dataset.color)].name));
}
$('reportPeriod').onchange=()=>loadReports().catch(e=>toast(e.message));
$('reportContent').onclick=event=>{
 const group=event.target.closest('[data-report-group]');
 if(group){const selected=reportGroups[Number(group.dataset.reportGroup)];if(!selected)return;if(reportCategory===null)reportCategory=selected.name;else reportDetail=selected.name;renderReport();return;}
 const back=event.target.closest('[data-report-back]');
 if(back){if(back.dataset.reportBack==='all')reportCategory=null;reportDetail=null;renderReport();}
};


$('overviewSync').onclick=()=>busy($('overviewSync'),async()=>{await api('sync',{});await load();toast('Данные Дзен-мани обновлены');});

function operationTable(rows){
 if(!rows.length)return '<p class="empty">За этот период подходящих операций нет.</p>';
 return `<div class="table-scroll"><table class="operations-table"><thead><tr><th>Дата</th><th>Получатель / примечание</th><th>Категория</th><th>Счёт</th><th class="numeric">Сумма</th><th>Статус</th><th></th></tr></thead><tbody>${rows.map(r=>{
 featureRecords.set(r.id,r);
 const review=['category','review'].includes(r.attention);
 const status=review?(r.attention==='category'?'Уточнить категорию':'Нужно решение'):r.pending&&!r.own_transfer?'Ожидает банка':r.own_transfer?'Свой перевод':!r.primary?'Для сверки':r.included?'Учтено':r.status==='income'?'Поступление':'Исключено';
 return `<tr class="${review?'needs-review':''}"><td class="date-cell">${esc(r.day.split('-').reverse().join('.'))}</td><td><strong>${esc(r.payee||(r.own_transfer?'Перевод между счетами':r.category))}</strong>${r.local_note?`<div class="local-note">${esc(r.local_note)}</div>`:''}</td><td>${esc(r.parts?.length?'Несколько категорий':r.category)}${r.subcategory?`<small>${esc(r.subcategory)}</small>`:''}</td><td>${esc(r.account)}${r.own_transfer?` → ${esc(r.account_to)}`:''}</td><td class="numeric"><strong>${r.kind==='income'?'+'+((r.display_amount??r.amount)/100).toLocaleString('ru-RU')+' ₽':money(r.rubles)}</strong></td><td><span class="table-status">${status}</span></td><td><button class="secondary" data-open-operation="${esc(r.id)}" aria-label="Изменить операцию ${esc(r.payee||r.category)}">Изменить</button></td></tr>`;
 }).join('')}</tbody></table></div>`;
}
document.body.insertAdjacentHTML('beforeend','<dialog id="operationDialog"><div class="panel-head"><h2>Операция</h2><button id="operationClose" class="quiet">Закрыть</button></div><div id="operationContent"></div></dialog>');
$('operationClose').onclick=()=>$('operationDialog').close();
document.addEventListener('click',event=>{
 const button=event.target.closest('[data-open-operation]');if(!button)return;
 const r=featureRecords.get(button.dataset.openOperation);if(!r)return;
 $('operationContent').innerHTML=rowHTML(r,true);$('operationContent').querySelector('.record-editor').open=true;$('operationDialog').showModal();
});
