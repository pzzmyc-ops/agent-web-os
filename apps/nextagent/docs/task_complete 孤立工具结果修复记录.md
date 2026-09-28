# task_complete 孤立工具结果修复记录

日期：2026-08-20

改动文件：

- `apps/nextagent/stream_adapter.py`：收尾/交接类工具改为成对落盘，界面仍不发工具卡
- `apps/nextagent/session_log.py`：新增 `is_orchestration_tool`；前端历史和任务快照跳过这类工具

相关检查：`apps/nextagent/llm.py` 的 `_fix_tool_ordering`（本次未改逻辑，只是它最先把问题暴露出来）

---

## 症状

模型已经正常说出回复，紧接着整轮报错：

```
ValueError: tool 消息没有声明它的 assistant tool_calls: ['...'] —— 多半是 handoff 工具被中间件短路后只留下了合成的 function_result
```

报错文案里的「handoff 被中间件短路」是当初写检查时的猜测。这次对上的是同一类事：内部收尾工具只留下了结果，没有对应的调用声明。

和模型、计划模式都无关。DeepSeek、Claude 都能复现；新开对话闲聊也能复现。

---

## 复现

### 计划被拒后切到 Claude

会话：`data/workspace/sessions/6e1c01ce4e2d490ab07b096b27ff27e3/events.jsonl`

1. DeepSeek 交了一份测试计划
2. 用户点拒绝，前端自动再发「用户拒绝了这个计划，根据情况制定下一步计划」（`apps/nextagent/static/js/plan-review.js`）
3. 下一轮已是 Claude（编号是 `toolu_...`；同一会话里 DeepSeek 是 `call_00_...`）
4. Claude 先写出「请告诉我拒绝的原因」
5. 下一毫秒报错，编号 `toolu_018o9oH2BCiamhY8nhzkd9d7` 只出现在报错里，从未成对落盘

### 新开对话，仅 DeepSeek

会话：`data/workspace/sessions/6cb59d9931944602add5e6583599f14a/events.jsonl`

1. 新会话，计划区为空，计划模式关闭
2. 用户：「你好，你会做什么」
3. 助手正常列出能力
4. 思考里写了：「答完按协议调用 task_complete」
5. 日志没有这条 `task_complete` 调用，下一毫秒报错，编号 `call_00_QJewPA1vz3hwmKsDa2XT5355`

第二条证明：不是计划流程，是日常「说完再收尾」。

---

## 原因

单聊协议要求：答完必须调用 `task_complete` 结束本轮（`apps/nextagent/stream_adapter.py` 的 `SOLO_PROTOCOL`）。

`task_complete` 和 `handoff_to_*` 被当成编排工具：不画工具卡，原先也**不写进事件日志**。理由是结果永远是 `round finished`，画出来对用户没意义；写进历史又会多一个空气泡。

实际发送链路是：

1. 模型先输出正文，再调 `task_complete`
2. 框架执行它，内存里有一条工具结果
3. 框架还要再问模型一句（带着这条结果）
4. 再问之前会从事件日志重装历史
5. 日志里没有这次调用声明，只剩结果
6. `llm.py` 的配对检查发现孤立 `tool` 消息，直接抛错

DeepSeek 交计划时是「交出方案然后卡住等人审」，中间不再问模型，所以那一轮不炸。被拒后的闲聊、以及任何「答完再收尾」的一轮，都会走到第 3 步，于是炸掉。

`task_complete` 本身没有算错。坏的是：用完它再问模型时，调用和结果没有成对带上。

---

## 修复

写入对话记录，但只给模型看，不给用户看。

1. 编排工具出现时，先把当前这段正文落盘，再记下 `tool_call`
2. 结果到达时记下对应的 `tool_result`
3. 不发 `block_open` / `block_end`，界面不出现工具卡
4. 前端历史投影和任务快照跳过这类工具，避免空气泡

模型侧历史因此能还原成「助手声明了调用 → 紧跟一条同编号的结果」。`_fix_tool_ordering` 不再把它们当成孤儿。

交接工具 `handoff_to_*` 用同一套处理，避免群聊走出同一条坑。

---

## 验收

新开对话，发「你好，你会做什么」。

- 正文正常结束，不再报上述 ValueError
- 界面没有 `task_complete` / `round finished` 工具卡
- 该会话 `events.jsonl` 里应能看到成对的 `tool_call` / `tool_result`，名字是 `task_complete`

计划模式拒绝后再交一版，也应能继续，不再在「请告诉我拒绝原因」之后炸掉。
