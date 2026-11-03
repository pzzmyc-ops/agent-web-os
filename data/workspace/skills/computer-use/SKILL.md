---
name: computer-use
description: 操作有 GUI 的 Windows 桌面。AX（UI Automation 控件树）优先，失败才降级视觉（PrintWindow 截图 + OCR + 坐标点击）。覆盖窗口枚举/置前/开关、点击、双击、右键、输入、滚动、进程诊断。仅 Windows。
---

# Computer Use（Windows GUI 操作）

驱动用户眼前这台 Windows 机器上的**真实 GUI 程序**：点按钮、填输入框、切窗口、滚动列表、开关应用。
发送的是**操作系统级输入事件**，与真人操作在系统层面一致；同时也支持**不抢焦点的 AX 通道**（见下）。

## 适用范围

**能操作**：任何有窗口的 Windows 程序 —— 资源管理器、Office（Word/Excel/PPT 窗口本身）、QQ/微信/飞书/钉钉、记事本、浏览器窗口、设置面板、系统对话框、安装器、第三方桌面软件。

**平台限制**：**只支持 Windows**。本 skill 的脚本全部基于 Win32 / UIAutomation API，macOS / Linux 无法使用。

**什么时候不该用它**（有更可靠的路子就别用 GUI）：

| 任务 | 该用 |
|---|---|
| 浏览器**页面内容**操作 | `playwright-browser`（CDP，快且稳） |
| 读写文件内容 | `file_access_*`，不要往编辑器里打字 |
| 改 Word/Excel/PPT 正文 | `office-suite`，不要模拟键鼠改正文 |
| 跑命令 | `terminal`，不要往终端窗口里打字 |
| 网页上的重复操作 | `playwright-browser` |

GUI 操作是**兜底手段**：只有当目标只有图形界面、没有 API/命令行时才用它。

## 控制策略：AX 优先，视觉兜底（先读这段）

**默认一律先走 UI Automation（AX）**：直接读窗口控件树，拿到每个可交互控件的角色、名称、坐标和它支持的 Action。
AX 能成的操作**不移动鼠标、不抢焦点、不用截图**，实测比视觉快一到两个数量级，而且能回读校验（`effect: confirmed`）。
只有 AX 树为空、或控件没有任何可执行 Action 时，才降级到**视觉**（PrintWindow 渲染窗口 + OCR + 坐标点击）。

统一入口 `act.py` 已封装这个顺序，**优先用它，不要一上来就截屏**：

```bash
# 看窗口里有什么控件：AX 有可用控件走 AX，树空/无 Action 自动改走视觉 OCR
python scripts/act.py tree --hwnd <hwnd> --query <关键词>

# 点：AX Invoke/Select/Toggle 优先，失败自动降级坐标点击
python scripts/act.py click --hwnd <hwnd> --name "保存"

# 输入：AX SetValue 优先（带 readback 校验），失败降级点击+键盘
python scripts/act.py type --hwnd <hwnd> --name "搜索框" --text "abc"
```

`act.py` 输出一行 JSON 结论，按 `route` 决定下一步：

| 输出 | 含义 | 下一步 |
|---|---|---|
| `{"route":"ax","effect":"confirmed",...}` | AX 完成且已回读确认 | **不要再补一次点击** |
| `{"route":"vision","effect":"unverified",...}` | 走了视觉坐标点击 | **必须再截一次图确认** |
| `{"route":"vision","effect":"not_found",...}` | 两条路都没找到 | 换关键词，或截图人工看 |
| `{"route":"ax","effect":"refused","code":...}` | 强制 `--route ax` 但 AX 不认 | 看 `code`，改用 `--route vision` |

**判断该走哪条路**：`act.py tree` 能列出带 `[Invoke]`/`[Value]`/`[SelectionItem]`/`[Toggle]` 的控件就走 AX。
以下情况 AX 通常为空或无 Action，**直接走视觉，别反复重试 AX**：

- Chromium/Electron 的**内容区**（QQ/微信/飞书/钉钉聊天区、VS Code 编辑区）
- canvas / WebGL / 游戏渲染 / 自绘界面
- 部分 UWP/WinUI 深层控件

