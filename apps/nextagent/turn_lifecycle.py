"""一轮对话的生命周期骨架:开总线、绑上下文、发受理与开始、收尾落盘、发 turn_complete。

单聊(run_turn)与多 agent(run_handoff_turn)共用这一套,各自只保留「怎么驱动 agent、
把 update 交给 ToolTracker 和块层」那一小段。收尾顺序在 __aexit__ 里固定下来:
先跑调用方登记的关闭钩子,再收未完成的工具块,再收文字块并落盘,然后解绑上下文,
最后写 turn_end、上报上下文水位、发 turn_complete、关总线。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Protocol

from . import session_log
from .llm import DSML_RECOVERED_KEY
from .toolkit import TurnContext, reset_turn, set_turn
from .tool_tracker import ToolTracker
from .turn_channels import ClarifyChannel
from .turn_event_bus import bus as event_bus
from .turn_protocol import TurnEmitter


class BlockLayer(Protocol):
    final_text: str
    final_reason: str

    async def split_segment(self) -> None: ...
    async def persist_segment(self) -> None: ...
    async def notice(self, text: str) -> None: ...
    async def finalize(self) -> None: ...


class TurnPolicyLike(Protocol):
    group_chat: bool
    plan: Any
    group_complete: dict | None


def iter_content_deltas(update: Any) -> tuple[str, str]:
    """从一个流式 update 中提取 (assistant文本增量, reasoning增量)。

    优先按 content.type 区分 text / text_reasoning;拿不到 contents 时退回 update.text。
    """
    text_delta = ""
    reason_delta = ""
    contents = getattr(update, "contents", None)
    if contents:
        for c in contents:
            ctype = getattr(c, "type", None)
            ctext = getattr(c, "text", None) or ""
            if not ctext:
                continue
            if ctype == "text_reasoning":
                reason_delta += ctext
            elif ctype == "text":
                text_delta += ctext
    if not text_delta and not reason_delta:
        text_delta = getattr(update, "text", None) or ""
    return text_delta, reason_delta


def usage_input_tokens(update: Any) -> int:
    """从一个流式 update 里取上游回报的输入 token 数,没有就是 0。

    一轮里工具循环会打好几次请求,每次回一份 usage。取最大的那次 —— 那就是这一轮
    提示词涨到的峰值,三段压缩的触发线按它判,不用本地估算去猜。
    """
    peak = 0
    for c in getattr(update, "contents", None) or []:
        if getattr(c, "type", None) != "usage":
            continue
        details = getattr(c, "usage_details", None) or {}
        count = details.get("input_token_count")
        if count:
            peak = max(peak, int(count))
    return peak


async def maybe_compact(web_agent, emitter: TurnEmitter, store, thread_id: str, task_id: str, model: str) -> bool:
    """开跑之前先看上下文有没有到线,到了就压一次。返回这轮有没有压过。"""
    from . import compaction

    if not compaction.should_compact(store, thread_id, web_agent.default_window):
        return False
    bid = f"notice:{task_id}:compact"
    await emitter.turn("block_open", {"blockId": bid, "kind": "notice"})
    await emitter.turn("block_delta", {"blockId": bid, "target": "text", "text": "上下文到线,正在压缩…"})
    stat = await compaction.compact_thread(web_agent, thread_id, model)
    await emitter.turn("block_delta", {"blockId": bid, "target": "text", "text": (
        f"\n已压缩:{stat['before']} 条 → {stat['after']} 条。"
        f"前段 {stat['head']} 条总结成一段文字、"
        f"中段 {stat['middle']} 条摊平成 {stat['middleAfter']} 条记录、"
        f"后段 {stat['tail']} 条原样保留"
    )})
    await emitter.turn("block_end", {"blockId": bid, "status": "done", "kind": "notice"})
    return True


class TurnRun:
    def __init__(
        self,
        web_agent,
        emitter: TurnEmitter,
        *,
        content: str,
        message_id: str,
        policy: TurnPolicyLike,
        blocks: BlockLayer,
        is_agent_recall: bool = False,
    ):
        self.web_agent = web_agent
        self.emitter = emitter
        self.store = web_agent.store
        self.task_id = emitter.task_id
        self.thread_id = emitter.thread_id
        self.content = content
        self.message_id = message_id
        self.policy = policy
        self.blocks = blocks
        self.is_agent_recall = is_agent_recall
        self.tracker = ToolTracker(
            emitter,
            task_id=self.task_id,
            thread_id=self.thread_id,
            store=self.store,
            message_id=message_id,
            on_split=blocks.split_segment,
            on_persist=blocks.persist_segment,
        )
        self.interrupted = False
        self.error_msg = ""
        self.compacted = False
        self.peak_input = 0
        self._closers: list[Callable[[], Awaitable[None]]] = []
        self._ctx_token: Any = None
        self._sk_token: Any = None

    def add_closer(self, fn: Callable[[], Awaitable[None]]) -> None:
        self._closers.append(fn)

    def cancelled_by(self, is_cancelled: Callable[[], bool] | None) -> bool:
        if is_cancelled and is_cancelled():
            self.interrupted = True
            return True
        return False

    async def consume(self, update: Any) -> None:
        recovered = (getattr(update, "additional_properties", None) or {}).get(DSML_RECOVERED_KEY)
        if recovered:
            await self.blocks.notice(f"模型以明文输出了 {recovered} 个工具调用(DSML 泄漏),已自动还原执行")
        for c in getattr(update, "contents", None) or []:
            await self.tracker.on_content(c)
        self.peak_input = max(self.peak_input, usage_input_tokens(update))

    async def compact_if_due(self, model: str) -> None:
        self.compacted = await maybe_compact(
            self.web_agent, self.emitter, self.store, self.thread_id, self.task_id, model
        )

    async def __aenter__(self) -> "TurnRun":
        await event_bus.open(self.task_id, thread_id=self.thread_id, message_id=self.message_id)
        plan = self.policy.plan
        plan_fields = plan.context_fields(self.tracker, self.emitter) if plan is not None else {}
        self._ctx_token = set_turn(TurnContext(
            thread_id=self.thread_id,
            task_id=self.task_id,
            clarify=ClarifyChannel(self.tracker, group_chat=self.policy.group_chat),
            store=self.store,
            group_complete=self.policy.group_complete,
            **plan_fields,
        ))
        from .maf_tools.session_key import set_current_session_key

        self._sk_token = set_current_session_key(self.thread_id)
        await self.emitter.control(
            "message_accepted",
            {
                "taskId": self.task_id,
                "threadId": self.thread_id,
                "messageId": self.message_id,
                "eventSeq": 0,
                "status": "queued",
            },
            status="queued",
        )
        await self.emitter.turn(
            "stream_start",
            {
                "taskType": "chat",
                "content": self.content,
                "isAgentRecall": self.is_agent_recall,
                "recallSource": "",
            },
        )
        await self.emitter.turn("turn_start", {})
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None and issubclass(exc_type, asyncio.CancelledError):
            self.interrupted = True
        elif exc_type is not None and issubclass(exc_type, Exception):
            self.error_msg = f"{exc_type.__name__}: {exc}"
        from . import compaction
        from .maf_tools.session_key import reset_current_session_key

        for fn in self._closers:
            await fn()
        await self.tracker.close_open()
        await self.blocks.finalize()
        reset_turn(self._ctx_token)
        reset_current_session_key(self._sk_token)
        if self.error_msg:
            self.store.append_event(
                self.thread_id,
                session_log.EV_SYSTEM,
                task_id=self.task_id,
                content=f"出错:{self.error_msg}",
            )
            await self.emitter.turn("stream_error", {"errorMessage": self.error_msg})
        self.store.append_event(
            self.thread_id,
            session_log.EV_TURN_END,
            task_id=self.task_id,
            interrupted=self.interrupted,
            error=self.error_msg,
        )
        if self.peak_input > 0:
            self.store.save_ctx_state(self.thread_id, used=self.peak_input)
        usage = compaction.usage_snapshot(self.store, self.thread_id, self.web_agent.default_window)
        await self.emitter.turn(
            "turn_complete",
            {
                "content": self.blocks.final_text,
                "thinking": self.blocks.final_reason,
                "compressed": self.compacted,
                "compressionCount": usage["compression_count"],
                "interrupted": self.interrupted,
            },
            status="ok",
        )
        await event_bus.close(self.task_id)
        return exc_type is not None and issubclass(exc_type, Exception)
