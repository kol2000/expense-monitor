"""Local categorisation, manual editing and review state. No upstream writes."""
import hashlib
import json
from datetime import date


def fingerprint(rows):
    values = sorted((r['id'], r['day'], r['amount'], r['payee'], r['note']) for r in rows)
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


class Features:
    def feature_init(self, db):
        db.executescript('''
          CREATE TABLE IF NOT EXISTS subcategories(category TEXT,name TEXT,PRIMARY KEY(category,name));
          CREATE TABLE IF NOT EXISTS record_details(id TEXT PRIMARY KEY,category TEXT,subcategory TEXT);
          CREATE TABLE IF NOT EXISTS rules(id INTEGER PRIMARY KEY,pattern TEXT NOT NULL,mode TEXT NOT NULL,
            category TEXT NOT NULL,subcategory TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS reviews(kind TEXT,key TEXT,PRIMARY KEY(kind,key));
        ''')

    def feature_settings(self):
        with self.db() as db:
            return {'subcategories':[dict(r) for r in db.execute('SELECT * FROM subcategories ORDER BY category,name')],
                    'rules':[dict(r) for r in db.execute('SELECT * FROM rules ORDER BY id')],
                    'trash':[dict(r) for r in db.execute("SELECT id,day,amount,payee,note,category FROM records WHERE source='manual' AND deleted=1 ORDER BY day DESC")]}

    def validate_detail(self, db, category, subcategory=''):
        if not db.execute('SELECT 1 FROM categories WHERE name=?',(category,)).fetchone():
            raise ValueError('Неизвестная категория')
        if subcategory and not db.execute('SELECT 1 FROM subcategories WHERE category=? AND name=?',(category,subcategory)).fetchone():
            raise ValueError('Подкатегория не принадлежит выбранной категории')

    def set_detail(self, db, record_id, category, subcategory):
        self.validate_detail(db,category,subcategory)
        db.execute('INSERT OR REPLACE INTO record_details VALUES(?,?,?)',(record_id,category,subcategory))

    def subcategory(self, values):
        category, name = str(values['category']), str(values['name']).strip()
        old = str(values.get('old',''))
        if not 1 <= len(name) <= 60: raise ValueError('Название: от 1 до 60 символов')
        with self.db() as db:
            self.validate_detail(db,category)
            db.execute('INSERT OR IGNORE INTO subcategories VALUES(?,?)',(category,name))
            if old and old != name:
                db.execute('UPDATE record_details SET subcategory=? WHERE category=? AND subcategory=?',(name,category,old))
                db.execute('UPDATE rules SET subcategory=? WHERE category=? AND subcategory=?',(name,category,old))
                db.execute('DELETE FROM subcategories WHERE category=? AND name=?',(category,old))

    @staticmethod
    def rule_matches(rule, row):
        value, pattern = row['payee'].strip().casefold(), rule['pattern'].strip().casefold()
        return bool(value) and (value == pattern if rule['mode']=='exact' else pattern in value)

    def rule(self, values):
        with self.db() as db:
            if values.get('delete'):
                db.execute('DELETE FROM rules WHERE id=?',(int(values['id']),))
                return
            pattern, mode = str(values['pattern']).strip(), values.get('mode','exact')
            category, sub = values['category'], str(values.get('subcategory',''))
            if not 1 <= len(pattern) <= 300 or mode not in ('exact','contains'):
                raise ValueError('Укажите получателя и способ сопоставления')
            self.validate_detail(db,category,sub)
            if values.get('id'):
                db.execute('UPDATE rules SET pattern=?,mode=?,category=?,subcategory=? WHERE id=?',
                           (pattern,mode,category,sub,int(values['id'])))
            else:
                db.execute('INSERT INTO rules(pattern,mode,category,subcategory) VALUES(?,?,?,?)',(pattern,mode,category,sub))

    def enrich(self, rows):
        with self.db() as db:
            rules = [dict(r) for r in db.execute('SELECT * FROM rules ORDER BY id')]
            details = {r['id']:dict(r) for r in db.execute('SELECT * FROM record_details')}
        for r in rows:
            r['rule_id'] = None
            r['subcategory'] = ''
            if r['source']=='zenmoney' and not r['custom_category'] and not r['own_transfer']:
                for rule in rules:
                    if self.rule_matches(rule,r):
                        r['category'],r['subcategory'],r['rule_id'] = rule['category'],rule['subcategory'],rule['id']
                        break
            detail = details.get(r['id'])
            if detail and detail['category']==r['category']:
                r['subcategory'] = detail['subcategory']
            r['attention'] = ('pending' if r['pending'] else 'review' if r['status']=='review'
                              else 'category' if r['included'] and r['category']=='Прочее' and not r['custom_category'] and not r['rule_id'] else '')
        return rows

    def manual_update(self, values):
        from ledger import cents, valid_date
        with self.db() as db:
            row = db.execute("SELECT * FROM records WHERE id=? AND source='manual'",(values['id'],)).fetchone()
            if not row: raise ValueError('Ручная операция не найдена')
            if values.get('action') in ('delete','restore'):
                db.execute('UPDATE records SET deleted=? WHERE id=?',(int(values['action']=='delete'),values['id']))
                return
            amount, day = cents(values['amount']), valid_date(values['day'])
            if not amount: raise ValueError('Укажите сумму больше нуля')
            category, sub = values['category'], str(values.get('subcategory',''))
            self.set_detail(db,values['id'],category,sub)
            db.execute('UPDATE records SET day=?,amount=?,category=?,custom_category=?,payee=?,note=? WHERE id=?',
                       (day,amount,category,category,str(values.get('payee',''))[:300],str(values.get('note',''))[:2000],values['id']))

    def review(self, values):
        kind = values['kind']
        if kind not in ('duplicate','reconcile'): raise ValueError('Неизвестный вид проверки')
        ids = values['ids']
        if not isinstance(ids,list) or not 1 <= len(ids) <= 2: raise ValueError('Выберите операции')
        rows = [r for r in self.records() if r['id'] in ids]
        if len(rows)!=len(set(ids)): raise ValueError('Операции изменены или удалены')
        with self.db() as db:
            if values.get('undo'):
                db.execute('DELETE FROM reviews WHERE kind=? AND key=?',(kind,fingerprint(rows)))
            else:
                db.execute('INSERT OR IGNORE INTO reviews VALUES(?,?)',(kind,fingerprint(rows)))

    def attention(self, month):
        rows = self.records(month)
        active = [r for r in rows if r['primary']]
        # Include neighbours outside the selected month to catch settlement across month boundaries.
        all_rows = self.records()
        with self.db() as db:
            checked = {r[0] for r in db.execute("SELECT key FROM reviews WHERE kind='duplicate'")}
        banks = [r for r in all_rows if r['source']=='zenmoney' and r['included']]
        pairs=[]
        for manual in (r for r in all_rows if r['source']=='manual' and r['included']):
            for bank in banks:
                if not (manual['day'].startswith(month) or bank['day'].startswith(month)): continue
                if manual['rubles']==bank['rubles'] and abs((date.fromisoformat(manual['day'])-date.fromisoformat(bank['day'])).days)<=3:
                    if fingerprint([manual,bank]) not in checked: pairs.append({'manual':manual,'bank':bank})
        return {'pending':[r for r in active if r['attention']=='pending'],
                'review':[r for r in active if r['attention']=='review'],
                'category':[r for r in active if r['attention']=='category'], 'duplicates':pairs}

    def reconcile_pairs(self, result):
        left, right = list(result['easyfinance_only']), list(result['app_only'])
        candidates=[]
        for a in left:
            for b in right:
                days=abs((date.fromisoformat(a['day'])-date.fromisoformat(b['day'])).days)
                delta=abs(a['rubles']-b['rubles'])
                if (delta==0 and days<=3) or (days==0 and delta<=1):
                    candidates.append((delta,days,a['id'],b['id'],a,b))
        used=set(); pairs=list(result.pop('exact_pairs',[]))
        for _,_,_,_,a,b in sorted(candidates):
            if a['id'] in used or b['id'] in used: continue
            used.update((a['id'],b['id']));pairs.append({'easyfinance':a,'app':b,'exact':False})
        with self.db() as db:
            checked={r[0] for r in db.execute("SELECT key FROM reviews WHERE kind='reconcile'")}
        for pair in pairs:
            pair['checked']=fingerprint([pair['easyfinance'],pair['app']]) in checked
        for row in left+right: row['checked']=fingerprint([row]) in checked
        result.update(pairs=pairs,easyfinance_only=[r for r in left if r['id'] not in used],
                      app_only=[r for r in right if r['id'] not in used])
        return result
