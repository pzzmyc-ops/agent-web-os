#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多语言封面换字工具：画刻度尺 / 抠纯色底字 / 贴回无字底图 / 拼总表

必须用带 numpy+cv2 的解释器运行（本机 D:/mafagent/python/python.exe）。

  # 1) 画刻度尺给自己看：只画线、不做任何识别，坐标由人自己读
  python cover_swap.py grid --image 原图.jpg --out grid.png --step 50 --scale 1.0 --crop 0.6,1.0
  # 2) 抠出纯色底上的字（逐像素颜色运算，结果要人眼看）
  python cover_swap.py key --plate 纯色底图.png --out keyed.png
  # 3) 贴回：--box 是人看图读出来的"新标题要占的区域"，脚本只做变换
  python cover_swap.py paste --text keyed.png --base 无字底图.png --out 成品.png \
      --box 180,868,800,1235 --glow-op 0.5 --glow-px 9
  # 4) 拼中文标签总表
  python cover_swap.py sheet --dir out --out 总表.png --labels "nl=荷兰语,fr=法语"

设计原则（重要）：
  · 本工具**不做任何文字区域识别**。凡是"文字在哪、标题占多大范围"这类判断，
    一律由人看图（可配合 grid 画的刻度线）读出来后作为参数传入。
    不用颜色阈值 / 形态学 / 连通块去猜——这些在星空图、人像高光、浅底图上全不可靠。
  · grid 只画刻度线，本身不做识别。
  · key 只是逐像素的颜色换算（底色→alpha），确定性运算，结果必须 render_media 给人看。
