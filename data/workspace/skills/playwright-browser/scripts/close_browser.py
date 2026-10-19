#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""关闭常驻浏览器实例。

python close_browser.py --port 9223     # 只关指定端口的实例
python close_browser.py --all           # 关掉所有带 remote-debugging-port 的 chrome 实例
"""
import argparse
import subprocess


def run_ps(cmd):
    return subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-Command", cmd],
        capture_output=True, text=True,
    ).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if not args.all and args.port is None:
        print("需要 --port N 或 --all")
        return

    if args.all:
        filt = "$_.CommandLine -match 'remote-debugging-port'"
        desc = "所有常驻浏览器实例"
    else:
        filt = f"$_.CommandLine -match 'remote-debugging-port={args.port}\\b'"
        desc = f"端口 {args.port} 的实例"

    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ {filt} }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId } | "
        "Measure-Object | Select-Object -ExpandProperty Count"
    )
    n = run_ps(cmd)
    print(f"已关闭 {desc}：终止 {n} 个 chrome 进程。")


if __name__ == "__main__":
    main()
