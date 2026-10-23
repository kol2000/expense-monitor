"""Expense ledger. All stored money is integer kopecks; reports round each expense up."""
import csv
import calendar
import hashlib
import io
import json
import re
import sqlite3
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta
from features import Features
from decimal import Decimal, InvalidOperation
from pathlib import Path

CATEGORIES = ['Продукты', 'Кафе и доставка', 'Дом и быт', 'Транспорт', 'Здоровье',
              'Покупки', 'Связь и подписки', 'Отдых', 'Семья и подарки', 'Прочее']

def cents(value):
    try:
        n = Decimal(str(value).replace(' ', '').replace('\xa0', '').replace(',', '.'))
        if not n.is_finite() or n < 0 or n > Decimal('1000000000') or n * 100 != (n * 100).to_integral_value():
            raise ValueError()
        return int(n * 100)
    except (InvalidOperation, ValueError):
        raise ValueError('Сумма должна быть положительной, максимум два знака после запятой') from None

def rubles(value):
    return (value + 99) // 100

def valid_date(value):
    return date.fromisoformat(value).isoformat()

def suggested_category(label):
    text = label.casefold()
    groups = [
        ('Парикмахерская и уход за собой', ['парикмахер', 'салон красоты', 'салоны красоты', 'уход за собой']),
        ('Семья и подарки', ['дети', 'детск', 'детей', 'подар', 'семь', 'материальная помощь']),
        ('Кафе и доставка', ['кафе', 'ресторан', 'фастфуд', 'еда вне', 'питание вне']),
        ('Продукты', ['продукт', 'супермаркет', 'питание дома']),
        ('Здоровье', ['здоров', 'медицин', 'лекарств', 'аптек']),
        ('Транспорт', ['транспорт', 'автомоб', 'такси', 'бензин', 'парков', 'проезд']),
        ('Связь и подписки', ['связь', 'интернет', 'телефон', 'подписк']),
        ('Дом и быт', ['коммун', 'жиль', 'квартир', 'ремонт', 'для дома', 'быт', 'домашнее хозяйство', 'дом, уют', 'животн', 'корм']),
        ('Отдых', ['развлеч', 'отдых', 'путешеств', 'хобби', 'спорт']),
        ('Семья и подарки', ['подар', 'семь', 'дети', 'благотвор']),
        ('Покупки', ['одежд', 'обув', 'техник', 'космет', 'покупк', 'уход за собой']),
    ]
    return next((cat for cat, keys in groups if any(k in text for k in keys)), 'Прочее')

def available_category(db, suggested):
    if db.execute('SELECT 1 FROM categories WHERE name=?',(suggested,)).fetchone():
        return suggested
    return db.execute("SELECT name FROM categories ORDER BY (name='Прочее') DESC,rowid LIMIT 1").fetchone()[0]

def resolve_category(db, original):
    names = {r[0].strip().casefold(): r[0] for r in db.execute('SELECT name FROM categories')}
    key = original.strip().casefold()
    if key in names:
        return names[key]
    renamed = db.execute('SELECT category FROM category_aliases WHERE original=?',(key,)).fetchone()
    if renamed and renamed[0].casefold() in names:
        return names[renamed[0].casefold()]
    groups = [('Дом и быт','Домашнее хозяйство','Дом, уют'),
              ('Здоровье','Медицина','Здоровье и Фитнес'),
              ('Машина','Автомобиль'),
              ('Семья и подарки','Семья, подарки, материальная помощь','Подарки'),
              ('Кафе и доставка','Кафе и рестораны'),
              ('Парикмахерская и уход за собой','Забота о себе')]
    suggested = suggested_category(original)
    for group in groups:
        if key in [v.casefold() for v in group] or suggested in group:
            for value in group:
                if value.casefold() in names:
                    return names[value.casefold()]
    renamed = db.execute('SELECT category FROM category_aliases WHERE original=?',(suggested.casefold(),)).fetchone()
    return available_category(db, renamed[0] if renamed else suggested)

