"""前端「块协议」的载波层。

只负责两件事:把回合事件包成前端认识的信封发出去并记进事件总线(TurnEmitter),
以及维护文本块 / 思考块 / 发言人块的开合与分段落盘(TextBlocks / SpeakerBlocks)。
这里不知道模型、工具、模式,任何业务分支都不该出现在这个文件里。

前端契约(逐字段已探明):
- 控制类事件(message_accepted)用无 seq 的简单信封;
- 回合流事件(stream_start/turn_start/block_open/block_delta/block_end/turn_complete)
  用带 seq 的信封,seq 每任务单调递增,前端按 seq 去重;
- 块靠 blockId 关联,文本块 blockId="text:<taskId>:<n>"、思考块 "thinking:<taskId>:<n>"、
  工具块 "tool:<taskId>:<n>";block_delta.data={blockId, target:"text"|"result", text}。
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from . import session_log
from .identity import current_user
from .turn_event_bus import EventKind, TurnEvent, bus as event_bus

SendFn = Callable[[dict], Awaitable[None]]

SEGMENT_PERSIST_CHARS = 500


def now_ms() -> int:
    return int(time.time() * 1000)


def turn_frame(event: TurnEvent, *, status: str = "streaming", user: str = "") -> dict[str, Any]:
    """把总线里的一条事件包成前端的回合信封。

    实时推送和断线续传走同一个函数,seq 就是总线序号 —— 前端按 seq 去重,两条路
    发出去的同一事件序号必须一致,否则续传补回来的帧会被当成重复丢掉或重复渲染。
    """
    payload = event.to_ws_data()
    return {
        "v": 1,
        "type": event.ws_type(),
        "messageId": "",
        "timestamp": event.ts or now_ms(),
        "status": status,
        "topic": "chat",
        "user": user or current_user(),
        "seq": event.seq,
        "taskId": payload["taskId"],
        "threadId": payload["threadId"],
        "data": payload,
    }


class TurnEmitter:
    def __init__(self, send: SendFn, *, task_id: str, thread_id: str):
        self._send = send
        self.task_id = task_id
        self.thread_id = thread_id
        self._user = current_user()
        self._chars_since_persist = 0

    def segment_due(self) -> bool:
        if self._chars_since_persist < SEGMENT_PERSIST_CHARS:
            return False
        self._chars_since_persist = 0
        return True

    async def turn(self, ev_type: str, data: dict[str, Any], *, status: str = "streaming") -> None:
        """推一条回合事件给前端,同时记进事件总线。

        两件事必须在同一个出口做。以前只有工具事件进总线,文本块和思考块不进 —— 结果
        断线重连时 turn_resume 只有工具卡,正在流的那段文字整段消失。走同一个出口就
        不存在「这个事件忘了记」的可能。
        """
        payload = dict(data)
        payload.setdefault("taskId", self.task_id)
        payload.setdefault("threadId", self.thread_id)
        if ev_type == "block_delta":
            self._chars_since_persist += len(str(payload.get("text") or ""))
        event = TurnEvent(
            kind=EventKind(ev_type),
            task_id=self.task_id,
            thread_id=self.thread_id,
            data=dict(payload),
        )
        await event_bus.append(event)
        await self._send(turn_frame(event, status=status, user=self._user))

    async def control(self, ev_type: str, data: dict[str, Any], *, status: str = "ok") -> None:
        await self._send(
            {
                "type": ev_type,
                "messageId": "",
                "timestamp": now_ms(),
                "status": status,
                "topic": "chat",
                "user": self._user,
                "data": data,
            }
        )


class TextBlocks:
    def __init__(self, emitter: TurnEmitter, store: Any, *, task_id: str, thread_id: str):
        self._em = emitter
        self._store = store
        self._task_id = task_id
        self._thread_id = thread_id
        self.full_text = ""
        self.full_reason = ""
        self._seg_text = ""
        self._seg_reason = ""
        self._text_open = False
        self._think_open = False
        self._text_seq = 1
        self._think_seq = 1
        self._notice_seq = 0
        self._text_bid = f"text:{task_id}:{self._text_seq}"
        self._think_bid = f"thinking:{task_id}:{self._think_seq}"

    @property
    def final_text(self) -> str:
        return self.full_text

    @property
    def final_reason(self) -> str:
        return self.full_reason

    async def _ensure_text(self) -> None:
        if not self._text_open:
            self._text_seq += 1
            self._text_bid = f"text:{self._task_id}:{self._text_seq}"
            await self._em.turn("block_open", {"blockId": self._text_bid, "kind": "text"})
            self._text_open = True

    async def _ensure_think(self) -> None:
        if not self._think_open:
            self._think_seq += 1
            self._think_bid = f"thinking:{self._task_id}:{self._think_seq}"
            await self._em.turn("block_open", {"blockId": self._think_bid, "kind": "thinking"})
            self._think_open = True

    async def text(self, delta: str) -> None:
        await self._ensure_text()
        self.full_text += delta
        self._seg_text += delta
        await self._em.turn("block_delta", {"blockId": self._text_bid, "target": "text", "text": delta})

    async def reasoning(self, delta: str) -> None:
        await self._ensure_think()
        self.full_reason += delta
        self._seg_reason += delta
        await self._em.turn("block_delta", {"blockId": self._think_bid, "target": "text", "text": delta})

    async def close_text(self) -> None:
        if self._text_open:
            await self._em.turn("block_end", {"blockId": self._text_bid, "status": "done", "kind": "text"})
            self._text_open = False

    async def close_think(self) -> None:
        if self._think_open:
            await self._em.turn("block_end", {"blockId": self._think_bid, "status": "done", "kind": "thinking"})
            self._think_open = False

    async def persist_segment(self) -> None:
        """把已累积的这一段写成一条 assistant_message 事件。空段不写。

        不再合并回上一条:合并需要重写已经落盘的内容,而事件日志只追加。相邻的同一
        发言人分段由投影时合并(session_log.logical_records),历史里看到的还是一个气泡。
        """
        if not self._seg_text.strip() and not self._seg_reason.strip():
            return
        self._store.append_event(
            self._thread_id,
            session_log.EV_ASSISTANT,
            task_id=self._task_id,
            content=self._seg_text,
            reasoning=self._seg_reason,
            name="",
        )
        self._seg_text = ""
        self._seg_reason = ""

    async def split_segment(self) -> None:
        """工具调用打断发言:关掉显示块,并把已说的部分单独落库。"""
        await self.close_text()
        await self.close_think()
        await self.persist_segment()

    async def notice(self, text: str) -> None:
        """居中灰色小字(系统提示)。先把已说的话收段落盘,提示才排在它后面。"""
        await self.split_segment()
        self._notice_seq += 1
        bid = f"notice:{self._task_id}:{self._notice_seq}"
        await self._em.turn("block_open", {"blockId": bid, "kind": "notice"})
        await self._em.turn("block_delta", {"blockId": bid, "target": "text", "text": text})
        await self._em.turn("block_end", {"blockId": bid, "status": "done", "kind": "notice"})
        self._store.append_event(self._thread_id, session_log.EV_SYSTEM, task_id=self._task_id, content=text)

    async def step_done(self) -> None:
        await self.close_think()
        await self.persist_segment()

    async def maybe_persist_due(self, has_open_calls: bool) -> None:
        if not has_open_calls and self._em.segment_due():
            await self.persist_segment()

    async def finalize(self) -> None:
        await self.close_think()
        await self.close_text()
        await self.persist_segment()


class SpeakerBlocks:
    def __init__(self, emitter: TurnEmitter, store: Any, *, task_id: str, thread_id: str):
        self._em = emitter
        self._store = store
        self._task_id = task_id
        self._thread_id = thread_id
        self._block_seq = 0
        self.cur_key = ""
        self._cur_block = ""
        self._cur_text = ""
        self._cur_display = ""

    @property
    def final_text(self) -> str:
        return ""

    @property
    def final_reason(self) -> str:
        return ""

    @property
    def has_block(self) -> bool:
        return bool(self._cur_block)

    async def open_block(self) -> None:
        """给当前发言人开一个新的文本块(工具调用后继续发言时,续块排在工具块下面)。"""
        self._block_seq += 1
        self._cur_block = f"text:{self._task_id}:{self._block_seq}"
        await self._em.turn("block_open", {"blockId": self._cur_block, "kind": "text", "name": self._cur_display})

    async def close_block(self) -> None:
        """只关显示块,不清发言人状态 —— 工具调用出现时调用,文字恢复时开续块。"""
        if self._cur_block:
            await self._em.turn(
                "block_end",
                {"blockId": self._cur_block, "status": "done", "kind": "text", "name": self._cur_display},
            )
            self._cur_block = ""

    async def persist_segment(self) -> None:
        """把已累积的文字写成一条 assistant_message 事件,清空累积但保留发言人身份。

        工具会把一个人的发言切成几段(说一段 → 调工具 → 接着说)。每段在被切开的当下
        单独落一条事件,顺序信息就全在日志里了。相邻的同一发言人分段由投影时合并
        (session_log.logical_records 按 name 判定),历史里看到的还是一个气泡,不会出现
        自续空转留下的「待命」碎片。
        """
        if not self._cur_text.strip():
            return
        self._store.append_event(
            self._thread_id,
            session_log.EV_ASSISTANT,
            task_id=self._task_id,
            content=self._cur_text,
            reasoning="",
            name=self._cur_display,
        )
        self._cur_text = ""

    async def split_segment(self) -> None:
        """工具调用打断发言:关掉显示块并把已说的部分落盘,发言人保持不变。"""
        await self.close_block()
        await self.persist_segment()

    async def flush(self) -> None:
        """结束当前发言人:关掉还开着的显示块,把剩下的话落盘(空发言不落、不留空气泡)。"""
        if self._cur_block:
            if self._cur_text.strip():
                await self._em.turn(
                    "block_end",
                    {"blockId": self._cur_block, "status": "done", "kind": "text", "name": self._cur_display},
                )
            else:
                await self._em.turn("block_remove", {"blockId": self._cur_block})
            self._cur_block = ""
        await self.persist_segment()
        self.cur_key = ""
        self._cur_text = ""
        self._cur_display = ""

    async def start(self, key: str, display: str) -> None:
        self.cur_key = key
        self._cur_text = ""
        self._cur_display = display
        await self.open_block()

    async def text(self, delta: str) -> None:
        if not self._cur_block:
            await self.open_block()
        self._cur_text += delta
        await self._em.turn("block_delta", {"blockId": self._cur_block, "target": "text", "text": delta})

    async def maybe_persist_due(self, has_open_calls: bool) -> None:
        if not has_open_calls and self._em.segment_due():
            await self.persist_segment()

    async def notice(self, text: str) -> None:
        """居中灰色小字(交接提示 / 系统提示),同样落盘,刷新后还在。"""
        self._block_seq += 1
        bid = f"notice:{self._task_id}:{self._block_seq}"
        await self._em.turn("block_open", {"blockId": bid, "kind": "notice"})
        await self._em.turn("block_delta", {"blockId": bid, "target": "text", "text": text})
        await self._em.turn("block_end", {"blockId": bid, "status": "done", "kind": "notice"})
        self._store.append_event(self._thread_id, session_log.EV_SYSTEM, task_id=self._task_id, content=text)

    async def finalize(self) -> None:
        await self.flush()
