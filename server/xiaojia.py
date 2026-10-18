#!/usr/bin/env python3
"""
小家的后端 · 第一步：放页面、存东西。

只用 Python 自带的库，服务器上什么都不用装。

    python3 server/xiaojia.py --set-password    第一次先设密码（在服务器终端里自己输，谁也看不到）
    python3 server/xiaojia.py                   跑起来，默认听 127.0.0.1:8090，ngrok 往这里转

数据是 data/ 下面的几个 JSON 文件（路径可以用 XIAOJIA_DATA 改），不进 git。
页面还是 web/index.html 那一份；从这里出去的时候会带上一句 window.__XJ_BACKEND__=1，
页面看见它就不走演示数据了。

还没接 Claude：聊天发出去，小克暂时回不了，要等第二步。小克该写的那几页（日记墙、夜里的话……）
先给空的，接口形状跟演示数据一致，到时候往里填就行。

免费 ngrok 一个月两万次请求、1 GB 流量，所以：页面压缩、没变的文件回 304、
聊天那条长轮询没消息就挂两分钟再回。
"""
import argparse
import base64
import copy
import getpass
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
DATA = Path(os.environ.get('XIAOJIA_DATA', ROOT / 'data'))
COOKIE = 'xj'
SESSION_DAYS = 365
POLL_HOLD = 120            # 长轮询挂多久（秒）
CN = timezone(timedelta(hours=8))

LOCK = threading.Lock()
WAKE = threading.Condition()   # 以后有新消息时 notify_all，长轮询就提前回
FAILS = {}                     # 每个来访地址最近登录失败的时间，防有人一直猜（按地址分开算，别人猜错锁不到你）


def now() -> int:
    return int(time.time())


def today_cn() -> str:
    return datetime.now(CN).strftime('%Y-%m-%d')


# ---------------- 存储：一个名字一个 JSON 文件，写的时候先写临时文件再换过去 ----------------

def load(name, default):
    p = DATA / f'{name}.json'
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return copy.deepcopy(default)


def save(name, obj):
    DATA.mkdir(parents=True, exist_ok=True)
    p = DATA / f'{name}.json'
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, p)


def new_id(items):
    return max([it.get('id', 0) for it in items] + [0]) + 1


# ---------------- 密码和登录 ----------------

def auth_conf():
    return load('auth', None)


def set_password():
    if sys.stdin.isatty():
        pw = getpass.getpass('给小家设个密码（输的时候不显示）：')
        again = getpass.getpass('再输一遍：')
        if pw != again:
            sys.exit('两次不一样，没改。')
    else:
        pw = sys.stdin.readline().rstrip('\n')
    if len(pw) < 6:
        sys.exit('至少 6 位。')
    salt = secrets.token_bytes(16)
    it = 240000
    h = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, it)
    # 每次改密码都换一把新的 secret：以前登录过的设备全部要重新输
    save('auth', {'salt': salt.hex(), 'hash': h.hex(), 'iter': it, 'secret': secrets.token_hex(32)})
    os.chmod(DATA / 'auth.json', 0o600)
    print('设好了。已经登录的设备都需要重新输一次密码。')


def check_password(pw):
    a = auth_conf()
    h = hashlib.pbkdf2_hmac('sha256', pw.encode(), bytes.fromhex(a['salt']), a['iter'])
    return hmac.compare_digest(h.hex(), a['hash'])


def make_token():
    a = auth_conf()
    body = base64.urlsafe_b64encode(str(now()).encode()).decode().rstrip('=')
    sig = hmac.new(bytes.fromhex(a['secret']), body.encode(), 'sha256').hexdigest()
    return body + '.' + sig


def token_ok(tok):
    a = auth_conf()
    if not a or not tok or '.' not in tok:
        return False
    body, sig = tok.rsplit('.', 1)
    good = hmac.new(bytes.fromhex(a['secret']), body.encode(), 'sha256').hexdigest()
    if not hmac.compare_digest(sig, good):
        return False
    try:
        issued = int(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)).decode())
    except ValueError:
        return False
    return now() - issued < SESSION_DAYS * 86400


def too_many_fails(ip):
    cut = time.time() - 600
    for k in list(FAILS):
        FAILS[k] = [t for t in FAILS[k] if t > cut]
        if not FAILS[k]:
            del FAILS[k]
    return len(FAILS.get(ip, [])) >= 8


# ---------------- 页面版本：index.html 一改，开着的页面会自己刷新 ----------------

