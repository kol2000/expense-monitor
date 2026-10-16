import csv
import io
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from ledger import Ledger, cents, rubles

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
            db.execute("DELETE FROM settings WHERE key='rub_only_v1'")
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
