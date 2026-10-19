---
name: cursor-subagent
description: 把一整件开发任务外包给 Cursor（用官方 cursor-sdk 起一个真 Cursor agent，带终端/文件/联网/技能），后台异步跑 + 中途读日志看它在干什么 + 结束后核验产物。用户说「把任务交给 Cursor」「让 Cursor 做」「用 Cursor 当子 agent」「看它进度」时用。
tags: [cursor, cursor-sdk, subagent, delegate, background, agent]
---

# 把任务交给 Cursor（子 agent）

**定位**：Cursor 在这个位置上是 **agent 而不是模型** —— 它有终端、文件读写、联网、自己的 skill 系统和 agent 循环。所以用它来"**把一整件活外包出去**"，不要用它当"带自己提示词和工具编排的模型"（那需要模型供应方，Cursor 不给）。

**做法**：后台起一个 `cursor_run.py`（官方 `cursor-sdk` 驱动），任务和过程实时写进日志；用 `file_access_read` 中途看进度；结束后**核验产物**，不信它的一面之词。

## 一、起任务（异步）

```bash
python D:/mafagent/data/workspace/skills/cursor-subagent/scripts/cursor_run.py \
  --cwd D:/path/to/project \
  "把 README 补上安装步骤，跑一次测试，最后用三句话汇报改了什么、测试结果如何"
```

- 用终端工具的 **`background=true`** 起，这一轮对话不用等它跑完；需要时加 `notify_on_complete=true`。
- 日志默认写 `D:/mafagent/data/workspace/cursor-re/runs/cursor-<时间戳>.log`（同时打一份到 stdout）。
- 启动即打印 `AGENT_ID=` / `RUN_ID=`，记下来后面能 `--resume`。

常用参数：

| 参数 | 说明 |
|---|---|
| `--cwd` | **agent 的工作目录，默认就是当前目录**。它真的会改这里的文件、跑命令，指准目标 |
| `--model` | 默认 `grok-4.7`（另有 `composer-2.5`、`claude-sonnet-5-*` 等，`Cursor.models.list()` 可查全量） |
| `--effort low\|medium\|high\|xhigh` / `--fast` / `--context 256k\|500k` | 调难度、速度、上下文 |
| `--timeout 3600` | 软超时，到点取消本次 run |
| `--resume <AGENT_ID>` | 接着同一会话继续发消息（保留上下文） |
| `--quiet` | 只写日志，不往 stdout 打正文分片 |
| `--log <path>` | 指定日志路径 |

## 二、中途看进度（比"调接口"强的地方）

日志是**实时流式**写的，用 `file_access_read <log> offset=<上次行数>` 翻新增部分即可（真实样例）：

```
[13:45:19] START model=grok-4.7 cwd=D:\mafagent\data\workspace\tmp\cursor-agent-cwd
[13:45:19] TASK 用 shell 统计当前目录里 txt 文件的个数，一句话汇报。
[13:45:19] LOG D:/mafagent/data/workspace/cursor-re/runs/final-check.log
[13:45:19] AGENT_ID=agent-10d99ab4-62c6-4375-a325-647e3f8c9dcf
[13:45:25] RUN_ID=run-9821de7a-0e6e-4dc4-b86c-d5b406a57b95
[13:45:29] · thinking 准备用 shell 命令统计当前目录中 txt 文件的数量，并以一句话汇报结果。
[13:45:30] · thinking 改用 PowerShell 命令 `Get-ChildItem -Filter *.txt -File` 统计…
│我用 PowerShell 统计当前目录下的 txt 文件数量。          ← 正文按分片流式出现
[13:45:30] · tool shell running args={'command': '(Get-ChildItem -Filter *.txt -File).Count', 'timeout': 30000}
[13:45:32] · tool shell completed args={…} result={'status': 'success', 'value': {'exitCode': 0, 'stdout': '4\r\n', 'stderr': ''}}
[13:45:33] · thinking 当前目录下共有 4 个 txt 文件。
[13:45:33] · usage TokenUsage(input_tokens=20262, output_tokens=296, cache_read_tokens=8064, total_tokens=28622, reasoning_tokens=198)
[13:45:33] · status FINISHED
[13:45:34] DONE status=finished elapsed=20.3s usage=TokenUsage(…)
FINAL: 我用 PowerShell 统计当前目录下的 txt 文件数量。当前目录 `D:\…` 里有 **4** 个 txt 文件。
RUN_DONE status=finished elapsed=20.3s agent_id=agent-10d99ab4-… run_id=run-9821de7a-… log=…
```