_ver = {'mtime': None, 'ver': ''}


def ui_ver():
    p = WEB / 'index.html'
    m = p.stat().st_mtime
    if m != _ver['mtime']:
        _ver['mtime'] = m
        _ver['ver'] = hashlib.sha1(p.read_bytes()).hexdigest()[:12]
    return _ver['ver']


# ---------------- 各个接口 ----------------

def api_todos(body):
    d = load('todos', {'mine': [], 'hers': []})
    today = today_cn()
    if body is not None:
        lst = d.get(body.get('list'))
        act = body.get('action')
        if lst is not None and act == 'add' and str(body.get('text', '')).strip():
            lst.append({'id': new_id(d['mine'] + d['hers']), 'text': str(body['text']).strip()[:300],
                        'done': False, 'at': str(body.get('at') or '')[:5], 'fixed': bool(body.get('fixed')),
                        'by': 'gu' if body.get('by') == 'gu' else 'her', 'created': now()})
        elif lst is not None and act in ('toggle', 'del'):
            for i, t in enumerate(lst):
                if t['id'] == body.get('id'):
                    if act == 'del':
                        lst.pop(i)
                    else:
                        t['done'] = not t['done']
                        t['done_day'] = today
                    break
        save('todos', d)
    # 「每天」的那种，过了零点自动变回没做
    for t in d['mine'] + d['hers']:
        if t.get('fixed') and t.get('done') and t.get('done_day') != today:
            t['done'] = False
    return {'ok': True, **d}


def api_cal(body):
    d = load('cal', {'events': [], 'period': {'days': {}}})
    if body is not None:
        act = body.get('action')
        if act == 'add_event' and str(body.get('text', '')).strip() and re.fullmatch(r'\d{4}-\d\d-\d\d', str(body.get('date', ''))):
            d['events'].append({'id': new_id(d['events']), 'date': body['date'], 'text': str(body['text']).strip()[:200],
                                'time': str(body.get('time') or '')[:5], 'yearly': bool(body.get('yearly')),
                                'type': 'special' if body.get('special') else 'normal', 'created': now()})
        elif act == 'del_event':
            d['events'] = [e for e in d['events'] if e['id'] != body.get('id')]
        elif act == 'day_record' and re.fullmatch(r'\d{4}-\d\d-\d\d', str(body.get('date', ''))):
            rec = {k: body.get(k) for k in ('flow', 'pain', 'mood', 'note', 'private') if body.get(k)}
            if rec:
                d['period']['days'][body['date']] = rec
            else:
                d['period']['days'].pop(body['date'], None)
        save('cal', d)
    return {'ok': True, 'cal': d, 'predict': None}


def api_notes(body):
    d = load('notes', {'gu': [], 'her': []})
    if body is not None:
        act, who = body.get('action'), body.get('who')
        if act == 'add' and str(body.get('text', '')).strip():
            d['her'].append({'id': new_id(d['gu'] + d['her']), 'text': str(body['text']).strip()[:800],
                             'at': now(), 'boxed': False})
        elif act in ('box', 'del') and who in d:
            for i, n in enumerate(d[who]):
                if n['id'] == body.get('id'):
                    if act == 'box':
                        n['boxed'] = not n.get('boxed')
                    elif who == 'her':          # 她贴的只能她自己撕
                        d[who].pop(i)
                    break
        save('notes', d)
    return d


def api_whisper(body):
    d = load('whisper', {'items': []})
    if body is not None and str(body.get('text', '')).strip():
        d['items'].append({'who': 'her', 'text': str(body['text']).strip()[:2000], 'at': now()})
        save('whisper', d)
    return {'ok': True, **d}


def api_herdiary(body):
    d = load('herdiary', {'items': []})
    if body is not None:
        if body.get('action') == 'add' and str(body.get('text', '')).strip():
            d['items'].insert(0, {'id': new_id(d['items']), 'text': str(body['text']).strip()[:5000], 'at': now()})
        elif body.get('action') == 'del':
            d['items'] = [it for it in d['items'] if it['id'] != body.get('id')]
        save('herdiary', d)
    return d


STORED = {
    'api/todos': api_todos,
    'api/cal': api_cal,
    'api/notes': api_notes,
    'api/whisper': api_whisper,
    'api/herdiary': api_herdiary,
}

