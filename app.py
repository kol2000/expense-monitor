"""Single-user WSGI service; production entrypoint is gunicorn app:application."""
import base64
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs
from ledger import Ledger, valid_date

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('DATA_DIR', ROOT / 'data'))
ledger = Ledger(DATA / 'ledger.sqlite3')
sync_lock = threading.Lock()
sessions = {}
attempts = {}
state_lock = threading.Lock()

def sync():
    token = os.environ.get('ZENMONEY_TOKEN','').strip()
    if not token: raise ValueError('Токен Дзен-мани не настроен. Добавьте ZENMONEY_TOKEN в .env на сервере.')
    if not sync_lock.acquire(blocking=False): raise ValueError('Синхронизация уже выполняется')
    try:
        payload = {'currentClientTimestamp':int(time.time()),'serverTimestamp':int(ledger.setting('zen_cursor','0'))}
        request = urllib.request.Request('https://api.zenmoney.ru/v8/diff/',
            data=json.dumps(payload).encode(), headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        # No local entities are sent: this integration never writes transactions to Zenmoney.
        with urllib.request.urlopen(request,timeout=45) as response:
            raw = response.read(32*1024*1024+1)
        if len(raw)>32*1024*1024: raise ValueError('Ответ API слишком большой')
        return ledger.apply_diff(json.loads(raw,parse_float=str))
    except (urllib.error.URLError,ValueError,KeyError) as exc:
        message = 'Не удалось обновить Дзен-мани. Проверьте токен и доступность API.'
        ledger.set_setting('sync_error',message)
        raise ValueError(message) from None
    finally: sync_lock.release()

def worker():
    last_backup = ''
    while True:
        today = datetime.now().date().isoformat()
        try:
            if today != last_backup:
                folder = DATA / 'backups'
                folder.mkdir(parents=True,exist_ok=True)
                ledger.backup(folder / (today+'.sqlite3'))
                last_backup = today
            if os.environ.get('ZENMONEY_TOKEN'): sync()
        except Exception:
            # Do not log payloads, account details, or tokens.
            print('Background sync/backup failed; inspect application status.',flush=True)
        time.sleep(max(60,int(os.environ.get('SYNC_SECONDS','900'))))

def reply(start,status,payload,ctype='application/json; charset=utf-8',extra=None):
    if not isinstance(payload,bytes): payload=json.dumps(payload,ensure_ascii=False).encode()
    start(status,[('Content-Type',ctype),('Content-Length',str(len(payload))),('Cache-Control','no-store'),
        ('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
        ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),*(extra or [])])
    return [payload]

def application(env,start):
    path, method = env.get('PATH_INFO','/'),env['REQUEST_METHOD']
    if path=='/health': return reply(start,'200 OK',{'status':'ok','version':'0.2.0'})
    password=os.environ.get('APP_PASSWORD','')
    if len(password)<12: return reply(start,'503 Service Unavailable',{'error':'Задайте APP_PASSWORD длиной не менее 12 символов.'})
    try:
        body = {}
        if method=='POST':
            if env.get('HTTP_X_REQUESTED_WITH')!='expense-monitor': return reply(start,'403 Forbidden',{'error':'Неверный запрос'})
            if not env.get('CONTENT_TYPE','').startswith('application/json'): raise ValueError('Ожидается JSON')
            length=int(env.get('CONTENT_LENGTH') or 0)
            if length>12*1024*1024: return reply(start,'413 Payload Too Large',{'error':'Файл больше 8 МБ'})
            body=json.loads(env['wsgi.input'].read(length))
        if path=='/api/login' and method=='POST':
            ip=env.get('REMOTE_ADDR','local')
            with state_lock:
                now=time.time()
                recent=[t for t in attempts.get(ip,[]) if now-t<300]
                attempts[ip]=recent
                if len(recent)>=10: return reply(start,'429 Too Many Requests',{'error':'Повторите вход через 5 минут'})
                if not hmac.compare_digest(str(body.get('password','')).encode(),password.encode()):
                    recent.append(now)
                    return reply(start,'401 Unauthorized',{'error':'Неверный пароль'})
                for key in list(sessions):
                    if sessions[key]<now: sessions.pop(key)
                session=secrets.token_urlsafe(32)
                sessions[session]=now+86400*7
            secure='; Secure' if os.environ.get('COOKIE_SECURE','1')=='1' else ''
            return reply(start,'200 OK',{'ok':True},extra=[('Set-Cookie',f'session={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800{secure}')])
        if path in ('/','/app.js','/features.js','/theme.js','/style.css','/manifest.webmanifest','/icon.svg') and method=='GET':
            file=ROOT/'static'/('index.html' if path=='/' else path[1:])
            return reply(start,'200 OK',file.read_bytes(),mimetypes.guess_type(str(file))[0] or 'application/octet-stream')
        cookie=SimpleCookie()
        cookie.load(env.get('HTTP_COOKIE',''))
        session=cookie['session'].value if 'session' in cookie else ''
        if sessions.get(session,0)<time.time(): return reply(start,'401 Unauthorized',{'error':'Войдите в приложение'})
        query=parse_qs(env.get('QUERY_STRING',''))
        month=query.get('month',[''])[0]
        if path=='/api/logout' and method=='POST':
            sessions.pop(session,None)
            return reply(start,'200 OK',{'ok':True},extra=[('Set-Cookie','session=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict')])
        if path=='/api/dashboard' and method=='GET':
            result=ledger.dashboard(month)
            result['zen_configured']=bool(os.environ.get('ZENMONEY_TOKEN'))
            return reply(start,'200 OK',result)
        if path=='/api/reconcile' and method=='GET': return reply(start,'200 OK',ledger.reconcile(month))
        if path=='/api/reports' and method=='GET':
            return reply(start,'200 OK',ledger.reports(month,query.get('period',['month'])[0],
                **{key:query.get(key,[''])[0] for key in ('start','end','compare_start','compare_end')}))
        if path=='/api/attention' and method=='GET': return reply(start,'200 OK',ledger.attention(month))
        if path=='/api/features' and method=='GET': return reply(start,'200 OK',ledger.feature_settings())
        if path=='/api/manual-update' and method=='POST':
            ledger.manual_update(body)
            return reply(start,'200 OK',{'ok':True})
        if path=='/api/rule' and method=='POST':
            ledger.rule(body)
            return reply(start,'200 OK',{'ok':True})
        if path=='/api/subcategory' and method=='POST':
            ledger.subcategory(body)
            return reply(start,'200 OK',{'ok':True})
        if path=='/api/review' and method=='POST':
            ledger.review(body)
            return reply(start,'200 OK',{'ok':True})
        if path=='/api/import' and method=='POST':
            return reply(start,'200 OK',ledger.import_csv(base64.b64decode(body['content'],validate=True)))
        if path=='/api/manual' and method=='POST': return reply(start,'200 OK',ledger.manual(body))
        if path=='/api/edit' and method=='POST': ledger.edit(body)
        elif path=='/api/mapping' and method=='POST': ledger.map_category(body['original'],body['category'])
        elif path=='/api/categories' and method=='POST':
            if body.get('old'): ledger.rename_category(body['old'],body['name'])
            else: ledger.add_category(body['name'])
        elif path=='/api/settings' and method=='POST': ledger.set_setting('cutover',valid_date(body['cutover']))
        elif path=='/api/sync' and method=='POST': return reply(start,'200 OK',sync())
        elif path=='/api/export' and method=='GET':
            stream=io.StringIO(newline='')
            writer=csv.writer(stream,delimiter=';')
            writer.writerow(['id','источник','дата','точная сумма','учёт рублей','категория','исходная категория','счёт','описание','комментарий','статус','в отчёте','подкатегория'])
            for r in ledger.records():
                def safe(v):
                    s=str(v)
                    return "'"+s if s.startswith(('=','+','-','@','\t','\r')) else s
                writer.writerow([safe(v) for v in [r['id'],r['source'],r['day'],f"{r['amount']//100}.{r['amount']%100:02}",r['rubles'],r['category'],r['original_category'],r['account'],r['payee'],r['note'],r['status'],r['included'],r['subcategory']]])
            return reply(start,'200 OK',stream.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8', [('Content-Disposition','attachment; filename="expenses.csv"')])
        else: return reply(start,'404 Not Found',{'error':'Не найдено'})
        return reply(start,'200 OK',{'ok':True})
    except (ValueError,KeyError,TypeError,csv.Error) as exc:
        return reply(start,'400 Bad Request',{'error':str(exc) or 'Неверные данные'})
    except Exception:
        return reply(start,'500 Internal Server Error',{'error':'Ошибка сервера. Данные операции не изменены.'})

if os.environ.get('BACKGROUND_WORKER','1')=='1':
    threading.Thread(target=worker,daemon=True).start()

if __name__=='__main__':
    from wsgiref.simple_server import make_server
    port=int(os.environ.get('PORT','8080'))
    print(f'Local preview: http://127.0.0.1:{port}',flush=True)
    make_server('127.0.0.1',port,application).serve_forever()
