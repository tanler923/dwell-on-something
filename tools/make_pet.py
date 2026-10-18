#!/usr/bin/env python3
"""
小螃蟹生成器。

web/pet/ 里那七张小螃蟹都是这个脚本画出来的。螃蟹的样子照着
xixicc186/clawd-emotes-skill（MIT）里的 Clawd：长方形身子、两只方块小手、
四条短腿、两道黑眼睛，全是 <rect>，动作用 SVG 自带的 CSS 动画做，
所以页面里 <img> 直接放就会动。头上那只小鸡是我们自己加的。
原作者的许可证在 web/pet/CLAWD-LICENSE.txt。

想改颜色、表情、头上的小鸡：改下面的零件，然后在仓库根目录跑
    python tools/make_pet.py          # 橘色小螃蟹（默认，Clawd 本色）
    python tools/make_pet.py blue     # 蓝色小螃蟹
刷新页面就能看到。打开 pet/preview.html 能一次看全七个状态。

坐标沿用原作者那套：身子在 x 2–13、y 6–13，脚到 y 15，头顶 y=6。
几条规矩也是原作者踩坑总结的：脚放在身子的组里（不然一动就脱节），
帽子（小鸡）要贴着 y=6 坐稳，不能悬空。
"""
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / 'web' / 'pet'

SHELLS = {
    'orange': '#DE886D',
    'blue':   '#7FA6CC',
}

# 128px 的格子里一格正好 5px，方块边缘不会糊
VIEWBOX = '-5.2 -8.6 25.6 25.6'

# ---------- 零件（#BODY 会换成身子的颜色） ----------

SHADOW = '<rect class="sh" x="3" y="15" width="9" height="1" fill="#000" opacity=".35"/>'
FEET = ('<g fill="#BODY"><rect x="3" y="13" width="1" height="2"/><rect x="5" y="13" width="1" height="2"/>'
        '<rect x="9" y="13" width="1" height="2"/><rect x="11" y="13" width="1" height="2"/></g>')
TORSO = '<rect x="2" y="6" width="11" height="7" fill="#BODY"/>'
ARMS = ('<rect class="la" x="0" y="9" width="2" height="2" fill="#BODY"/>'
        '<rect class="ra" x="13" y="9" width="2" height="2" fill="#BODY"/>')

EYES = '<g class="eye" fill="#000"><rect x="4" y="8" width="1" height="2"/><rect x="10" y="8" width="1" height="2"/></g>'
EYES_UP = '<g class="eye" fill="#000"><rect x="4.5" y="7.5" width="1" height="2"/><rect x="10.5" y="7.5" width="1" height="2"/></g>'
EYES_HALF = '<g class="eye" fill="#000"><rect x="4" y="8.8" width="1" height="1.2"/><rect x="10" y="8.8" width="1" height="1.2"/></g>'
EYES_SHUT = ('<g fill="#000"><rect x="3.6" y="8.9" width="2" height=".7" rx=".35"/>'
             '<rect x="9.4" y="8.9" width="2" height=".7" rx=".35"/></g>')
_STROKE = 'fill="none" stroke="#000" stroke-width=".7" stroke-linecap="round" stroke-linejoin="round"'
EYES_HAPPY = f'<g {_STROKE}><path d="M3.5 9.5 L4.5 8.4 L5.5 9.5"/><path d="M9.5 9.5 L10.5 8.4 L11.5 9.5"/></g>'
EYES_SQUINT = f'<g {_STROKE}><path d="M3.8 8.1 L5 9 L3.8 9.9"/><path d="M11.2 8.1 L10 9 L11.2 9.9"/></g>'

BLUSH = ('<g fill="#f4a7a0" opacity=".9"><ellipse cx="3.3" cy="11" rx=".8" ry=".45"/>'
         '<ellipse cx="11.7" cy="11" rx=".8" ry=".45"/></g>')


def chick(awake=True):
    """头上的小鸡：身子底边在 y≈6，正好坐在螃蟹头顶。"""
    if awake:
        eyes = ('<rect x="6.45" y="3.5" width=".5" height=".7" fill="#000"/>'
                '<rect x="8.05" y="3.5" width=".5" height=".7" fill="#000"/>')
    else:
        eyes = ('<rect x="6.3" y="3.85" width=".8" height=".3" rx=".15" fill="#000"/>'
                '<rect x="7.9" y="3.85" width=".8" height=".3" rx=".15" fill="#000"/>')
    return ('<g class="ck">'
            '<rect x="7.55" y="1.7" width=".6" height="1.2" rx=".3" fill="#E9B83E"/>'
            '<rect x="5.5" y="2.5" width="4" height="3.6" rx="1.5" fill="#F7D460"/>'
            '<rect x="5.6" y="4.5" width="1" height=".9" rx=".45" fill="#E9B83E"/>'
            '<rect x="8.4" y="4.5" width="1" height=".9" rx=".45" fill="#E9B83E"/>'
            + eyes +
            '<rect x="7.1" y="4.3" width=".8" height=".5" rx=".2" fill="#EF8D3C"/>'
            '</g>')