## 坐标系：只有一套（逻辑坐标）

**全部工具统一使用逻辑坐标**（`mouse.py screen` 给出的尺寸，本机 1707×1067），
和 `screen.py --grid` 画在截图上的网格标签**完全一致**——看到图上标着 `400`，就传 `400`。

- `screen.py --grid` 网格标签 = 逻辑坐标 → 直接喂 `mouse.py`
- `mouse.py click <x> <y> --hwnd <hwnd>` 收的就是逻辑坐标
- `winfocus.py` 会自动实测 `SetCursorPos` 接受的到底是逻辑还是物理，并把换算因子缓存起来
  （本机实测 factor=1.0，即逻辑坐标直传；别的机器若不同，工具自己会算，你不用管）
- **不存在第二套坐标**：不要自己乘除 DPI，不要用 AX 的物理像素直接点击，也不要用 OCR 的框坐标点击

### 点击前自动置前 + 命中校验（这是硬约束）

前端窗口会被别的窗口盖住，而窗口级截图（PrintWindow）看不出来——它只渲染窗口自身内容。
所以**每次点击都必须带 `--hwnd`**，`mouse.py` 会依次做：

1. 把目标窗口置到前台，并**验证 `GetForegroundWindow()` 真的变成了它**（没成功就中止）
2. 按缓存的换算因子把逻辑坐标换算成真实光标位置
3. 用 `WindowFromPoint` **校验这个落点真的属于目标窗口**
4. 全部通过才点击；任何一步不通过都**报错退出、绝不盲点**

```
[前置] attach-input  factor=1.0  logical=(400,300) -> cursor=[400,300]
[点击] 逻辑 (400, 300)
```

失败时是这样（说明该点被别的窗口挡住或坐标算错了），此时不要重试同一坐标，先重新截图定位：

```
[中止] 逻辑坐标 (400,300) 换算后 (600,450) 命中的是窗口 1234567 'xxx'，不是目标 22939946
```

## 标准流程

1. **先枚举窗口拿 hwnd**：`ax.py find --query <关键词>`（比 `windows.py` 多了 hwnd，AX 所有操作都要它）。
   需要置前用 `windows.py --grep <关键词> --focus`（AttachThreadInput 绕过前台锁，能恢复最小化/托盘窗口）。
2. **开程序/文件/网址**：`windows.py --launch <exe或路径或URL>`（ShellExecuteW）。
   **关窗口**：`windows.py --grep <关键词> --close`（PostMessage WM_CLOSE，优雅关闭，不会强杀）。
3. **问 AX 能做什么**：`act.py tree --hwnd <hwnd>`，能列出带 Action 的控件就 `act.py click/type`，别截图。
4. **AX 不可用就看图定位**：`screen.py --window <关键词> --grid` 截**整个目标窗口**并叠加逻辑坐标网格，
   网格标签就是逻辑坐标，直接读、直接喂 `mouse.py`。
   `vision.py tree` 只用来**读文字内容**，它的坐标是参考值，**不要拿它当点击依据**（OCR 框会偏几十像素）。
5. **点击**：`mouse.py click <逻辑X> <逻辑Y> --hwnd <hwnd>`（自动置前 + 命中校验）。
6. **输入**：`act.py type` 优先（AX 走 SetValue）；需要前台打字时用 `keyboard.py type`
   （SendInput 直送 Unicode，**绕过输入法**，中英文不吞字）；按键/组合键用 `keyboard.py key/hotkey`。
7. **滚动**：`mouse.py move <区域中心>` + `mouse.py scroll <正数向上>`，再截图确认。
8. **复查**：AX 的 `confirmed` 即可；视觉的 `unverified` 必须重新截图确认。

## 点不动 / 失败排查（别死点，按顺序）

1. 窗口在不在、是否最小化/隐藏/无响应：`windows.py --grep <关键词>`（无响应标 `[无响应!]`）。
   **托盘应用**（QQ/微信最小化到通知区）窗口会变成「隐藏(visible=0)」，普通列表看不到，
   必须加 `--hidden` 才找得到，再用 `--focus` 唤回。
