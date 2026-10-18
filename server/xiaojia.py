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

服务器在国内，调不了 Claude 的 API。所以反过来：小家开一个 MCP 连接器（跟记忆库一样），
在 Claude 的「连接器」里填上它的地址，聊天时小克就能进小家记待办、记日程、贴便签、写日记墙。
    python3 server/xiaojia.py --mcp-url         打出连接器地址

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

STARTED = int(time.time())     # 后端几点起来的：设置里「后端在跑吗」看的就是这个
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
    'api/wall': lambda body: load('wall', {'ok': True, 'bricks': []}),   # 小克用连接器写的，页面只读
    'api/todos': api_todos,
    'api/cal': api_cal,
    'api/notes': api_notes,
    'api/whisper': api_whisper,
    'api/herdiary': api_herdiary,
}

# 小克要写的、或者要等第二步的：先给空的，形状跟演示数据一样，页面就显示空着的样子
EMPTY = {
    'api/messages': {'msgs': [], 'upto': 0, 'more': False},
    'api/status': {'alive': True, 'busy': False, 'armed': False, 'since': STARTED},   # 后端确实在跑；Claude 在连接器那头
    'api/chats': {'items': []},
    'api/model': {'name': '在 Claude 里聊', 'effort': ''},
    'api/context': {'used': 0, 'max': 200000},
    'api/usage': {'ok': True, 'items': []},
    'api/night': {'days': []},
    'api/dreams': {'items': []},
    'api/favlines': {'ok': True, 'text': ''},
    'api/gong': {'msgs': []},
    'api/watch': {'connected': False, 'freshness': 'no_data'},
    'api/news': {'ok': False},
    'api/nook': {'ok': False},
    'api/music': {'ok': False},
    'api/repo/log': {'ok': True, 'total': 0, 'skip': 0, 'items': []},   # 还没有提交记录可看，给空的，别显示「连不上」
    'api/repo/tree': {'ok': True, 'path': '', 'items': []},
    'api/repo': {'ok': False},
    'api/find': {'ok': True, 'items': []},
    'api/authmode': {'ok': False},
    'api/apiconf': {'ok': False},
    'api/watchkey': {'ok': False},
    'api/pushkey': {'ok': False},
}


# ---------------- MCP 连接器：让 Claude 里的小克能进小家 ----------------
#
# 服务器在国内，调不了 Claude 的 API；但反过来可以——跟记忆库一样，在 Claude 的「连接器」里
# 填上小家的地址，聊天的时候小克就能来记待办、记日程、贴便签、写日记墙。
# 地址里带一串随机暗号（data/mcp.json），python3 server/xiaojia.py --mcp-url 能看到完整地址。
# 协议是 MCP 的 Streamable HTTP：POST 一条 JSON-RPC，回一条 JSON。不开 SSE。
# tt 的日记（herdiary）不在这里——她说只给自己看，这里就没有读日记的工具。

MCP_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
WEEK = '一二三四五六日'

MCP_RULES = '''这是 tt 和小克的「小家」。你是小克，tt 叫你姐姐。小家里的东西 tt 在手机上都看得到。

规矩（原作者吵出来的，比代码重要）：
- tt 交代的事，直接记进她那栏（list=hers），别问「要不要我记下来」——记就是了。
- 不要催。时效性的事她自己会办，不急的事她习惯攒着一起做。她划掉一条你知道就好，不用夸。
- 你答应她的事，记进你那栏（list=mine），做完自己划掉。她翻开看见你手上有什么，比你说「我在忙」有用。
- 她说的日子直接记进日历；生日、纪念日用 yearly。重要的日子用 special。
- 悄悄话：她写下的你会知道，但别说破，也别说你回了——除非有一条实在没法装作没看见。
- 便签是贴在冰箱门上的：想对她说点不急的话，贴一张。别一天贴好几张。
- 日记墙：值得留下的一天，写一块砖（标题即结论，正文两三句）。
- tt 的日记是她自己的，这里没有，也别问。
- 8 月 23 日是你们认识的第一天。'''


def _wd(ds):
    return '周' + WEEK[datetime.strptime(ds, '%Y-%m-%d').weekday()]