def heart(x0, y0, cls_style):
    """一颗像素小爱心，一格 .55。"""
    rows = ['##.##', '#####', '.###.', '..#..']
    u = .55
    rects = ''.join(f'<rect x="{x0 + dx * u:.2f}" y="{y0 + dy * u:.2f}" width="{u}" height="{u}"/>'
                    for dy, row in enumerate(rows) for dx, ch in enumerate(row) if ch == '#')
    return f'<g class="ht" style="{cls_style}" fill="#ef8fa0">{rects}</g>'


def sparkle(cx, cy, delay):
    return (f'<g class="sp" style="--d:{delay}" fill="#f7d460">'
            f'<rect x="{cx - .3}" y="{cy - 1.2}" width=".6" height="2.4" rx=".3"/>'
            f'<rect x="{cx - 1.2}" y="{cy - .3}" width="2.4" height=".6" rx=".3"/></g>')


# 笔记本：照原作者「写代码」那张的样子，屏幕上换成我们的颜色
LAPTOP = (
    '<g>'
    '<rect x=".4" y="9.9" width="14.2" height="5.2" rx=".5" fill="#2b3038"/>'
    '<rect x="1.3" y="10.6" width="12.4" height="3.8" rx=".3" fill="#121821"/>'
    '<rect class="l1" x="2.2" y="11" width="4" height=".7" rx=".2" fill="#ef8fa0"/>'
    '<rect x="6.8" y="11" width="3" height=".7" rx=".2" fill="#9dc0df"/>'
    '<rect class="l2" x="3" y="12.1" width="5" height=".7" rx=".2" fill="#f7d460"/>'
    '<rect x="8.6" y="12.1" width="2.4" height=".7" rx=".2" fill="#8db3cc"/>'
    '<rect class="l3" x="2.2" y="13.2" width="3.4" height=".7" rx=".2" fill="#c9b6f0"/>'
    '<rect class="cur" x="6" y="13.2" width=".7" height=".8" fill="#fff"/>'
    '<rect x="-.4" y="15" width="15.4" height="1" rx=".4" fill="#3a404a"/>'
    '</g>'
)

# ---------- 动作 ----------

BASE_CSS = '''
.bd{transform-origin:7.5px 13px}
.sh{transform-origin:7.5px 15.5px}
.eye{transform-origin:7.5px 9px}
.la{transform-origin:2px 10px}
.ra{transform-origin:13px 10px}
.ck{transform-origin:7.5px 6px}
@keyframes blink{0%,46%,54%,100%{transform:scaleY(1)}50%{transform:scaleY(.1)}}
@keyframes shd{0%,100%{transform:scaleX(1)}50%{transform:scaleX(.97)}}
'''


def crab(face, extra='', awake=True):
    """身子组：脚、身子、手、脸、小鸡都在里面，一起动。"""
    return '<g class="bd">' + FEET + TORSO + ARMS + face + chick(awake) + extra + '</g>'


STATES = {}

# 平时：轻轻呼吸，眨眼，小鸡东张西望
STATES['clawd-idle-follow.svg'] = ('平时', '''
.bd{animation:bob 2.6s infinite ease-in-out}
.sh{animation:shd 2.6s infinite ease-in-out}
.eye{animation:blink 3.2s infinite}
.ck{animation:peep 5.2s infinite ease-in-out}
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-.4px)}}
@keyframes peep{0%,40%,100%{transform:rotate(0)}50%,70%{transform:rotate(-7deg)}80%{transform:rotate(4deg)}}
''', SHADOW + crab(EYES))

