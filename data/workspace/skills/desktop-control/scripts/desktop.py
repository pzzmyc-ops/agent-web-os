"""驱动文件管理器桌面的命令行客户端。

一次调用 = 连一次 /api/desktop/control、发一条请求、打印回执、退出。桌面能做什么由桌面
自报,这个脚本不认识任何具体操作,也不校验参数 —— 写错了由桌面报错,原文照抄给你。

    python desktop.py list --base http://127.0.0.1
    python desktop.py exec --base http://127.0.0.1 --desktop d123 --op os.operations
    python desktop.py exec --base http://127.0.0.1 --desktop d123 --op app.invoke \
        --params '{"winId":"onlyoffice-1","capability":"doc.reload"}'

回执是 JSON:成功 {"ok":true,...},失败 {"ok":false,"error":"..."} 且退出码为 1。
"""
from __future__ import annotations

import argparse
import json
import sys

from websockets.sync.client import connect


def ws_url(base: str) -> str:
    base = base.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + "/api/desktop/control"
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):] + "/api/desktop/control"
    raise SystemExit(f"--base 要是 http:// 或 https:// 开头的地址,收到的是: {base}")


def request(base: str, payload: dict, timeout: float) -> dict:
    with connect(ws_url(base), open_timeout=timeout) as ws:
        ws.send(json.dumps({"type": "hello"}))
        hello = json.loads(ws.recv(timeout=timeout))
        if not hello.get("ok"):
            return hello
        ws.send(json.dumps(payload))
        return json.loads(ws.recv(timeout=timeout))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base", default=argparse.SUPPRESS, help="服务地址,如 http://127.0.0.1;放在子命令前后都行")
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS, help="等回执的秒数,默认 30")
    ap = argparse.ArgumentParser(description="驱动文件管理器桌面", parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="列出在线的桌面(屏幕)", parents=[common])
    ex = sub.add_parser("exec", help="在某块屏幕上执行一个操作", parents=[common])
    ex.add_argument("--desktop", required=True, help="desktopId,来自 list")
    ex.add_argument("--op", required=True, help="操作名,来自 os.operations")
    ex.add_argument("--params", default="{}", help="该操作的参数,JSON 字符串")
    args = ap.parse_args()

    base = getattr(args, "base", None)
    if not base:
        raise SystemExit("缺少 --base(服务地址,如 http://127.0.0.1)")
    timeout = getattr(args, "timeout", 30.0)

    if args.cmd == "list":
        payload = {"type": "list"}
    else:
        params = json.loads(args.params)
        if not isinstance(params, dict):
            raise SystemExit(f"--params 要是一个 JSON 对象,收到的是: {args.params}")
        payload = {"type": "exec", "desktopId": args.desktop, "op": args.op, "params": params}

    reply = request(base, payload, timeout)
    print(json.dumps(reply, ensure_ascii=False, indent=2))
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