def zen_category_key(original, payee):
    # Generic service labels cannot classify every merchant as a phone bill.
    if original.casefold().strip() in ('', 'услуги', 'прочее') and payee.casefold().strip() in ('мтс', 'mts'):
        return 'Дзен-мани: МТС (связь)'
    return original

def own_transfer(item, entities):
    oa, ia = str(item.get('outcomeAccount','')), str(item.get('incomeAccount',''))
    account, other = entities.get(('account',oa),{}), entities.get(('account',ia),{})
    return bool(cents(item.get('outcome',0)) and cents(item.get('income',0)) and oa != ia
                and account and other and account.get('type') != 'debt' and other.get('type') != 'debt')

def transfer_hint(item, original):
    """Conservative review hints, never proof that a transfer is an expense."""
    text = ' '.join(str(value or '') for value in
                    (original, item.get('payee'), item.get('comment'))).casefold()
    if any(word in text for word in ('перевод', 'сбп', 'transfer', 'card2card', 'p2p')):
        return True
    payee = str(item.get('payee') or '').strip()
    # Russian personal names with initials or a patronymic. Do not classify
    # arbitrary two-word merchant names as people.
    initials = r'[А-ЯЁа-яё][а-яё-]+\s+(?:[А-ЯЁа-яё][а-яё-]+\s+)?[А-ЯЁа-яё]\.(?:\s*[А-ЯЁа-яё]\.)?'
    patronymic = r'(?:[А-ЯЁа-яё][а-яё-]+\s+){1,2}[А-ЯЁа-яё][а-яё-]*(?:вич|вна|ична)(?:\s+[А-ЯЁа-яё][а-яё-]+)?'
    return bool(re.fullmatch(initials, payee) or re.fullmatch(patronymic, payee))


def zen_kind(item, entities, original):
    """All imported amounts are RUB; only settlement and transfer evidence affect review."""
    out, inc = cents(item.get('outcome', 0)), cents(item.get('income', 0))
    if not out:
        return 'income'
    if own_transfer(item, entities):
        return 'ignored'
    if item.get('hold'):
        return 'review'
    oa, ia = str(item.get('outcomeAccount', '')), str(item.get('incomeAccount', ''))
    account, other = entities.get(('account', oa), {}), entities.get(('account', ia), {})
    if inc > 0 and oa != ia:
        return 'ignored' if account and other and account.get('type') != 'debt' and other.get('type') != 'debt' else 'review'
    if transfer_hint(item, original):
        return 'review'
    return 'expense'