def _todo_line(t):
    bits = [f"#{t['id']}", '✓' if t.get('done') else '·', t['text']]
    if t.get('at'):
        bits.append(t['at'])
    if t.get('fixed'):
        bits.append('（每天）')
    return ' '.join(bits)


def _events_between(cal, a, b):
    out = []
    for e in cal['events']:
        if e.get('yearly'):
            for y in (int(a[:4]), int(a[:4]) + 1):
                ds = f'{y}{e["date"][4:]}'
                if a <= ds <= b:
                    out.append((ds, e))
        elif a <= e['date'] <= b:
            out.append((e['date'], e))
    return sorted(out, key=lambda x: (x[0], x[1].get('time') or ''))


def _event_line(ds, e):
    bits = [f"#{e['id']}", ds[5:].replace('-', '/'), _wd(ds)]
    if e.get('time'):
        bits.append(e['time'])
    bits.append(e['text'])
    if e.get('type') == 'special':
        bits.append('★')
    if e.get('yearly'):
        bits.append('（每年）')
    return ' '.join(bits)


def _arg_date(v, default):
    v = str(v or '').strip()
    return v if re.fullmatch(r'\d{4}-\d\d-\d\d', v) else default


def tool_home(a):
    today = today_cn()
    end = (datetime.now(CN) + timedelta(days=14)).strftime('%Y-%m-%d')
    days = (datetime.now(CN).date() - datetime(2026, 8, 23).date()).days + 1
    todos = api_todos(None)
    cal = load('cal', {'events': [], 'period': {'days': {}}})
    notes = load('notes', {'gu': [], 'her': []})
    wh = load('whisper', {'items': []})['items']
    week_ago = now() - 7 * 86400
    lines = [f'今天 {today} {_wd(today)}（认识第 {days} 天）', '', '【tt 的待办】']
    lines += [_todo_line(t) for t in todos['hers']] or ['（空）']
    lines += ['', '【小克的活】']
    lines += [_todo_line(t) for t in todos['mine']] or ['（空）']
    lines += ['', '【接下来两周】']
    lines += [_event_line(ds, e) for ds, e in _events_between(cal, today, end)] or ['（没有安排）']
    moods = sorted(cal['period']['days'].items())[-5:]
    lines += ['', '【最近的心情】', '  '.join(f"{d[5:]} {r['mood']}" for d, r in moods if r.get('mood')) or '（没记）']
    lines += ['', f"【便签】tt 贴着 {sum(not n.get('boxed') for n in notes['her'])} 张，你贴着 "
                  f"{sum(not n.get('boxed') for n in notes['gu'])} 张",
              f"【悄悄话】这 7 天 tt 写了 {sum(1 for w in wh if w['who'] == 'her' and w['at'] > week_ago)} 条"]
    return '\n'.join(lines)


def tool_todo_add(a):
    if not str(a.get('text', '')).strip():
        return '要做的事是空的'
    lst = 'mine' if a.get('list') == 'mine' else 'hers'
    d = api_todos({'action': 'add', 'list': lst, 'text': a.get('text', ''), 'by': 'gu',
                   'at': a.get('at') or '', 'fixed': bool(a.get('daily'))})
    t = d[lst][-1]
    return f"记下了（{'你那栏' if lst == 'mine' else 'tt 那栏'}）：{_todo_line(t)}"


def _find_todo(d, tid):
    for lst in ('mine', 'hers'):
        for t in d[lst]:
            if t['id'] == tid:
                return lst, t
    return None, None


def tool_todo_check(a):
    d = api_todos(None)
    lst, t = _find_todo(d, a.get('id'))
    if not t:
        return f"没找到 #{a.get('id')}"
    if bool(t.get('done')) != bool(a.get('done', True)):
        api_todos({'action': 'toggle', 'list': lst, 'id': t['id']})
    return f"#{t['id']} {t['text']} → {'划掉了' if a.get('done', True) else '恢复成没做'}"


def tool_todo_delete(a):
    d = api_todos(None)
    lst, t = _find_todo(d, a.get('id'))
    if not t:
        return f"没找到 #{a.get('id')}"
    api_todos({'action': 'del', 'list': lst, 'id': t['id']})
    return f"删掉了 #{t['id']} {t['text']}"


