#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把浏览器截图(peek.png)里的物理像素坐标，换算成系统级鼠标能用的逻辑屏幕坐标。

为什么需要它：
  - CDP 截图是【物理像素】（含 devicePixelRatio 放大）
  - 系统级鼠标(mouse.py, DPI-unaware)用的是【逻辑坐标】
  - Chrome 窗口位置(screenX/screenY)也是【逻辑坐标】
  换算：逻辑屏幕坐标 = (窗口视口逻辑原点) + (截图物理像素 ÷ devicePixelRatio)
        视口逻辑原点 = (screenX + 左边框, screenY + 顶部条高)  ← 脚本自动从浏览器读，不手算

用法：
  python to_screen.py --px 989 --py 533 [--port 9222] [--tab 0]
  输出可直接喂给 input-control 的 mouse.py：mouse.py click <X> <Y>
"""
import argparse
from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--px", type=float, required=True, help="截图物理像素 x")
    ap.add_argument("--py", type=float, required=True, help="截图物理像素 y")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--tab", type=int, default=0)
    args = ap.parse_args()

    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://localhost:{args.port}")
        pages = [pg for ctx in b.contexts for pg in ctx.pages]
        page = pages[args.tab]
        page.bring_to_front()
        info = page.evaluate(
            """() => ({sx:window.screenX, sy:window.screenY,
                       ow:window.outerWidth, oh:window.outerHeight,
                       iw:window.innerWidth, ih:window.innerHeight,
                       dpr:window.devicePixelRatio})"""
        )
        dpr = info["dpr"]
        css_x = args.px / dpr
        css_y = args.py / dpr
        origin_x = info["sx"] + (info["ow"] - info["iw"]) / 2
        origin_y = info["sy"] + (info["oh"] - info["ih"])
        screen_x = round(origin_x + css_x)
        screen_y = round(origin_y + css_y)

        print(f"dpr={dpr} 视口逻辑原点=({origin_x:.0f},{origin_y:.0f}) 视口CSS=({css_x:.0f},{css_y:.0f})")
        print(f"逻辑屏幕坐标: {screen_x} {screen_y}")
        print(f"-> python skills/input-control/scripts/mouse.py click {screen_x} {screen_y}")
        b.close()


if __name__ == "__main__":
    main()
