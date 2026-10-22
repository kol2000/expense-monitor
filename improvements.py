"""Local split allocation, audit/undo and comparable-period insights."""
import calendar
import json
from datetime import datetime, date, timezone, timedelta

AUDIT_TABLES = {'records':'id', 'record_details':'id', 'rules':'id', 'mapping':'original',
                'categories':'name', 'subcategories':'category,name', 'category_aliases':'original',
                'settings':'key', 'splits':'id', 'imports':'hash', 'reviews':'kind,key'}

class Improvements:
    def improvements_init(self, db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS splits(id TEXT PRIMARY KEY,amount INTEGER NOT NULL,parts TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,created TEXT NOT NULL,label TEXT NOT NULL,
                changes TEXT NOT NULL,undone INTEGER NOT NULL DEFAULT 0,undoable INTEGER NOT NULL DEFAULT 1);
        """)
        if 'action' not in [r[1] for r in db.execute('PRAGMA table_info(rules)')]:
            db.execute("ALTER TABLE rules ADD COLUMN action TEXT NOT NULL DEFAULT ''")

        if 'preferred' not in [r[1] for r in db.execute('PRAGMA table_info(rules)')]:
            db.execute("ALTER TABLE rules ADD COLUMN preferred INTEGER NOT NULL DEFAULT 0")

    def snapshot(self, db):
        result={}
        for table, keys in AUDIT_TABLES.items():
            result[table]={tuple(r[k] for k in keys.split(',')):dict(r) for r in db.execute('SELECT * FROM '+table)}
        return result

    def audit_changes(self, db, before, label):
        after=self.snapshot(db); changes=[]
        for table in before:
            for key in before[table].keys() | after[table].keys():
                a,b=before[table].get(key),after[table].get(key)
                if a!=b: changes.append({'table':table,'key':key,'before':a,'after':b})
        if changes:
            db.execute('INSERT INTO audit(created,label,changes) VALUES(?,?,?)',
                (datetime.now(timezone.utc).isoformat(),label,json.dumps(changes,ensure_ascii=False)))

    def history(self):
        with self.db() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100')]
        for r in rows:
            changes=json.loads(r.pop('changes'));r['count']=len(changes)
            r['details']=[{'table':c['table'],'name':(c['after'] or c['before']).get('payee') or
                (c['after'] or c['before']).get('pattern') or str(c['key'][0]),
                'fields': [{'field':k,'before':(c['before'] or {}).get(k),'after':(c['after'] or {}).get(k)}
                    for k in ('category','custom_category','subcategory','decision','action','amount','day','deleted','name','value','parts')
                    if (c['before'] or {}).get(k)!=(c['after'] or {}).get(k)]} for c in changes[:30]]
        return rows

    def undo(self, values):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM audit WHERE id=?',(int(values['id']),)).fetchone()
            if not row or row['undone'] or not row['undoable']: raise ValueError('Это изменение нельзя отменить')
            changes=json.loads(row['changes'])
            for c in changes:
                keys=AUDIT_TABLES[c['table']].split(',')
                current=db.execute('SELECT * FROM '+c['table']+' WHERE '+ ' AND '.join(k+'=?' for k in keys),c['key']).fetchone()
                if (dict(current) if current else None)!=c['after']:
                    raise ValueError('Данные уже изменились. Сначала отмените более позднее изменение или исправьте запись вручную.')
            for c in reversed(changes):
                table=c['table'];keys=AUDIT_TABLES[table].split(',')
                db.execute('DELETE FROM '+table+' WHERE '+' AND '.join(k+'=?' for k in keys),c['key'])
                if c['before']:
                    cols=list(c['before']);db.execute('INSERT INTO '+table+' ('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',list(c['before'].values()))
            # Reject undo if newer records would reference a category being removed.
            categories={r[0] for r in db.execute('SELECT name FROM categories')}
            for table,columns in [('records',('category','custom_category')),('rules',('category',)),('mapping',('category',)),('record_details',('category',)),('subcategories',('category',))]:
                for entry in db.execute('SELECT * FROM '+table):
                    if any(entry[c] and entry[c] not in categories for c in columns):
                        raise ValueError('Категория используется более новыми данными. Отмена невозможна.')
            for table in ('record_details','rules'):
                for entry in db.execute('SELECT category,subcategory FROM '+table):
                    self.validate_detail(db,entry['category'],entry['subcategory'])
            record_ids={r[0] for r in db.execute('SELECT id FROM records')}
            for table in ('record_details','splits'):
                if any(r[0] not in record_ids for r in db.execute('SELECT id FROM '+table)):
                    raise ValueError('Операция используется более новыми уточнениями. Отмените их сначала.')
            for entry in db.execute('SELECT parts FROM splits'):
                for part in json.loads(entry[0]): self.validate_detail(db,part['category'],part['subcategory'])
            db.execute('UPDATE audit SET undone=1 WHERE id=?',(row['id'],))
            db.execute('INSERT INTO audit(created,label,changes,undoable) VALUES(?,?,?,0)',
                       (datetime.now(timezone.utc).isoformat(),'Отменено: '+row['label'],'[]'))

    def split(self, values):
        from ledger import cents
        with self.db(audit='Разделение покупки') as db:
            row=db.execute('SELECT * FROM records WHERE id=? AND deleted=0',(values['id'],)).fetchone()
            if not row: raise ValueError('Операция не найдена')
            if values.get('remove'):
                db.execute('DELETE FROM splits WHERE id=?',(row['id'],));return
            parts=values.get('parts')
            if not isinstance(parts,list) or not 2<=len(parts)<=20: raise ValueError('Укажите от 2 до 20 частей')
            clean=[]
            for part in parts:
                amount=cents(part['amount']);category=str(part['category']);sub=str(part.get('subcategory',''))
                if not amount: raise ValueError('Сумма каждой части должна быть больше нуля')
                self.validate_detail(db,category,sub)
                clean.append({'amount':amount,'category':category,'subcategory':sub})
            if sum(p['amount'] for p in clean)!=row['amount']: raise ValueError('Сумма частей должна точно совпадать с суммой покупки, включая копейки')
            db.execute('INSERT OR REPLACE INTO splits VALUES(?,?,?)',(row['id'],row['amount'],json.dumps(clean,ensure_ascii=False)))

    @staticmethod
    def expense_parts(rows):
        result=[]
        for row in rows:
            if row.get('parts') and not row.get('split_stale'):
                parts=row['parts'];rounded=[p['amount']//100 for p in parts]
                remaining=row['rubles']-sum(rounded)
                order=sorted(range(len(parts)),key=lambda i:-(parts[i]['amount']%100))
                for i in order[:remaining]: rounded[i]+=1
                for i,part in enumerate(parts):
                    result.append(dict(row,**part,rubles=rounded[i],parent_id=row['id'],allocation=True,parts=[],display_amount=part['amount']))
            else: result.append(row)
        return result

    def insights(self, month):
        anchor=date.fromisoformat(month+'-01');today=datetime.now(timezone(timedelta(hours=3))).date()
        last=calendar.monthrange(anchor.year,anchor.month)[1]
        end=min(last,today.day) if (anchor.year,anchor.month)==(today.year,today.month) else last
        previous=anchor-timedelta(days=1)
        end=min(end,previous.day)  # Same number of calendar days in both periods.
        current_end=anchor.replace(day=end)
        old_start=previous.replace(day=1);old_end=previous.replace(day=end)
        rows=[r for r in self.records() if r['included']]
        current=[r for r in rows if anchor.isoformat()<=r['day']<=current_end.isoformat()]
        old=[r for r in rows if old_start.isoformat()<=r['day']<=old_end.isoformat()]
        def totals(items):
            out={}
            for r in self.expense_parts(items): out[r['category']]=out.get(r['category'],0)+r['rubles']
            return out
        a,b=totals(current),totals(old)
        return {'start':anchor.isoformat(),'end':current_end.isoformat(),'previous_start':old_start.isoformat(),
                'previous_end':old_end.isoformat(),'total':sum(a.values()),'previous_total':sum(b.values()),
                'categories':[{'category':c,'total':a.get(c,0),'previous':b.get(c,0),'delta':a.get(c,0)-b.get(c,0)}
                    for c in sorted(a.keys()|b.keys(),key=lambda c:-abs(a.get(c,0)-b.get(c,0)))],
                'largest':sorted(current,key=lambda r:-r['amount'])[:5]}