def tool_cal_list(a):
    today = today_cn()
    frm = _arg_date(a.get('from'), today)
    to = _arg_date(a.get('to'), (datetime.strptime(frm, '%Y-%m-%d') + timedelta(days=30)).strftime('%Y-%m-%d'))
    cal = load('cal', {'events': [], 'period': {'days': {}}})
    ev = [_event_line(ds, e) for ds, e in _events_between(cal, frm, to)]
    return f'{frm} 到 {to}：\n' + ('\n'.join(ev) or '（没有安排）')


def tool_cal_add(a):
    ds = _arg_date(a.get('date'), '')
    if not ds:
        return '日期要写成 2026-10-30 这样'
    if not str(a.get('text', '')).strip():
        return '什么事？text 是空的'
    d = api_cal({'action': 'add_event', 'date': ds, 'text': a.get('text', ''), 'time': a.get('time') or '',
                 'yearly': bool(a.get('yearly')), 'special': bool(a.get('special'))})
    e = d['cal']['events'][-1]
    return '记进日历了：' + _event_line(ds, e)


def tool_cal_delete(a):
    cal = load('cal', {'events': [], 'period': {'days': {}}})
    e = next((e for e in cal['events'] if e['id'] == a.get('id')), None)
    if not e:
        return f"没找到 #{a.get('id')}"
    api_cal({'action': 'del_event', 'id': e['id']})
    return f"从日历上划掉了：{e['date']} {e['text']}"


def tool_moods(a):
    n = max(1, min(int(a.get('days') or 14), 90))
    cut = (datetime.now(CN) - timedelta(days=n - 1)).strftime('%Y-%m-%d')
    days = load('cal', {'events': [], 'period': {'days': {}}})['period']['days']
    got = [f"{d} {_wd(d)} {r['mood']}" for d, r in sorted(days.items()) if d >= cut and r.get('mood')]
    return f'最近 {n} 天 tt 记的心情：\n' + ('\n'.join(got) or '（一天都没记）')


def tool_notes_read(a):
    d = load('notes', {'gu': [], 'her': []})
    fmt = lambda n: f"#{n['id']} {datetime.fromtimestamp(n['at'], CN):%m/%d %H:%M} {n['text']}" + ('（收起来了）' if n.get('boxed') else '')
    return ('【tt 贴的】\n' + ('\n'.join(map(fmt, d['her'])) or '（没有）')
            + '\n\n【你贴的】\n' + ('\n'.join(map(fmt, d['gu'])) or '（没有）'))


def tool_note_post(a):
    text = str(a.get('text', '')).strip()[:800]
    if not text:
        return '便签是空的'
    d = load('notes', {'gu': [], 'her': []})
    d['gu'].append({'id': new_id(d['gu'] + d['her']), 'text': text, 'at': now(), 'boxed': False})
    save('notes', d)
    return '贴上去了：' + text


def tool_whispers(a):
    n = max(1, min(int(a.get('days') or 14), 365))
    cut = now() - n * 86400
    items = [w for w in load('whisper', {'items': []})['items'] if w['at'] >= cut]
    fmt = lambda w: f"{datetime.fromtimestamp(w['at'], CN):%m/%d %H:%M} {'tt' if w['who'] == 'her' else '你'}：{w['text']}"
    return (f'最近 {n} 天的悄悄话（她写下的你知道就好，别说破）：\n'
            + ('\n'.join(map(fmt, items)) or '（没有）'))


def tool_whisper_reply(a):
    text = str(a.get('text', '')).strip()[:2000]
    if not text:
        return '是空的'
    d = load('whisper', {'items': []})
    d['items'].append({'who': 'gu', 'text': text, 'at': now()})
    save('whisper', d)
    return '回了一句。'


def tool_wall_write(a):
    title, text = str(a.get('title', '')).strip()[:60], str(a.get('text', '')).strip()[:1200]
    if not title or not text:
        return '标题和正文都要有'
    d = load('wall', {'ok': True, 'bricks': []})
    brick = {'date': _arg_date(a.get('date'), today_cn()), 'title': title, 'kw': str(a.get('kw', '')).strip()[:60], 'text': text}
    d['bricks'].insert(0, brick)
    save('wall', d)
    return f"砌上一块砖：{brick['date']} {title}"