"""
import argparse
import os
import sys

import cv2
import numpy as np


def parse_nums(s):
    return tuple(float(v) for v in str(s).split(','))


def parse_int_box(s):
    return tuple(int(v) for v in str(s).split(','))


def parse_band(s):
    """'0.35' -> (0.0, 0.35)   '0.05,0.40' -> (0.05, 0.40)"""
    parts = [float(v) for v in str(s).split(',')]
    return (0.0, parts[0]) if len(parts) == 1 else (parts[0], parts[1])


# ---------------------------------------------------------------- grid
def _font_path():
    for p in ('C:/Windows/Fonts/arial.ttf', 'C:/Windows/Fonts/msyh.ttc',
              'C:/Windows/Fonts/tahoma.ttf'):
        if os.path.exists(p):
            return p
    return ''


def cmd_grid(a):
    """给图叠坐标刻度线，供人目测读坐标。只画线、只标数，不做任何识别。"""
    from PIL import Image, ImageDraw, ImageFont
    img = cv2.imread(a.image)
    if img is None:
        print('读不到图: %s' % a.image, file=sys.stderr)
        sys.exit(1)
    H, W = img.shape[:2]
    y0f, y1f = parse_band(a.crop) if a.crop else (0.0, 1.0)
    y0, y1 = int(H * y0f), int(H * y1f)
    S = a.scale
    step = a.step

    fp = _font_path()
    if not fp:
        print('找不到可用字体（arial/msyh/tahoma）', file=sys.stderr)
        sys.exit(1)
    fsize = a.fontsize
    font = ImageFont.truetype(fp, fsize)
    probe = Image.new('RGB', (8, 8))
    pd = ImageDraw.Draw(probe)

    xs = list(range(0, W, step))
    ys = list(range((y0 // step) * step, y1, step))
    if not xs or not ys:
        print('步长太大，没有刻度线', file=sys.stderr)
        sys.exit(1)

    def tw(t):
        return pd.textbbox((0, 0), t, font=font)[2]

    max_x_len = max(tw(str(v)) for v in xs)
    max_y_w = max(tw(str(v)) for v in ys)
    pad_top = max_x_len + 6
    pad_left = max_y_w + 8
    gap = fsize + 6
    if step * S < gap:
        print('⚠️ 刻度太密（每档只有 %.1fpx，一个字高要 %dpx）：数字会互相压。'
              '请调大 --scale 或增大 --step，本工具不替你跳号。' % (step * S, gap))

    ow, oh = int(round(W * S)) + pad_left, int(round((y1 - y0) * S)) + pad_top
    canvas = np.zeros((oh, ow, 3), np.uint8)
    small = cv2.resize(img[y0:y1], (int(round(W * S)), int(round((y1 - y0) * S))),
                       interpolation=cv2.INTER_LANCZOS4)
    canvas[pad_top:, pad_left:] = small

    def xy(x, y):
        return pad_left + int(round(x * S)), pad_top + int(round((y - y0) * S))

    for y in ys:
        _, yy = xy(0, y)
        cv2.line(canvas, (pad_left, yy), (ow, yy), (0, 190, 0), 1)
    for x in xs:
        xx, _ = xy(x, y0)
        cv2.line(canvas, (xx, pad_top), (xx, oh), (0, 170, 200), 1)

    pil = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    for v in ys:
        _, yy = xy(0, v)
        d.text((pad_left - 4 - tw(str(v)), yy - fsize // 2 - 2), str(v), font=font, fill=(0, 255, 0))
    for v in xs:
        xx, _ = xy(v, y0)
        t = Image.new('RGBA', (tw(str(v)) + 2, fsize + 10), (0, 0, 0, 0))
        ImageDraw.Draw(t).text((0, 0), str(v), font=font, fill=(0, 255, 255))
        t = t.rotate(90, expand=True)
        pil.paste(t, (xx - t.width // 2, pad_top - t.height - 4), t)
    cv2.imwrite(a.out, cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR))
    print('刻度图 -> %s  %dx%d（图区放大 %.1f 倍，上方留 %dpx、左侧留 %dpx 放刻度）  原图 %dx%d  步长 %d'
          % (a.out, ow, oh, S, pad_top, pad_left, W, H, step))
    print('刻度是全的：每条线都有数字，青字(竖排)=x，绿字=y。图外黑边是刻度位，不遮画面。')


# ---------------------------------------------------------------- key
def key_plate(plate_path, lo=30, hi=110, erode=1, blur=0.9, despill=True,
              min_alpha=0.25, key_color=None):
    """纯色底上的字 -> RGBA。逐像素确定性运算，不做区域识别。"""
    plate = cv2.imread(plate_path)
    if plate is None:
        print('读不到图: %s' % plate_path, file=sys.stderr)
        sys.exit(1)
    plate = plate.astype(np.float32)
    if key_color is None:
        border = np.concatenate([plate[0:8].reshape(-1, 3), plate[-8:].reshape(-1, 3),
                                 plate[:, 0:8].reshape(-1, 3), plate[:, -8:].reshape(-1, 3)])
        key_color = np.median(border, axis=0)      # 底色：从图边缘取中值
    d = np.linalg.norm(plate - key_color, axis=2)
    alpha = np.clip((d - lo) / (hi - lo), 0, 1)
    a8 = cv2.erode((alpha * 255).astype(np.uint8), np.ones((3, 3), np.uint8), erode) if erode \
        else (alpha * 255).astype(np.uint8)
    alpha = cv2.GaussianBlur(a8.astype(np.float32), (0, 0), blur) / 255.0
    b, g, r = [plate[:, :, i] for i in range(3)]
    if despill:
        plate = np.dstack([np.minimum(b, np.maximum(r, g)), g, r])
    a3 = alpha[:, :, None]
    fg = np.clip(np.where(a3 > 0.06, (plate - (1 - a3) * key_color) / np.maximum(a3, 0.06), 0), 0, 255)
    ys, xs = np.nonzero(alpha > min_alpha)
    rgba = np.dstack([fg, alpha * 255]).astype(np.uint8)
    return rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1], key_color


def cmd_key(a):
    rgba, key_color = key_plate(a.plate, a.lo, a.hi, a.erode, a.blur, not a.no_despill,
                                a.min_alpha, parse_nums(a.key_color) if a.key_color else None)
    cv2.imwrite(a.out, rgba)
    print('底色 BGR = %s  抠出 %d x %d -> %s' % (key_color, rgba.shape[1], rgba.shape[0], a.out))
    print('务必 render_media 给人看：有没有底色描边、笔画有没有变薄。')


# ---------------------------------------------------------------- paste
def cmd_paste(a):
    crop = cv2.imread(a.text, cv2.IMREAD_UNCHANGED)
    if crop is None or crop.ndim != 3 or crop.shape[2] != 4:
        print('--text 必须是带 alpha 的 RGBA png', file=sys.stderr)
        sys.exit(1)
    base = cv2.imread(a.base)
    if base is None:
        print('读不到底图: %s' % a.base, file=sys.stderr)
        sys.exit(1)
    crop = crop.astype(np.float32)
    base = base.astype(np.float32)
    Hh, Ww = base.shape[:2]

    bx0, by0, bx1, by1 = a.box            # 人看图读出来的目标区域（底图像素坐标）
    bw, bh = bx1 - bx0, by1 - by0
    if bw <= 4 or bh <= 4:
        print('--box 太小/反了：x0,y0,x1,y1 要按底图像素给', file=sys.stderr)
        sys.exit(1)

    ch, cw = crop.shape[:2]
    if a.fit == 'both':
        fx, fy = bw / float(cw), bh / float(ch)
        print('⚠️ --fit both 会非等比拉伸字形（横 %.3f / 纵 %.3f），通常不该用' % (fx, fy))
    else:
        fx = fy = bw / float(cw)          # 等比：按目标区域宽度定缩放
    nw, nh = max(1, int(round(cw * fx))), max(1, int(round(ch * fy)))
    txt = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)

    if a.halign == 'center':
        x0 = bx0 + (bw - nw) // 2
    else:
        x0 = bx0
    if a.valign == 'top':
        y0 = by0
    elif a.valign == 'bottom':
        y0 = by1 - nh
    else:
        y0 = by0 + (bh - nh) // 2

    alp = txt[:, :, 3] / 255.0
    rgb = txt[:, :, :3]

    # 柔光层（可选）：把 alpha 外扩模糊后垫在字下面，模拟原设计的发光
    pad = int(a.glow_px * 3) if a.glow_op > 0 else 0
    if pad > 0:
        gl = np.zeros((nh + 2 * pad, nw + 2 * pad), np.float32)
        gl[pad:pad + nh, pad:pad + nw] = alp
        gl = cv2.GaussianBlur(gl, (0, 0), a.glow_px) * a.glow_op
        rc = np.zeros((nh + 2 * pad, nw + 2 * pad, 3), np.float32)
        rc[pad:pad + nh, pad:pad + nw] = rgb
        ac = np.zeros((nh + 2 * pad, nw + 2 * pad), np.float32)
        ac[pad:pad + nh, pad:pad + nw] = alp
        gx, gy = x0 - pad, y0 - pad
        nw2, nh2 = nw + 2 * pad, nh + 2 * pad
    else:
        gl = None
        gx, gy = x0, y0
        nw2, nh2 = nw, nh

    x_lo, y_lo = max(gx, 0), max(gy, 0)
    x_hi, y_hi = min(gx + nw2, Ww), min(gy + nh2, Hh)
    if x_hi <= x_lo or y_hi <= y_lo:
        print('文字块完全在画布外：检查 --box / --halign / --valign', file=sys.stderr)
        sys.exit(1)
    fx0, fy0 = x_lo - gx, y_lo - gy

    def cut(arr):
        return arr[fy0:fy0 + (y_hi - y_lo), fx0:fx0 + (x_hi - x_lo)]

    reg = base[y_lo:y_hi, x_lo:x_hi]

    if gl is not None:
        g = cut(gl)[:, :, None]
        reg = reg * (1 - g) + np.array(parse_nums(a.glow_color), np.float32) * g
        aa = cut(ac)[:, :, None]
        rc2 = cut(rc)
    else:
        aa = cut(alp)[:, :, None]
        rc2 = cut(rgb)

    # 投影（可选）
    ash = None
    if a.shadow_op > 0:
        sc = a.scale_ratio
        shift = np.float32([[1, 0, a.shadow_dx * sc], [0, 1, a.shadow_dy * sc]])
        ash = cv2.warpAffine(alp, shift, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=0)
        ash = cv2.GaussianBlur(ash, (0, 0), a.shadow_blur * sc) * a.shadow_op
        pad2 = max(0, pad)
        sh_canvas = np.zeros((nh + 2 * pad2, nw + 2 * pad2), np.float32)
        sh_canvas[pad2:pad2 + nh, pad2:pad2 + nw] = ash
        sh = sh_canvas[fy0:fy0 + (y_hi - y_lo), fx0:fx0 + (x_hi - x_lo)][:, :, None]
        reg = reg * (1 - sh) + np.array(parse_nums(a.shadow_color), np.float32) * sh

    reg = reg * (1 - aa) + rc2 * aa
    base[y_lo:y_hi, x_lo:x_hi] = reg
    cv2.imwrite(a.out, base.astype(np.uint8))
    print('贴回 %s  目标框=%s  等比缩放=%.4f  落在=(%d,%d) 尺寸=%dx%d  柔光=%.2f  投影不透明度=%.2f' % (
        a.out, a.box, fx, x0, y0, nw, nh, a.glow_op, a.shadow_op))
    print('务必 render_media 给人看，并和原图并排对比位置/字号。')


# ---------------------------------------------------------------- sheet
def cmd_sheet(a):
    from PIL import Image, ImageDraw, ImageFont
    labels = {}
    if a.labels:
        for kv in a.labels.split(','):
            k, v = kv.split('=')
            labels[k.strip()] = v.strip()
    codes = [f[:-4] for f in sorted(os.listdir(a.dir)) if f.lower().endswith('.png')]
    if not codes:
        print('目录里没有 png: %s' % a.dir, file=sys.stderr)
        sys.exit(1)
    font = ImageFont.truetype(a.font, a.fontsize)
    tiles = []
    for c in codes:
        im = cv2.imread(os.path.join(a.dir, c + '.png'))
        im = cv2.resize(im, (a.tile_w, a.tile_h), interpolation=cv2.INTER_AREA)
        tiles.append(Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB)))
    rows = (len(tiles) + a.cols - 1) // a.cols
    sheet = Image.new('RGB', (a.tile_w * a.cols, a.tile_h * rows), 'white')
    d = ImageDraw.Draw(sheet)
    for i, (im, c) in enumerate(zip(tiles, codes)):
        x, y = (i % a.cols) * a.tile_w, (i // a.cols) * a.tile_h
        sheet.paste(im, (x, y))
        txt = labels.get(c, c)
        d.rectangle([x, y, x + a.fontsize * (len(txt) + 1), y + a.fontsize + 16], fill=(0, 0, 0))
        d.text((x + 8, y + 8), txt, font=font, fill=(255, 255, 255))
    sheet.save(a.out)
    print('总表 -> %s %s  共 %d 张' % (a.out, sheet.size, len(tiles)))


# ---------------------------------------------------------------- CLI
parser = argparse.ArgumentParser(description='多语言封面换字工具（不识别文字区域，坐标由人读）')
sub = parser.add_subparsers(dest='cmd', required=True)

p0 = sub.add_parser('grid', help='给图叠坐标刻度线，供人目测读坐标（不做识别）')
p0.add_argument('--image', required=True)
p0.add_argument('--out', required=True)
p0.add_argument('--step', type=int, default=20, help='刻度步长（原图像素）')
p0.add_argument('--scale', type=float, default=2.0, help='放大倍数，看不清就调大')
p0.add_argument('--crop', default='', help="只画某一段，如 '0,0.45'")
p0.add_argument('--fontsize', type=int, default=15, help='刻度字号')
p0.set_defaults(func=cmd_grid)

p1 = sub.add_parser('key', help='纯色底图 -> 带 alpha 的字（逐像素颜色运算）')
p1.add_argument('--plate', '--blue', dest='plate', required=True, help='纯色底的字图')
p1.add_argument('--out', required=True)
p1.add_argument('--lo', type=float, default=30)
p1.add_argument('--hi', type=float, default=110)
p1.add_argument('--erode', type=int, default=1)
p1.add_argument('--blur', type=float, default=0.9)
p1.add_argument('--min-alpha', type=float, default=0.25)
p1.add_argument('--key-color', default='', help='手动指定底色 B,G,R；默认取图边缘中值')
p1.add_argument('--no-despill', action='store_true')
p1.set_defaults(func=cmd_key)

p2 = sub.add_parser('paste', help='按人读出的目标框把字贴回无字底图')
p2.add_argument('--text', required=True, help='key 出来的 RGBA png')
p2.add_argument('--base', required=True, help='无字底图')
p2.add_argument('--out', required=True)
p2.add_argument('--box', required=True, type=parse_int_box,
                help='新标题要占的区域 x0,y0,x1,y1（底图像素坐标，人看图读出）')
p2.add_argument('--fit', choices=['width', 'both'], default='width', help='等比(默认)或强制fit')
p2.add_argument('--halign', choices=['left', 'center'], default='left')
p2.add_argument('--valign', choices=['center', 'top', 'bottom'], default='center')
p2.add_argument('--scale-ratio', type=float, default=1.0, help='投影等参数的缩放基准')
p2.add_argument('--glow-op', type=float, default=0.0, help='柔光不透明度；0=不要柔光')
p2.add_argument('--glow-px', type=float, default=9.0, help='柔光模糊半径（底图像素）')
p2.add_argument('--glow-color', default='200,235,250', help='柔光颜色 B,G,R（暖白）')
p2.add_argument('--shadow-color', default='10,12,58', help='投影颜色 B,G,R')
p2.add_argument('--shadow-dx', type=float, default=3.0)
p2.add_argument('--shadow-dy', type=float, default=4.0)
p2.add_argument('--shadow-blur', type=float, default=2.4)
p2.add_argument('--shadow-op', type=float, default=0.0, help='投影不透明度；0=不要投影（默认无）')
p2.set_defaults(func=cmd_paste)

p3 = sub.add_parser('sheet', help='拼中文标签总表')
p3.add_argument('--dir', required=True)
p3.add_argument('--out', required=True)
p3.add_argument('--labels', default='')
p3.add_argument('--cols', type=int, default=4)
p3.add_argument('--tile-w', type=int, default=300)
p3.add_argument('--tile-h', type=int, default=450)
p3.add_argument('--fontsize', type=int, default=22)
p3.add_argument('--font', default='C:/Windows/Fonts/msyh.ttc')
p3.set_defaults(func=cmd_sheet)

if __name__ == '__main__':
    args = parser.parse_args()
    args.func(args)