# 小克要写的、或者要等第二步的：先给空的，形状跟演示数据一样，页面就显示空着的样子
EMPTY = {
    'api/messages': {'msgs': [], 'upto': 0, 'more': False},
    'api/status': {'alive': False, 'busy': False, 'armed': False, 'since': 0},
    'api/chats': {'items': []},
    'api/model': {'name': '还没接上', 'effort': ''},
    'api/context': {'used': 0, 'max': 200000},
    'api/usage': {'ok': True, 'items': []},
    'api/wall': {'ok': True, 'bricks': []},
    'api/night': {'days': []},
    'api/dreams': {'items': []},
    'api/favlines': {'ok': True, 'text': ''},
    'api/gong': {'msgs': []},
    'api/watch': {'connected': False, 'freshness': 'no_data'},
    'api/news': {'ok': False},
    'api/nook': {'ok': False},
    'api/music': {'ok': False},
    'api/repo': {'ok': False},
    'api/find': {'ok': True, 'items': []},
    'api/authmode': {'ok': False},
    'api/apiconf': {'ok': False},
    'api/watchkey': {'ok': False},
    'api/pushkey': {'ok': False},
}


# ---------------- 登录页（没进门之前只给这一页，外加小螃蟹的图） ----------------

LOGIN_PAGE = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#f7f9fb" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#20242a" media="(prefers-color-scheme: dark)">
<title>我们的小家</title>
<style>
  :root { --bg:#f7f9fb; --card:#fff; --line:#e1e7ee; --text:#262b31; --dim:#7d8793; --accent:#5f87ad; --red:#c0392b; }
  @media (prefers-color-scheme: dark) { :root { --bg:#20242a; --card:#2a2f36; --line:#3a4048; --text:#eef2f6; --dim:#9aa4b0; --red:#e07a6a; } }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px 16px;
         background:var(--bg); color:var(--text); font:15px/1.6 -apple-system, system-ui, "PingFang SC", "Microsoft YaHei", sans-serif; }
  form { width:100%; max-width:320px; text-align:center; }
  img { width:112px; height:112px; display:block; margin:0 auto 4px; }
  h1 { font:400 26px/1.3 "Songti SC", "Noto Serif SC", Georgia, serif; margin:0 0 4px; }
  p { color:var(--dim); font-size:13px; margin:0 0 22px; }
  input { width:100%; font:inherit; font-size:16px; padding:12px 14px; border-radius:12px; border:1px solid var(--line);
          background:var(--card); color:var(--text); outline:none; text-align:center; }
  input:focus { border-color:var(--accent); }
  button { width:100%; margin-top:12px; border:0; border-radius:999px; padding:12px; font:inherit; font-size:15px;
           background:var(--accent); color:#fff; cursor:pointer; }
  .err { color:var(--red); font-size:13px; min-height:20px; margin-top:10px; }
</style></head><body>
<form id="f">
  <img src="pet/clawd-idle-follow.svg" alt="">
  <h1>我们的小家</h1>
  <p>敲敲门</p>
  <input id="pw" type="password" placeholder="密码" autocomplete="current-password" autofocus>
  <button>进门</button>
  <div class="err" id="err"></div>
</form>
<script>
document.getElementById('f').onsubmit = async (e) => {
  e.preventDefault();
  const err = document.getElementById('err');
  err.textContent = '';
  try {
    const r = await fetch('login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: document.getElementById('pw').value }) });
    const d = await r.json();
    if (d.ok) location.replace('./');
    else err.textContent = d.msg || '不对哦';
  } catch { err.textContent = '敲不开，网不太好'; }
};
</script></body></html>'''

NO_PASSWORD_PAGE = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>我们的小家</title></head>
<body style="font:15px/1.8 system-ui,sans-serif;padding:40px 20px;text-align:center;color:#262b31;background:#f7f9fb">
小家还没设门锁。<br>请在服务器上运行：<br><code>python3 server/xiaojia.py --set-password</code></body></html>'''


# ---------------- 请求处理 ----------------

class Handler(BaseHTTPRequestHandler):
    server_version = 'xiaojia'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):     # 只记路径和状态，不记 cookie
        sys.stdout.write('%s %s\n' % (self.log_date_time_string(), fmt % args))
        sys.stdout.flush()

    # ---- 发送 ----
    def send(self, code, data, ctype, extra=None, cacheable=False):
        etag = None
        if cacheable:
            etag = '"' + hashlib.sha1(data).hexdigest()[:16] + '"'
            if self.headers.get('If-None-Match') == etag:
                self.send_response(304)
                self.send_header('ETag', etag)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
        zipped = (len(data) > 1024 and 'gzip' in self.headers.get('Accept-Encoding', '')
                  and (ctype.startswith('text/') or 'json' in ctype or 'svg' in ctype or 'javascript' in ctype))
        if zipped:
            data = gzip.compress(data, 6)
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if zipped:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        if etag:
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', 'no-cache')
        else:
            self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def json(self, obj, code=200, extra=None):
        self.send(code, json.dumps(obj, ensure_ascii=False).encode(), 'application/json; charset=utf-8', extra)

    def html(self, text, code=200):
        self.send(code, text.encode(), 'text/html; charset=utf-8')

    # ---- 读请求 ----
    def body_json(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > 1_000_000:
            return {}
        raw = self.rfile.read(n) if n else b''
        try:
            v = json.loads(raw or b'{}')
            return v if isinstance(v, dict) else {}
        except ValueError:
            return {}

    def authed(self):
        for part in (self.headers.get('Cookie') or '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == COOKIE:
                return token_ok(v)
        return False

    def client_ip(self):
        # 走 ngrok 进来时真实地址在 X-Forwarded-For 的最后一个（前面的可能是对方自己乱填的）
        xff = self.headers.get('X-Forwarded-For')
        return xff.split(',')[-1].strip() if xff else self.client_address[0]

    def secure(self):
        return self.headers.get('X-Forwarded-Proto') == 'https' or not self.client_address[0].startswith('127.')

    # ---- 路由 ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.route(None)

    def do_POST(self):
        self.route(self.body_json())

    def route(self, body):
        url = urlparse(self.path)
        path = url.path.lstrip('/')

        if not auth_conf():
            return self.html(NO_PASSWORD_PAGE, 503)

        if path == 'login' and body is not None:
            return self.login(body)
        if path.startswith('pet/') and body is None:       # 登录页要用小螃蟹，图是公开的
            return self.static(path)

        if not self.authed():
            if path.startswith('api/'):
                return self.json({'ok': False, 'msg': '没登录'}, 401)
            return self.html(LOGIN_PAGE)

        if path.startswith('api/'):
            return self.api(path, url, body)
        return self.static(path or 'index.html')

    def login(self, body):
        ip = self.client_ip()
        if too_many_fails(ip):
            return self.json({'ok': False, 'msg': '试太多次了，十分钟后再来'}, 429)
        if not check_password(str(body.get('password', ''))):
            FAILS.setdefault(ip, []).append(time.time())
            return self.json({'ok': False, 'msg': '不对哦'}, 403)
        cookie = f'{COOKIE}={make_token()}; Path=/; Max-Age={SESSION_DAYS * 86400}; HttpOnly; SameSite=Lax'
        if self.secure():
            cookie += '; Secure'
        return self.json({'ok': True}, extra={'Set-Cookie': cookie})

    def api(self, path, url, body):
        if path == 'api/poll':
            return self.poll(url)
        if path == 'api/send' and body is not None:
            return self.json({'ok': False, 'msg': '小克还没接上'}, 503)
        if path in STORED:
            with LOCK:
                return self.json(STORED[path](body))
        for prefix, val in EMPTY.items():
            if path == prefix or path.startswith(prefix + '/'):
                return self.json(val)
        return self.json({'ok': True})

    def poll(self, url):
        since = parse_qs(url.query).get('since', ['0'])[0]
        with WAKE:
            WAKE.wait(POLL_HOLD)
        return self.json({'ver': ui_ver(), 'next': since, 'events': []})

    def static(self, path):
        f = (WEB / path).resolve()
        if WEB.resolve() not in f.parents or not f.is_file():
            return self.send(404, b'not found', 'text/plain; charset=utf-8')
        data = f.read_bytes()
        ctype = mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype == 'image/svg+xml':
            ctype += '; charset=utf-8'
        if f.name == 'index.html':
            data = data.replace(b'<head>', b'<head><script>window.__XJ_BACKEND__=1</script>', 1)
        self.send(200, data, ctype, cacheable=True)


def main():
    ap = argparse.ArgumentParser(description='小家的后端')
    ap.add_argument('--set-password', action='store_true', help='设置或修改进门密码')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8090)
    a = ap.parse_args()
    if a.set_password:
        return set_password()
    mimetypes.add_type('image/svg+xml', '.svg')
    mimetypes.add_type('application/manifest+json', '.webmanifest')
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f'小家开门了：http://{a.host}:{a.port}  数据在 {DATA}', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
