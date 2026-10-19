#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""截屏工具：可截整屏或指定窗口，并叠加坐标网格（网格标注的是逻辑坐标，可直接喂 mouse.py）。

用法：
    python screen.py                         # 整屏
    python screen.py --grid                  # 整屏 + 坐标网格(每100逻辑px)
    python screen.py --window QQ --grid      # 只截标题含 QQ 的窗口 + 网格
    python screen.py --window QQ --grid --step 50
"""
import argparse
import ctypes
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont, ImageGrab
import time

user32 = ctypes.windll.user32


def logical_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def load_font(size):
    """尽量用系统 TrueType 粗体，找不到再回退默认位图字体。"""
    for name in ("arialbd.ttf", "arial.ttf", "msyhbd.ttc", "msyh.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def find_window(keyword):
    """返回标题含 keyword 的可见非最小化窗口里面积最大的那个的逻辑 rect (l,t,r,b)。"""
    hits = []
    P = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        if not user32.IsWindowVisible(h):
            return True
        if user32.IsIconic(h):   # 跳过最小化窗口（IsWindowVisible 对最小化也返回 True）
            return True
        n = user32.GetWindowTextLengthW(h)
        if n == 0:
            return True
        b = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, b, n + 1)
        if keyword.lower() in b.value.lower():
            r = wintypes.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            if r.right > r.left and r.bottom > r.top:
                hits.append((r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumWindows(P(cb), 0)
    if not hits:
        return None
    return max(hits, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))


def main():
    import random
    default_out = f"D:/mafagent/data/workspace/temp/screen_{int(time.time())}_{random.randint(100,999)}.png"
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=default_out)
    ap.add_argument("--window", default=None, help="标题关键词，只截该窗口")
    ap.add_argument("--grid", action="store_true", help="叠加坐标网格(逻辑坐标)")
    ap.add_argument("--step", type=int, default=100, help="网格步长(逻辑px)")
    ap.add_argument("--click-point", nargs=2, type=int, default=None, metavar=('X', 'Y'), help="在截图上画一个显眼的靶心，标记出你准备点击的逻辑坐标位置")
    args = ap.parse_args()

    if not args.click_point:
        try:
            import os
            last_click_file = "D:/mafagent/data/workspace/temp/.last_click"
            if os.path.exists(last_click_file):
                with open(last_click_file, "r") as f:
                    txt = f.read().strip()
                    if txt:
                        args.click_point = [int(i) for i in txt.split(',')]
        except:
            pass

    lw, lh = logical_size()
    for _ in range(3):
        try:
            im = ImageGrab.grab().convert("RGB")
            break
        except OSError:
            time.sleep(1)
    else:
        im = ImageGrab.grab().convert("RGB")
        
    scale = im.width / lw  # 物理/逻辑

    if args.window:
        rect = find_window(args.window)
        if not rect:
            print(f"[未找到窗口] {args.window}")
            return
        l, t, r, b = rect
        im = im.crop((int(l * scale), int(t * scale), int(r * scale), int(b * scale)))
        print(f"[窗口 {args.window}] 逻辑rect=({l},{t},{r},{b})")
    else:
        l, t = 0, 0

    if args.grid:
        d = ImageDraw.Draw(im)
        step_px = args.step * scale          # 网格间距(物理像素)
        fsize = max(18, min(56, int(step_px * 0.24)))   # 字号随间距自适应
        font = load_font(fsize)
        line_w = max(1, round(im.width / 1280))
        pad = max(3, fsize // 5)

        def label(x_img, y_img, text):
            bb = d.textbbox((0, 0), text, font=font)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            if x_img + tw + 2 * pad > im.width or y_img + th + 2 * pad > im.height:
                return None  
            try:
                d.text((x_img + pad - bb[0], y_img + pad - bb[1]), text, font=font, fill=(255, 220, 0), stroke_width=2, stroke_fill=(0, 0, 0))
            except TypeError:
                d.text((x_img + pad - bb[0], y_img + pad - bb[1]), text, font=font, fill=(255, 220, 0))
            return (x_img, y_img, x_img + tw + 2 * pad, y_img + th + 2 * pad)

        start_x = (l // args.step) * args.step
        x = start_x
        prev_right = -1
        while x * scale - l * scale <= im.width:
            px = int(x * scale) - int(l * scale)
            if 0 <= px < im.width:
                d.line([(px, 0), (px, im.height)], fill=(255, 0, 0), width=line_w)
                if px + line_w + 2 > prev_right:
                    box = label(px + line_w + 2, 4, str(x))
                    if box:
                        prev_right = box[2]
            x += args.step
        start_y = (t // args.step) * args.step
        y = start_y
        prev_bottom = -1
        while y * scale - t * scale <= im.height:
            py = int(y * scale) - int(t * scale)
            if 0 <= py < im.height:
                d.line([(0, py), (im.width, py)], fill=(255, 0, 0), width=line_w)
                if py + line_w + 2 > prev_bottom:
                    box = label(4, py + line_w + 2, str(y))
                    if box:
                        prev_bottom = box[3]
            y += args.step

    if args.click_point:
        cx_log, cy_log = args.click_point
        cx_img = int(cx_log * scale) - int(l * scale)
        cy_img = int(cy_log * scale) - int(t * scale)
        
        d_click = ImageDraw.Draw(im)
        r = 15  # 靶心半径
        # 绘制绿色双重空心圆
        d_click.ellipse([cx_img - r, cy_img - r, cx_img + r, cy_img + r], outline=(0, 255, 0), width=3)
        d_click.ellipse([cx_img - 3, cy_img - 3, cx_img + 3, cy_img + 3], fill=(0, 255, 0))
        # 绘制十字星
        d_click.line([(cx_img - r - 10, cy_img), (cx_img + r + 10, cy_img)], fill=(0, 255, 0), width=2)
        d_click.line([(cx_img, cy_img - r - 10), (cx_img, cy_img + r + 10)], fill=(0, 255, 0), width=2)
        
        # 绘制靶心旁边的坐标标注文字
        font_click = load_font(24)
        text_click = f"TARGET CLICK ({cx_log}, {cy_log})"
        try:
            d_click.text((cx_img + r + 8, cy_img - 12), text_click, font=font_click, fill=(0, 255, 0), stroke_width=2, stroke_fill=(0, 0, 0))
        except TypeError:
            d_click.text((cx_img + r + 8, cy_img - 12), text_click, font=font_click, fill=(0, 255, 0))
        print(f"[标记点击位置] 逻辑坐标({cx_log},{cy_log}) -> 截图物理像素({cx_img},{cy_img})")

    im.save(args.out)
    print(f"[截图] {args.out} {im.size}  (网格标签=逻辑坐标，可直接 mouse.py click X Y)")

if __name__ == "__main__":
    main()
