#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""连接常驻浏览器做 Google 搜索：打开首页 → 输入关键词 → 回车（DOM 操作，不拼直链）。
结果标题直接打到 stdout，截图另存供 view_image。不落中间 json，内容随用随取。

python search.py --q "关键词" [--port 9222] [--shot 路径]
"""
import argparse
import time
from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", required=True)
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--shot", default="D:/mafagent/data/workspace/temp/_search.png")
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{args.port}")
        page = browser.contexts[0].new_page()
        page.goto("https://www.google.com")
        page.wait_for_load_state("domcontentloaded")
        box = page.locator('textarea[name="q"], input[name="q"]').first
        box.wait_for(state="visible", timeout=15000)
        box.fill(args.q)
        box.press("Enter")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(2)

        url = page.url.lower()
        blocked = "/sorry" in url or "captcha" in url
        print(f"title: {page.title()}")
        print(f"url: {page.url}")
        print("status: BLOCKED(触发验证，停下让用户处理)" if blocked else "status: OK")
        if not blocked:
            for i, t in enumerate(page.locator("h3").all_inner_texts()[:10], 1):
                print(f"{i}. {t}")

        page.screenshot(path=args.shot, animations="disabled", timeout=20000)
        print(f"[截图] {args.shot}")
        browser.close()


if __name__ == "__main__":
    main()
