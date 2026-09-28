from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .protocol import InboundMention
from .store import BindingStore, channel_label

_LOG_LIMIT = 800
_LOG_BRIEF = 240


def _brief(value: Any, limit: int = _LOG_BRIEF) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
    else:
        text = json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _now_ms() -> int:
    return int(time.time() * 1000)


class GroupChatMode:
    def __init__(self, store, web_agent, bindings_path: Path, default_model: str) -> None:
        self._store = store
        self._web_agent = web_agent
        self._bindings = BindingStore(bindings_path)
        self._default_model = default_model
        self._adapters: dict[str, Any] = {}
        self._logs: list[dict[str, Any]] = []
        self._seen: set[str] = set()
        self._seen_tool_open: set[str] = set()
        self._text_acc: dict[str, str] = {}
        self._handle_lock = asyncio.Lock()
        self._turn_id = ""
        self._turn_thread = ""
        self._turn_task: asyncio.Task | None = None
        self._cancelled: set[str] = set()
        self._turn_cancelled = False

    @property
    def bindings(self) -> BindingStore:
        return self._bindings

    def register(self, name: str, adapter: Any) -> None:
        channel = str(name or "").strip()
        if not channel:
            raise RuntimeError("通道名不能为空")
        self._adapters[channel] = adapter

    def adapter(self, channel: str) -> Any:
        name = str(channel or "").strip()
        item = self._adapters.get(name)
        if item is None:
            raise RuntimeError("未注册通道: " + name)
        return item

    def log(self, level: str, text: str) -> None:
        self._logs.append({"ts": _now_ms(), "level": level, "text": text})
        if len(self._logs) > _LOG_LIMIT:
            self._logs = self._logs[-_LOG_LIMIT:]

    def model(self) -> str:
        from ..config import resolve_default_chat_model

        return resolve_default_chat_model(self._bindings.get_model() or self._default_model)[0]

    def set_model(self, model: str) -> None:
        from ..config import load_chat_models

        mid = (model or "").strip()
        if not mid:
            raise RuntimeError("模型不能为空")
        ids = {str(item.get("id") or "").strip() for item in load_chat_models()}
        if mid not in ids:
            raise RuntimeError("未知模型: " + mid)
        self._bindings.set_model(mid)
        self.log("info", f"模型已切换为 {mid}")

    def status(self) -> dict[str, Any]:
        channels = {name: item.status() for name, item in self._adapters.items()}
        data = dict(self.adapter("dingtalk").status())
        data.update(
            {
                "model": self.model(),
                "busy": bool(self._turn_id),
                "task_id": self._turn_id,
                "bindings": self._bindings.list_items(),
                "logs": list(self._logs),
                "feishu": self.adapter("feishu").status(),
                "channels": channels,
            }
        )
        return data

    def cancel_turn(self) -> None:
        from ..maf_tools.interrupt import interrupt_task
        from ..toolkit.clarify import group_clarify_registry

        tid = self._turn_id
        if not tid:
            raise RuntimeError("当前没有正在运行的任务")
        self._cancelled.add(tid)
        interrupt_task(tid)
        if self._turn_thread:
            group_clarify_registry.discard(self._turn_thread)
        task = self._turn_task
        if task is not None and not task.done():
            task.cancel()
        self.log("info", "已请求停止当前任务")

    def cancel_if_match(self, task_id: str = "", thread_id: str = "") -> None:
        tid = self._turn_id
        if not tid:
            return
        if task_id and task_id == tid:
            self.cancel_turn()
            return
        if thread_id and thread_id == self._turn_thread:
            self.cancel_turn()

    def is_bound_thread(self, thread_id: str) -> bool:
        return self._bindings.get_by_thread(thread_id) is not None

    def annotate_conversations(self, convs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._bindings.annotate_conversations(convs)

    def deliver_text(self, thread_id: str, text: str) -> None:
        bind = self._bindings.get_by_thread(thread_id)
        if bind is None:
            raise RuntimeError("当前对话没有绑定群")
        cid = str(bind.get("conversation_id") or "").strip()
        if not cid:
            raise RuntimeError("绑定缺少会话ID")
        self.adapter(str(bind["channel"])).send_text(cid, text)

    def take_clarify(self, mention: InboundMention) -> bool:
        from ..toolkit.clarify import group_clarify_registry

        bind = self._bindings.get(mention.conversation_id)
        if bind is None:
            return False
        thread_id = str(bind.get("thread_id") or "")
        if not thread_id:
            return False
        if mention.event_id and mention.event_id in self._seen:
            return False
        if not group_clarify_registry.resolve(thread_id, mention.content or "[无正文]"):
            return False
        if mention.event_id:
            self._seen.add(mention.event_id)
        self.log("info", f"clarify 收到回复 {mention.sender}: {mention.content or '[无正文]'}")
        return True

    async def handle_mention(self, mention: InboundMention) -> None:
        async with self._handle_lock:
            self._turn_task = asyncio.current_task()
            try:
                await self._handle_at(mention)
            finally:
                self._turn_task = None

    async def _handle_at(self, mention: InboundMention) -> None:
        if mention.event_id in self._seen:
            return
        self._seen.add(mention.event_id)
        adapter = self.adapter(mention.channel)
        bind = self._bindings.get(mention.conversation_id)
        if bind is None or not self._store.thread_exists(str(bind.get("thread_id") or "")):
            group_name = await asyncio.to_thread(adapter.group_name, mention.conversation_id)
            thread_id = self._store.create_thread(f"{channel_label(mention.channel)} · {group_name}")
            bind = {
                "thread_id": thread_id,
                "group_name": group_name,
                "channel": mention.channel,
            }
            if mention.identity:
                bind["identity"] = mention.identity
            self._bindings.put(mention.conversation_id, bind)
            self.log("info", f"新建会话 {group_name}")
        else:
            group_name = str(bind.get("group_name") or "")
            if not group_name:
                raise RuntimeError("绑定缺少群名")
            thread_id = str(bind["thread_id"])
            old_ident = str(bind.get("identity") or "").strip()
            if mention.identity and old_ident and old_ident != mention.identity:
                raise RuntimeError("同一群对应多个企业身份")
            if mention.identity and not old_ident:
                bind["identity"] = mention.identity
                self._bindings.put(
                    mention.conversation_id,
                    {
                        "thread_id": thread_id,
                        "group_name": group_name,
                        "channel": mention.channel,
                        "identity": mention.identity,
                    },
                )
        name, sid = await asyncio.to_thread(adapter.resolve_sender, mention)
        if not self._store.agent_speak_on(thread_id):
            owner_name, owner_id = await asyncio.to_thread(adapter.owner_person, mention.identity)
            if name != owner_name or sid != owner_id:
                self.log("info", f"已忽略 @ {group_name} · {name}")
                return
        self.log("info", f"收到@ {group_name} · {name}: {mention.content or '[无正文]'}")
        user_text = f"{name}: {mention.content}" if mention.content else f"{name}: [无正文]"
        await self._run_agent(thread_id, user_text, mention.channel)
        if self._turn_cancelled:
            return
        self.log("info", f"本轮结束 {group_name}")

    def _mirror_turn(self, obj: dict[str, Any]) -> None:
        ev = str(obj.get("type") or "")
        data = obj.get("data")
        if not isinstance(data, dict):
            data = {}
        kind = str(data.get("kind") or "")
        name = str(data.get("name") or "")
        bid = str(data.get("blockId") or "")
        if ev == "block_open":
            if kind == "tool" and name:
                if bid and bid in self._seen_tool_open:
                    inp = data.get("input")
                    if isinstance(inp, dict) and inp:
                        self.log("agent", f"{name} 参数 {_brief(inp)}")
                    return
                if bid:
                    self._seen_tool_open.add(bid)
                self.log("agent", f"调用 {name}")
            elif kind == "thinking":
                self.log("agent", "思考中")
            return
        if ev == "block_delta":
            text = str(data.get("text") or "")
            if text and bid:
                self._text_acc[bid] = self._text_acc.get(bid, "") + text
            return
        if ev == "block_end":
            if kind == "tool":
                status = str(data.get("status") or "")
                dur = data.get("durationMs")
                extra = ""
                if isinstance(dur, (int, float)):
                    extra = f" {dur / 1000:.1f}s"
                if status == "error":
                    self.log("error", f"{name} 失败{extra} {_brief(data.get('result'))}")
                else:
                    result = _brief(data.get("result"))
                    line = f"{name} 完成{extra}"
                    if result:
                        line += f" {result}"
                    self.log("agent", line)
                return
            if kind in ("text", "notice"):
                text = self._text_acc.pop(bid, "").strip() if bid else ""
                if text:
                    self.log("agent", text)
                return
            if bid:
                self._text_acc.pop(bid, None)
            return
        if ev == "stream_error":
            self.log("error", str(data.get("errorMessage") or "stream_error"))

    async def _emit_and_log(self, obj: dict[str, Any]) -> None:
        from ..live_conns import broadcast

        self._mirror_turn(obj)
        await broadcast(obj)

    async def inject_user_message(self, thread_id: str, content: str) -> str:
        bind = self._bindings.get_by_thread(thread_id)
        if bind is None:
            raise RuntimeError("当前对话没有绑定群")
        channel = str(bind.get("channel") or "").strip()
        if not channel:
            raise RuntimeError("绑定缺少通道")
        return await self._run_agent(thread_id, content, channel)

    async def _run_agent(self, thread_id: str, content: str, channel: str) -> str:
        if not self._store.thread_exists(thread_id):
            raise RuntimeError(f"会话不存在: {thread_id}")
        name = str(channel or "").strip()
        if not name:
            raise RuntimeError("通道名不能为空")
        task_id = name + "_" + uuid.uuid4().hex[:12]
        self._seen_tool_open.clear()
        self._text_acc.clear()
        self._turn_id = task_id
        self._turn_thread = thread_id
        self._turn_cancelled = False
        from ..ws_chat import mark_thread_task, unmark_thread_task
        from ..message_runner import run_user_message
        from ..stream_adapter import TurnEmitter

        mark_thread_task(thread_id, task_id)
        try:
            if not str(self._store.load_session_ui(thread_id).get("agentProfile") or "").strip():
                self._store.apply_selected_chat_model(thread_id, self.model())
            emitter = TurnEmitter(self._emit_and_log, task_id=task_id, thread_id=thread_id)
            reply = await run_user_message(
                self._web_agent,
                self._store,
                thread_id,
                content=content,
                emitter=emitter,
                group_chat=True,
                is_cancelled=lambda: task_id in self._cancelled,
            )
            if task_id in self._cancelled:
                self._turn_cancelled = True
                return ""
            return reply
        except asyncio.CancelledError:
            self._turn_cancelled = True
            return ""
        finally:
            unmark_thread_task(thread_id, task_id)
            self._cancelled.discard(task_id)
            if self._turn_id == task_id:
                self._turn_id = ""
                self._turn_thread = ""
