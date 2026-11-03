#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Verify a LOGICAL coordinate before clicking (single coordinate system).

用法：
    python clickprobe.py --hwnd 12345 --x 400 --y 300 [--click]

做的事：
  1. 把目标窗口置前，并确认它真的成了前台窗口（否则直接失败）
  2. 把光标放到该逻辑坐标，读出真实落点
  3. 报告这个落点究竟属于哪个窗口
  4. --click 时才会真正点击；落点不属于目标窗口则拒绝点击

坐标系只有一套：**逻辑坐标**（与 screen.py --grid 的网格标签一致）。
"""
import argparse
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winfocus  # noqa: E402

u = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=int, required=True)
    ap.add_argument("--x", type=int, required=True)
    ap.add_argument("--y", type=int, required=True)
    ap.add_argument("--click", action="store_true")
    ap.add_argument("--recalibrate", action="store_true", help="重新实测坐标空间换算")
    a = ap.parse_args()

    lw, lh = winfocus.logical_screen()
    pw, ph = winfocus.physical_screen()
    print(f"logical={lw}x{lh} physical={pw}x{ph} scale={winfocus.scale():.4f}")

    ok, how = winfocus.ensure_front(a.hwnd)
    print(f"ensure_front: ok={ok} via={how} foreground={winfocus.window_title(winfocus.foreground())!r}")
    if not ok:
        return 4

    if a.recalibrate:
        f = winfocus.calibrate(a.hwnd, force=True)
        print(f"recalibrated factor = {f}")
        if f is None:
            return 4

    ok, info = winfocus.point(a.hwnd, a.x, a.y)
    print(f"logical ({a.x},{a.y}) -> cursor {info['cursor']} (factor={info['factor']})")
    if ok:
        print(f"HIT  window {a.hwnd} {winfocus.window_title(a.hwnd)!r}")
    else:
        print(f"MISS  landed on {info['hit']} {info.get('hit_title','')!r}, expected {info['expected']}")

    if a.click:
        if not ok:
            print("refused: 落点不属于目标窗口，未点击")
            return 5
        u.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.06)
        u.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(1.0)
        print(f"clicked at cursor {info['cursor']}")
    return 0 if ok else 5


if __name__ == "__main__":
    sys.exit(main())
