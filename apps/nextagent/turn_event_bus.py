"""TurnEvent —— 回合内的一切(文本增量、工具调用/结果、错误)的统一事件模型。

和 TurnEmitter 的关系:
- TurnEmitter 是把事件**推送**给前端(WebSocket 帧);一条帧对应一次 send。
- TurnEventBus 是把事件**记在**内存里(ring buffer),供断线补发(turn_resume)
  和快照(tasks_snapshot)使用。
- 两者用同一份事件流,bus.append(event) 的同时也 emitter.turn(event.ws_type(), data)。

为什么需要 bus:刷新页面后 WS 重连,如果不做 turn_resume,前端就剩一个空壳——
标题栏还在、对话气泡全没了。turn_resume 就是「你现在应该见过哪些事件」的重放
协议,而 BusStreamBridge 靠 _EventLog._apply_block 维护一份块级累积快照,
让重放时前端能一次拿到完整块列表,不用从 delta 开始回放。

(从 hermes 抄来,去掉了和 task_store / snapshot 持久化 / media 相关的内容,
因为 mafagent 还没有 task_store 和 media engine,这些是后续增量。)
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventKind(StrEnum):
    STREAM_START = "stream_start"
    TURN_START = "turn_start"
    BLOCK_OPEN = "block_open"
    BLOCK_DELTA = "block_delta"
    BLOCK_END = "block_end"
    BLOCK_PATCH = "block_patch"
    BLOCK_REMOVE = "block_remove"
    TURN_COMPLETE = "turn_complete"
    STREAM_ERROR = "stream_error"
    FS_CHANGED = "fs_changed"


BLOCK_TEXT = "text"
BLOCK_THINKING = "thinking"
BLOCK_TOOL = "tool"

RING_MAX = 8000


@dataclass
class TurnEvent:
    """一个回合内的事件。seq 是单调序号,前端靠它去重/排序。"""
    kind: EventKind
    task_id: str
    thread_id: str
    message_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    ts: int = 0

    def ws_type(self) -> str:
        return str(self.kind)

    def to_ws_data(self) -> dict[str, Any]:
        out = dict(self.data)
        out.setdefault("taskId", self.task_id)
        out.setdefault("threadId", self.thread_id)
        return out


@dataclass
class EventLogSnapshot:
    """_EventLog 的快照,发给前端做 turn_resume。"""
    task_id: str
    thread_id: str
    last_seq: int
    content: str
    reasoning: str
    status: str = "running"
    blocks: list[dict] = field(default_factory=list)


@dataclass
class _EventLog:
    """一个 task 的事件日志 + 累积块状态。"""
    task_id: str
    thread_id: str
    user_id: str
    message_id: str
    events: deque[tuple[int, TurnEvent]] = field(default_factory=deque)
    last_seq: int = 0
    min_seq: int = 0
    content: str = ""
    reasoning: str = ""
    blocks: dict[str, dict] = field(default_factory=dict)
    finished_at: float = 0.0
    waiters: list[asyncio.Event] = field(default_factory=list)

    def _trim(self) -> None:
        while len(self.events) > RING_MAX:
            old = self.events.popleft()
            self.min_seq = old[0] + 1

    def _apply_block(self, event: TurnEvent) -> None:
        d = event.data
        kind = event.kind
        if kind == EventKind.BLOCK_OPEN:
            bid = str(d.get("blockId") or "")
            if not bid:
                return
            # block_open 会发两次(调用出现时 input 还空,结果到达时补上解析好的 input,
            # 见 ToolTracker 的说明)。所以这里必须是**合并**语义 —— 直接重建会把
            # 第一次 open 之后 patch 进来的 clarify / media 擦掉,刷新后问题和图就没了。
            blk = self.blocks.get(bid)
            if blk is None:
                blk = {
                    "blockId": bid,
                    "kind": str(d.get("kind") or ""),
                    "name": str(d.get("name") or ""),
                    "input": d.get("input") or {},
                    "text": "",
                    "result": "",
                    "status": "open",
                }
                if d.get("extra_content"):
                    blk["extra_content"] = d["extra_content"]
                self.blocks[bid] = blk
                return
            if d.get("kind"):
                blk["kind"] = str(d["kind"])
            if d.get("name"):
                blk["name"] = str(d["name"])
            if d.get("input"):
                blk["input"] = {**(blk.get("input") or {}), **(d["input"] or {})}
            if d.get("extra_content"):
                blk["extra_content"] = d["extra_content"]
        elif kind == EventKind.BLOCK_DELTA:
            blk = self.blocks.get(str(d.get("blockId") or ""))
            if blk is None:
                return
            text = str(d.get("text") or "")
            if str(d.get("target") or "text") == "result":
                blk["result"] += text
            else:
                blk["text"] += text
                if blk.get("kind") == BLOCK_THINKING:
                    self.reasoning += text
                else:
                    self.content += text
        elif kind == EventKind.BLOCK_END:
            blk = self.blocks.get(str(d.get("blockId") or ""))
            if blk is None:
                return
            blk["status"] = str(d.get("status") or "done")
            if d.get("result") is not None:
                blk["result"] = str(d.get("result"))
            if d.get("content") is not None:
                blk["text"] = str(d.get("content"))
            if d.get("durationMs") is not None:
                blk["durationMs"] = d.get("durationMs")
            if d.get("completedAt") is not None:
                blk["completedAt"] = d.get("completedAt")
            if d.get("media"):
                # 媒体载荷必须进快照:刷新 / 重连 / 落盘后的历史都靠它把图渲回来
                blk["media"] = {**(blk.get("media") or {}), **dict(d["media"])}
        elif kind == EventKind.BLOCK_REMOVE:
            # 撤块(发言人一句话没说就交接了):快照里也得去掉,
            # 否则刷新后那个空气泡又冒出来
            self.blocks.pop(str(d.get("blockId") or ""), None)
        elif kind == EventKind.BLOCK_PATCH:
            blk = self.blocks.get(str(d.get("blockId") or ""))
            if blk is None:
                return
            if "status" in d:
                blk["status"] = str(d.get("status") or blk.get("status"))
            if d.get("clarify"):
                blk["clarify"] = dict(d["clarify"])
            if d.get("media"):
                # 媒体载荷必须进快照:刷新 / 重连 / 落盘后的历史都靠它把图渲回来
                blk["media"] = {**(blk.get("media") or {}), **dict(d["media"])}

    def append(self, event: TurnEvent) -> int:
        self.last_seq += 1
        event.seq = self.last_seq
        event.ts = int(time.time() * 1000)
        self._apply_block(event)
        self.events.append((self.last_seq, event))
        self._trim()
        for w in self.waiters:
            w.set()
        self.waiters.clear()
        return self.last_seq

    def events_after(self, after_seq: int) -> list[TurnEvent]:
        out: list[TurnEvent] = []
        for seq, ev in self.events:
            if seq > after_seq:
                out.append(ev)
        return out

    def snapshot(self) -> EventLogSnapshot:
        return EventLogSnapshot(
            task_id=self.task_id,
            thread_id=self.thread_id,
            last_seq=self.last_seq,
            content=self.content,
            reasoning=self.reasoning,
            blocks=[dict(b) for b in self.blocks.values()],
        )

    def has_gap(self, client_seq: int) -> bool:
        if client_seq <= 0:
            return False
        if not self.events:
            return False
        return client_seq < self.min_seq


class TurnEventBus:
    """进程内事件总线:每个 task_id 一个 _EventLog。"""

    def __init__(self) -> None:
        self._logs: dict[str, _EventLog] = {}
        # 用 asyncio 锁,因为所有操作都在同一个 event loop 里且 await 穿插
        self._lock = asyncio.Lock()

    async def open(
        self,
        task_id: str,
        *,
        thread_id: str,
        user_id: str = "",
        message_id: str = "",
    ) -> _EventLog:
        async with self._lock:
            log = _EventLog(
                task_id=task_id,
                thread_id=thread_id,
                user_id=user_id,
                message_id=message_id,
            )
            self._logs[task_id] = log
            return log

    async def get(self, task_id: str) -> _EventLog | None:
        async with self._lock:
            return self._logs.get(task_id)

    async def append(self, event: TurnEvent) -> int:
        async with self._lock:
            log = self._logs.get(event.task_id)
            if log is None:
                raise KeyError(f"event bus log missing: {event.task_id}")
            return log.append(event)

    async def close(self, task_id: str) -> None:
        async with self._lock:
            log = self._logs.get(task_id)
            if log is not None:
                log.finished_at = time.monotonic()

    async def discard(self, task_id: str) -> None:
        async with self._lock:
            self._logs.pop(task_id, None)

    async def cleanup_finished(self, ttl_seconds: float = 180.0) -> int:
        now = time.monotonic()
        removed = 0
        async with self._lock:
            stale = [
                tid
                for tid, log in self._logs.items()
                if log.finished_at > 0 and (now - log.finished_at) >= ttl_seconds
            ]
            for tid in stale:
                self._logs.pop(tid, None)
                removed += 1
        return removed

    async def wait_for_seq(self, task_id: str, after_seq: int, timeout: float = 30.0) -> list[TurnEvent]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            async with self._lock:
                log = self._logs.get(task_id)
                if log is None:
                    return []
                batch = log.events_after(after_seq)
                if batch:
                    return batch
                ev = asyncio.Event()
                log.waiters.append(ev)
            try:
                await asyncio.wait_for(ev.wait(), timeout=min(1.0, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                pass
        async with self._lock:
            log = self._logs.get(task_id)
            if log is None:
                return []
            return log.events_after(after_seq)

    async def snapshot(self, task_id: str) -> EventLogSnapshot | None:
        async with self._lock:
            log = self._logs.get(task_id)
            if log is None:
                return None
            return log.snapshot()

    async def is_active(self, task_id: str) -> bool:
        """返回 True 表示这个日志仍在接收事件(finished_at == 0)。"""
        async with self._lock:
            log = self._logs.get(task_id)
            if log is None:
                return False
            return log.finished_at == 0.0

    @property
    def inflight_task_ids(self) -> list[str]:
        # 无锁快照:python 的 dict keys view 在遍历时是安全的
        return list(self._logs.keys())

    @property
    def active_task_ids(self) -> list[str]:
        """只返回正在运行的任务(还没 close 的)。"""
        return [tid for tid, log in self._logs.items() if log.finished_at == 0.0]


# 进程级单例。工具和 stream_adapter 都通过它存取事件。
bus = TurnEventBus()
