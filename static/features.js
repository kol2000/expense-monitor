'use strict';
function subOptions(category,selected=''){
 return '<option value="">Без уточнения</option>'+(data?.subcategories||[]).filter(s=>s.category===category).map(s=>`<option${s.name===selected?' selected':''}>${esc(s.name)}</option>`).join('');
}
function categoryColor(name){
 const fixed={'Продукты':'#51a58f','Кафе и доставка':'#c69b45','Покупки':'#ac7bcd','Связь и подписки':'#41a7bd','Отдых':'#8fa844','Прочее':'#7f91ac','Домашнее хозяйство':'#b58062','Дом и быт':'#b58062','Семья, подарки, материальная помощь':'#bf75a8','Семья и подарки':'#bf75a8','Медицина':'#d56c83','Здоровье':'#d56c83','Машина':'#e68d49','Налоги':'#a99b70','Дети':'#ddbd56','Транспорт':'#5488dc','Парикмахерская и уход за собой':'#db85ab'};
 if(name.startsWith('/')&&fixed[name.slice(1)])return fixed[name.slice(1)];
 let h=0;for(const c of name)h=(Math.imul(h,31)+c.codePointAt(0))|0;return `hsl(${(h>>>0)%360} 52% 57%)`;
}
function reportQuery(){
 const q=new URLSearchParams({month:$('month').value,period:$('reportPeriod').value});
 if($('reportPeriod').value==='range'){q.set('start',$('reportStart').value);q.set('end',$('reportEnd').value);}
 if($('compareEnabled').checked){q.set('compare_start',$('compareStart').value);q.set('compare_end',$('compareEnd').value);}
 return q;
}
function comparisonHTML(d,rows){
 if(!d.compare_start)return '';
 const old=d.comparison.filter(r=>(reportCategory===null||r.category===reportCategory)&&(reportDetail===null||detailOf(r)===reportDetail));
 const before=old.reduce((n,r)=>n+r.rubles,0),now=rows.reduce((n,r)=>n+r.rubles,0),delta=now-before;
 return `<article class="panel"><h2>Сравнение выбранных периодов</h2><div class="stats"><div><p>Выбранный период</p><h2>${money(now)}</h2></div><div><p>${esc(d.compare_start)} — ${esc(d.compare_end)}</p><h2>${money(before)}</h2></div><div><p>Разница</p><h2>${delta>0?'+':''}${money(delta)}</h2><p>${before?((delta/before)*100).toLocaleString('ru-RU',{maximumFractionDigits:1})+'%':'Процент не рассчитывается: база сравнения равна нулю'}</p></div></div><p class="muted">Сравниваются суммы за указанные даты. Проверьте равную длительность и полноту периодов.</p></article>`;
}
// Feature controls live in the existing settings page.
$('settings').insertAdjacentHTML('beforeend',`<article class="panel"><h2>Подкатегории</h2><p>Можно оставить расход без уточнения. Переименование в существующее название объединит подкатегории.</p><form id="subForm" class="feature-form"><label>Категория<select id="subParent" name="category"></select></label><label>Название<input name="name" maxlength="60" required></label><button>Добавить подкатегорию</button></form><div id="subList"></div></article>
<article class="panel"><h2>Правила для получателей</h2><p>Применяются к операциям Дзен-мани без индивидуального назначения категории. Правила, запомненные из операции, имеют приоритет; затем используется первое совпавшее правило в списке. Изменение или удаление правила сразу пересчитывает классификацию истории.</p><form id="ruleForm" class="feature-form"><input type="hidden" name="id"><label>Получатель<input name="pattern" maxlength="300" required></label><label>Совпадение<select name="mode"><option value="exact">Полное название</option><option value="contains">Содержит текст</option></select></label><label>Категория<select id="ruleCategory" name="category"></select></label><label>Подкатегория<select id="ruleSub" name="subcategory"></select></label><button>Сохранить правило</button><button type="button" id="ruleCancel" class="secondary">Сбросить форму</button></form><div id="ruleList"></div></article>
<article class="panel"><h2>Корзина ручных расходов</h2><p>Удалённые вручную записи не входят в отчёты. Их можно восстановить.</p><div id="trashList"></div></article>
<article class="panel"><h2>На телефоне</h2><p>Откройте приложение в браузере телефона и выберите «Добавить на главный экран». Для установки как отдельного приложения может потребоваться HTTPS. Черновик расхода сохраняется только в этом браузере; очистка данных браузера удалит его.</p></article>`);
$('expenseForm').insertAdjacentHTML('afterbegin','<input type="hidden" name="id">');
$('manualCategory').closest('label').insertAdjacentHTML('afterend','<label>Подкатегория<select id="manualSub" name="subcategory"></select></label>');
$('expenseForm').insertAdjacentHTML('beforeend','<button type="button" id="discardDraft" class="secondary">Очистить черновик</button>');
function renderFeatures(){
 for(const id of ['subParent','ruleCategory']){const old=$(id).value;$(id).innerHTML=options(old);}
 const selected=$('reportCategoryFilter').value;
 $('reportCategoryFilter').innerHTML='<option value="">Все категории</option>'+options(selected);
 $('manualSub').innerHTML=subOptions($('manualCategory').value);
 $('ruleSub').innerHTML=subOptions($('ruleCategory').value,$('ruleSub').value);
 $('subList').innerHTML=data.subcategories.map((s,i)=>`<div class="category-row"><span>${esc(s.category)}</span><input data-sub-name="${i}" value="${esc(s.name)}" maxlength="60" aria-label="Название подкатегории"><button data-sub-rename="${i}" class="secondary">Переименовать</button></div>`).join('');
 $('ruleList').innerHTML=data.rules.map(r=>`<div class="rule-row"><div><strong>${esc(r.pattern)}</strong><p>${r.mode==='exact'?'Полное совпадение':'Содержит'} → ${esc(r.category)}${r.subcategory?' / '+esc(r.subcategory):''} · ${esc({'':'Только категория',review:'Всегда на разбор',expense:'Считать расходом',ignored:'Не учитывать'}[r.action||''])}</p></div><button data-rule-edit="${r.id}" class="secondary">Изменить</button><button data-rule-delete="${r.id}" class="quiet">Удалить правило</button></div>`).join('')||'<p>Правил пока нет.</p>';
 $('trashList').innerHTML=data.trash.map(r=>`<div class="rule-row"><span>${esc(r.day)} · ${esc(r.payee||r.category)} · ${money(Math.ceil(r.amount/100))}</span><button data-restore="${esc(r.id)}" class="secondary">Восстановить</button></div>`).join('')||'<p>Корзина пуста.</p>';
}
function openExpense(record){
 const form=$('expenseForm');form.reset();
 const values=record?{...record,amount:(record.amount/100).toFixed(2)}:readDraft();
 form.elements.day.value=values.day||localDate();
 for(const key of ['id','amount','payee','note'])form.elements[key].value=values[key]||'';
 $('manualCategory').innerHTML=options(values.category||data.categories[0]);
 $('manualSub').innerHTML=subOptions($('manualCategory').value,values.subcategory);
 form.querySelector('h2').textContent=record?'Редактировать расход':'Добавить расход';
 $('discardDraft').hidden=!!record;
 $('expenseDialog').showModal();
}
function readDraft(){try{return JSON.parse(localStorage.getItem('expense-draft')||'{}');}catch{return {};}}
function saveDraft(){if($('expenseForm').elements.id.value)return;try{localStorage.setItem('expense-draft',JSON.stringify(Object.fromEntries(new FormData($('expenseForm')))));}catch{toast('Браузер не разрешил сохранить черновик');}}
$('expenseForm').addEventListener('input',saveDraft);
$('expenseForm').addEventListener('change',saveDraft);
$('manualCategory').onchange=()=>{$('manualSub').innerHTML=subOptions($('manualCategory').value);saveDraft();};
$('discardDraft').onclick=()=>{localStorage.removeItem('expense-draft');$('expenseDialog').close();openExpense();};
$('add').onclick=()=>openExpense();
$('expenseForm').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{
 const values=Object.fromEntries(new FormData(event.target));await api(values.id?'manual-update':'manual',values);
 if(!values.id)localStorage.removeItem('expense-draft');$('expenseDialog').close();await load();toast('Расход сохранён');
});};
$('ruleCategory').onchange=()=>{$('ruleSub').innerHTML=subOptions($('ruleCategory').value);};
$('ruleCancel').onclick=()=>{$('ruleForm').reset();$('ruleForm').elements.id.value='';$('ruleSub').innerHTML=subOptions($('ruleCategory').value);};
$('subForm').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{await api('subcategory',Object.fromEntries(new FormData(event.target)));event.target.elements.name.value='';await load();toast('Подкатегория добавлена');});};
$('ruleForm').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{await api('rule',Object.fromEntries(new FormData(event.target)));$('ruleCancel').click();await load();toast('Правило сохранено');});};
document.addEventListener('change',event=>{if(event.target.matches('[data-category]')){const editor=event.target.closest('.record-actions');editor.querySelector('[data-subcategory]').innerHTML=subOptions(event.target.value);}});
document.addEventListener('click',event=>{
 const b=event.target.closest('button');if(!b)return;
 if(b.dataset.autoCategory)busy(b,async()=>{const r=featureRecords.get(b.dataset.autoCategory);await api('edit',{id:r.id,decision:r.decision,reset_category:true});await load();toast('Автоматическая категория восстановлена');});
 if(b.dataset.manualEdit)openExpense(featureRecords.get(b.dataset.manualEdit));
 if(b.dataset.manualDelete)busy(b,async()=>{await api('manual-update',{id:b.dataset.manualDelete,action:'delete'});await load();toast('Запись в корзине. Восстановление — в «Категории и данные».');});
 if(b.dataset.restore)busy(b,async()=>{await api('manual-update',{id:b.dataset.restore,action:'restore'});await load();});
 if(b.hasAttribute('data-sub-rename'))busy(b,async()=>{const i=Number(b.dataset.subRename),s=data.subcategories[i];await api('subcategory',{category:s.category,old:s.name,name:document.querySelector(`[data-sub-name="${i}"]`).value});await load();});
 if(b.dataset.ruleDelete)busy(b,async()=>{await api('rule',{id:b.dataset.ruleDelete,delete:true});await load();});
 if(b.dataset.ruleFrom||b.dataset.ruleEdit){
  const r=b.dataset.ruleFrom?featureRecords.get(b.dataset.ruleFrom):data.rules.find(r=>String(r.id)===b.dataset.ruleEdit);
  showPage('settings');const f=$('ruleForm');f.elements.id.value=b.dataset.ruleFrom?'':r.id;f.elements.pattern.value=r.pattern||r.payee;f.elements.mode.value=r.mode||'exact';f.elements.action.value=r.action||'';$('ruleCategory').value=r.category;$('ruleSub').innerHTML=subOptions(r.category,r.subcategory);f.scrollIntoView({behavior:'smooth',block:'center'});
 }
});
let attentionSequence=0;
async function loadAttention(){
 const sequence=++attentionSequence;$('attentionContent').innerHTML='<p>Загрузка…</p>';
 const r=await api('attention?month='+encodeURIComponent($('month').value));
 if(sequence!==attentionSequence||currentPage!=='attention')return;
 renderAttention(r);
}
function renderAttention(r){
 const section=(title,rows,hint)=>`<article class="panel"><h2>${title} (${rows.length})</h2><p>${hint}</p>${rows.map(x=>rowHTML(x,true)).join('')||'<p class="muted">Нет операций</p>'}</article>`;
 $('attentionContent').innerHTML=section('Банк обрабатывает покупку',r.pending,'Ждём окончательного списания. Повторно добавлять расход не нужно.')+section('Проверить назначение',r.review,'Уточните, является ли операция расходом или переводом.')+section('Уточнить категорию',r.category,'Расходы в «Прочем» без индивидуального назначения или правила.')+`<article class="panel"><h2>Возможные дубли (${r.duplicates.length})</h2><p>Одинаковая сумма после округления, даты отличаются не больше чем на три дня. Совпадение не доказывает дублирование.</p>${r.duplicates.map(p=>`<div class="review-pair"><div class="pair-columns">${rowHTML(p.manual,true)}${rowHTML(p.bank,true)}</div><button data-duplicate-dismiss="${esc(p.manual.id)}" data-other="${esc(p.bank.id)}" class="secondary">Это разные расходы</button><button data-duplicate-exclude="${esc(p.manual.id)}" class="secondary">Исключить ручную запись</button></div>`).join('')||'<p>Совпадений нет</p>'}</article>`;
}
$('reviewOpen').onclick=()=>showPage('attention');
document.addEventListener('click',event=>{
 const b=event.target.closest('button');if(!b)return;
 if(b.dataset.duplicateDismiss)busy(b,async()=>{await api('review',{kind:'duplicate',ids:[b.dataset.duplicateDismiss,b.dataset.other]});await loadAttention();});
 if(b.dataset.duplicateExclude)busy(b,async()=>{const r=featureRecords.get(b.dataset.duplicateExclude);await api('edit',{id:r.id,category:r.category,subcategory:r.subcategory,decision:'ignored'});await load();toast('Ручная запись исключена. Вернуть можно в «Операции → Исключённые».');});
 if(b.dataset.checkIds)busy(b,async()=>{await api('review',{kind:'reconcile',ids:JSON.parse(b.dataset.checkIds),undo:b.dataset.undo==='true'});await runReconcile();});
});
function checkButton(rows,checked){return `<button class="secondary" data-check-ids="${esc(JSON.stringify(rows.map(r=>r.id)))}" data-undo="${checked}">${checked?'Проверено · отменить отметку':'Отметить проверенным'}</button>`;}
async function runReconcile(){
 const r=await api('reconcile?month='+encodeURIComponent($('month').value));
 const single=(row)=>`<div class="review-pair">${rowHTML(row)}${checkButton([row],row.checked)}</div>`;
 $('comparison').innerHTML=`<div class="stats"><div><p>EasyFinance</p><h2>${money(r.easyfinance_total)}</h2></div><div><p>Новый учёт</p><h2>${money(r.app_total)}</h2></div><div><p>Разница</p><h2>${money(r.app_total-r.easyfinance_total)}</h2></div></div><p>${esc(r.note)}</p>
 <h2>Пары операций (${r.pairs.length})</h2>${r.pairs.map(p=>`<div class="review-pair"><p><strong>${p.exact?'Совпадают дата и сумма':'Возможное совпадение'}</strong> · Разница: ${money(p.app.rubles-p.easyfinance.rubles)} · Даты: ${esc(p.easyfinance.day)} / ${esc(p.app.day)}</p><div class="pair-columns">${rowHTML(p.easyfinance)}${rowHTML(p.app)}</div>${checkButton([p.easyfinance,p.app],p.checked)}</div>`).join('')}
 <h2>Без пары в EasyFinance (${r.easyfinance_only.length})</h2>${r.easyfinance_only.map(single).join('')}<h2>Без пары в новом учёте (${r.app_only.length})</h2>${r.app_only.map(single).join('')}`;
}
$('reconcileRun').onclick=()=>busy($('reconcileRun'),runReconcile);
$('reportPeriod').onchange=()=>{updatePeriodLabels();$('reportRange').hidden=$('reportPeriod').value!=='range';if($('reportPeriod').value!=='range')loadReports().catch(e=>toast(e.message));};
$('compareEnabled').onchange=()=>{$('compareRange').hidden=!$('compareEnabled').checked;};
$('reportStart').value=localDate().slice(0,4)+'-01-01';$('reportEnd').value=localDate();
$('compareStart').value=(Number(localDate().slice(0,4))-1)+'-01-01';$('compareEnd').value=(Number(localDate().slice(0,4))-1)+'-12-31';
$('reportApply').onclick=()=>busy($('reportApply'),loadReports);
$('reportCategoryFilter').onchange=()=>{reportCategory=$('reportCategoryFilter').value||null;reportDetail=null;if(reportData)renderReport();};
// Keep financial data out of an offline cache. The manifest supplies home-screen launch metadata.