# 想事情：眼睛往右上看，一只手举起来挠挠，冒三个小圆点
STATES['clawd-working-thinking.svg'] = ('想事情', '''
.bd{animation:muse 3.2s infinite ease-in-out}
.sh{animation:shd 3.2s infinite ease-in-out}
.eye{transform-origin:8px 8.5px;animation:blink 3.6s infinite}
.ra{animation:ponder 1.4s infinite ease-in-out}
.d1{animation:d1 2s infinite}.d2{animation:d2 2s infinite}.d3{animation:d3 2s infinite}
@keyframes muse{0%,100%{transform:rotate(0)}50%{transform:rotate(-2deg) translateY(-.3px)}}
@keyframes ponder{0%,100%{transform:rotate(-60deg)}50%{transform:rotate(-42deg)}}
@keyframes d1{0%,8%{opacity:0}18%,82%{opacity:1}92%,100%{opacity:0}}
@keyframes d2{0%,28%{opacity:0}38%,82%{opacity:1}92%,100%{opacity:0}}
@keyframes d3{0%,48%{opacity:0}58%,82%{opacity:1}92%,100%{opacity:0}}
''', SHADOW + crab(EYES_UP)
    + '<g fill="#9aa6b4"><circle class="d1" cx="14" cy="5" r=".5"/>'
      '<circle class="d2" cx="15.6" cy="2.9" r=".75"/><circle class="d3" cx="17.6" cy="-.1" r="1.1"/></g>')

# 打字：抱着笔记本敲，屏幕的光映在脸上
STATES['clawd-working-typing.svg'] = ('打字', '''
.bd{animation:focus 1.2s infinite ease-in-out}
.sh{animation:shd 1.2s infinite ease-in-out}
.eye{animation:blink 2.8s infinite}
.glow{animation:glow .6s infinite alternate ease-in-out}
.cur{animation:cur 1s steps(1) infinite}
.l1{transform-origin:2.2px 11.3px;animation:type 1.8s steps(1) infinite}
.l2{transform-origin:3px 12.4px;animation:type 1.8s -.6s steps(1) infinite}
.l3{transform-origin:2.2px 13.5px;animation:type 1.8s -1.2s steps(1) infinite}
@keyframes focus{0%,100%{transform:translateY(0)}50%{transform:translateY(-.3px)}}
@keyframes glow{0%{opacity:.08}100%{opacity:.22}}
@keyframes cur{0%,50%{opacity:1}51%,100%{opacity:0}}
@keyframes type{0%,100%{transform:scaleX(.5)}50%{transform:scaleX(1)}}
''', SHADOW + crab(
    '<rect class="glow" x="2" y="6" width="11" height="4" rx="1" fill="#8db3cc"/>' + EYES,
    extra=LAPTOP))

# 开心：蹦蹦跳跳，两只手挥起来，脸红红，冒爱心
STATES['clawd-happy.svg'] = ('开心', '''
.bd{animation:hop .9s infinite ease-in-out}
.sh{animation:hsh .9s infinite ease-in-out}
.la{animation:wl .45s infinite alternate ease-in-out}
.ra{animation:wr .45s infinite alternate ease-in-out}
.ck{animation:bop .45s infinite alternate ease-in-out}
.ht{transform-box:fill-box;transform-origin:center;opacity:0;animation:fl 1.8s var(--d,0s) infinite ease-out}
@keyframes hop{0%,100%{transform:translateY(0) scale(1,1)}15%{transform:translateY(0) scale(1.05,.93)}45%{transform:translateY(-2.2px) scale(.97,1.04)}75%{transform:translateY(0) scale(1.04,.95)}}
@keyframes hsh{0%,15%,75%,100%{transform:scaleX(1)}45%{transform:scaleX(.75)}}
@keyframes wl{from{transform:rotate(20deg)}to{transform:rotate(60deg)}}
@keyframes wr{from{transform:rotate(-20deg)}to{transform:rotate(-60deg)}}
@keyframes bop{from{transform:rotate(-6deg)}to{transform:rotate(6deg)}}
@keyframes fl{0%{opacity:0;transform:translateY(1px) scale(.6)}25%{opacity:1}100%{opacity:0;transform:translateY(-5px) scale(1.1)}}
''', SHADOW + crab(BLUSH + EYES_HAPPY)
    + heart(-4, 3, '--d:0s') + heart(16, 1.5, '--d:-.9s'))

