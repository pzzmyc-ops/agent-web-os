---
name: playwright-browser
description: 有头常驻浏览器 + 固定用户身份(profile) + CDP 连接的联网搜索方案。
---

# playwright-browser

有头 Chrome 常驻窗口（固定 profile + CDP 端口 9222），窗口不关；脚本用 `connect_over_cdp` 连进去操作，`browser.close()` 只断开连接、不关窗口。

## 实例纪律（每次动手前先做）

**默认只有一个实例：端口 9222 + 固定 profile。** 别重复开、别开完不管。

1. 任何浏览器操作前，先 `list_browsers.py` 看当前开着几个。
2. 9222 已在跑就直接连它，**不要再 `start_browser.py`**（它端口占用会跳过，但别依赖这个）。
3. 临时/实验实例用完立刻 `close_browser.py --port N` 关掉，别堆积。
4. 收尾或发现多余实例，`close_browser.py --all` 一键清。

## 核心前提：脚本不替你判断，一切自己看

**这些脚本只负责「操作浏览器」。** 它们打印的 title/url/若干标题只是抓手，**不是搜索答案**。要获取信息，必须自己动手看：

- **看截图**：脚本存的 `--shot` 图，用 view_image 打开，肉眼读版面、验证码、图形、广告位、答案框等
- **读文字**：`page.evaluate("document.body.innerText")` 或 `locator.inner_text()` 拿可见文字
- **读 DOM**：`page.content()` 存 HTML，需要精确结构时自己分析
- **按需再操作**：读完决定下一步（翻页、点某条、点验证码…）

不要看脚本 stdout 的几行就当作结论。**搜索结果、页面内容、是否有你要的信息，全部由你看截图/DOM/文字自行判断。**

## 搜索流程（怎么搜）

1. 确保浏览器在跑（没跑先 `start_browser.py`）。
2. `search.py --q "关键词"`：开 Google 首页 → 定位搜索框 `fill()` 输入 → `press("Enter")`（DOM 操作，禁止拼 `/search?q=` 直链）。脚本会打印 url、是否触发验证、前若干条 h3 标题，并存截图。
3. **自己看**：先看 `status`——若 BLOCKED（url 含 `/sorry`/`captcha`）则停下让用户在窗口过验证，不硬闯；若 OK，打开截图 + 按需 `peek.py`/读 DOM，自己判断结果里有没有要找的信息。
4. 要点进某条结果、翻页、看正文 → 继续用 `peek.py`/`click.py` 操作，再自己看。

## 脚本用法

```bash
# 查当前所有实例（端口/PID/profile）——动手前先跑
python scripts/list_browsers.py

# 启动常驻浏览器（一次即可）。全新实例传 --port/--profile
python scripts/start_browser.py [--port 9222] [--profile 目录] [--url 网址]

# 关闭实例
python scripts/close_browser.py --port 9223    # 关指定端口
python scripts/close_browser.py --all          # 关全部

# 搜索：开首页→输入→回车。打印 url/验证状态/前若干标题，截图存 --shot（信息靠自己看截图/DOM）
python scripts/search.py --q "关键词" [--port 9222] [--shot out.png]

# 查看某标签页：打印标签列表/title/url/可见文字，截图存 --shot，DOM 存 --dom
python scripts/peek.py [--port 9222] [--tab N] [--shot out.png] [--dom out.html]

# 点击（CDP 页面坐标，免屏幕换算）
python scripts/click.py --px 989 --py 533          # 截图物理像素(自动÷devicePixelRatio)
python scripts/click.py --vx 659 --vy 355          # 视口CSS坐标
python scripts/click.py --sel "<css>"              # 点元素中心
python scripts/click.py --sel "<css>" --ix 150 --iy 60   # 元素内相对坐标
# 公共：--port --tab --dclick --move-only

# 截图物理像素 → 逻辑屏幕坐标（仅在降级用系统级鼠标时需要）
python scripts/to_screen.py --px 989 --py 533
```

## 规则

- 永远有头，绝不 headless。
- 搜索禁止拼直链，必须开首页→`fill()`→`press("Enter")`。
- 触发验证/登录 → 停下让用户在窗口里过，不硬闯；无账户/被挡就跳过。
- 取页面内容用 Playwright 通用手段，按当前 DOM 临时定选择器，不硬编码某网站的 id/class。

## 点击坐标

浏览器内点击**首选 CDP 页面坐标**（click.py），换算只一步：`视口CSS = 截图物理像素 ÷ devicePixelRatio`，浏览器自己消化缩放。

极少数验证码会检测 CDP 合成事件（点了无反应）→ **降级到系统级鼠标**（input-control skill）。此时鼠标要的是**逻辑屏幕坐标**，别手拼公式，用 `to_screen.py` 自动算：

```bash
python scripts/to_screen.py --px 989 --py 533   # 截图物理像素 → 逻辑屏幕坐标(自读窗口原点/dpr)
# 它会输出可直接执行的 mouse.py click X Y
```

## 约定

- profile 固定目录：`D:/mafagent/data/workspace/playwright-profile`（永不复制/重建）
- 系统 Chrome：`C:\Program Files\Google\Chrome\Application\chrome.exe`
- **临时文件（截图/DOM）统一存 `D:/mafagent/data/workspace/temp/`**，脚本默认已指向这里；别散落到工作区根目录。
