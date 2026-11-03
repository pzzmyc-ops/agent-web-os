#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""系统级鼠标模拟（ctypes 调 Windows user32.dll，零第三方依赖）。

发送的是操作系统级输入事件，与真人操作在系统层面无法区分，最适合点验证码等「真实输入」场景。

用法:
    python scripts/mouse.py screen          # 屏幕逻辑分辨率
    python scripts/mouse.py pos             # 当前鼠标位置
    python scripts/mouse.py move X Y        # 平滑移动鼠标到屏幕坐标
    python scripts/mouse.py click [X Y]     # 左键点击（可带坐标，先移动再点）
    python scripts/mouse.py dclick [X Y]    # 双击
    python scripts/mouse.py rclick [X Y]    # 右键
    python scripts/mouse.py scroll N        # 滚轮（正=向上，负=向下）
"""
import ctypes
import os
import sys
import time
import random

user32 = ctypes.windll.user32

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_cursor():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def move_to(x, y):
    user32.SetCursorPos(int(x), int(y))


def smooth_move(x, y, steps=None):
    """分步移动到逻辑坐标并验证位置；不允许静默报告成功。"""
    sx, sy = get_cursor()
    dx, dy = int(x) - sx, int(y) - sy
    dist = max(abs(dx), abs(dy))
    if steps is None:
        steps = max(6, min(20, dist // 30))
    for i in range(1, steps + 1):
        t = i / steps
        move_to(round(sx + dx * t), round(sy + dy * t))
        time.sleep(random.uniform(0.005, 0.02))
    target = (int(x), int(y))
    # 系统可能在最后一步仍处理鼠标消息；短暂等待再验证，最多重新定位两次。
    for _ in range(3):
        time.sleep(0.06)
        actual = get_cursor()
        if max(abs(actual[0] - target[0]), abs(actual[1] - target[1])) <= 2:
            return
        move_to(*target)
    actual = get_cursor()
    if max(abs(actual[0] - target[0]), abs(actual[1] - target[1])) > 2:
        raise RuntimeError(f"mouse move failed: expected {target}, actual {actual}")


def click(x=None, y=None, button="left", count=1):
    if x is not None and y is not None:
        smooth_move(x, y)
    if button == "left":
        dn, up = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
    elif button == "right":
        dn, up = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    else:
        dn, up = MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
    for _ in range(count):
        user32.mouse_event(dn, 0, 0, 0, 0)
        time.sleep(random.uniform(0.02, 0.06))
        user32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(random.uniform(0.03, 0.08))


def scroll(n):
    """正=向上，负=向下（一格约 120 单位）。"""
    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(n) * 120, 0)
    time.sleep(0.05)


def _pop_hwnd(argv):
    """取走 --hwnd N，返回 (hwnd|None, 其余参数)。"""
    if "--hwnd" not in argv:
        return None, argv
    i = argv.index("--hwnd")
    try:
        hwnd = int(argv[i + 1])
    except (IndexError, ValueError):
        raise SystemExit("--hwnd 需要一个整数窗口句柄")
    return hwnd, argv[:i] + argv[i + 2:]


def _prepare(hwnd, lx, ly):
    """点击前的统一准备：置前 + 逻辑坐标换算 + 命中校验。失败直接退出不点。"""
    if hwnd is None:
        return
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import winfocus

    ok, how = winfocus.ensure_front(hwnd)
    if not ok:
        raise SystemExit(f"[中止] 无法把窗口 {hwnd} 置到前台（GetForegroundWindow 未变成它），不点击")
    ok, info = winfocus.point(hwnd, lx, ly)
    if not ok:
        raise SystemExit(
            f"[中止] 逻辑坐标 ({lx},{ly}) 换算后 ({info['cursor']}) 命中的是 "
            f"窗口 {info['hit']} {info.get('hit_title','')!r}，不是目标 {hwnd} —— 该点被别的窗口挡住或坐标被误解，不点击"
        )
    print(f"[前置] {how}  factor={info['factor']}  logical=({lx},{ly}) -> cursor={info['cursor']}")


def _record_click(hwnd, lx, ly):
    """记录落点，供后续截图把上一次点击位置画出来。"""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import annotate
        title = ""
        if hwnd:
            import winfocus
            title = winfocus.window_title(hwnd)
        annotate.save_last_click(lx, ly, hwnd or 0, title)
    except Exception:
        pass


def main():
    hwnd, argv = _pop_hwnd(sys.argv[1:])
    if not argv:
        print(__doc__)
        return
    cmd = argv[0].lower()
    if cmd == "screen":
        print(f"[屏幕] {user32.GetSystemMetrics(0)} x {user32.GetSystemMetrics(1)} (逻辑像素)")
    elif cmd == "pos":
        x, y = get_cursor()
        print(f"[鼠标位置] ({x}, {y})")
    elif cmd == "move":
        x, y = int(argv[1]), int(argv[2])
        _prepare(hwnd, x, y)
        smooth_move(x, y)
        print(f"[移动] 到逻辑 ({x}, {y})")
    elif cmd == "click":
        x = y = None
        if len(argv) >= 3:
            x, y = int(argv[1]), int(argv[2])
        if x is not None:
            _prepare(hwnd, x, y)
        click(x, y, button="left")
        if x is not None:
            _record_click(hwnd, x, y)
        print(f"[点击] 逻辑 ({x}, {y})")
    elif cmd == "dclick":
        x = y = None
        if len(argv) >= 3:
            x, y = int(argv[1]), int(argv[2])
        if x is not None:
            _prepare(hwnd, x, y)
        click(x, y, button="left", count=2)
        if x is not None:
            _record_click(hwnd, x, y)
        print(f"[双击] 逻辑 ({x}, {y})")
    elif cmd == "rclick":
        x = y = None
        if len(argv) >= 3:
            x, y = int(argv[1]), int(argv[2])
        if x is not None:
            _prepare(hwnd, x, y)
        click(x, y, button="right")
        if x is not None:
            _record_click(hwnd, x, y)
        print(f"[右键] 逻辑 ({x}, {y})")
    elif cmd == "scroll":
        scroll(int(argv[1]))
        print(f"[滚轮] {argv[1]}")
    else:
        print("未知命令:", cmd)
        print(__doc__)


if __name__ == "__main__":
    main()
