import csv
import io
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from ledger import Ledger, cents, rubles, resolve_category
from datetime import datetime, timezone, timedelta

class TestDirectory:
    """Inherit Windows workspace ACLs (TemporaryDirectory's mode 0700 can block sandbox access)."""
    def __init__(self):
        self.root=Path(tempfile.gettempdir()).resolve()
        self.path=self.root/('expense-test-'+uuid.uuid4().hex)
        self.path.mkdir()
        self.name=str(self.path)
    def cleanup(self):
        assert self.path.resolve().parent==self.root
        for child in self.path.iterdir():
            if child.is_file():child.unlink()
        self.path.rmdir()

def export(rows):
    out=io.StringIO()
    w=csv.writer(out,delimiter=';')
    w.writerow(['дата','тип операции','сумма в валюте по умолчанию','валюта по умолчанию','категория'])
    for day,amount,category in rows: w.writerow([day,'расход',amount,'Российский рубль',category])
    return out.getvalue().encode('cp1251')

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TestDirectory()
        self.ledger=Ledger(Path(self.tmp.name)/'db.sqlite3')
    def tearDown(self): self.tmp.cleanup()
    def diff(self, transactions, stamp=1):
        return {'serverTimestamp':stamp,'instrument':[{'id':2,'shortTitle':'RUB'}],
                'account':[{'id':'a','type':'ccard','title':'Карта'},{'id':'b','type':'checking','title':'Мой счёт'},{'id':'d','type':'debt','title':'Человек'}],
                'transaction':transactions}
    def tx(self, id='one', **extra):
        return dict({'id':id,'date':'2026-10-15','outcome':'1347.27','income':0,'outcomeAccount':'a','incomeAccount':'a','outcomeInstrument':2},**extra)
    def test_money_ceil_per_operation(self):
        self.assertEqual(rubles(cents('1347,27')),1348)
        self.assertEqual(rubles(cents('248.80')),249)
        self.assertEqual(rubles(cents('100')),100)

    def test_category_resolution_exact_alias_and_explicit_mapping(self):
        l=self.ledger
        l.rename_category('Дом и быт','Домашнее хозяйство')
        l.rename_category('Здоровье','Медицина')
        l.add_category('Машина')
        with l.db() as db:
            self.assertEqual(resolve_category(db,' машина '),'Машина')
            self.assertEqual(resolve_category(db,'Дом, уют'),'Домашнее хозяйство')
            self.assertEqual(resolve_category(db,'Медицинские услуги'),'Медицина')
        l.rename_category('Машина','Мой автомобиль')
        with l.db() as db:
            self.assertEqual(resolve_category(db,'Машина'),'Мой автомобиль')
        diff=self.diff([self.tx(tag=['car'])]);diff['tag']=[{'id':'car','title':'Машина'}]
        l.apply_diff(diff)
        self.assertEqual(l.records()[0]['category'],'Мой автомобиль')
        l.map_category('Машина','Покупки')
        l.apply_diff(diff)
        self.assertEqual(l.records()[0]['category'],'Покупки')

    def test_year_total_is_current_year_independent_of_selected_month(self):
        l=self.ledger
        today=datetime.now(timezone(timedelta(hours=3))).date()
        l.set_setting('cutover',f'{today.year}-01-01')
        l.manual({'day':f'{today.year}-01-01','amount':'100.01','category':'Покупки'})
        l.manual({'day':f'{today.year-1}-12-31','amount':'200','category':'Покупки'})
        l.manual({'day':f'{today.year+1}-01-01','amount':'300','category':'Покупки'})
        self.assertEqual(l.dashboard('2020-01')['year_total'],101)

    def test_person_transfers_review_saved_imports_and_keep_decisions(self):
        l = self.ledger
        txs = [self.tx('comment', payee='Получатель', comment='Перевод по номеру телефона'),
               self.tx('initials', payee='Анна Сергеевна П.'),
               self.tx('name', payee='Иван Петрович Иванов'),
               self.tx('own', income='1347.27', incomeAccount='b'),
               self.tx('shop', payee='Пятёрочка')]
        l.apply_diff(self.diff(txs))
        # Existing records classified by an older version are re-evaluated.
        with l.db() as db:
            db.execute("UPDATE records SET kind='expense'")
        rows = {r['source_id']: r for r in l.records()}
        for sid in ('comment', 'initials', 'name'):
            self.assertEqual(rows[sid]['status'], 'review')
            self.assertFalse(rows[sid]['included'])
        self.assertEqual(rows['own']['status'], 'ignored')
        self.assertTrue(rows['shop']['included'])
        l.edit({'id':'zm:comment', 'decision':'ignored'})
        l.edit({'id':'zm:initials', 'decision':'expense'})
        l.apply_diff(self.diff(txs, 2))
        rows = {r['source_id']: r for r in l.records()}
        self.assertEqual(rows['comment']['status'], 'ignored')
        self.assertTrue(rows['initials']['included'])
        sid = l.manual({'day':'2026-10-21', 'amount':'1000', 'category':'Семья и подарки',
                        'payee':'Отец', 'note':'Перевод по СБП'})['id']
        self.assertTrue(next(r for r in l.records() if r['id']==sid)['included'])

    def test_ignored_pending_transfer_stays_ignored_on_sync(self):
        tx = self.tx(payee='Анна П.', hold=True)
        self.ledger.apply_diff(self.diff([tx]))
        self.ledger.edit({'id':'zm:one', 'decision':'ignored'})
        self.ledger.apply_diff(self.diff([tx], 2))
        self.assertEqual(self.ledger.records()[0]['status'], 'ignored')

    def test_rules_priority_override_and_sync(self):
        l=self.ledger
        l.subcategory({'category':'Покупки','name':'Техника'})
        l.rule({'pattern':'shop','mode':'contains','category':'Покупки','subcategory':'Техника'})
        l.rule({'pattern':'shop','mode':'exact','category':'Продукты'})
        l.apply_diff(self.diff([self.tx(payee='SHOP')]))
        row=l.records()[0]
        self.assertEqual((row['category'],row['subcategory']),('Покупки','Техника'))
        self.assertEqual(l.dashboard('2026-10')['totals'],{'Покупки':1348})
        l.edit({'id':row['id'],'category':'Продукты','decision':None,'subcategory':''})
        l.apply_diff(self.diff([self.tx(payee='SHOP',outcome='2')],2))
        self.assertEqual(l.records()[0]['category'],'Продукты')
        l.edit({'id':row['id'],'decision':None,'reset_category':True})
        self.assertEqual(l.records()[0]['category'],'Покупки')
        with self.assertRaises(ValueError):l.rule({'pattern':'x','category':'Продукты','subcategory':'Техника'})
        l.rename_category('Покупки','Новая')
        self.assertEqual(l.feature_settings()['rules'][0]['category'],'Новая')
        self.assertEqual(l.feature_settings()['subcategories'][0]['category'],'Новая')

    def test_manual_edit_trash_restore_and_subcategory_merge(self):
        l=self.ledger
        l.subcategory({'category':'Покупки','name':'Техника'})
        sid=l.manual({'day':'2026-10-16','amount':'100','category':'Покупки','subcategory':'Техника'})['id']
        l.manual_update({'id':sid,'day':'2026-11-01','amount':'200.01','category':'Покупки','subcategory':'Техника','payee':'Новый','note':'Комментарий'})
        self.assertEqual(l.dashboard('2026-10')['total'],0)
        self.assertEqual(l.dashboard('2026-11')['total'],201)
        l.subcategory({'category':'Покупки','old':'Техника','name':'Компьютер'})
        self.assertEqual(l.records()[0]['subcategory'],'Компьютер')
        l.manual_update({'id':sid,'action':'delete'})
        self.assertEqual(l.records(),[])
        self.assertEqual(len(l.feature_settings()['trash']),1)
        l.manual_update({'id':sid,'action':'restore'})
        self.assertEqual(l.records()[0]['payee'],'Новый')
        l.apply_diff(self.diff([self.tx()]))
        with self.assertRaises(ValueError):l.manual_update({'id':'zm:one','action':'delete'})

    def test_duplicate_review_and_reconcile_fingerprints(self):
        l=self.ledger
        sid=l.manual({'day':'2026-10-16','amount':'1347.27','category':'Покупки'})['id']
        l.apply_diff(self.diff([self.tx()]))
        self.assertEqual(len(l.attention('2026-10')['duplicates']),1)
        l.review({'kind':'duplicate','ids':[sid,'zm:one']})
        self.assertEqual(l.attention('2026-10')['duplicates'],[])
        l.manual_update({'id':sid,'day':'2026-10-17','amount':'1347.27','category':'Покупки'})
        self.assertEqual(len(l.attention('2026-10')['duplicates']),1)
        l.import_csv(export([('15-10-2026','1349','Покупки')]))
        pairs=l.reconcile('2026-10')['pairs']
        self.assertEqual(len(pairs),1)
        self.assertFalse(pairs[0]['exact'])
        ids=[pairs[0]['easyfinance']['id'],pairs[0]['app']['id']]
        l.review({'kind':'reconcile','ids':ids})
        self.assertTrue(l.reconcile('2026-10')['pairs'][0]['checked'])
        l.review({'kind':'reconcile','ids':ids,'undo':True})
        self.assertFalse(l.reconcile('2026-10')['pairs'][0]['checked'])
        l.manual_update({'id':sid,'day':'2026-10-17','amount':'1348','category':'Покупки'})
        self.assertEqual(len(l.attention('2026-10')['duplicates']),1)

    def test_range_quarter_comparison(self):
        l=self.ledger;l.set_setting('cutover','2020-01-01')
        for day,amount in [('2025-01-01','10'),('2026-01-01','20'),('2026-03-31','30'),('2026-04-01','40')]:
            l.manual({'day':day,'amount':amount,'category':'Покупки'})
        self.assertEqual(l.reports('2026-02','quarter')['total'],50)
        r=l.reports('2026-01','range','2026-01-01','2026-04-01','2025-01-01','2025-12-31')
        self.assertEqual((r['total'],len(r['buckets'])),(90,4))
        self.assertEqual(sum(x['rubles'] for x in r['comparison']),10)
        self.assertEqual(l.reports('2026-01','range','2025-01-01','2026-04-01')['total'],100)
        with self.assertRaises(ValueError):l.reports('2026-01','range','2026-02-01','2026-01-01')

    def test_reports_cutover_rounding_and_exclusions(self):
        self.ledger.import_csv(export([('14-10-2026','10.01','Топливо'),('15-10-2026','99','Топливо')]))
        self.ledger.apply_diff(self.diff([
            self.tx('old',date='2026-10-14',outcome='88'),
            self.tx('new',outcome='20.01'),
            self.tx('held',outcome='30',hold=True),
            self.tx('transfer',outcome='40',income='40',incomeAccount='b'),
            self.tx('receipt',outcome=0,income=50)]))
        self.ledger.manual({'day':'2026-11-02','amount':'5.01','category':'Покупки'})
        month=self.ledger.reports('2026-10')
        self.assertEqual((month['total'],month['count']),(32,2))
        self.assertEqual(month['total'],self.ledger.dashboard('2026-10')['total'])
        year=self.ledger.reports('2026-10','year')
        self.assertEqual((year['total'],year['count']),(38,3))
        self.assertEqual(len(year['buckets']),12)
        self.assertEqual(len(self.ledger.reports('2024-02')['buckets']),29)
        self.assertEqual(self.ledger.reports('2025-01')['rows'],[])
        for month,period in [('2026-13','month'),('2026','year'),('2026-01','bad')]:
            with self.assertRaises(ValueError):self.ledger.reports(month,period)

    def test_new_categories_migrate_once_preserving_overrides(self):
        self.ledger.import_csv(export([('01-01-2026','100','Такси'),('02-01-2026','200','Уход за собой')]))
        with self.ledger.db() as db:
            db.execute("DELETE FROM settings WHERE key='transport_care_v1'")
            db.execute("UPDATE records SET category='Покупки'")
            db.execute("UPDATE records SET custom_category='Покупки' WHERE original_category='Уход за собой'")
        ledger=Ledger(self.ledger.path)
        rows={r['original_category']:r for r in ledger.records()}
        self.assertEqual(rows['Такси']['category'],'Транспорт')
        self.assertEqual(rows['Уход за собой']['category'],'Покупки')
        self.assertIn('Парикмахерская и уход за собой',ledger.categories())
        ledger.map_category('Такси','Покупки')
        ledger=Ledger(self.ledger.path)
        self.assertEqual(next(r for r in ledger.records() if r['original_category']=='Такси')['category'],'Покупки')
        self.assertEqual(rubles(cents('0.01')),1)
        for value in ('NaN','Infinity','-1','1.001'):
            with self.assertRaises(ValueError): cents(value)
    def test_receipt_display_does_not_change_expenses(self):
        self.ledger.apply_diff(self.diff([self.tx('cashback',outcome=0,income='165.27',incomeInstrument=2),self.tx()]))
        row=next(r for r in self.ledger.records() if r['source_id']=='cashback')
        self.assertEqual(row['display_amount'],16527)
        self.assertEqual(row['display_currency'],'RUB')
        self.assertEqual(row['amount'],0)
        self.assertFalse(row['included'])
        self.assertEqual(self.ledger.dashboard('2026-10')['total'],1348)
    def test_service_categories_and_existing_record_upgrade(self):
        diff=self.diff([self.tx('mts',payee='МТС',tag=['services']),self.tx('other',payee='Мастер',tag=['services'])])
        diff['tag']=[{'id':'services','title':'Услуги'}]
        self.ledger.apply_diff(diff)
        rows={r['source_id']:r for r in self.ledger.records()}
        self.assertEqual(rows['mts']['category'],'Связь и подписки')
        self.assertEqual(rows['other']['category'],'Прочее')
        with self.ledger.db() as db:
            db.execute("UPDATE records SET original_category='Услуги',category='Прочее',custom_category='Дом и быт' WHERE source_id='mts'")
            db.execute("DELETE FROM settings WHERE key='zen_categories_v2'")
        upgraded=Ledger(self.ledger.path)
        self.assertEqual(next(r for r in upgraded.records() if r['source_id']=='mts')['category'],'Дом и быт')
        upgraded.map_category('Дзен-мани: МТС (связь)','Покупки')
        upgraded.apply_diff(diff)
        with upgraded.db() as db:
            self.assertEqual(db.execute("SELECT category FROM records WHERE source_id='mts'").fetchone()[0],'Покупки')
    def test_import_repeated_rows_preserved_reimport_idempotent(self):
        raw=export([('14-10-2026','1348,00','Продукты')]*2)
        self.assertEqual(self.ledger.import_csv(raw)['inserted'],2)
        self.assertEqual(self.ledger.import_csv(raw)['inserted'],0)
        self.assertEqual(self.ledger.dashboard('2026-10')['total'],2696)
    def test_invalid_import_is_atomic(self):
        with self.assertRaises(ValueError): self.ledger.import_csv(export([('14-10-2026','10','Дом'),('wrong','20','Дом')]))
        self.assertEqual(len(self.ledger.records()),0)
    def test_transfer_debt_and_unknown(self):
        self.ledger.apply_diff(self.diff([self.tx('own',income=1347.27,incomeAccount='b'),self.tx('debt',income=1347.27,incomeAccount='d'),self.tx('unknown',income=1347.27,incomeAccount='unknown'),self.tx('sbp',payee='Перевод СБП')]))
        statuses={r['source_id']:r['status'] for r in self.ledger.records()}
        self.assertEqual(statuses,{'own':'ignored','debt':'review','unknown':'review','sbp':'review'})
        self.assertEqual(self.ledger.dashboard('2026-10')['total'],0)
        self.ledger.edit({'id':'zm:debt','decision':'expense','category':'Дом и быт'})
        self.assertEqual(self.ledger.dashboard('2026-10')['total'],1348)
    def test_sync_update_delete_preserves_category(self):
        self.ledger.apply_diff(self.diff([self.tx()]))
        self.ledger.edit({'id':'zm:one','category':'Дом и быт','decision':'expense'})
        self.ledger.apply_diff(self.diff([self.tx(outcome='200.01')],2))
        rows=self.ledger.records();self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['category'],'Дом и быт');self.assertEqual(rows[0]['rubles'],201)
        self.ledger.apply_diff({'serverTimestamp':3,'deletion':[{'object':'transaction','id':'one'}]})
        self.assertEqual(self.ledger.records(),[])
    def test_diff_rollback_cursor(self):
        with self.assertRaises(ValueError):self.ledger.apply_diff(self.diff([self.tx(),self.tx('bad',date='bad')]))
        self.assertEqual(self.ledger.records(),[]);self.assertEqual(self.ledger.setting('zen_cursor'),'')
    def test_shadow_and_multiset_reconcile(self):
        self.ledger.import_csv(export([('15-10-2026','1348','Продукты')]*2))
        self.ledger.apply_diff(self.diff([self.tx()]))
        self.assertEqual(self.ledger.dashboard('2026-10')['total'],1348)
        result=self.ledger.reconcile('2026-10')
        self.assertEqual(result['matched'],1);self.assertEqual(len(result['easyfinance_only']),1)
    def test_hold_waits_then_becomes_expense(self):
        self.ledger.apply_diff(self.diff([self.tx(hold=True,outcomeInstrument=9)]))
        with self.assertRaisesRegex(ValueError, 'окончательного списания'):
            self.ledger.edit({'id':'zm:one','decision':'expense'})
        self.assertIn('Ожидает', self.ledger.records()[0]['review_reason'])
        self.assertEqual(self.ledger.dashboard('2026-10')['total'],0)
        self.ledger.apply_diff(self.diff([self.tx(hold=False,outcomeInstrument=9)],2))
        self.assertEqual(self.ledger.dashboard('2026-10')['total'],1348)
    def test_currency_metadata_is_ignored(self):
        diff=self.diff([self.tx(outcomeInstrument=9),self.tx('receipt',outcome=0,income=50,incomeInstrument=9)])
        diff['instrument']=[{'id':9,'shortTitle':'USD'}]
        self.ledger.apply_diff(diff)
        self.assertEqual(self.ledger.dashboard('2026-10')['total'],1348)
        self.assertTrue(all(r['display_currency']=='RUB' for r in self.ledger.records()))
        self.ledger.apply_diff({'serverTimestamp':2,'transaction':[self.tx('missing',outcomeInstrument=None)]})
        self.assertEqual(self.ledger.dashboard('2026-10')['total'],2696)
    def test_rub_migration_repairs_old_blocks_and_preserves_overrides(self):
        self.ledger.apply_diff(self.diff([self.tx('buy'),self.tx('held',hold=True),self.tx('own',income=1347.27,incomeAccount='b'),self.tx('custom')]))
        self.ledger.edit({'id':'zm:custom','decision':'ignored','category':'Здоровье'})
        with self.ledger.db() as db:
            for row in db.execute("SELECT id,raw FROM records").fetchall():
                raw=json.loads(row['raw']);raw['_blocked']=True
                db.execute("UPDATE records SET kind='review',raw=? WHERE id=?",(json.dumps(raw),row['id']))
            db.execute("DELETE FROM settings WHERE key='owned_transfers_v2'")
        upgraded=Ledger(self.ledger.path)
        rows={r['source_id']:r for r in upgraded.records()}
        self.assertEqual(rows['buy']['status'],'expense')
        self.assertEqual(rows['held']['status'],'review')
        self.assertEqual(rows['own']['status'],'ignored')
        self.assertEqual(rows['custom']['status'],'ignored')
        self.assertEqual(rows['custom']['category'],'Здоровье')
        self.assertEqual(upgraded.setting('zen_cursor'),'1')
        self.assertEqual(upgraded.dashboard('2026-10')['total'],1348)
        self.assertEqual(Ledger(self.ledger.path).dashboard('2026-10')['total'],1348)
    def test_pending_own_transfer_never_counts_as_expense(self):
        self.ledger.apply_diff(self.diff([self.tx('saving',outcome=250,income=250,incomeAccount='b',hold=True),self.tx('purchase',outcome=2250,hold=True)]))
        rows={r['source_id']:r for r in self.ledger.records()}
        self.assertEqual(rows['saving']['status'],'ignored')
        self.assertTrue(rows['saving']['own_transfer'])
        self.assertEqual(rows['saving']['account_to'],'Мой счёт')
        self.assertEqual(self.ledger.dashboard('2026-10')['review'],1)
        with self.assertRaisesRegex(ValueError,'между своими'):
            self.ledger.edit({'id':'zm:saving','decision':'expense'})
        self.ledger.apply_diff(self.diff([self.tx('saving',outcome=250,income=250,incomeAccount='b',hold=False),self.tx('purchase',outcome=2250,hold=False)],2))
        self.assertEqual(self.ledger.dashboard('2026-10')['total'],2250)
    def test_transfer_recognized_when_account_metadata_arrives_later(self):
        self.ledger.apply_diff({'serverTimestamp':1,'transaction':[self.tx('saving',outcome=250,income=250,incomeAccount='b',hold=True)]})
        self.assertEqual(self.ledger.records()[0]['status'],'review')
        self.ledger.apply_diff(self.diff([],2))
        self.assertEqual(self.ledger.records()[0]['status'],'ignored')
        self.assertEqual(self.ledger.dashboard('2026-10')['review'],0)
    def test_category_merge_and_backup(self):
        self.ledger.manual({'day':'2026-10-15','amount':'1.01','category':'Продукты'})
        self.ledger.rename_category('Продукты','Покупки')
        self.assertNotIn('Продукты',self.ledger.categories())
        target=Path(self.tmp.name)/'backup.sqlite3';self.ledger.backup(target)
        restored=Ledger(target)
        self.assertNotIn('Продукты',restored.categories())
        self.assertEqual(restored.dashboard('2026-10')['totals'],{'Покупки':2})
        restored.import_csv(export([('14-10-2026','25','Продуктовый магазин')]))
        self.assertTrue(all(r['category'] in restored.categories() for r in restored.records()))

