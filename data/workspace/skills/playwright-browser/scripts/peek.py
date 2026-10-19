#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""连接常驻浏览器，看某个标签页当前状态：标签列表、title、url、可见文字直接打到 stdout。
截图另存文件（图片无法走 stdout），路径打印出来供 view_image。DOM 太大不打，需要时加 --dom 存文件。

python peek.py [--port 9222] [--tab N] [--shot 路径] [--dom 路径]
"""
import argparse
from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--tab", type=int, default=-1)
    ap.add_argument("--shot", default="D:/mafagent/data/workspace/temp/_peek.png")
    ap.add_argument("--dom", default=None)
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{args.port}")
        pages = [pg for ctx in browser.contexts for pg in ctx.pages]
        for i, pg in enumerate(pages):
            print(f"[{i}] {pg.title()} | {pg.url}")

        idx = args.tab if args.tab >= 0 else len(pages) - 1
        page = pages[idx]
        page.bring_to_front()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass

        print(f"\n=== 当前标签 [{idx}] {page.title()} | {page.url} ===")
        print(page.evaluate("document.body ? document.body.innerText : ''").strip()[:6000])

        page.screenshot(path=args.shot, animations="disabled", timeout=20000)
        print(f"\n[截图] {args.shot}")
        if args.dom:
            with open(args.dom, "w", encoding="utf-8") as f:
                f.write(page.content())
            print(f"[DOM] {args.dom}")
        browser.close()


if __name__ == "__main__":
    main()