2. 看进程状态（Responding / CPU / 3 秒 CPU 增量）：
   ```bash
   powershell -ExecutionPolicy Bypass -File scripts/app_status.ps1 -Name <进程名>
   ```
3. **Electron 应用**（QQ/微信/飞书/钉钉/VS Code）：进程 API 常显示 `Responding=True`、
   `IsHungAppWindow=False`，但**渲染进程已卡死**，表现就是点不动。此时 API 判断不可靠。
4. 判定卡死 → 杀掉重启：
   ```bash
   powershell -ExecutionPolicy Bypass -File scripts/app_restart.ps1 -Name <进程名>
   ```
   重启后若出现登录窗，`vision.py tree --hwnd <hwnd>` 或 `screen.py --window <关键词>` 看图，再点击。
5. 点了没反应：**先看是不是窗口被盖住了**。窗口级截图（PrintWindow）看不见遮挡，
   所以判断依据只能是 `mouse.py --hwnd` 的命中校验输出——它已经帮你校验过，如果它没报错说明点确实落在目标窗口上，
   那就换坐标（重新 `screen.py --grid` 读）或换目标控件，别原地连点。
6. 同一坐标连续失败两次，就停下来重新截图定位，不要连点。

> PowerShell 脚本**内容必须纯英文**：中文会因编码问题解析失败。

## 脚本清单

- `scripts/act.py` — **AX 入口**：`tree`(看控件) / `click` / `type`。AX 不认就报错并提示改用网格坐标，**不会用 OCR 猜坐标**
- `scripts/ax.py` — UI Automation：`find`(窗口/hwnd) / `tree`(控件树) / `invoke` / `setvalue` / `select` / `toggle` / `getvalue` / `stop`
- `scripts/uia_server.ps1` — 常驻 UIA 服务（`act.py`/`ax.py` 自动拉起，不用手调）
- `scripts/winfocus.py` — 置前 + 命中校验 + 坐标空间校准（`mouse.py --hwnd` 内部调用）
- `scripts/clickprobe.py` — 点击前验证某个逻辑坐标会落到哪个窗口（`--click` 才真点）
- `scripts/vision.py` — **只读内容**：`tree`(OCR 出文字) / `shot`(PrintWindow 渲染窗口)。不要用它定位点击
- `scripts/windows.py` — 窗口：`--grep` 找窗口 / `--hidden` 含托盘隐藏窗口 / `--focus` 置前 / `--max` 最大化 / `--launch` 开程序 / `--close` 关窗口
- `scripts/screen.py` — 整屏/窗口截图 + **逻辑坐标网格（点击坐标的唯一来源）**
- `scripts/mouse.py` — 鼠标：`screen`/`pos`/`move`/`click`/`dclick`/`rclick`/`scroll`，**点击务必带 `--hwnd`**

## 用法

> 本机 Python 解释器是 **`D:/mafagent/python/python.exe`**（已装 pywin32 / Pillow / rapidocr_onnxruntime）。
> 命令里的 `python` 要换成这个绝对路径。脚本用**绝对路径**调用，别依赖当前目录。

