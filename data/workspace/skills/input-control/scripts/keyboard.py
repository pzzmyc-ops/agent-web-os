#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""系统级键盘模拟（ctypes 调 Windows user32，零第三方依赖）。

type 用 SendInput + KEYEVENTF_UNICODE 逐字符送 Unicode，**绕过输入法**（不会弹候选、不丢字），
中英文、符号都能打；key/hotkey 用虚拟键码。

用法:
    python keyboard.py type "文本"    # 输入文本(含中文)
    python keyboard.py key enter      # 按单个键
    python keyboard.py hotkey ctrl+c  # 组合键（+ 分隔）
"""
import ctypes
from ctypes import wintypes
import sys
import time

user32 = ctypes.windll.user32

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1

# 常用虚拟键码
VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10, "ctrl": 0x11,
    "alt": 0x12, "capslock": 0x14, "esc": 0x1B, "space": 0x20, "pageup": 0x21,
    "pagedown": 0x22, "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "delete": 0x2E, "insert": 0x2D,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35,
    "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45, "f": 0x46,
    "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A, "k": 0x4B, "l": 0x4C,
    "m": 0x4D, "n": 0x4E, "o": 0x4F, "p": 0x50, "q": 0x51, "r": 0x52,
    "s": 0x53, "t": 0x54, "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58,
    "y": 0x59, "z": 0x5A,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


# 64 位下 sizeof(INPUT) 必须是 40（union 取最大成员 MOUSEINPUT=32），否则 SendInput 失败
user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
assert ctypes.sizeof(INPUT) == 40, ctypes.sizeof(INPUT)


def _send_unicode(ch):
    code = ord(ch)
    arr = (INPUT * 2)()
    arr[0].type = INPUT_KEYBOARD
    arr[0].u.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, None)
    arr[1].type = INPUT_KEYBOARD
    arr[1].u.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
    n = user32.SendInput(2, ctypes.byref(arr), ctypes.sizeof(INPUT))
    if n != 2:
        raise OSError(f"SendInput 失败, 返回 {n}")


def press_key(name):
    vk = VK.get(name.lower())
    if vk is None:
        raise ValueError(f"未知按键: {name}")
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.03)


def type_text(text):
    """逐字符 SendInput Unicode，绕过输入法。"""
    for ch in text:
        if ch == "\n":
            press_key("enter")
        elif ch == "\t":
            press_key("tab")
        else:
            _send_unicode(ch)
        time.sleep(0.01)


def hotkey(keys):
    parts = []
    for k in keys.split("+"):
        vk = VK.get(k.lower())
        if vk is None:
            raise ValueError(f"未知组合键: {keys}")
        parts.append(vk)
    for vk in parts:
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.03)
    for vk in reversed(parts):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return
    cmd = argv[0].lower()
    if cmd == "type":
        type_text(" ".join(argv[1:]))
        print("[输入] 完成")
    elif cmd == "key":
        press_key(argv[1])
        print(f"[按键] {argv[1]}")
    elif cmd == "hotkey":
        hotkey(argv[1])
        print(f"[组合键] {argv[1]}")
    else:
        print("未知命令:", cmd)
        print(__doc__)


if __name__ == "__main__":
    main()
