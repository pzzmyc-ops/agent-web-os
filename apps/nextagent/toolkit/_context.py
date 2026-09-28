"""每一轮对话的上下文,供工具读取。

为什么需要它:工具函数在服务端执行,但有些动作(向用户提问、等用户审阅计划)必须回到
**那一个用户的**连接上。工具被调用时没有任何参数告诉它「我是哪个会话」,所以要有一个
隐式的当前轮上下文。

用 ContextVar 而不是全局变量:一个进程里同时可能有多个会话在跑(每个 WS 连接一个
asyncio 任务),ContextVar 天然按任务隔离。

绑定点在 turn_lifecycle.TurnRun —— 它是唯一知道 thread_id / task_id / 这条连接
怎么发消息的地方。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Protocol


class ClarifyChannel(Protocol):
    """服务端 → 用户的提问通道(clarify 工具用)。

    实现方(turn_channels)负责:给当前工具块补上 clarify 载荷让前端渲染出问题、
    登记等待、收到 clarify_respond 后返回答案、超时则返回一句说明而不是抛错。
    """

    async def ask(self, question: str, choices: list[str], *, timeout: float) -> str:
        """问用户一个问题并等待。返回用户的答案(超时返回一句说明性文本)。"""
        ...


class PlanReviewChannel(Protocol):
    async def ask(self, plan: str, todos: list) -> dict:
        ...


@dataclass
class TurnContext:
    """当前这一轮的身份与能力句柄。"""

    thread_id: str
    task_id: str
    #: 提问通道。为 None 表示这一轮没有能回答问题的用户(单测、非 WS 入口)。
    clarify: ClarifyChannel | None = None
    plan_review: PlanReviewChannel | None = None
    store: Any = None
    plan_status: dict | None = None
    emit_plan_status: Any = None
    group_complete: dict | None = None
    file_delete_count: int = 0
    file_delete_warns: int = 0
    grep_cache: dict | None = None


_current: ContextVar[TurnContext | None] = ContextVar("toolkit_turn_context", default=None)


def current_turn() -> TurnContext | None:
    """当前轮上下文;不在一轮对话里(例如单测直接调工具)时返回 None。"""
    return _current.get()


def require_turn() -> TurnContext:
    """拿当前轮上下文,拿不到就抛 —— 需要会话身份的工具用这个。"""
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("当前不在一轮对话里,无法确定目标会话")
    return ctx


@contextmanager
def bind_turn(ctx: TurnContext) -> Iterator[TurnContext]:
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


def set_turn(ctx: TurnContext) -> Any:
    """绑定当前轮上下文,返回 token。给已经有 try/finally 结构的调用方用
    (run_turn 的主体上百行,不适合再套一层 with)。必须在 finally 里 reset_turn。"""
    return _current.set(ctx)


def reset_turn(token: Any) -> None:
    try:
        _current.reset(token)
    except ValueError:
        pass  # 跨任务 reset 会抛,这种情况直接忽略即可
