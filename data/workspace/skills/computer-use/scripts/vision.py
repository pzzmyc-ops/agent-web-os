#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Vision fallback: render the window itself (PrintWindow) + OCR.

Renders the target window directly, so it works even when the window is covered
by other windows (a plain screen crop would capture whatever is on top).

Usage:
    python vision.py tree --hwnd 12345 [--query 发送] [--image out.png]
    python vision.py shot --hwnd 12345 --image D:/path/out.png

Element table fields: index, text, conf, px (window-local px), rect, screen=((x,y)).
"""
import argparse
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32


def window_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def render_window(hwnd):
    """Render window pixels via PrintWindow(PW_RENDERFULLCONTENT); None on failure."""
    x, y, w, h = window_rect(hwnd)
    if w <= 0 or h <= 0:
        return None
    if user32.IsIconic(wintypes.HWND(hwnd)):
        user32.ShowWindow(wintypes.HWND(hwnd), 4)
    try:
        import win32gui
        import win32ui
        from PIL import Image

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hwnd_dc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        # Render at native (physical) size: this process is DPI-unaware, so the
        # window rect is in logical px. A logical-size bitmap would only cover
        # the top-left slice of the window.
        try:
            dpi = int(ctypes.windll.user32.GetDpiForWindow(wintypes.HWND(hwnd))) or 96
        except Exception:
            dpi = 96
        scale = dpi / 96.0
        bw, bh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        bmp.CreateCompatibleBitmap(src, bw, bh)
        mem.SelectObject(bmp)
        ok = user32.PrintWindow(wintypes.HWND(hwnd), mem.GetSafeHdc(), 2)
        img = None
        if ok:
            info = bmp.GetInfo()
            bits = bmp.GetBitmapBits(True)
            img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                                   bits, "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        return img
    except Exception:
        return None


def _is_blank(img, max_colors=64, min_colors=8):
    """Chromium/Electron windows often return a solid-colour PrintWindow bitmap."""
    if img is None:
        return True
    colors = img.getcolors(max_colors)
    return colors is not None and len(colors) < min_colors


def _bring_front(hwnd):
    """Raise the window past Windows' foreground lock (same trick as windows.py)."""
    kernel32 = ctypes.windll.kernel32
    h = wintypes.HWND(hwnd)
    if not user32.IsWindowVisible(h):
        user32.ShowWindow(h, 5)
        time.sleep(0.2)
    if user32.IsIconic(h):
        user32.ShowWindow(h, 9)
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    tgt_tid = user32.GetWindowThreadProcessId(h, None)
    cur_tid = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(cur_tid, fg_tid, True)
    user32.AttachThreadInput(cur_tid, tgt_tid, True)
    user32.BringWindowToTop(h)
    user32.SetForegroundWindow(h)
    user32.AttachThreadInput(cur_tid, fg_tid, False)
    user32.AttachThreadInput(cur_tid, tgt_tid, False)
    time.sleep(0.35)


def grab_window(hwnd, out_path=None):
    """Return (image, (x,y,w,h)) with window pixels, PrintWindow first."""
    # Restore first: a minimized window reports x/y = -32000 and renders black.
    if user32.IsIconic(wintypes.HWND(hwnd)):
        user32.ShowWindow(wintypes.HWND(hwnd), 4)
        time.sleep(0.25)
    x, y, w, h = window_rect(hwnd)
    img = render_window(hwnd)
    source = "printwindow"
    if _is_blank(img):
        img = None
    if img is None:
        # PrintWindow gave us a solid colour (Chromium/Electron). The screen is
        # the only remaining source, so the window must actually be on top.
        _bring_front(hwnd)
        from PIL import ImageGrab
        shot = ImageGrab.grab().convert("RGB")
        scale = shot.width / float(user32.GetSystemMetrics(0))
        img = shot.crop((int(x * scale), int(y * scale),
                         int((x + w) * scale), int((y + h) * scale)))
        source = "screencrop"
    if out_path:
        try:
            import annotate as _ann
            _ann.annotate(img, (x, y, w, h), extra=f"src={source}")
        except Exception:
            pass
        img.save(out_path)
    return img, (x, y, w, h), source


def ocr_elements(img, query="", max_width=1600):
    from rapidocr_onnxruntime import RapidOCR

    orig_w, orig_h = img.size
    k = 1.0
    if orig_w > max_width:
        k = max_width / float(orig_w)
        img = img.resize((max(1, int(orig_w * k)), max(1, int(orig_h * k))))
    rows, _ = RapidOCR()(img)
    out = []
    for box, text, score in rows or []:
        text = (text or "").strip()
        if not text:
            continue
        if query and query.lower() not in text.lower():
            continue
        xs = [p[0] / k for p in box]
        ys = [p[1] / k for p in box]
        left, top = int(min(xs)), int(min(ys))
        w, h = int(max(xs) - min(xs)), int(max(ys) - min(ys))
        out.append({
            "index": len(out) + 1,
            "text": text,
            "conf": round(float(score), 3),
            "px": [left + w // 2, top + h // 2],
            "rect": [left, top, w, h],
            "img_size": [orig_w, orig_h],
        })
    return out


def to_screen(el, win_rect, img_size):
    """图上像素 -> 逻辑屏幕坐标（唯一坐标系）。

    注意：这里只是给"读内容"用的参考位置。OCR 的框会偏几十像素，
    **不要拿它当点击坐标**。要点击请用 screen.py --grid 的网格读数。
    """
    wx, wy, ww, wh = win_rect
    iw, ih = img_size
    kx = ww / float(iw) if iw else 1.0
    ky = wh / float(ih) if ih else 1.0
    return int(round(wx + el["px"][0] * kx)), int(round(wy + el["px"][1] * ky))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["tree", "shot"])
    ap.add_argument("--hwnd", type=int, required=True)
    ap.add_argument("--query", default="")
    ap.add_argument("--image", default="")
    args = ap.parse_args()

    img, rect, source = grab_window(args.hwnd, args.image or None)
    print(f"[vision] hwnd={args.hwnd} source={source} window={rect} img={img.size}")
    if args.op == "shot":
        return 0
    els = ocr_elements(img, args.query)
    if not els:
        print("[vision] no text found")
        return 3
    for e in els:
        sx, sy = to_screen(e, rect, img.size)
        print(f"  #{e['index']:<3} {e['text'][:40]!r:<44} conf={e['conf']:<5} screen=({sx},{sy}) local={e['px']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
