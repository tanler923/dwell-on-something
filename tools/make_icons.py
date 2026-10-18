#!/usr/bin/env python3
"""
小家的桌面图标。

手机「添加到主屏幕」、浏览器标签页上用的那几张小螃蟹，都是这个脚本画的：
螃蟹的坐标跟 tools/make_pet.py 里那只一样（身子 x 2–13、y 6–13，脚到 y 15，头顶坐着小鸡），
戴着大耳机在听歌（tt 想要的「小克听歌」那种），旁边飘两个音符，背景是雪山蓝。只用 Python 自带的库：自己一像素一像素地画，4×4 超采样让边缘不发毛。

    python tools/make_icons.py

画出来放在 web/icons/，页面头上那几行 <link> 会用到；web/manifest.json 也是这里一起写的。

为什么 manifest 里的图标是一长串 data: 而不是 icons/xxx.png：免费 ngrok 会把「浏览器来的、
没带它 cookie 的请求」先拦到一张英文提醒页上；Chrome 下载 manifest 图标时偏偏不带 cookie，
拿到的就成了那张提醒页，结果「添加到主屏幕」加不上。把图标直接写进 manifest，就不用再下载了。
"""
import base64
import json
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / 'web' / 'icons'

BG = (0xdc, 0xe8, 0xf2)          # 雪山蓝的浅底（跟页面里 --sage-track 一个颜色）
BODY = (0xde, 0x88, 0x6d)        # Clawd 本色
INK = (0x2a, 0x25, 0x22)
CHICK, CHICK_D, BEAK = (0xf7, 0xd4, 0x60), (0xe9, 0xb8, 0x3e), (0xef, 0x8d, 0x3c)
PINK = (0xf4, 0xa7, 0xa0)
PHONES = (0x26, 0x2b, 0x31)      # 耳机
NOTE = (0x7d, 0x87, 0x93)        # 音符

# (形状, 参数, 颜色, 不透明度)。坐标是螃蟹的格子单位；先画的在下面
CRAB = [
    ('rect', (3, 15, 9, .7), (0, 0, 0), .16),                       # 影子
    *[('rect', (x, 13, 1, 2), BODY, 1) for x in (3, 5, 9, 11)],      # 四条腿
    ('rect', (2, 6, 11, 7), BODY, 1),                                # 身子（两只手捂在耳机后面，就不画了）
    ('ring', (7.5, 9, 5.5, 4.6, 9), PHONES, 1),                      # 耳机头梁：一道弧，从头顶绕过去
    ('rrect', (.6, 7.4, 2.4, 3.8, 1.2), PHONES, 1), ('rrect', (12, 7.4, 2.4, 3.8, 1.2), PHONES, 1),   # 两只耳罩
    ('rrect', (3.6, 8.9, 2, .6, .3), INK, 1), ('rrect', (9.4, 8.9, 2, .6, .3), INK, 1),   # 眯着眼听，陶醉
    ('ellipse', (4.6, 10.7, .8, .45), PINK, .85), ('ellipse', (10.4, 10.7, .8, .45), PINK, .85),   # 腮红
    # 两个音符：一个 ♪，一个 ♫
    ('ellipse', (-.6, 4.6, .65, .5), NOTE, 1), ('rect', (-.08, 1.9, .3, 2.7), NOTE, 1), ('rrect', (-.08, 1.9, 1.1, .4, .2), NOTE, 1),
    ('ellipse', (.95, 3.0, .55, .42), NOTE, 1), ('ellipse', (2.4, 2.6, .55, .42), NOTE, 1),
    ('rect', (1.35, .6, .28, 2.4), NOTE, 1), ('rect', (2.8, .2, .28, 2.4), NOTE, 1), ('rect', (1.35, .2, 1.73, .42), NOTE, 1),
    ('rrect', (7.55, 1.7, .6, 1.2, .3), CHICK_D, 1),                 # 小鸡（坐在头梁上）：呆毛
    ('rrect', (5.5, 2.5, 4, 3.6, 1.5), CHICK, 1),                    # 小鸡：身子
    ('rrect', (5.6, 4.5, 1, .9, .45), CHICK_D, 1), ('rrect', (8.4, 4.5, 1, .9, .45), CHICK_D, 1),
    ('rect', (6.45, 3.5, .5, .7), INK, 1), ('rect', (8.05, 3.5, .5, .7), INK, 1),
    ('rrect', (7.1, 4.3, .8, .5, .2), BEAK, 1),
]
CRAB_BOX = (-1.25, .2, 14.4, 15.7)   # 螃蟹连小鸡、音符、影子的外框，用来居中