// Refresh only read views. Never rebuild settings or an open editor in the background.
let refreshRunning=false, interactionRevision=0, lastRefreshAttempt=0;
for(const type of ['input','change','click','toggle'])document.addEventListener(type,()=>interactionRevision++,true);
function canAutoRefresh(){
 return data&&!document.hidden&&!$('workspace').hidden&&!activeActions&&
  ['overview','transactions','attention'].includes(currentPage)&&
  !document.querySelector('dialog[open], .record-editor[open], .compact-operation[open]')&&
  !document.activeElement?.matches('input,select,textarea,[contenteditable="true"]');
}
async function autoRefresh(){
 if(refreshRunning||!canAutoRefresh()||Date.now()-lastRefreshAttempt<1000)return;
 refreshRunning=true;lastRefreshAttempt=Date.now();
 const page=currentPage,month=$('month').value,revision=interactionRevision,sequence=loadSequence,attentionVersion=attentionSequence;
 try{
  const [next,attention]=await Promise.all([
   api('dashboard?month='+encodeURIComponent(month)),
   page==='attention'?api('attention?month='+encodeURIComponent(month)):Promise.resolve(null)
  ]);
  // The user may have started editing or navigated while requests were in flight.
  if(!canAutoRefresh()||revision!==interactionRevision||sequence!==loadSequence||
     attentionVersion!==attentionSequence||page!==currentPage||month!==$('month').value)return;
  const x=window.scrollX,y=window.scrollY;
  const changed=JSON.stringify(data)!==JSON.stringify(next);
  data=next;
  if(changed){renderOverview();if(page==='transactions')renderRows();}
  if(attention)renderAttention(attention);
  window.scrollTo(x,y);
 }catch(error){
  // Keep the last successful view on transient network failures; retry next tick.
 }finally{refreshRunning=false;}
}
setInterval(autoRefresh,5*60*1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)autoRefresh();});
window.addEventListener('focus',autoRefresh);
// Initial load runs after improvements.js has installed its controls.