class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=TestDirectory()
        os.environ.update(DATA_DIR=cls.tmp.name,APP_PASSWORD='test-password-only',BACKGROUND_WORKER='0',COOKIE_SECURE='0')
        import app
        cls.app=app
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()
    def request(self,path,body=None,cookie='',csrf=True):
        raw=json.dumps(body).encode() if body is not None else b''
        env={'PATH_INFO':path,'REQUEST_METHOD':'POST' if body is not None else 'GET','wsgi.input':io.BytesIO(raw),'CONTENT_LENGTH':str(len(raw)),'CONTENT_TYPE':'application/json','HTTP_COOKIE':cookie,'REMOTE_ADDR':'test'}
        if csrf: env['HTTP_X_REQUESTED_WITH']='expense-monitor'
        result={}
        def start(status,headers):result.update(status=status,headers=dict(headers))
        result['body']=b''.join(self.app.application(env,start))
        return result
    def test_auth_and_csrf(self):
        self.assertTrue(self.request('/api/dashboard')['status'].startswith('401'))
        for path in ('/api/features','/api/attention','/api/reports'):
            self.assertTrue(self.request(path)['status'].startswith('401'))
        self.assertTrue(self.request('/api/login',{'password':'test-password-only'},csrf=False)['status'].startswith('403'))
        result=self.request('/api/login',{'password':'test-password-only'})
        cookie=result['headers']['Set-Cookie'].split(';')[0]
        self.assertTrue(self.request('/api/dashboard',cookie=cookie)['status'].startswith('200'))
        self.assertTrue(self.request('/api/manual',{},cookie,csrf=False)['status'].startswith('403'))
        self.assertTrue(self.request('/api/logout',{},cookie)['status'].startswith('200'))
        self.assertTrue(self.request('/api/dashboard',cookie=cookie)['status'].startswith('401'))
    def test_static_and_traversal(self):
        self.assertTrue(self.request('/')['status'].startswith('200'))
        self.assertNotIn(b'APP_PASSWORD',self.request('/../.env')['body'])

if __name__=='__main__': unittest.main()
