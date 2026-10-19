#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Windows 窗口工具：列窗口 / 找 / 置前 / 最大化 / 开 / 关。坐标是逻辑坐标(与 mouse.py 一致)。

用法：
    python windows.py                        # 列出可见窗口(去重、过滤系统窗口、标 最小化/隐藏/无响应)
    python windows.py --hidden                # 连隐藏窗口(最小化到托盘的)也列出
    python windows.py --grep QQ               # 只看标题含 QQ 的
    python windows.py --grep QQ --focus        # 置前(自动恢复 最小化/隐藏)
    python windows.py --grep 异能 --max        # 最大化(看内容前先做大)
    python windows.py --grep QQ --hidden --focus  # 连托盘里的隐藏窗口也找出来置前
    python windows.py --launch notepad        # 打开程序/文件/URL (ShellExecuteW)
    python windows.py --grep 记事本 --close    # 关闭匹配窗口 (WM_CLOSE 优雅关闭)
"""
import argparse
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
SW_RESTORE = 9
SW_MAXIMIZE = 3
SW_SHOW = 5
WM_CLOSE = 0x0010

# 系统/内部窗口，列表里没意义，过滤掉
SKIP_EXACT = {
    "Program Manager", "Microsoft Text Input Application",
    "Windows Shell Experience 主机", "Windows Input Experience",
    "dummyLayeredWnd", "Default IME", "MSCTFIME UI", "Application Frame Host",
}


def list_windows(include_hidden=False):
    out = []
    P = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        vis = bool(user32.IsWindowVisible(h))
        if not vis and not include_hidden:
            return True
        n = user32.GetWindowTextLengthW(h)
        if n == 0:
            return True
        b = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, b, n + 1)
        title = b.value.strip()
        if title in SKIP_EXACT:
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        if r.right <= r.left or r.bottom <= r.top:
            return True
        out.append([h, title, r.left, r.top, r.right, r.bottom,
                    bool(user32.IsIconic(h)), vis])
        return True

    user32.EnumWindows(P(cb), 0)

    seen, uniq = set(), []
    for w in out:
        key = (w[1], w[2], w[3], w[4], w[5], w[6], w[7])
        if key not in seen:
            seen.add(key)
            uniq.append(w)
    # 可见的在前，其次非最小化，再按面积降序
    uniq.sort(key=lambda w: (not w[7], w[6], -((w[4] - w[2]) * (w[5] - w[3]))))
    return uniq


def show_window(hwnd):
    """把窗口弄到前台可见：隐藏→SHOW，最小化→RESTORE，再绕过前台锁置前。"""
    if not user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, SW_SHOW)
        time.sleep(0.2)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
    cur_tid = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(cur_tid, fg_tid, True)
    user32.AttachThreadInput(cur_tid, tgt_tid, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(cur_tid, fg_tid, False)
    user32.AttachThreadInput(cur_tid, tgt_tid, False)


def pick(wins):
    """选一个目标窗口：可见非最小化优先，其次可见，再次第一个。"""
    vis_norm = [w for w in wins if w[7] and not w[6]]
    if vis_norm:
        return vis_norm[0]
    vis = [w for w in wins if w[7]]
    return vis[0] if vis else wins[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grep", default=None)
    ap.add_argument("--hidden", action="store_true", help="连隐藏窗口(托盘里的)也纳入")
    ap.add_argument("--focus", action="store_true", help="置前(自动恢复 最小化/隐藏)")
    ap.add_argument("--max", action="store_true", help="最大化(看内容前先做大,一屏看更多)")
    ap.add_argument("--launch", default=None, help="用 ShellExecuteW 打开程序/文件/URL")
    ap.add_argument("--close", action="store_true", help="配合 --grep 关闭匹配的窗口(WM_CLOSE)")
    args = ap.parse_args()

    if args.launch:
        r = ctypes.windll.shell32.ShellExecuteW(None, "open", args.launch, None, None, 1)
        print(f"[launch] {args.launch} -> 返回值 {r} (>32 表示成功)")
        return

    wins = list_windows(include_hidden=args.hidden)
    if args.grep:
        wins = [w for w in wins if args.grep.lower() in w[1].lower()]

    if args.close:
        for w in wins:
            user32.PostMessageW(w[0], WM_CLOSE, 0, 0)
            print(f"[关闭] {w[1]}")
        return

    for hwnd, title, l, t, r, b, iconic, vis in wins:
        hung = " [无响应!]" if user32.IsHungAppWindow(hwnd) else ""
        if not vis:
            print(f"[隐藏]{hung}   {title}")
        elif iconic:
            print(f"[最小化]{hung}  {title}")
        else:
            cx, cy = (l + r) // 2, (t + b) // 2
            print(f"[{l},{t}-{r},{b}] 中心({cx},{cy})  {title}{hung}")

    if args.focus and wins:
        w = pick(wins)
        show_window(w[0])
        time.sleep(0.5)
        rr = wintypes.RECT()
        user32.GetWindowRect(w[0], ctypes.byref(rr))
        print(f"[已置前] {w[1]} rect=({rr.left},{rr.top},{rr.right},{rr.bottom})")

    if args.max and wins:
        w = pick(wins)
        if not user32.IsWindowVisible(w[0]):
            user32.ShowWindow(w[0], SW_SHOW)
            time.sleep(0.2)
        if user32.IsIconic(w[0]):
            user32.ShowWindow(w[0], SW_RESTORE)
        user32.ShowWindow(w[0], SW_MAXIMIZE)
        show_window(w[0])
        time.sleep(0.5)
        rr = wintypes.RECT()
        user32.GetWindowRect(w[0], ctypes.byref(rr))
        print(f"[已最大化] {w[1]} rect=({rr.left},{rr.top},{rr.right},{rr.bottom})")

    print(f"共 {len(wins)} 个窗口")


if __name__ == "__main__":
    main()