def inside(kind, p, x, y):
    if kind == 'rect':
        x0, y0, w, h = p
        return x0 <= x < x0 + w and y0 <= y < y0 + h
    if kind == 'ellipse':
        cx, cy, rx, ry = p
        return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1
    if kind == 'ring':                        # 圆环的上半截（y 不超过 ymax）：耳机头梁
        cx, cy, ro, ri, ymax = p
        d = (x - cx) ** 2 + (y - cy) ** 2
        return ri * ri <= d <= ro * ro and y <= ymax
    x0, y0, w, h, r = p                       # 圆角矩形
    if not (x0 <= x < x0 + w and y0 <= y < y0 + h):
        return False
    cx = min(max(x, x0 + r), x0 + w - r)
    cy = min(max(y, y0 + r), y0 + h - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def bbox(kind, p):
    if kind == 'ellipse':
        cx, cy, rx, ry = p
        return cx - rx, cy - ry, cx + rx, cy + ry
    if kind == 'ring':
        cx, cy, ro, ri, ymax = p
        return cx - ro, cy - ro, cx + ro, ymax
    x0, y0, w, h = p[:4]
    return x0, y0, x0 + w, y0 + h


def draw(size, crab_frac, rounded=0.0, ss=4):
    """crab_frac：螃蟹宽占整张图的几成。rounded>0 时背景是圆角方块、外面透明（给浏览器标签页用）。"""
    px = [[list(BG) + [0.0] for _ in range(size)] for _ in range(size)]
    k = crab_frac * size / (CRAB_BOX[2] - CRAB_BOX[0])           # 一格多少像素
    ox = size / 2 - k * (CRAB_BOX[0] + CRAB_BOX[2]) / 2
    oy = size / 2 - k * (CRAB_BOX[1] + CRAB_BOX[3]) / 2

    def paint(kind, p, color, alpha, to_units):
        x0, y0, x1, y1 = bbox(kind, p) if to_units else (0, 0, size, size)
        if to_units:
            x0, y0, x1, y1 = ox + x0 * k, oy + y0 * k, ox + x1 * k, oy + y1 * k
        for j in range(max(0, int(y0)), min(size, int(y1) + 1)):
            for i in range(max(0, int(x0)), min(size, int(x1) + 1)):
                hit = 0
                for sj in range(ss):
                    for si in range(ss):
                        sx, sy = i + (si + .5) / ss, j + (sj + .5) / ss
                        if to_units:
                            sx, sy = (sx - ox) / k, (sy - oy) / k
                        hit += inside(kind, p, sx, sy)
                if not hit:
                    continue
                a = alpha * hit / (ss * ss)
                c = px[j][i]
                for n in range(3):
                    c[n] = c[n] * (1 - a) + color[n] * a
                c[3] = c[3] + (1 - c[3]) * a

    if rounded:
        paint('rrect', (0, 0, size, size, size * rounded), BG, 1, False)
    else:
        for row in px:
            for c in row:
                c[3] = 1.0
    for kind, p, color, alpha in CRAB:
        paint(kind, p, color, alpha, True)
    return px


def png(px):
    h, w = len(px), len(px[0])
    raw = b''.join(b'\x00' + bytes(min(255, max(0, round(v if n < 3 else v * 255)))
                                   for c in row for n, v in enumerate(c)) for row in px)
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def ico(png_bytes, size):
    """一张 PNG 包成 .ico（现在的浏览器都认这种）。"""
    return (struct.pack('<HHH', 0, 1, 1)
            + struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(png_bytes), 22) + png_bytes)


MANIFEST = {
    'name': '我们的小家', 'short_name': '我们的小家', 'lang': 'zh-CN',
    'start_url': './', 'scope': './', 'display': 'standalone',
    'background_color': '#f7f9fb', 'theme_color': '#f7f9fb',
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # 主屏幕图标：铺满背景，螃蟹放在中间——安卓会把图标裁成圆形，留够边才不会切到小鸡和音符
    icons = {}
    for size in (512, 192, 180):
        icons[size] = png(draw(size, .55))
        (OUT / f'icon-{size}.png').write_bytes(icons[size])
    data = lambda b: 'data:image/png;base64,' + base64.b64encode(b).decode()
    manifest = {**MANIFEST, 'icons': [
        {'src': data(icons[192]), 'sizes': '192x192', 'type': 'image/png'},
        {'src': data(icons[512]), 'sizes': '512x512', 'type': 'image/png'},
        {'src': data(icons[512]), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
    ]}
    (OUT.parent / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                              encoding='utf-8', newline='\n')
    # 浏览器标签页：圆角小方块，螃蟹撑大一点，小尺寸才看得清
    fav = png(draw(64, .82, rounded=.22))
    (OUT / 'favicon-64.png').write_bytes(fav)
    (OUT / 'favicon.ico').write_bytes(ico(fav, 64))
    print('done:', ', '.join(sorted(p.name for p in OUT.iterdir())))


if __name__ == '__main__':
    main()
