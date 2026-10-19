#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""列出当前所有常驻浏览器实例（带 remote-debugging-port 的 chrome）。
每行：端口 / profile / 主进程PID。开始任何浏览器操作前先跑这个，别重复开。

python list_browsers.py
"""
import subprocess


def main():
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -match 'remote-debugging-port' } | "
        "ForEach-Object { "
        "  $port = if ($_.CommandLine -match 'remote-debugging-port=(\\d+)') { $matches[1] } else { '?' }; "
        "  $prof = if ($_.CommandLine -match 'user-data-dir=\\\"?([^\\\" ]+)') { $matches[1] } else { '?' }; "
        "  '{0}|{1}|{2}' -f $port, $_.ProcessId, $prof "
        "}"
    )
    out = subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True, text=True,
    ).stdout

    rows = []
    for line in out.splitlines():
        line = line.strip()
        if "|" in line:
            port, pid, prof = line.split("|", 2)
            rows.append((port, pid, prof))

    seen = {}
    for port, pid, prof in rows:
        if port not in seen:
            seen[port] = (pid, prof)

    if not seen:
        print("当前没有常驻浏览器实例。")
        return
    print(f"当前 {len(seen)} 个常驻浏览器实例：")
    for port in sorted(seen):
        pid, prof = seen[port]
        print(f"  端口 {port}  PID {pid}  profile {prof}")


if __name__ == "__main__":
    main()