看这几个东西就够了：

- `· thinking <文本>` —— 它当前在想什么（**这就等于你能读到它的进度和意图**）。
- `· tool <名字> running/completed args={…} result={…}` —— 它正在跑什么命令/改什么文件、结果是什么（shell 的 stdout/stderr/exitCode 都在 `result.value` 里）。
- `│…` —— 回答正文的分片。
- `· status FINISHED` / `RUN_DONE status=…` —— 结束。

判读法：

- 只有 `thinking`、没有 `tool`：它在推理/等模型，正常（首响 15~25s）。
- 出现 `· tool …shell/edit/write`：开始动文件了 —— 这时去 `git status` / `ls` 看真实变化。
- 反复试错（大量 `tool` + thinking、几百秒没进展）：任务太含糊或它在乱撞，杀掉、把任务拆小重发。
- `RUN_DONE status=error` 或日志里 `ERROR`/`TRACE`：读原因（认证/网络/参数/脚本 bug）。

## 三、结束后必须核验（不要只信 FINAL）

1. 看 `FINAL:` 是否真的回答了问题、有没有回避。
2. **看产物**：在 `--cwd` 里 `file_access_ls` / `grep` / `git diff --stat`，确认它说的改动确实存在且内容正确。
3. 它说"测试通过"之类，要自己找证据（日志里的 shell 输出、测试文件、CI 记录）。
4. 不满意 → `--resume <AGENT_ID> "把 XX 改成 YY"`，它保留上下文，比新起 agent 便宜。

## 四、注意事项

- **它是 agent，不是模型**：别用它实现"我自己的 system prompt + 我自己的 tool loop"。提示词、工具集（shell/read/edit/grep/task/webSearch…）、循环节奏都在 Cursor 那边；自定义工具只在"不限制内置工具白名单"时才被调用。要纯模型请走别的供应商。
- **节奏与成本**：每次起 bridge + agent，首响 15~25s，多步任务 30s~几分钟；适合派活，不适合高频小调用。
- **并行**：可以同时跑多个（不同 `--cwd`/任务，各有日志）；同目录并发改文件会互相踩，尽量错开或用不同分支/副本。
- **环境**：`cursor-sdk`（1.0.37）必须装在跑脚本的解释器里（本机 `python` 已装）；脚本自带 Windows bridge 管道补丁（否则 `WinError 10038`）。API key 取 `CURSOR_API_KEY` 或 `cursor-re/data/cursor_api_key.txt`；别把 key 外发/写日志。
- **不要**去逆向 Cursor 私有协议"反代出裸模型"：推理面强制客户端完整性校验（`x-cursor-checksum` 由原生层机器密钥生成），仿造不出来，而且属于会被封的做法。

## 五、参考

- 脚本：`scripts/cursor_run.py`（自带 bridge 补丁、进度日志、`FINAL:`/`RUN_DONE` 收尾、`--resume`、出错带 `TRACE`）
- 样例日志（真实跑通、含工具调用全过程）：`D:/mafagent/data/workspace/cursor-re/runs/final-check.log`
- 相关验证脚本：`D:/mafagent/data/workspace/cursor-re/scripts/sdk_prompt_test.py`、`sdk_customtool_test.py`、`sdk_msg_shapes.py`（dump SDK 消息结构）
- 实测结论备查：Cursor 只给 agent，不给裸模型 —— `api.cursor.com` 没有 `/v1/chat/completions`（404），API key 也认不了 `api2.cursor.sh` 的推理面（401）；`tools` 白名单只接受 Cursor 内置工具名，自定义工具仅在"不限制白名单"时才会被调用。
