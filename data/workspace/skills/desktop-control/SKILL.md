---
name: desktop-control
description: 驱动用户桌面：开关窗口、操作资源管理器/编辑器/Office 预览等 app 的能力
tags: [desktop, windows, office, editor, explorer]
---

# 驱动用户的桌面

用户说"打开"、"看看我现在开着什么"时，指的是他眼前那块屏幕。用户可以在 Office 窗口里手改。你改 Word/Excel 正文走 office-suite，不要用桌面能力改正文。
没有桌面专用工具，用终端跑这个脚本，一条命令一次调用：

```bash
${AGENT_PYTHON} ${HERMES_SKILL_DIR}/scripts/desktop.py --base ${GATEWAY_BASE} list
```

`${AGENT_PYTHON}` 和 `${GATEWAY_BASE}` 加载时已替换成本机真实值，照抄即可，不要自己写
python 路径或端口。不需要任何 token 或鉴权。

桌面能做什么**不在这份文档里**，由桌面自己上报。所以流程固定是：先看有哪些屏 → 问那块屏
支持什么 → 再动手。不要凭印象写操作名。

## 1. 先确定操作哪块屏

一个浏览器标签页就是一块独立的屏幕，各有自己的窗口。`list` 给出在线的屏幕：

```json
{"ok": true, "desktops": [{"desktopId": "dmt1s52ay8lykpz", "connectedAt": 1787246029.6}]}
```

只有一块就用它。有多块说明用户开了多个标签页，**问用户操作哪一块**，不要挑一个猜。
一块都没有说明桌面页面没开着，告诉用户去打开，不要重试。

## 2. 再问那块屏支持什么

```bash
${AGENT_PYTHON} ${HERMES_SKILL_DIR}/scripts/desktop.py --base ${GATEWAY_BASE} exec \
  --desktop <desktopId> --op os.operations
```

返回每个操作的名字、说明、参数 schema、是否只读。这是唯一权威的清单——它变了以它为准。

想知道"用户现在在做什么"用 `os.snapshot`：开着哪些窗口、哪个在最前、在哪个目录、
选中了哪些文件、装了哪些 app。

## 3. 动手

典型顺序（具体参数看第 2 步的 schema）：

1. 开窗口：`file.open`（按路径开合适的 app）、`explorer.open`、`app.launch`，返回 `winId`。
2. 看这个窗口能干什么：`app.capabilities`，参数 `{"winId": "..."}`，返回能力名 + 参数 schema。
3. 真正干活：`app.invoke`，参数 `{"winId":"...","capability":"...","args":{...}}`。

改编辑器内容、列目录、切目录，全都是第 3 步，没有别的入口。Office 窗口给人编辑。import 之前对已打开窗口调用 `doc.save`，等到返回再读磁盘。compile 写回后调用 `doc.reload`。桌面也会自己重载，调两次没关系。PowerPoint 可以打开，agent 暂不编辑。

```bash
${AGENT_PYTHON} ${HERMES_SKILL_DIR}/scripts/desktop.py --base ${GATEWAY_BASE} exec \
  --desktop <desktopId> --op app.invoke \
  --params '{"winId":"<winId>","capability":"<能力名>","args":{}}'
```

## 4. 报错照原文改，不要换写法重试

失败一律是 `{"ok": false, "error": "..."}`，退出码 1，`error` 里已经写清了怎么改：

- `未知操作: xxx；可用的是 ...` —— 操作名写错了，从列出的里面挑。
- `xxx 缺少必填参数 yyy` —— 参数漏了，回第 2 步看 schema。
- `桌面 xxx 不在线；在线的是 ...` —— desktopId 过期或写错了，重新 `list`。
- `某 app 不支持 xxx；支持的是 ...` —— 能力名写错了，回第 2 步用 `app.capabilities`。
- `没有在 20 秒内响应` —— 页面关了或编辑器正忙，告诉用户，不要连着重试。

## 5. 两个容易踩的坑

- **winId 不能跨刷新用**。刷新页面后 desktopId 不变，但窗口是重建的，winId 全变了。
  连续操作之间用户可能刷新过，报"窗口不存在"就重新 `os.snapshot` 取当前的 winId。
- **别把 Windows 控制台的输出管道给 JSON 解析器**。控制台是 cp936，中文会被弄坏，
  看起来像"接口返回空"。直接看原文即可。