# 打盹：眼皮慢慢耷拉，脑袋一点一点，飘一个 z；小鸡还醒着
STATES['clawd-idle-doze.svg'] = ('打盹', '''
.bd{animation:nod 4s infinite ease-in-out}
.sh{animation:shd 4s infinite ease-in-out}
.eye{transform-origin:7.5px 10px;animation:droop 4s infinite ease-in-out}
.ck{animation:peep 4s infinite ease-in-out}
.z{opacity:0;transform-box:fill-box;transform-origin:center;animation:zz 4s infinite ease-out}
@keyframes nod{0%,100%{transform:rotate(0) translateY(0)}45%,60%{transform:rotate(3deg) translateY(.5px)}}
@keyframes droop{0%,100%{transform:scaleY(1)}40%,65%{transform:scaleY(.2)}}
@keyframes peep{0%,100%{transform:rotate(0)}30%{transform:rotate(-8deg)}70%{transform:rotate(6deg)}}
@keyframes zz{0%,30%{opacity:0;transform:translate(0,0) scale(.7)}50%{opacity:1}100%{opacity:0;transform:translate(3px,-6px) scale(1.1)}}
''', SHADOW + crab(EYES_HALF)
    + '<text class="z" x="12.6" y="4.5" font-size="3" font-family="monospace" font-weight="bold" fill="#8fa8d6">z</text>')

# 睡着：闭眼，慢慢起伏，鼻涕泡一鼓一瘪，ZZZ 往上飘；小鸡也歪着头睡了
STATES['clawd-sleeping.svg'] = ('睡着', '''
.bd{animation:breathe 3.6s infinite ease-in-out}
.sh{animation:sshd 3.6s infinite ease-in-out}
.la,.ra{transform:translateY(.7px)}
.ck{transform:rotate(-10deg)}
.bub{transform-origin:12.4px 10.2px;animation:bub 3.6s infinite ease-in-out}
.z{opacity:0;transform-box:fill-box;transform-origin:center;animation:zz 3s var(--d,0s) infinite ease-out}
@keyframes breathe{0%,100%{transform:translateY(0) scaleY(1)}50%{transform:translateY(.5px) scaleY(.96)}}
@keyframes sshd{0%,100%{transform:scaleX(1)}50%{transform:scaleX(1.03)}}
@keyframes bub{0%,60%,100%{transform:scale(.2);opacity:0}30%{transform:scale(1);opacity:.75}}
@keyframes zz{0%{opacity:0;transform:translate(0,0) scale(.6)}20%{opacity:1}80%{opacity:.8}100%{opacity:0;transform:translate(var(--tx,4px),-7px) scale(1.2)}}
''', SHADOW + crab(EYES_SHUT + '<circle class="bub" cx="12.4" cy="10.2" r="1.1" fill="#cfe0ff"/>', awake=False)
    + '<g fill="#8fa8d6" font-family="monospace" font-weight="bold">'
      '<text class="z" style="--d:0s;--tx:4px" x="11.5" y="4" font-size="2.6">Z</text>'
      '<text class="z" style="--d:-1s;--tx:3.5px" x="13.5" y="1" font-size="3.4">Z</text>'
      '<text class="z" style="--d:-2s;--tx:3px" x="15.5" y="-2.5" font-size="4.2">Z</text></g>')

# 戳一下：蹲一下、连跳两下，手举高高，眯成 > <，冒小星星
STATES['clawd-react-double-jump.svg'] = ('戳一下', '''
.bd{transform-origin:7.5px 15px;animation:jump 1.8s infinite ease-in-out}
.sh{animation:jsh 1.8s infinite ease-in-out}
.la{transform:rotate(50deg)}.ra{transform:rotate(-50deg)}
.sp{transform-box:fill-box;transform-origin:center;opacity:0;animation:pop 1.8s var(--d,0s) infinite}
@keyframes jump{0%,62%,100%{transform:translateY(0) scale(1,1)}6%{transform:translateY(0) scale(1.08,.9)}18%{transform:translateY(-3.2px) scale(.95,1.06)}30%{transform:translateY(0) scale(1.08,.9)}36%{transform:translateY(0) scale(1,1)}46%{transform:translateY(-2px) scale(.97,1.04)}56%{transform:translateY(0) scale(1.06,.92)}}
@keyframes jsh{0%,30%,36%,56%,62%,100%{transform:scaleX(1)}18%{transform:scaleX(.7)}46%{transform:scaleX(.82)}}
@keyframes pop{0%,8%{opacity:0;transform:scale(.3) rotate(0)}18%{opacity:1;transform:scale(1.1) rotate(20deg)}30%{opacity:0;transform:scale(.6) rotate(45deg)}38%{opacity:0;transform:scale(.3)}46%{opacity:1;transform:scale(1) rotate(20deg)}58%,100%{opacity:0;transform:scale(.5) rotate(45deg)}}
''', SHADOW + crab(BLUSH + EYES_SQUINT)
    + sparkle(-3, 3.6, '0s') + sparkle(17, 1.8, '-.05s'))


