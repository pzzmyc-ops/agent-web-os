#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Window foreground + coordinate-space helper (single source of truth).

对外只有一套坐标：**逻辑坐标**（GetSystemMetrics，例如 1707x1067），
和 screen.py --grid 画在截图上的网格标签完全一致。

SetCursorPos 到底吃逻辑还是物理，本机实测会因进程 DPI 感知而不同，
所以这里只做一次实测校准并把结果缓存，之后 mouse.py 统一按缓存换算。
调用方永远只传逻辑坐标，不需要关心这件事。

    import winfocus
    ok, how = winfocus.ensure_front(hwnd)        # 置前并验证
    winfocus.point(hwnd, x, y)                   # 逻辑坐标 -> 真实落点并验证命中
"""
from __future__ import annotations

import ctypes
import json
import os
import tempfile
import time
from ctypes import wintypes

u = ctypes.windll.user32
k32 = ctypes.windll.kernel32
gdi = ctypes.windll.gdi32

CACHE = os.path.join(tempfile.gettempdir(), "computer-use-coord.json")


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def logical_screen():
    return u.GetSystemMetrics(0), u.GetSystemMetrics(1)


def physical_screen():
    hdc = u.GetDC(0)
    try:
        return gdi.GetDeviceCaps(hdc, 118), gdi.GetDeviceCaps(hdc, 117)
    finally:
        u.ReleaseDC(0, hdc)


def scale():
    lw, _ = logical_screen()
    pw, _ = physical_screen()
    return (pw / float(lw)) if lw else 1.0


def foreground():
    return int(u.GetForegroundWindow())


def window_title(hwnd):
    n = u.GetWindowTextLengthW(wintypes.HWND(hwnd))
    b = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(wintypes.HWND(hwnd), b, n + 1)
    return b.value


def window_at(x, y):
    """Root window sitting at these *SetCursorPos* coordinates."""
    h = u.WindowFromPoint(POINT(int(x), int(y)))
    root = u.GetAncestor(wintypes.HWND(h), 2)
    return int(root or h)


def window_at_cursor():
    p = POINT()
    u.GetCursorPos(ctypes.byref(p))
    return window_at(p.x, p.y)


def window_rect_logical(hwnd):
    r = wintypes.RECT()
    u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def ensure_front(hwnd, timeout=1.5):
    """Raise the window and PROVE it is the foreground window.

    Returns (ok, how). Never returns True without checking GetForegroundWindow.
    """
    hwnd = int(hwnd)
    if foreground() == hwnd:
        return True, "already-front"
    u.ShowWindow(wintypes.HWND(hwnd), 9)  # SW_RESTORE
    time.sleep(0.15)
    fg = foreground()
    fg_tid = u.GetWindowThreadProcessId(wintypes.HWND(fg), None)
    tgt_tid = u.GetWindowThreadProcessId(wintypes.HWND(hwnd), None)
    cur_tid = k32.GetCurrentThreadId()
    u.AttachThreadInput(cur_tid, fg_tid, True)
    u.AttachThreadInput(cur_tid, tgt_tid, True)
    u.BringWindowToTop(wintypes.HWND(hwnd))
    u.SetForegroundWindow(wintypes.HWND(hwnd))
    u.AttachThreadInput(cur_tid, fg_tid, False)
    u.AttachThreadInput(cur_tid, tgt_tid, False)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if foreground() == hwnd:
            time.sleep(0.25)  # let the renderer settle
            return True, "attach-input"
        time.sleep(0.05)
    return False, "failed"


def load_factor():
    try:
        with open(CACHE, "r", encoding="utf-8") as f:
            return float(json.load(f)["factor"])
    except Exception:
        return None


def save_factor(v):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"factor": v}, f)
    except Exception:
        pass


def calibrate(hwnd, force=False):
    """决定 逻辑坐标 -> SetCursorPos 的乘数（1.0 或 scale），并缓存。

    判据：把光标放到窗口逻辑中心。如果落点仍在目标窗口内，说明
    SetCursorPos 接受的就是逻辑坐标；否则乘 scale 再试，命中即为物理。
    """
    if not force:
        cached = load_factor()
        if cached is not None:
            return cached
    ok, _ = ensure_front(hwnd)
    if not ok:
        return None
    x, y, w, h = window_rect_logical(hwnd)
    if w <= 0 or h <= 0:
        return None
    cx, cy = x + w // 2, y + h // 2
    s = scale()

    u.SetCursorPos(cx, cy)
    time.sleep(0.18)
    if window_at_cursor() == int(hwnd):
        save_factor(1.0)
        return 1.0

    u.SetCursorPos(int(round(cx * s)), int(round(cy * s)))
    time.sleep(0.18)
    if window_at_cursor() == int(hwnd):
        save_factor(s)
        return s
    return None


def factor_for(hwnd):
    f = load_factor()
    if f is None:
        f = calibrate(hwnd)
    return f if f is not None else 1.0


def point(hwnd, lx, ly, allow_move_only=False):
    """Move the cursor to a LOGICAL coordinate and verify it lands on hwnd.

    Returns (ok, detail). ok is False when the cursor did not end up on the
    target window (i.e. something else covers that point) — caller must not click.
    """
    hwnd = int(hwnd)
    f = factor_for(hwnd)
    px, py = int(round(lx * f)), int(round(ly * f))
    u.SetCursorPos(px, py)
    time.sleep(0.12)
    hit = window_at_cursor()
    if hit == hwnd:
        return True, {"logical": [lx, ly], "cursor": [px, py], "factor": f, "hit": hwnd}
    return False, {"logical": [lx, ly], "cursor": [px, py], "factor": f,
                   "hit": hit, "hit_title": window_title(hit), "expected": hwnd}
