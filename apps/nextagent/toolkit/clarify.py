"""clarify 工具 —— 让 agent 在必须由人决定时问用户一句,并等到答案。

为什么需要它:模型遇到「这事有两三种做法,选哪个取决于你的偏好」时,只有两条烂路 ——
自己猜(猜错就白干一轮),或者输出一段话然后结束回合(用户回一句,上下文已经散了)。
clarify 给第三条:在回合中间停下来问,拿到答案继续把活干完。

它不是审批。clarify 只问「具体要怎么做」,不做危险命令拦截 —— 这个项目不拦截任何
命令(见 toolkit/terminal.py)。把它当审批用会让模型学会「先问一句就能干任何事」,
既没有安全价值又拖慢每一轮。

## 协议(前端已经实现好了,后端必须对齐)

前端把 clarify 当成一个**工具块**渲染,不是独立气泡:

    static/js/blocks-renderer.js  renderClarify()   问题 + 选项按钮 + 自由输入框
    static/js/history.js          按 input.question / input.choices / 结果里的
                                  answer 字段还原历史里的 clarify 块
    static/css/main.css           .tool-step-clarify / .clarify-option 等样式

所以约定是硬的:

- 工具名必须是 ``clarify``(history.js 按名字分派)
- 参数必须叫 ``question`` / ``choices``(history.js 从 input 里读这两个字段)
- 返回的 JSON 必须有 ``answer`` 字段(history.js 读它显示「已回答:…」)
- 问题下发靠给**当前工具块**补一个 ``clarify: {question, choices}`` 字段
  (block_open 在前端是合并语义,见 blocks-renderer.js 的 upsertBlock)
- 用户作答后前端发 ``clarify_respond {toolCallId, answer, threadId}``,
  其中 toolCallId 就是那个工具块的 blockId

下发与等待都由 turn_channels 里的 ClarifyChannel 实现(它才拿得到 emitter、
事件总线和当前工具块的 blockId);本模块只负责工具定义与「谁在等答案」的登记。
"""
from __future__ import annotations

import asyncio
import json
import os

from ._context import require_turn
from ._registry import registry

#: 等用户作答的上限。超时不算失败 —— 返回一句说明,让模型自己做最佳判断并继续,
#: 而不是把整轮卡死或报错。桌面场景用户通常就在跟前,给足时间但不无限等。
DEFAULT_TIMEOUT_SECONDS = 180.0


def _timeout_seconds() -> float:
    raw = os.getenv("MAFAGENT_CLARIFY_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return val if val > 0 else DEFAULT_TIMEOUT_SECONDS


class ClarifyRegistry:
    """谁在等答案 —— 按工具块 blockId 登记。

    用 asyncio.Future 而不是 threading.Event:等待方(工具)和唤醒方(WS 收到
    clarify_respond)在同一个事件循环的两个 task 里,Future 天然够用,不用线程跳转。
    """

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future[str]] = {}

    def register(self, block_id: str) -> asyncio.Future[str]:
        """登记一个等待者。同一个 blockId 重复登记时,旧的先被取消。"""
        block_id = str(block_id or "")
        old = self._waiters.pop(block_id, None)
        if old is not None and not old.done():
            old.cancel()
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._waiters[block_id] = fut
        return fut

    def resolve(self, block_id: str, answer: str) -> bool:
        """交付答案。返回 False 表示没人在等这个 blockId(重复提交、或已超时)。"""
        fut = self._waiters.pop(str(block_id or ""), None)
        if fut is None or fut.done():
            return False
        fut.set_result(str(answer or ""))
        return True

    def discard(self, block_id: str) -> None:
        """放弃等待(超时/取消)。必须调,否则 dict 会随会话增长。"""
        fut = self._waiters.pop(str(block_id or ""), None)
        if fut is not None and not fut.done():
            fut.cancel()

    def pending_count(self) -> int:
        return len(self._waiters)


#: 进程级单例。ws_chat 收到 clarify_respond 时调 resolve()。
clarify_registry = ClarifyRegistry()
group_clarify_registry = ClarifyRegistry()


DESCRIPTION = "向用户提问"

CLARIFY_DOC = """向用户提问并等待回答。调用会阻塞,直到用户作答或超时;返回值里带着他们的答案。

**只在真的需要人来决定时用。** 能自己查到、能从上下文推断、能试出来的,不要问 ——
问一次就打断用户一次。

该用 clarify 的情况:
- 有两三种做法,选哪个取决于用户的偏好而不是事实(用 A 库还是 B 库、改这个文件还是新建一个)
- 用户的指令有多种合理解读,而选错方向会让接下来的活全白做
- 指令太含糊(「优化一下」「修好它」),而上下文不足以判断他到底想要什么

不该用 clarify 的情况:
- 能通过读文件、跑命令、看目录结构查明的事实 —— 自己去查
- 你只是想让用户确认你的方案 —— 直接做,做完说明你的取舍
- 危险操作前想求个许可 —— 这个项目不拦截命令,该做就做,做之前把风险讲清楚

规则:
- 一次只问一个问题。需要多个决定就问完一个再问下一个,不要把几个问题塞进一句话
- 必须给 2 到 4 个具体选项。用户可以点选项,也可以自己打字回答
- 不要问没有选项的开放式问题 —— 那等于把思考推回给用户
- 超时(默认 180 秒)会返回一句说明,此时你要自己做最佳判断继续做,并在事后
  向用户说明你按什么假设做的"""


@registry.register(
    toolset="ask",
    name="clarify",
    summary="问用户一个带选项的问题并等待回答",
    description=DESCRIPTION,
    doc=CLARIFY_DOC,
)
async def clarify(question: str, choices: list[str]) -> str:
    """问用户一个问题,阻塞等待答案。

    Args:
        question: 要问的问题。一次只问一件事。
        choices: 2 到 4 个具体选项。用户可以选其一,也可以自己打字。
    """
    q = str(question or "").strip()
    if not q:
        return json.dumps({"error": "question 不能为空"}, ensure_ascii=False)

    opts = [s for s in (str(c).strip() for c in (choices or [])) if s][:4]
    if len(opts) < 2:
        return json.dumps(
            {"error": "choices 至少要 2 个具体选项 —— 不要问没有选项的开放式问题"},
            ensure_ascii=False,
        )

    ctx = require_turn()
    channel = getattr(ctx, "clarify", None)
    if channel is None:
        # 没有交互通道(单测直接调、或将来的非 WS 入口)。不报错 —— 让模型知道
        # 没人能回答,自己做判断继续,比抛异常中断整轮好。
        return json.dumps(
            {
                "question": q,
                "choices_offered": opts,
                "answer": "[当前没有可交互的用户,请自行做出最佳判断并继续,事后向用户说明你的假设]",
            },
            ensure_ascii=False,
        )

    answer = await channel.ask(q, opts, timeout=_timeout_seconds())
    return json.dumps(
        {"question": q, "choices_offered": opts, "answer": answer},
        ensure_ascii=False,
    )