```bash
# AX 优先（默认就该走这个）
python scripts/ax.py find --query 记事本                      # 拿 hwnd
python scripts/act.py tree  --hwnd <hwnd> --query 保存         # 看有哪些可用控件
python scripts/act.py click --hwnd <hwnd> --name 保存          # AX 点，不抢焦点
python scripts/act.py type  --hwnd <hwnd> --name 搜索框 --text 你好

# AX 不可用时：网格坐标 + 必带 --hwnd（自动置前 + 命中校验）
python scripts/screen.py --window <关键词> --grid              # 读图上网格标签 = 逻辑坐标
python scripts/mouse.py click <逻辑X> <逻辑Y> --hwnd <hwnd>     # 置前→换算→校验→才点
python scripts/keyboard.py type "要输入的文本"                  # 需要前台打字时

# 点击前想先确认落点属于哪个窗口（不点）
python scripts/clickprobe.py --hwnd <hwnd> --x 400 --y 300
python scripts/clickprobe.py --hwnd <hwnd> --x 400 --y 300 --click   # 校验通过才真点

# 只读内容（不要用它的坐标点击）
python scripts/vision.py tree --hwnd <hwnd> --query 发送
python scripts/vision.py shot --hwnd <hwnd> --image D:/tmp/win.png

# 窗口：找 / 置前 / 最大化 / 开 / 关
python scripts/windows.py                                      # 列出所有可见窗口
python scripts/windows.py --grep <标题关键词>                   # 找标题含关键词的窗口
python scripts/windows.py --grep <关键词> --focus               # 置前(自动恢复 最小化/隐藏)
python scripts/windows.py --grep <关键词> --max                 # 最大化(看内容前先做大)
python scripts/windows.py --grep <关键词> --hidden --focus      # 托盘隐藏的窗口也能找出来置前
python scripts/windows.py --launch <exe/路径/URL>               # 打开程序/文件/URL
python scripts/windows.py --grep <关键词> --close               # 关闭匹配窗口(WM_CLOSE)

# 截屏 + 坐标网格
python scripts/screen.py --window <标题关键词> --grid           # 截该窗口并画逻辑坐标网格
python scripts/screen.py --grid                                # 整屏 + 网格

# 鼠标 / 键盘
python scripts/mouse.py click <逻辑X> <逻辑Y> --hwnd <hwnd>
python scripts/mouse.py dclick <逻辑X> <逻辑Y> --hwnd <hwnd>
python scripts/mouse.py scroll <正数>                          # 正=向上翻页
python scripts/keyboard.py type "要输入的文本"
python scripts/keyboard.py key enter
python scripts/keyboard.py hotkey ctrl+c
```

## 要点（都是踩过的坑）

- **AX 优先级高于截图**：AX 一次几百毫秒、不抢焦点、可回读；视觉一次 5–8 秒、要置前、还得二次核对。
  能用 AX 就别截图。
- **没有 `drag` 命令**：拖拽要自己组合「`mouse.py move` 到起点 → 按下 → 分步移动 → 抬起」，脚本未封装；
  优先找 AX 的 SelectionItem / 目标控件的专用 Action 代替拖拽。
- **AX 的"可用"判据是"有 Action 的控件"**，不是"树非空"。有些窗口只暴露一个 Window 节点、
  没有任何 Action（典型如 QQ 聊天窗），这种等于不可用，必须走视觉。
- **元素索引只对最近一次 tree/find 有效**：界面变了就重新取。索引过期返回 `stale_index`，不会点到别的元素。
- **AX server 常驻**：首次调用拉起 PowerShell UIA 服务（约 1 秒），之后每次调用几十毫秒；`ax.py stop` 关闭。
- **视觉兜底用 PrintWindow 渲染窗口本身**，不是截屏幕，所以窗口被遮挡也能拿到内容；
  但 Chromium/Electron 的 PrintWindow 常返回纯色空图（已做检测），此时自动回退屏幕抓取并置前窗口。
- **OCR 前会把图缩到 1600px 宽**：原图太大（2500px+）OCR 反而读不出字。
- `mouse_event`/`keybd_event`/`SendInput` 是系统级事件，比 CDP/Playwright 合成事件更难被检测。
- `smooth_move` 分步移动模拟真人轨迹，点击带间隔；移动后有位置校验，偏差超过 2px 会报错而不是假装成功。
- 窗口 `IsWindowVisible` 对**最小化窗口也返回 True**，要配合 `IsIconic` 排除；
  托盘隐藏窗口 `IsWindowVisible=False`，要用 `--hidden` 才能枚举到。
- 输入中文**别用剪贴板粘贴**（会被输入法吞字/弹候选），用 `SendInput + KEYEVENTF_UNICODE` 直送；
  且 `INPUT` 结构在 64 位下必须是 **40 字节**（union 要含 MOUSEINPUT），否则 `SendInput` 静默失败。
- `--close` 是 `WM_CLOSE` 优雅关闭：程序有未保存内容会先弹「是否保存」对话框，需再点按钮处理。
- **临时截图统一存在 `D:/mafagent/data/workspace/temp/`，必须随机/时间戳命名（`screen.py` 默认已实现）。
  看完或展示后立刻删除（rm / Remove-Item），不要让 temp 目录堆垃圾。**