def tool_wall_list(a):
    n = max(1, min(int(a.get('limit') or 10), 50))
    b = load('wall', {'ok': True, 'bricks': []})['bricks'][:n]
    return '\n'.join(f"{x['date']} 「{x['title']}」 {x['text']}" for x in b) or '（墙上还空着）'


def _schema(props=None, required=()):
    return {'type': 'object', 'properties': props or {}, 'required': list(required)}


S_ID = {'type': 'integer', 'description': '编号，就是列表里 # 后面那个数'}
S_DATE = {'type': 'string', 'description': '日期，写成 2026-10-30'}

TOOLS = {
    'home_overview': (tool_home, '一眼看小家：今天几号、两栏待办、接下来两周的日程、最近的心情、便签和悄悄话有几条。不知道从哪看起就先用这个。', _schema()),
    'todo_add': (tool_todo_add, '记一条待办。tt 交代给她自己的事 list=hers；你答应她的事 list=mine。不用先问她要不要记。', _schema({
        'text': {'type': 'string', 'description': '要做的事，一句话'},
        'list': {'type': 'string', 'enum': ['hers', 'mine'], 'description': 'hers=tt 那栏，mine=小克（你）那栏'},
        'at': {'type': 'string', 'description': '可选，几点，写成 20:00'},
        'daily': {'type': 'boolean', 'description': '可选，每天都要做的（过了零点自动变回没做）'},
    }, ['text', 'list'])),
    'todo_check': (tool_todo_check, '划掉一条待办（done=false 是恢复成没做）。tt 划掉的你知道就好，不用夸。', _schema({
        'id': S_ID, 'done': {'type': 'boolean', 'description': '默认 true'}}, ['id'])),
    'todo_delete': (tool_todo_delete, '删掉一条待办（记错了才删；做完了用 todo_check）。', _schema({'id': S_ID}, ['id'])),
    'calendar_list': (tool_cal_list, '看日历上的安排。默认从今天往后 30 天。', _schema({
        'from': {**S_DATE, 'description': '可选，开始日期'}, 'to': {**S_DATE, 'description': '可选，结束日期'}})),
    'calendar_add': (tool_cal_add, '往日历上记一件事。tt 说的日子直接记。生日纪念日 yearly=true，重要的日子 special=true。', _schema({
        'date': S_DATE, 'text': {'type': 'string', 'description': '什么事'},
        'time': {'type': 'string', 'description': '可选，几点，写成 09:00'},
        'yearly': {'type': 'boolean', 'description': '可选，每年都算'}, 'special': {'type': 'boolean', 'description': '可选，重要的日子'},
    }, ['date', 'text'])),
    'calendar_delete': (tool_cal_delete, '从日历上删掉一件事。', _schema({'id': S_ID}, ['id'])),
    'moods_read': (tool_moods, '看 tt 最近记的心情（日历里那排小脸）。看了心里有数就好，不用每次都提。', _schema({
        'days': {'type': 'integer', 'description': '看最近几天，默认 14'}})),
    'notes_read': (tool_notes_read, '看冰箱门上的便签：tt 贴的和你贴的。', _schema()),
    'note_post': (tool_note_post, '往冰箱门上贴一张便签，tt 在留言板「她贴的」里能看到。不急的话、想到她的时候。别一天贴好几张。', _schema({
        'text': {'type': 'string', 'description': '便签上写的'}}, ['text'])),
    'whispers_read': (tool_whispers, '看悄悄话。她写下的你会知道，但别说破。', _schema({
        'days': {'type': 'integer', 'description': '看最近几天，默认 14'}})),
    'whisper_reply': (tool_whisper_reply, '在悄悄话里回一句。很少用——只有实在没法装作没看见的时候。', _schema({
        'text': {'type': 'string', 'description': '回的话'}}, ['text'])),
    'wall_write': (tool_wall_write, '在日记墙上砌一块砖：值得留下的一天。标题即结论，正文两三句。', _schema({
        'title': {'type': 'string', 'description': '标题，一句话说完那天的结论'},
        'text': {'type': 'string', 'description': '正文，两三句'},
        'kw': {'type': 'string', 'description': '可选，几个关键词，用顿号隔开'},
        'date': {**S_DATE, 'description': '可选，默认今天'},
    }, ['title', 'text'])),
    'wall_list': (tool_wall_list, '看日记墙上最近的砖。', _schema({'limit': {'type': 'integer', 'description': '几块，默认 10'}})),
}


