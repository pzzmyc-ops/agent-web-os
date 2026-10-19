#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""连接常驻浏览器做点击 —— 用 CDP 页面坐标（视口 CSS 坐标），无需屏幕坐标换算。

这是最优雅的方式：CDP 的鼠标事件用页面视口坐标，浏览器自己消化渲染偏移/缩放，
不用去算「窗口位置+边框+标题栏+DPI」。

用法（三种坐标任选其一）:
    python scripts/click.py --px 989 --py 533        # 截图物理像素坐标（自动 ÷devicePixelRatio）
    python scripts/click.py --vx 659 --vy 355        # 视口 CSS 坐标
    python scripts/click.py --sel "<css>" --ix 150 --iy 60   # 元素内相对坐标
    python scripts/click.py --sel "<css>"            # 点元素中心

公共参数:
    --port N     端口（默认 9222）
    --tab N      指定标签页(0 起)，默认当前(最后一个)
    --dclick     双击
    --move-only  只移动不点击
    --steps N    分步移动步数(模拟真人轨迹，默认 12)

只断开连接、不关浏览器。
"""
import argparse
from playwright.sync_api import sync_playwright


def smooth_move(page, vx, vy, steps=12):
    """分步移动到目标，模拟真人轨迹。"""
    for i in range(1, steps + 1):
        t = i / steps
        # 简单线性插值，避免瞬间跳转
        page.mouse.move(int(vx * t), int(vy * t))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--px", type=float, default=None, help="截图物理像素 x")
    ap.add_argument("--py", type=float, default=None, help="截图物理像素 y")
    ap.add_argument("--vx", type=float, default=None, help="视口 CSS x")
    ap.add_argument("--vy", type=float, default=None, help="视口 CSS y")
    ap.add_argument("--sel", default=None, help="CSS 选择器")
    ap.add_argument("--ix", type=float, default=None, help="元素内相对 x")
    ap.add_argument("--iy", type=float, default=None, help="元素内相对 y")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--tab", type=int, default=-1)
    ap.add_argument("--dclick", action="store_true")
    ap.add_argument("--move-only", action="store_true")
    ap.add_argument("--steps", type=int, default=12)
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{args.port}")
        pages = [pg for ctx in browser.contexts for pg in ctx.pages]
        idx = args.tab if args.tab >= 0 else len(pages) - 1
        page = pages[idx]
        page.bring_to_front()

        vx, vy = args.vx, args.vy

        if args.sel:
            el = page.locator(args.sel).first
            el.wait_for(state="visible", timeout=15000)
            el.scroll_into_view_if_needed()
            box = el.bounding_box()
            if args.ix is not None and args.iy is not None:
                vx, vy = box["x"] + args.ix, box["y"] + args.iy
            else:
                vx, vy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        elif args.px is not None and args.py is not None:
            dpr = page.evaluate("window.devicePixelRatio")
            vx, vy = args.px / dpr, args.py / dpr

        if vx is None or vy is None:
            print("请用 --px/--py、--vx/--vy 或 --sel 之一给出目标坐标")
            browser.close()
            return

        smooth_move(page, vx, vy, steps=args.steps)
        if args.move_only:
            print(f"[移动] 视口坐标 ({vx:.1f}, {vy:.1f})")
        else:
            page.mouse.click(vx, vy, click_count=2 if args.dclick else 1)
            print(f"[点击] 视口坐标 ({vx:.1f}, {vy:.1f})")
        browser.close()  # 只断开，不关浏览器


if __name__ == "__main__":
    main()