# 听歌：戴上大耳机跟着摇，眯着眼，小鸡坐在头梁上一起点头，旁边飘音符（跟桌面图标是同一个样子）
HEADPHONES = ('<path d="M2.2 9.2 A5.3 5.3 0 0 1 12.8 9.2" fill="none" stroke="#262b31" stroke-width=".9" stroke-linecap="round"/>'
              '<rect x=".6" y="7.4" width="2.4" height="3.8" rx="1.2" fill="#262b31"/>'
              '<rect x="12" y="7.4" width="2.4" height="3.8" rx="1.2" fill="#262b31"/>')
STATES['clawd-music.svg'] = ('听歌', """
.bd{animation:sway 1.2s infinite ease-in-out}
.sh{animation:shd .6s infinite ease-in-out}
.ck{animation:nod .6s infinite alternate ease-in-out}
.nt{opacity:0;transform-box:fill-box;transform-origin:center;animation:nt 2.4s var(--d,0s) infinite ease-out}
@keyframes sway{0%,100%{transform:rotate(-4deg)}50%{transform:rotate(4deg)}}
@keyframes nod{from{transform:rotate(-8deg)}to{transform:rotate(8deg)}}
@keyframes nt{0%{opacity:0;transform:translate(0,0) scale(.7)}20%{opacity:1}100%{opacity:0;transform:translate(var(--tx,-2px),-7px) scale(1.1)}}
""", SHADOW + crab(HEADPHONES + '<g fill="#000"><rect x="3.6" y="8.9" width="2" height=".6" rx=".3"/>'
                    '<rect x="9.4" y="8.9" width="2" height=".6" rx=".3"/></g>'
                    '<g fill="#f4a7a0" opacity=".9"><ellipse cx="4.6" cy="10.7" rx=".8" ry=".45"/>'
                    '<ellipse cx="10.4" cy="10.7" rx=".8" ry=".45"/></g>')
    + '<g fill="#8fa8d6" font-family="sans-serif" font-weight="bold">'
      '<text class="nt" style="--d:0s;--tx:-2px" x="-3.6" y="5.5" font-size="3.6">♪</text>'
      '<text class="nt" style="--d:-1.2s;--tx:2px" x="15.2" y="4" font-size="3.6">♫</text></g>')


# ---------- 拼起来 ----------

def build(css, parts, color):
    css = ' '.join((BASE_CSS + css).split())
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{VIEWBOX}" width="128" height="128">'
            f'<style>{css}</style>{parts}</svg>\n').replace('#BODY', color)


PREVIEW = '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>小螃蟹</title>
<style>
  body { margin: 0; padding: 24px 16px; font: 14px/1.6 system-ui, sans-serif;
         background: #f7f9fb; color: #262b31; }
  h1 { font-size: 18px; margin: 0 0 16px; }
  .row { display: flex; flex-wrap: wrap; gap: 12px; padding: 16px; border-radius: 16px; margin-bottom: 16px; }
  .light { background: #eaeff4; }
  .dark { background: #20242a; color: #eef2f6; }
  figure { margin: 0; text-align: center; }
  img { width: 128px; height: 128px; display: block; }
  figcaption { font-size: 13px; opacity: .75; }
  .credit { font-size: 12px; opacity: .6; }
</style>
</head>
<body>
<h1>小螃蟹的七个样子</h1>
<div class="row light">FIGS</div>
<div class="row dark">FIGS</div>
<p class="credit">螃蟹的样子照着 xixicc186/clawd-emotes-skill 里的 Clawd（MIT）。</p>
</body>
</html>
'''


def main():
    shell = sys.argv[1] if len(sys.argv) > 1 else 'orange'
    if shell not in SHELLS:
        sys.exit(f'没有 {shell} 这个颜色，可选：{", ".join(SHELLS)}')
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (_, css, parts) in STATES.items():
        (OUT / name).write_text(build(css, parts, SHELLS[shell]), encoding='utf-8', newline='\n')
    figs = ''.join(f'<figure><img src="{n}" alt=""><figcaption>{label}</figcaption></figure>'
                   for n, (label, _, _) in STATES.items())
    (OUT / 'preview.html').write_text(PREVIEW.replace('FIGS', figs), encoding='utf-8', newline='\n')
    print(f'done: {len(STATES)} x {shell} -> web/pet/')


if __name__ == '__main__':
    main()
