#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给所有"给模型看的截图"统一加标注：逻辑坐标网格 + 上次点击位置。

唯一坐标系仍然是逻辑坐标（与 screen.py --grid 网格标签一致）。
mouse.py 每次点击后会把落点写进状态文件，这里的 annotate() 自动读出来画上去。

    import annotate
    annotate.annotate(img, win_rect)          # 网格 + 上次点击
    annotate.annotate(img, win_rect, click=(x, y))
"""
from __future__ import annotations

import json
import os
import tempfile

from PIL import ImageDraw, ImageFont

STATE = os.path.join(tempfile.gettempdir(), "computer-use-last-click.json")


def save_last_click(lx, ly, hwnd=0, title=""):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump({"x": int(lx), "y": int(ly), "hwnd": int(hwnd), "title": title}, f)
    except Exception:
        pass


def load_last_click():
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _font(size):
    for name in ("arialbd.ttf", "arial.ttf", "msyhbd.ttc", "msyh.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _to_img(lx, ly, win_rect, img_size):
    wx, wy, ww, wh = win_rect
    iw, ih = img_size
    kx = iw / float(ww) if ww else 1.0
    ky = ih / float(wh) if wh else 1.0
    return (lx - wx) * kx, (ly - wy) * ky


def draw_grid(img, win_rect, step=100):
    wx, wy, ww, wh = win_rect
    iw, ih = img.size
    d = ImageDraw.Draw(img)
    font = _font(max(14, int(ih / 70)))
    lx = int(wx // step) * step
    while lx <= wx + ww:
        px = (lx - wx) * (iw / float(ww) if ww else 1.0)
        if 0 <= px < iw:
            d.line([(px, 0), (px, ih)], fill=(255, 0, 0), width=1)
            d.text((px + 2, 2), str(lx), font=font, fill=(255, 220, 0),
                   stroke_width=2, stroke_fill=(0, 0, 0))
        lx += step
    ly = int(wy // step) * step
    while ly <= wy + wh:
        py = (ly - wy) * (ih / float(wh) if wh else 1.0)
        if 0 <= py < ih:
            d.line([(0, py), (iw, py)], fill=(255, 0, 0), width=1)
            d.text((2, py + 2), str(ly), font=font, fill=(255, 220, 0),
                   stroke_width=2, stroke_fill=(0, 0, 0))
        ly += step
    return img


def draw_click(img, win_rect, click=None):
    """在上次点击位置画一个醒目的红圈，圈旁标出该点的逻辑坐标。"""
    c = click if click is not None else load_last_click()
    if not c or "x" not in c:
        return img
    iw, ih = img.size
    px, py = _to_img(c["x"], c["y"], win_rect, img.size)
    if not (-60 <= px <= iw + 60 and -60 <= py <= ih + 60):
        return img
    d = ImageDraw.Draw(img)
    font = _font(max(22, int(ih / 46)))
    r = 26
    # 白色外描边保证在任何背景上都看得见，再画红圈
    d.ellipse([px - r - 3, py - r - 3, px + r + 3, py + r + 3],
              outline=(255, 255, 255), width=6)
    d.ellipse([px - r, py - r, px + r, py + r], outline=(255, 0, 0), width=5)
    d.ellipse([px - 4, py - 4, px + 4, py + 4], fill=(255, 0, 0))
    label = f"({c['x']},{c['y']})"
    d.text((px + r + 10, py - r - 4), label, font=font, fill=(255, 0, 0),
           stroke_width=3, stroke_fill=(255, 255, 255))
    return img


def draw_footer(img, win_rect, extra=""):
    iw, ih = img.size
    d = ImageDraw.Draw(img)
    font = _font(max(14, int(ih / 70)))
    wx, wy, ww, wh = win_rect
    txt = f"win_rect=({wx},{wy},{ww},{wh})  grid=logical 100px"
    if extra:
        txt = f"{extra}   {txt}"
    d.text((6, ih - 24), txt, font=font, fill=(255, 220, 0),
           stroke_width=2, stroke_fill=(0, 0, 0))
    return img


def annotate(img, win_rect, click=None, step=100, extra=""):
    draw_grid(img, win_rect, step)
    draw_click(img, win_rect, click)
    return img