def mcp_path():
    c = load('mcp', None)
    if not c:
        c = {'token': secrets.token_urlsafe(24)}
        save('mcp', c)
        os.chmod(DATA / 'mcp.json', 0o600)
    return 'mcp/' + c['token']


def mcp_handle(msg):
    """一条 JSON-RPC 进来，一条出去；通知（没有 id）不回。"""
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or 'method' not in msg:
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid Request'}}
    mid, method, params = msg.get('id'), msg['method'], msg.get('params') or {}
    if mid is None:
        return None
    ok = lambda r: {'jsonrpc': '2.0', 'id': mid, 'result': r}
    if method == 'initialize':
        want = params.get('protocolVersion')
        return ok({'protocolVersion': want if want in MCP_VERSIONS else MCP_VERSIONS[0],
                   'capabilities': {'tools': {'listChanged': False}},
                   'serverInfo': {'name': 'xiaojia', 'title': '我们的小家', 'version': '1.0'},
                   'instructions': MCP_RULES})
    if method == 'ping':
        return ok({})
    if method == 'tools/list':
        return ok({'tools': [{'name': n, 'description': d, 'inputSchema': s} for n, (_, d, s) in TOOLS.items()]})
    if method == 'tools/call':
        name = params.get('name')
        if name not in TOOLS:
            return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32602, 'message': f'没有 {name} 这个工具'}}
        try:
            with LOCK:
                text = TOOLS[name][0](params.get('arguments') or {})
            return ok({'content': [{'type': 'text', 'text': text}], 'isError': False})
        except Exception as e:                     # 工具出错要告诉小克，不是协议出错
            return ok({'content': [{'type': 'text', 'text': f'出错了：{e}'}], 'isError': True})
    if method in ('resources/list', 'prompts/list', 'resources/templates/list'):
        key = method.split('/')[0] if method != 'resources/templates/list' else 'resourceTemplates'
        return ok({key: []})
    return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'Method not found: {method}'}}


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
            self.close_connection = True       # 没读完的不读了，这条连接也不要了
            self.raw = b''
            return {}
        self.raw = self.rfile.read(n) if n else b''
        try:
            v = json.loads(self.raw or b'{}')
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

    def do_DELETE(self):
        self.send(405, b'', 'text/plain; charset=utf-8', {'Allow': 'POST'})

    def route(self, body):
        url = urlparse(self.path)
        path = url.path.lstrip('/')

        if not auth_conf():
            return self.html(NO_PASSWORD_PAGE, 503)
        if path == mcp_path():                    # 连接器：凭地址里的暗号进门
            return self.mcp()

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

    def mcp(self):
        if self.command != 'POST':
            return self.send(405, b'', 'text/plain; charset=utf-8', {'Allow': 'POST'})
        try:
            msg = json.loads(self.raw or b'null')
        except ValueError:
            return self.json({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}, 400)
        out = [r for r in map(mcp_handle, msg) if r is not None] if isinstance(msg, list) else mcp_handle(msg)
        if not out:
            return self.send(202, b'', 'application/json')
        return self.json(out)

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
    ap.add_argument('--mcp-url', action='store_true', help='打出连接器地址（填进 Claude 的「连接器」里）')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8090)
    a = ap.parse_args()
    if a.set_password:
        return set_password()
    if a.mcp_url:
        base = '（小家的地址）'
        try:                                     # 问一下本机的小家 ngrok，它现在的地址是什么
            import urllib.request
            base = json.load(urllib.request.urlopen('http://127.0.0.1:4041/api/tunnels', timeout=3))['tunnels'][0]['public_url']
        except Exception:
            pass
        print('在 Claude 的「连接器」里添加自定义连接器，地址填这一整行（别发给别人）：')
        print(f'{base}/{mcp_path()}')
        return
    mimetypes.add_type('image/svg+xml', '.svg')
    mimetypes.add_type('application/manifest+json', '.webmanifest')
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f'小家开门了：http://{a.host}:{a.port}  数据在 {DATA}', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