class Ledger(Features):
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS categories(name TEXT PRIMARY KEY);
              CREATE TABLE IF NOT EXISTS mapping(original TEXT PRIMARY KEY,category TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS category_aliases(original TEXT PRIMARY KEY,category TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS records(
                id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
                day TEXT NOT NULL, amount INTEGER NOT NULL, category TEXT NOT NULL,
                original_category TEXT NOT NULL DEFAULT '', account TEXT NOT NULL DEFAULT '',
                payee TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL, decision TEXT, custom_category TEXT,
                deleted INTEGER NOT NULL DEFAULT 0, raw TEXT NOT NULL DEFAULT '{}',
                UNIQUE(source,source_id));
              CREATE INDEX IF NOT EXISTS records_day ON records(day);
              CREATE TABLE IF NOT EXISTS entities(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id));
              CREATE TABLE IF NOT EXISTS imports(hash TEXT PRIMARY KEY,created TEXT,count INTEGER);
            ''')
            self.feature_init(db)
            if not db.execute("SELECT 1 FROM settings WHERE key='categories_initialized'").fetchone():
                db.executemany('INSERT OR IGNORE INTO categories VALUES(?)', [(c,) for c in CATEGORIES])
                db.execute("INSERT INTO settings VALUES('categories_initialized','1')")
            db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', ('cutover','2026-10-15'))
            if not db.execute("SELECT 1 FROM settings WHERE key='transport_care_v1'").fetchone():
                db.executemany('INSERT OR IGNORE INTO categories VALUES(?)',
                               [('Транспорт',), ('Парикмахерская и уход за собой',)])
                # Only unambiguous source labels move; individual overrides remain intact.
                for original, category in [
                    ('Такси','Транспорт'), ('Общественный транспорт','Транспорт'),
                    ('Проезд, транспорт','Транспорт'), ('Проезд','Транспорт'),
                    ('Салоны красоты и парикмахерские','Парикмахерская и уход за собой'),
                    ('Уход за собой','Парикмахерская и уход за собой')]:
                    db.execute('UPDATE mapping SET category=? WHERE original=?',(category,original))
                    db.execute('UPDATE records SET category=? WHERE original_category=?',(category,original))
                db.execute("INSERT INTO settings VALUES('transport_care_v1','1')")
            if not db.execute("SELECT 1 FROM settings WHERE key='zen_categories_v2'").fetchone():
                for row in db.execute("SELECT * FROM records WHERE source='zenmoney'").fetchall():
                    original = zen_category_key(row['original_category'], row['payee'])
                    if original == row['original_category']:
                        continue
                    category = available_category(db, suggested_category(original))
                    db.execute('INSERT OR IGNORE INTO mapping VALUES(?,?)', (original, category))
                    category = db.execute('SELECT category FROM mapping WHERE original=?', (original,)).fetchone()[0]
                    db.execute('UPDATE records SET original_category=?,category=? WHERE id=?',
                               (original, category if row['category']=='Прочее' else row['category'], row['id']))
                db.execute("INSERT INTO settings VALUES('zen_categories_v2','1')")
            if not db.execute("SELECT 1 FROM settings WHERE key='owned_transfers_v2'").fetchone():
                entities = {(r['kind'],r['id']):json.loads(r['data']) for r in db.execute('SELECT * FROM entities')}
                for row in db.execute("SELECT * FROM records WHERE source='zenmoney'").fetchall():
                    raw = json.loads(row['raw'])
                    raw['_blocked'] = bool(cents(raw.get('outcome',0)) and raw.get('hold'))
                    db.execute('UPDATE records SET kind=?,raw=? WHERE id=?',
                               (zen_kind(raw,entities,row['original_category']),json.dumps(raw,ensure_ascii=False),row['id']))
                db.execute("INSERT INTO settings VALUES('owned_transfers_v2','1')")


    @contextmanager
    def db(self, audit=None):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                if audit:
                    db.execute("BEGIN IMMEDIATE")
                    before = self.snapshot(db)
                yield db
                if audit: self.audit_changes(db,before,audit)
        finally:
            db.close()

    def setting(self, key, default=''):
        with self.db() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return row[0] if row else default

    def set_setting(self, key, value):
        with self.db(audit='Дата перехода' if key=='cutover' else None) as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key,value))

    def import_csv(self, content):
        digest = hashlib.sha256(content).hexdigest()
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = content.decode('cp1251')
        reader = csv.DictReader(io.StringIO(text), delimiter=';', strict=True)
        required = {'дата','тип операции','сумма в валюте по умолчанию','категория'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Ожидается CSV операций EasyFinance с разделителем ;')
        parsed, occurrences = [], Counter()
        for line, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f'Строка {line}: неверное число столбцов')
            if row['тип операции'].strip().lower() != 'расход':
                raise ValueError(f'Строка {line}: импорт поддерживает только расходы')
            try:
                day = datetime.strptime(row['дата'], '%d-%m-%Y').date().isoformat()
                amount = cents(row['сумма в валюте по умолчанию'])
                if not amount: raise ValueError('Нулевая сумма')
            except ValueError as e:
                raise ValueError(f'Строка {line}: {e}') from None
            raw = json.dumps(row, ensure_ascii=False, sort_keys=True)
            signature = hashlib.sha256(raw.encode()).hexdigest()
            occurrences[signature] += 1
            parsed.append((f'{signature}:{occurrences[signature]}',day,amount,row,raw))
        if not parsed: raise ValueError('Файл пуст')
        inserted = 0
        with self.db(audit='Импорт EasyFinance') as db:
            for sid, day, amount, row, raw in parsed:
                original = row['категория'].strip() or 'Без категории'
                if original == 'Разное':
                    original += ' / ' + row.get('системная категория','Без группы')
                category = available_category(db,suggested_category(original + ' ' + row.get('системная категория','')))
                db.execute('INSERT OR IGNORE INTO mapping VALUES(?,?)',(original,category))
                category = db.execute('SELECT category FROM mapping WHERE original=?',(original,)).fetchone()[0]
                cur = db.execute('''INSERT OR IGNORE INTO records
                    (id,source,source_id,day,amount,category,original_category,account,note,kind,raw)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                    ('ef:'+sid,'easyfinance',sid,day,amount,category,original,row.get('счет откуда',''),row.get('комментарий',''),'expense',raw))
                inserted += cur.rowcount
            db.execute('INSERT OR IGNORE INTO imports VALUES(?,?,?)',
                       (digest,datetime.now(timezone.utc).isoformat(),len(parsed)))
        return {'rows':len(parsed),'inserted':inserted,'already_present':len(parsed)-inserted,
                'identical_pairs_extra':sum(n-1 for n in occurrences.values()),
                'total_rubles':sum(rubles(p[2]) for p in parsed)}

    def manual(self, values):
        amount = cents(values['amount'])
        if not amount: raise ValueError('Укажите сумму больше нуля')
        day = valid_date(values['day'])
        category = str(values['category'])
        with self.db(audit="Добавление ручного расхода") as db:
            if not db.execute('SELECT 1 FROM categories WHERE name=?',(category,)).fetchone():
                raise ValueError('Неизвестная категория')
            sid = str(uuid.uuid4())
            db.execute('''INSERT INTO records(id,source,source_id,day,amount,category,payee,note,kind)
                VALUES(?,?,?,?,?,?,?,?,?)''', (sid,'manual',sid,day,amount,category,
                str(values.get('payee',''))[:300],str(values.get('note',''))[:2000],'expense'))
            self.set_detail(db,sid,category,str(values.get('subcategory','')))
        return {'id':sid}

    def edit(self, values):
        with self.db(audit="Изменение операции") as db:
            record = db.execute('SELECT * FROM records WHERE id=?',(values['id'],)).fetchone()
            if not record:
                raise ValueError('Операция не найдена')
            entities = {(r['kind'],r['id']):json.loads(r['data']) for r in db.execute('SELECT * FROM entities')}
            if values.get('decision')=='expense' and record['source']=='zenmoney' and own_transfer(json.loads(record['raw']),entities):
                raise ValueError('Перевод между своими счетами не является расходом.')
            decision = values.get('decision')
            if decision not in (None,'expense','ignored','review'):
                raise ValueError('Неизвестное решение')
            if decision == 'expense' and json.loads(record['raw']).get('_blocked'):
                raise ValueError('Операция ожидает окончательного списания банком. Дождитесь обновления из Дзен-мани; повторно вручную расход добавлять не нужно.')
            category = values.get('category') or None
            if category and not db.execute('SELECT 1 FROM categories WHERE name=?',(category,)).fetchone():
                raise ValueError('Неизвестная категория')
            db.execute('UPDATE records SET decision=?,custom_category=? WHERE id=?',(decision,category,values['id']))
            if values.get('reset_category'):
                db.execute('UPDATE records SET custom_category=NULL WHERE id=?',(values['id'],))
                db.execute('DELETE FROM record_details WHERE id=?',(values['id'],))
            if values.get('remember'):
                if record['source']!='zenmoney' or not record['payee'].strip(): raise ValueError('Правило доступно для получателя из Дзен-мани')
                self.save_rule(db,dict(pattern=record['payee'],mode='exact',category=category or record['category'],subcategory=str(values.get('subcategory','')),action=values.get('future_action',''),preferred=True))
            if 'subcategory' in values:
                self.set_detail(db,values['id'],category or record['category'],str(values['subcategory']))

    def map_category(self, original, category):
        with self.db(audit="Соответствие категории") as db:
            if not db.execute('SELECT 1 FROM categories WHERE name=?',(category,)).fetchone():
                raise ValueError('Неизвестная категория')
            db.execute('INSERT OR REPLACE INTO mapping VALUES(?,?)',(original,category))
            db.execute('UPDATE records SET category=? WHERE original_category=?',(category,original))

    def categories(self):
        with self.db() as db:
            return [r[0] for r in db.execute('SELECT name FROM categories ORDER BY rowid')]

    def add_category(self, name):
        name = str(name).strip()
        if not 1 <= len(name) <= 60: raise ValueError('Название: от 1 до 60 символов')
        with self.db(audit="Новая категория") as db: db.execute('INSERT OR IGNORE INTO categories VALUES(?)',(name,))

    def rename_category(self, old, new):
        new = str(new).strip()
        if not 1 <= len(new) <= 60: raise ValueError('Название: от 1 до 60 символов')
        with self.db(audit="Переименование категории") as db:
            if not db.execute('SELECT 1 FROM categories WHERE name=?',(old,)).fetchone():
                raise ValueError('Категория не найдена')
            db.execute('INSERT OR IGNORE INTO categories VALUES(?)',(new,))
            db.execute('UPDATE category_aliases SET category=? WHERE category=?',(new,old))
            db.execute('INSERT OR REPLACE INTO category_aliases VALUES(?,?)',(old.strip().casefold(),new))
            db.execute('UPDATE records SET category=? WHERE category=?',(new,old))
            db.execute('UPDATE records SET custom_category=? WHERE custom_category=?',(new,old))
            db.execute('UPDATE mapping SET category=? WHERE category=?',(new,old))
            if old != new:
                db.execute('INSERT OR IGNORE INTO subcategories SELECT ?,name FROM subcategories WHERE category=?',(new,old))
                db.execute('DELETE FROM subcategories WHERE category=?',(old,))
                db.execute('UPDATE record_details SET category=? WHERE category=?',(new,old))
                db.execute('UPDATE rules SET category=? WHERE category=?',(new,old))
            for row in db.execute('SELECT * FROM splits').fetchall():
                parts=json.loads(row['parts'])
                for part in parts:
                    if part['category']==old: part['category']=new
                db.execute('UPDATE splits SET parts=? WHERE id=?',(json.dumps(parts,ensure_ascii=False),row['id']))
            if old != new: db.execute('DELETE FROM categories WHERE name=?',(old,))

    def apply_diff(self, diff):
        """Apply all entities, records and sync cursor in one transaction; preserve user decisions."""
        if not isinstance(diff,dict) or not isinstance(diff.get('serverTimestamp'),int):
            raise ValueError('Неверный ответ API Дзен-мани')
        with self.db() as db:
            for kind in ('account','tag','instrument'):
                for item in diff.get(kind,[]):
                    db.execute('INSERT OR REPLACE INTO entities VALUES(?,?,?)',(kind,str(item['id']),json.dumps(item,ensure_ascii=False)))
            entities = {(r['kind'],r['id']):json.loads(r['data']) for r in db.execute('SELECT * FROM entities')}
            sync_before={r['source_id']:dict(r) for r in db.execute("SELECT * FROM records WHERE source='zenmoney'")}
            for item in diff.get('transaction',[]):
                sid = str(item['id'])
                if item.get('deleted'):
                    db.execute("UPDATE records SET deleted=1 WHERE source='zenmoney' AND source_id=?",(sid,))
                    continue
                out, inc = cents(item.get('outcome',0)), cents(item.get('income',0))
                oa, ia = str(item.get('outcomeAccount','')), str(item.get('incomeAccount',''))
                account = entities.get(('account',oa),{})
                original = ' / '.join(entities.get(('tag',str(t)),{}).get('title','') for t in (item.get('tag') or []))
                original = zen_category_key(original, item.get('payee') or '')
                kind = zen_kind(item, entities, original)
                item = dict(item)
                item['_blocked'] = bool(out and item.get('hold'))
                if item['_blocked']:
                    db.execute("UPDATE records SET decision=NULL WHERE source='zenmoney' AND source_id=? AND decision='expense'",(sid,))
                mapped = db.execute('SELECT category FROM mapping WHERE original=?',(original,)).fetchone()
                category = mapped[0] if mapped else resolve_category(db,original)
                db.execute('INSERT OR IGNORE INTO mapping VALUES(?,?)',(original,category))
                db.execute('''INSERT INTO records(id,source,source_id,day,amount,category,original_category,account,payee,note,kind,deleted,raw)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,source_id) DO UPDATE SET
                    day=excluded.day,amount=excluded.amount,category=excluded.category,original_category=excluded.original_category,
                    account=excluded.account,payee=excluded.payee,note=excluded.note,kind=excluded.kind,deleted=0,raw=excluded.raw''',
                    ('zm:'+sid,'zenmoney',sid,valid_date(item['date']),out,category,original,
                     account.get('title',oa),item.get('payee') or '',item.get('comment') or '',kind,0,json.dumps(item,ensure_ascii=False)))
            for item in diff.get('deletion',[]):
                if item['object'] == 'transaction':
                    db.execute("UPDATE records SET deleted=1 WHERE source='zenmoney' AND source_id=?",(str(item['id']),))
                else:
                    db.execute('DELETE FROM entities WHERE kind=? AND id=?',(item['object'],str(item['id'])))
            sync_after={r['source_id']:dict(r) for r in db.execute("SELECT * FROM records WHERE source='zenmoney'")}
            added=sum(k not in sync_before and not v['deleted'] for k,v in sync_after.items())
            updated=sum(k in sync_before and not v['deleted'] and v!=sync_before[k] for k,v in sync_after.items())
            deleted=sum(k in sync_before and v['deleted'] and not sync_before[k]['deleted'] for k,v in sync_after.items())
            stats={'added':added,'updated':updated,'deleted':deleted}
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('sync_stats',json.dumps(stats)))
            if added or updated or deleted:
                db.execute('INSERT INTO audit(created,label,changes,undoable) VALUES(?,?,?,0)',
                    (datetime.now(timezone.utc).isoformat(),f'Дзен-мани: добавлено {added}, изменено {updated}, удалено {deleted}',json.dumps([{'table':'records','key':[k],'before':sync_before.get(k),'after':v} for k,v in sync_after.items() if sync_before.get(k)!=v],ensure_ascii=False)))
            for key,value in [('zen_cursor',str(diff['serverTimestamp'])),('last_sync',datetime.now(timezone.utc).isoformat()),('sync_error','')]:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,value))
        return {'received':len(diff.get('transaction',[]))}

    def records(self, month=''):
        if month:
            datetime.strptime(month,'%Y-%m')
            if len(month)!=7: raise ValueError('Неверный месяц')
        with self.db() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM records WHERE deleted=0 AND day LIKE ? ORDER BY day DESC,id',(month+'%',))]
            entities = {(r['kind'],r['id']):json.loads(r['data']) for r in db.execute('SELECT * FROM entities')}
        cutover = self.setting('cutover')
        for r in rows:
            raw = json.loads(r.pop('raw'))
            # Re-evaluate saved imports too, without overwriting explicit decisions.
            if r['source']=='zenmoney':
                r['kind'] = zen_kind(raw, entities, r['original_category'])
            # Keep the expense amount unchanged; display receipts separately, including old imports.
            r['display_amount'] = cents(raw.get('income',0)) if r['source']=='zenmoney' and r['kind']=='income' else r['amount']
            r['display_currency'] = 'RUB'
            r['own_transfer'] = r['source']=='zenmoney' and own_transfer(raw,entities)
            r['pending'] = bool(raw.get('hold')) and not r['own_transfer']
            r['account_to'] = entities.get(('account',str(raw.get('incomeAccount',''))),{}).get('title','') if r['own_transfer'] else ''
            if r['own_transfer']:
                r['kind'] = 'ignored'
            r['status'] = 'ignored' if r['own_transfer'] else r['decision'] or r['kind']
            r['review_reason'] = ''
            if r['status']=='review':
                r['review_reason'] = ('Ожидает окончательного списания банком. Обновится из Дзен-мани.'
                                      if raw.get('hold') else 'Возможный перевод физлицу. Выберите «Считать расходом» или «Не учитывать». До решения в расходы не входит.')
            r['category'] = r['custom_category'] or r['category']
            r['rubles'] = rubles(r['amount'])
            r['primary'] = (r['source']=='easyfinance' and r['day']<cutover) or (r['source']!='easyfinance' and r['day']>=cutover)
            r['included'] = r['primary'] and r['status']=='expense'
        return self.enrich(rows)

    def accounts(self):
        """Current upstream balances, independent of expense filters and cutover."""
        with self.db() as db:
            entities = [json.loads(r[0]) for r in db.execute("SELECT data FROM entities WHERE kind='account'")]
        rows = []
        for account in entities:
            balance = None
            try:
                value = Decimal(str(account.get('balance')))
                if value.is_finite():
                    balance = int((value * 100).quantize(Decimal('1')))
            except (InvalidOperation, ValueError, TypeError):
                pass
            archived = bool(account.get('archive'))
            included = bool(account.get('inBalance')) and not archived and account.get('type') != 'debt'
            rows.append({'id':account['id'], 'title':account.get('title') or 'Без названия',
                         'type':account.get('type') or '', 'balance':balance,
                         'archived':archived, 'included':included,
                         'savings':bool(account.get('savings'))})
        rows.sort(key=lambda r:(r['archived'], not r['included'], r['title'].casefold()))
        return {'rows':rows, 'total':sum(r['balance'] for r in rows if r['included'] and r['balance'] is not None),
                'missing':sum(r['included'] and r['balance'] is None for r in rows),
                'included_count':sum(r['included'] for r in rows)}

    def dashboard(self, month):
        rows = self.records(month)
        included = [r for r in rows if r['included']]
        totals = Counter()
        for r in self.expense_parts(included): totals[r['category']] += r['rubles']
        with self.db() as db:
            mapping = [dict(r) for r in db.execute('SELECT * FROM mapping ORDER BY original')]
            for entry in mapping:
                entry['suggested'] = resolve_category(db, entry['original'])
            months = [r[0] for r in db.execute('SELECT DISTINCT substr(day,1,7) FROM records WHERE deleted=0 ORDER BY 1 DESC')]
        today = datetime.now(timezone(timedelta(hours=3))).date().isoformat()
        year_start = today[:4]+'-01-01'
        year_total = sum(r['rubles'] for r in self.records()
                         if r['included'] and year_start <= r['day'] <= today)
        attention=self.attention(month)
        return {'accounts':self.accounts(),'insights':self.insights(month or today[:7]),'sync_stats':json.loads(self.setting('sync_stats','{}')),'year_total':year_total,'year_start':year_start,'year_end':today,
                'rows':rows,'total':sum(r['rubles'] for r in included),'count':len(included),
                'review':len({r['id'] for key in ('review','category') for r in attention[key]} | {r['id'] for pair in attention['duplicates'] for r in pair.values()}),
                'pending_count':len(attention['pending']),
                'totals':dict(totals.most_common()),'categories':self.categories(),'mapping':mapping,'months':months,
                'cutover':self.setting('cutover'),'last_sync':self.setting('last_sync'),'sync_error':self.setting('sync_error'),
                **self.feature_settings()}

    def reports(self, month, period='month', start='', end='', compare_start='', compare_end=''):
        if period not in ('month','year','quarter','range'):
            raise ValueError('Выберите месяц, квартал, год или диапазон')
        if len(month)!=7:
            raise ValueError('Неверный месяц')
        anchor = date.fromisoformat(month+'-01')
        if period=='range':
            first,last=date.fromisoformat(start),date.fromisoformat(end)
        elif period=='year': first,last=date(anchor.year,1,1),date(anchor.year,12,31)
        elif period=='quarter':
            m=(anchor.month-1)//3*3+1
            first,last=date(anchor.year,m,1),date(anchor.year,m+2,calendar.monthrange(anchor.year,m+2)[1])
        else: first,last=anchor,date(anchor.year,anchor.month,calendar.monthrange(anchor.year,anchor.month)[1])
        if last<first or (last-first).days>3660: raise ValueError('Диапазон: от одного дня до десяти лет')
        all_rows=self.expense_parts([r for r in self.records() if r['included']])
        rows=[r for r in all_rows if first.isoformat()<=r['day']<=last.isoformat()]
        daily=period=='month' or (period=='range' and (last-first).days<62)
        if daily: keys=[(first+timedelta(days=i)).isoformat() for i in range((last-first).days+1)]
        else:
            keys=[]; index=first.year*12+first.month-1
            while index<=last.year*12+last.month-1:
                keys.append(f'{index//12:04d}-{index%12+1:02d}');index+=1
        comparison=[]
        if compare_start or compare_end:
            cs,ce=date.fromisoformat(compare_start),date.fromisoformat(compare_end)
            if ce<cs or (ce-cs).days>3660: raise ValueError('Неверный период сравнения')
            comparison=[r for r in all_rows if cs.isoformat()<=r['day']<=ce.isoformat()]
        return {'rows':rows,'comparison':comparison,'compare_start':compare_start,'compare_end':compare_end,
                'start':first.isoformat(),'end':last.isoformat(),'daily':daily,
                'buckets':keys,'period':period,'month':month,
                'total':sum(r['rubles'] for r in rows),'count':len({r['id'] for r in rows})}

    def reconcile(self, month):
        rows = self.records(month)
        ef = [r for r in rows if r['source']=='easyfinance' and r['status']=='expense']
        app = [r for r in rows if r['source']!='easyfinance' and r['status']=='expense']
        # Multiset matching: never collapse legitimate repeated equal purchases.
        buckets = {}
        for r in app: buckets.setdefault((r['day'],r['rubles']),[]).append(r)
        matches, missing, exact_pairs = [], [], []
        for r in ef:
            candidates = buckets.get((r['day'],r['rubles']),[])
            if candidates:
                other=candidates.pop()
                matches.append({'easyfinance':r['id'],'app':other['id']})
                exact_pairs.append({'easyfinance':r,'app':other,'exact':True})
            else: missing.append(r)
        return self.reconcile_pairs({'matched':len(matches),'exact_pairs':exact_pairs,'easyfinance_only':missing,'app_only':[r for group in buckets.values() for r in group],
                'easyfinance_total':sum(r['rubles'] for r in ef),'app_total':sum(r['rubles'] for r in app),
                'note':'Предполагаемые пары: одинаковая сумма с разницей до 3 дней или до 1 ₽ в тот же день. Подтверждение не изменяет расходы.'})

    def backup(self, target):
        with self.db() as db:
            dest = sqlite3.connect(str(target))
            try: db.backup(dest)
            finally: dest.close()
