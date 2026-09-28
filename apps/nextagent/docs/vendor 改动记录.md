# vendor 框架改动记录

`vendor/` 下是第三方库 agent-framework 的源码。升级或重新安装框架会覆盖这里的所有修改。
本文件记录我们打过的补丁：改了什么、为什么改、升级后怎么改回来。

每次再动 vendor 都要往这里追加一条。

---

## 1. handoff 群聊没有流式输出

改动文件：`vendor/agent_framework/_workflows/_workflow.py`
函数：`Workflow._run_workflow_with_tracing`
当前位置：第 569-592 行

### 症状

群聊（handoff）模式下前端收不到流式增量。模型输出和工具调用全部跑完之后，整段文字才一次性刷出来。
单聊模式正常。

### 原因

工作流的起始执行器（start executor）在 runner 的 superstep 主循环之外运行。原代码用
`await initial_executor_fn()` 直接等它跑完，期间它产生的所有事件只能堆在 `self._runner.context`
的事件队列里，等这个 await 返回后才被主循环一次性排空。

在 handoff 编排里，起始执行器就是协调者 agent，所以「第一个 agent 的一整轮」——包括工具调用和
全部文本增量——都会被压到最后成批送出。这就是一次性输出的来源。

诊断证据：用探针 monkeypatch `RunnerContext.add_event` 记录入队时间戳，发现入队时间分散在几秒内，
而出队时间集中在同一瞬间。

### 原始代码

```python
                # Execute initial setup if provided
                if initial_executor_fn:
                    await initial_executor_fn()
```

### 改后代码

```python
                # Execute initial setup if provided.
                # The start executor runs outside the runner's superstep loop, so its events must be
                # streamed here explicitly. Awaiting it directly would hold every event it produces in
                # the context queue until it returned: for orchestrations whose start executor is an
                # agent (handoff coordinator), that is the entire first agent turn - tool calls and all
                # text deltas - delivered in one burst after the fact. Poll the queue while it runs,
                # mirroring RunnerImpl.run_until_convergence.
                if initial_executor_fn:
                    initial_task = asyncio.create_task(initial_executor_fn())
                    try:
                        while not initial_task.done():
                            try:
                                event = await asyncio.wait_for(self._runner.context.next_event(), timeout=0.05)
                            except asyncio.TimeoutError:
                                continue
                            yield event
                    except asyncio.CancelledError:
                        initial_task.cancel()
                        try:
                            await initial_task
                        except asyncio.CancelledError:
                            pass
                        raise
                    await initial_task
```

把起始执行器改成后台任务，一边跑一边轮询事件队列并 yield，行为和 `RunnerImpl.run_until_convergence`
对齐。`CancelledError` 分支保证取消回合时不会漏掉那个后台任务。

---

## 2. deepseek 的思考内容被丢弃，前端没有思考卡片

改动文件：`vendor/agent_framework_openai/_chat_completion_client.py`
函数：`OpenAIBaseChatClient._parse_response_from_openai`（非流式）
　　　`OpenAIBaseChatClient._parse_response_update_from_openai`（流式）

### 症状

用 deepseek 模型时，单聊和群聊都不显示思考卡片。界面上的 Thinking 开关和 Effort 档位看起来毫无作用。

### 原因

思考内容在进入框架的第一步就被丢掉了，属于字段名不匹配。

deepseek 走 OpenAI chat completions 协议，思考文本放在 `delta.reasoning_content`
（此时 `delta.content` 是 null）。实测抓到的上游分片：

```
data: {"choices":[{"delta":{"content":null,"reasoning_content":"We"}}]}
data: {"choices":[{"delta":{"content":null,"reasoning_content":" need"}}]}
```

而框架只认 `reasoning_details`（OpenRouter 的字段名），deepseek 从不发这个键，于是那行判断永远取不到值。

往下游看，`apps/nextagent/stream_adapter.py` 的 `_iter_content_deltas` 靠 `type == "text_reasoning"`
分流思考增量。客户端既然从没产出过这种 content，这个分支永不命中，前端一个思考块都收不到。

openai SDK 会把非标准字段原样保留在 delta 上，所以 `getattr(delta, "reasoning_content")` 能直接取到值，
补一个分支即可，不需要改协议或改 SDK。

### 改动内容

两处都是在原有的 `reasoning_details` 判断后面追加一个分支，不动原有逻辑。

流式（`_parse_response_update_from_openai`）：

```python
            if reasoning_details := getattr(choice.delta, "reasoning_details", None):
                contents.append(Content.from_text_reasoning(protected_data=json.dumps(reasoning_details)))
            if reasoning_text := getattr(choice.delta, "reasoning_content", None):
                contents.append(Content.from_text_reasoning(text=reasoning_text, raw_representation=choice))
```

