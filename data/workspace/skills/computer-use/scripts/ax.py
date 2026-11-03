#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AX-first desktop control (Windows UI Automation).

Keeps one long-lived PowerShell UIA server (see uia_server.ps1) and talks to it
over a localhost TCP socket, so each call costs milliseconds instead of a new
process + screenshot round trip.

Usage:
    python ax.py tree  --hwnd 12345 [--max 200] [--query 保存]
    python ax.py invoke --hwnd 12345 --index 12
    python ax.py setvalue --hwnd 12345 --index 5 --text "hello"
    python ax.py select --hwnd 12345 --index 3
    python ax.py toggle --hwnd 12345 --index 8
    python ax.py getvalue --hwnd 12345 --index 5
    python ax.py stop

Exit codes: 0 = AX handled the request, 3 = AX unavailable (fall back to vision).
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "uia_server.ps1")
INFO = os.path.join(tempfile.gettempdir(), "computer-use-ax.json")


def find_windows(keyword=""):
    """Enumerate top-level windows; return [{hwnd,pid,title,rect}] matching keyword."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    out = []

    def rect_of(h):
        r = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if not user32.IsWindowVisible(h):
            return True
        n = user32.GetWindowTextLengthW(h)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, buf, n + 1)
        title = buf.value
        if keyword and keyword.lower() not in title.lower():
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        out.append({
            "hwnd": int(h), "pid": int(pid.value), "title": title,
            "rect": rect_of(h), "minimized": bool(user32.IsIconic(h)),
        })
        return True

    user32.EnumWindows(cb, 0)
    return out



def _read_info():
    try:
        with open(INFO, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _alive(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4) as s:
            s.sendall(b'{"op":"ping"}\n')
            data = s.recv(4096)
            return b'"ok":true' in data
    except Exception:
        return False


def ensure_server(timeout=12.0):
    info = _read_info()
    if info and _alive(info.get("port")):
        return info["port"]
    try:
        os.remove(INFO)
    except OSError:
        pass
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SERVER],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = _read_info()
        if info and _alive(info.get("port")):
            return info["port"]
        time.sleep(0.1)
    return None


def request(payload, timeout=25.0):
    port = ensure_server()
    if not port:
        return {"ok": False, "code": "ax_unavailable"}
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    if not buf.strip():
        return {"ok": False, "code": "ax_no_reply"}
    return json.loads(buf.decode("utf-8", errors="replace"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("op")
    ap.add_argument("--hwnd", type=int)
    ap.add_argument("--index", type=int)
    ap.add_argument("--text", default="")
    ap.add_argument("--max", type=int, default=400)
    ap.add_argument("--query", default="")
    args = ap.parse_args()

    if args.op == "stop":
        info = _read_info()
        if info:
            try:
                os.kill(int(info["pid"]), 9)
            except Exception:
                pass
        try:
            os.remove(INFO)
        except OSError:
            pass
        print("[ax] stopped")
        return 0

    if args.op == "find":
        wins = find_windows(args.query or "")
        if not wins:
            print("[ax] no window matched")
            return 3
        for w in wins:
            x, y, wd, ht = w["rect"]
            flag = " [minimized]" if w["minimized"] else ""
            print(f"  hwnd={w['hwnd']:<10} pid={w['pid']:<7} @({x},{y},{wd},{ht}){flag}  {w['title'][:60]}")
        return 0

    payload = {"op": args.op}
    if args.hwnd:
        payload["hwnd"] = args.hwnd
    if args.index is not None:
        payload["index"] = args.index
    if args.text:
        payload["text"] = args.text
    if args.op == "tree":
        payload["max"] = args.max
        if args.query:
            payload["query"] = args.query

    res = request(payload)

    if args.op == "tree" and res.get("ok"):
        print(f"[ax] hwnd={res['hwnd']} title={res.get('title')} total={res.get('total')} took={res.get('took_ms')}ms")
        for e in res.get("elements") or []:
            pats = e.get("patterns") or "-"
            print(f"  #{e['index']:<3} {e['role']:<16} {e['name'][:52]!r:<54} "
                  f"@({e['x']},{e['y']},{e['w']},{e['h']}) [{pats}]")
        return 0

    print(json.dumps(res, ensure_ascii=False))
    if not res.get("ok"):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
