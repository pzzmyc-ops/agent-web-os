---
name: input-control
description: 系统级鼠标键盘 + 窗口/截屏工具（ctypes 调 Windows API，零第三方依赖），用屏幕坐标，面向浏览器之外的场景：桌面应用、其他窗口、系统级点击/输入。
---

# 系统级输入控制（鼠标 + 键盘 + 窗口）

用 ctypes 直接调 Windows `user32/shell32`，发送**操作系统级真实输入事件**，与真人操作在系统层面无法区分。

## 定位：浏览器之外

浏览器内的操作走 `playwright-browser`（CDP）；本 skill 面向浏览器管不到的场景：桌面软件、其他窗口、系统弹窗、全局快捷键。也作为浏览器内 CDP 点击被检测时的降级方案。

## 操作桌面任务的正确流程（关键，别一上来就截屏瞎找）

1. **先枚举窗口**：`windows.py --grep <关键词>` 直接拿到窗口标题 + 矩形 + 中心点，比看截图快且准。需要就 `--focus` 置前（AttachThreadInput 绕过前台锁，能恢复最小化窗口）。
2. **开程序/文件/网址**：`windows.py --launch <exe或路径或URL>`（ShellExecuteW）。**关窗口**：`windows.py --grep <关键词> --close`（PostMessage WM_CLOSE，优雅关闭）。
3. **看内容前先最大化窗口**：`windows.py --grep <关键词> --max`，窗口做大后截图一屏能看更多（少翻页、引用不易被截断）。
4. **再看图定位**：`screen.py --window <关键词> --grid` 截**整个目标窗口**并叠加坐标网格，网格标签就是逻辑坐标，直接读、直接用，**不要反复裁切猜坐标**。
5. **点击**：`mouse.py click <逻辑X> <逻辑Y>`。
6. **输入**：`keyboard.py type/key/hotkey`（type 用 SendInput 直送 Unicode，**绕过输入法**、中英文不吞字）。
7. **翻页读更多**：`mouse.py move <聊天区中间>` + `mouse.py scroll <正数向上>`，再截图。
8. 每步之后用 `screen.py --window ... --grid` 复查，确认无误再下一步。

## 点不动怎么办（别死点，先判断）

点击没反应时**不要反复重试**，按顺序排查：

1. 看窗口在不在、是否最小化/隐藏/无响应：`windows.py --grep <关键词>`（无响应标 `[无响应!]`）。**托盘应用**（QQ/微信最小化到右下角通知区）窗口会变成「隐藏(visible=0)」，普通列表看不到，必须加 `--hidden` 才找得到，再用 `--focus` 唤回。
2. 看进程状态（Responding / CPU / 3 秒 CPU 增量）：
   ```bash
   powershell -ExecutionPolicy Bypass -File scripts/app_status.ps1 -Name <进程名>
   ```
   3 秒 CPU 增量持续高 = 进程在忙；全部 Responding=True 但点不动 = 见下条。
3. **注意 Electron 应用**（QQ、微信、飞书、钉钉等）：进程 API 常显示 `Responding=True`、`IsHungAppWindow=False`，但**渲染进程已卡死**，表现就是点不动。此时 API 判断不可靠——以「点了确实没反应（自己也点过）+ 多次」为准。
4. 判定卡死 → **杀掉重启**：
   ```bash
   powershell -ExecutionPolicy Bypass -File scripts/app_restart.ps1 -Name <进程名>
   ```
   重启后若出现登录窗，`screen.py --window <窗口关键词>` 截图 → `mouse.py click` 点登录/扫码。

> PowerShell 脚本**内容必须纯英文**：中文会因编码问题解析失败。

## 坐标系（实测）

- 本进程 **DPI-unaware**，鼠标用**逻辑坐标**（`mouse.py screen` 查，本机 1707×1067；物理 2560×1600、缩放 1.5x）。
- `screen.py` 截图是物理像素，但 `--grid` 网格标签画的是**逻辑坐标**，可直接喂 `mouse.py`。
- 从浏览器截图定位再系统级点击：用 `playwright-browser/scripts/to_screen.py --px --py` 换算。

## 脚本

- `scripts/windows.py` — 窗口：`--grep` 找窗口(标 隐藏/最小化/[无响应!]) / `--hidden` 连托盘隐藏窗口 / `--focus` 置前(自动恢复 最小化/隐藏) / `--max` 最大化 / `--launch` 开程序 / `--close` 关窗口
- `scripts/app_status.ps1` / `scripts/app_restart.ps1` — 进程状态诊断 / 杀进程重启（点不动时用）
- `scripts/screen.py` — 截屏：`--window` 截指定窗口 / `--grid` 叠加逻辑坐标网格 / `--step` 步长
- `scripts/mouse.py` — 鼠标：`screen`/`pos`/`move`/`click`/`dclick`/`rclick`/`scroll`
- `scripts/keyboard.py` — 键盘：`type` 文本(Unicode 直送,绕输入法) / `key` 按键 / `hotkey` 组合键

## 用法

```bash
# 窗口：找 / 置前 / 最大化 / 开 / 关
python scripts/windows.py                                     # 列出所有可见窗口
python scripts/windows.py --grep <标题关键词>                  # 找标题含关键词的窗口
python scripts/windows.py --grep <关键词> --focus              # 置前(自动恢复 最小化/隐藏)
python scripts/windows.py --grep <关键词> --max                # 最大化(看内容前先做大)
python scripts/windows.py --grep <关键词> --hidden --focus     # 托盘隐藏的窗口也能找出来置前
python scripts/windows.py --launch <exe/路径/URL>              # 打开程序/文件/URL
python scripts/windows.py --grep <关键词> --close              # 关闭匹配窗口(WM_CLOSE)

# 截屏 + 坐标网格
python scripts/screen.py --window <标题关键词> --grid          # 截该窗口并画逻辑坐标网格
python scripts/screen.py --grid                               # 整屏 + 网格

# 鼠标 / 键盘
python scripts/mouse.py click <逻辑X> <逻辑Y>
python scripts/mouse.py dclick <逻辑X> <逻辑Y>
python scripts/mouse.py scroll <正数>                         # 正=向上翻页
python scripts/keyboard.py type "要输入的文本"
python scripts/keyboard.py key enter
python scripts/keyboard.py hotkey ctrl+c
```

## 要点

- `mouse_event`/`keybd_event`/`SendInput` 是系统级事件，比 CDP/Playwright 合成事件更难被检测。
- `smooth_move` 分步移动模拟真人轨迹，点击带间隔。
- 窗口 `IsWindowVisible` 对**最小化窗口也返回 True**，要配合 `IsIconic` 排除；托盘隐藏窗口 `IsWindowVisible=False`，要用 `--hidden` 才能枚举到。
- 输入中文**别用剪贴板粘贴**（会被输入法吞字/弹候选），要用 `SendInput + KEYEVENTF_UNICODE` 直送；且 `INPUT` 结构在 64 位下必须是 **40 字节**（union 要含 MOUSEINPUT），否则 `SendInput` 静默失败。
- `--close` 是 `WM_CLOSE` 优雅关闭：程序若有未保存内容会先弹「是否保存」对话框，需再点按钮处理（不会强杀）。
- **临时截图统一存在 `D:/mafagent/data/workspace/temp/`，并且必须使用随机/时间戳命名（`screen.py` 默认已实现）。你作为 Agent，在看完图或展示给用户之后，必须立刻通过 rm / Remove-Item 删除该截图**，绝不能让 temp 目录堆积垃圾。