非流式（`_parse_response_from_openai`）：

```python
            if reasoning_details := getattr(choice.message, "reasoning_details", None):
                contents.append(Content.from_text_reasoning(protected_data=json.dumps(reasoning_details)))
            if reasoning_text := getattr(choice.message, "reasoning_content", None):
                contents.append(Content.from_text_reasoning(text=reasoning_text, raw_representation=choice))
```

后两行就是新增的部分。升级框架后如果思考卡片又消失了，先来这两个函数确认这两行还在不在。

### 相关但不在 vendor 的改动

思考开关的参数透传没有改 vendor，走的是 SDK 原生的 `extra_body`：

- `apps/nextagent/ws_chat.py` 的 `_reasoning_params` 从 WebSocket 帧里取 `thinking` 和 `reasoning_effort`；
- `apps/nextagent/stream_adapter.py` 的 `_reasoning_extra_body` 把它们包成 `extra_body`，
  经 `options` / `default_options` 交给客户端，`_prepare_options` 会原样带进请求体，
  最后由网关的模型 adapter 解释。

之所以必须用 `extra_body`：这两个键不是 chat completions 的标准参数，
而框架最终调用的是 `client.chat.completions.create(**options_dict)`，SDK 的签名不接受未知关键字。

---

## 3. 群聊点停止之后，协调者还在后台继续跑

改动文件：`vendor/agent_framework/_workflows/_workflow.py`
函数：`Workflow._run_workflow_with_tracing`（就是第 1 条改过的那段）

### 症状

群聊回合被取消（用户点停止）后，`run_handoff_turn` 已经返回、`turn_complete` 已经发出，
但协调者 agent 的那一整轮还在后台跑：继续调工具、继续打模型，产出的事件没有任何人消费。

诊断证据：探针在 `run_handoff_turn` 返回后每隔几秒数一次 `asyncio.all_tasks()`，
取消后 12 秒仍有一个任务活着，名字对得上第 1 条补丁里 `asyncio.create_task(initial_executor_fn())`
起的那个任务。

### 原因

消费方停止的方式是从 `async for` 里 `break`。`break` 不取消任何人，它只是让这个异步生成器
被关闭，在挂起的 `yield` 处收到 `GeneratorExit`。而第 1 条补丁只写了
`except asyncio.CancelledError`，`GeneratorExit` 不是它的子类，于是 `initial_task.cancel()`
永远不会执行。

### 改动内容

把那个 `except` 改成同时接 `GeneratorExit`：

```python
                    except (asyncio.CancelledError, GeneratorExit):
                        initial_task.cancel()
```

---

## 4. 每次取消都在日志里留一段 "Failed to detach context"

改动文件：`vendor/agent_framework/_workflows/_workflow.py`（函数 `Workflow.run` 的核心执行路径）
　　　　　`vendor/agent_framework/_types.py`（`ResponseStream` 新增 `aclose`）

### 症状

群聊回合被取消后，日志里出现一段 opentelemetry 的报错：

```
Failed to detach context
...
  File "vendor/agent_framework/_workflows/_workflow.py", line 584, in _run_workflow_with_tracing
    yield event
GeneratorExit
ValueError: <Token ...> was created in a different Context
```

不影响功能（回合照样收尾），但每取消一次就多一段 traceback，而且那个 workflow span 永远不收尾。

### 原因

`GeneratorExit` 从 `async for` 里传出去时，**不会**关掉正在被迭代的那个生成器 —— 它被留给
异步生成器终结器处理，而终结器是在另起的任务里跑的，那个任务复制的是别人的上下文。
`_run_workflow_with_tracing` 里的 workflow span 是在消费方的上下文里 attach 的，
从终结器去 detach 就会撞上 `Token was created in a different Context`。

链条上有三层，每层都得显式往下传关闭动作，缺一层就还是落到终结器手里：

1. `apps/nextagent/stream_adapter.py` 的 `run_handoff_turn` 在 `finally` 里 `await events.aclose()`；
2. `ResponseStream` 原本没有 `aclose`（调了直接 `AttributeError`），补一个，转发给内层迭代器；
3. `Workflow.run` 的核心生成器把内层的 `_run_workflow_with_tracing` 存进变量，在自己的
   `finally` 里 `await traced_events.aclose()`。

### 改动内容

`_types.py` 在 `ResponseStream.__aiter__` 后面新增 `aclose`，把关闭转发给 `self._iterator`。

`_workflow.py` 把原来直接写在 `async for` 里的 `self._run_workflow_with_tracing(...)` 提到
变量 `traced_events`，并在函数已有的 `finally` 开头加：

```python
            if traced_events is not None:
                await traced_events.aclose()
```

升级框架后如果取消群聊又开始刷这段 traceback，先确认这三层还在不在。
