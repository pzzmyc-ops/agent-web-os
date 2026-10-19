#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""启动常驻浏览器：有头 Chrome + 指定 profile + CDP 调试端口，长驻运行，供随时连接。

用法:
    python scripts/start_browser.py                                  # 默认：端口9222 + 固定身份 profile + Google
    python scripts/start_browser.py --port 9223 --profile <目录> --url <网址>   # 全新/其它实例
"""
import argparse
import socket
import subprocess
import time

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEFAULT_PROFILE = r"D:\mafagent\data\workspace\playwright-profile"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def port_open(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--url", default="https://www.google.com")
    args = ap.parse_args()

    if port_open(args.port):
        print(f"[跳过] 端口 {args.port} 已有实例在跑。", flush=True)
        return

    cmd = [
        CHROME_PATH,
        f"--remote-debugging-port={args.port}",
        f"--user-data-dir={args.profile}",
        "--start-maximized",
        args.url,
    ]
    print(f"[启动] 端口 {args.port}，profile {args.profile}……", flush=True)
    subprocess.Popen(
        cmd,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    for _ in range(30):
        if port_open(args.port):
            print(f"[就绪] 常驻浏览器已启动（端口 {args.port}），窗口保持打开。", flush=True)
            return
        time.sleep(1)
    print("[失败] 启动超时。", flush=True)


if __name__ == "__main__":
    main()
