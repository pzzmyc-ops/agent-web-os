from __future__ import annotations

import asyncio
import uuid
from typing import Callable

from . import session_log
from .recall_runner import _lock_for
from .stream_adapter import TurnEmitter, run_turn


def _last_event_id(store, thread_id: str) -> int:
    events = store.load_events(thread_id)
    if not events:
        return 0
    return int(events[-1].get("id") or 0)


def _assistant_text(store, thread_id: str, after_id: int) -> str:
    last = ""
    for event in store.load_events(thread_id):
        if int(event.get("id") or 0) <= after_id:
            continue
        if event.get("type") != session_log.EV_ASSISTANT:
            continue
        text = str(event.get("content") or "").strip()
        if text:
            last = text
    return last


async def run_user_message(
    web_agent,
    store,
    thread_id: str,
    *,
    content: str,
    emitter: TurnEmitter,
    group_chat: bool = False,
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    if not thread_id or not store.thread_exists(thread_id):
        raise RuntimeError(f"会话不存在: {thread_id}")
    async with _lock_for(thread_id):
        after_id = _last_event_id(store, thread_id)
        await run_turn(
            web_agent,
            emitter,
            content=content,
            message_id="",
            model=store.selected_chat_model(thread_id),
            media_passthrough=store.media_passthrough_on(thread_id),
            skip_user_save=False,
            is_agent_recall=False,
            group_chat=group_chat,
            is_cancelled=is_cancelled,
        )
        return _assistant_text(store, thread_id, after_id)


async def _wakeup_web(web_agent, store, thread_id: str, content: str, task_id: str) -> None:
    from .live_conns import broadcast
    from .ws_chat import clear_cancelled_task, task_is_cancelled, unmark_thread_task, unregister_wakeup_task

    try:
        emitter = TurnEmitter(broadcast, task_id=task_id, thread_id=thread_id)
        await run_user_message(
            web_agent,
            store,
            thread_id,
            content=content,
            emitter=emitter,
            is_cancelled=lambda: task_is_cancelled(task_id),
        )
    finally:
        clear_cancelled_task(task_id)
        unregister_wakeup_task(task_id)
        unmark_thread_task(thread_id, task_id)


async def _wakeup_group(thread_id: str, content: str, task_id: str) -> None:
    from .groupchat.runtime import require_group_chat
    from .ws_chat import clear_cancelled_task, unmark_thread_task, unregister_wakeup_task

    try:
        await require_group_chat().inject_user_message(thread_id, content)
    finally:
        clear_cancelled_task(task_id)
        unregister_wakeup_task(task_id)
        unmark_thread_task(thread_id, task_id)


async def dispatch_process_completions(web_agent, store) -> None:
    from .groupchat.runtime import require_group_chat
    from .maf_tools.process_registry import process_registry
    from .ws_chat import mark_thread_task, register_wakeup_task, thread_is_busy

    evts = process_registry.drain_notifications(skip_poll_observed=False)
    if not evts:
        return
    from fm.backend.desktop import notify_fs_changed

    notify_fs_changed()
    mode = require_group_chat()
    by_thread: dict[str, list[dict]] = {}
    for evt, _text in evts:
        if str(evt.get("type") or "completion") != "completion":
            continue
        thread_id = str(evt.get("session_key") or "")
        if not thread_id:
            raise RuntimeError("process notification missing session_key")
        command = str(evt.get("command") or "").strip()
        if not command:
            raise RuntimeError("process notification missing command")
        by_thread.setdefault(thread_id, []).append(evt)
    for thread_id, items in by_thread.items():
        if thread_is_busy(thread_id):
            for evt in items:
                process_registry.completion_queue.put(evt)
            continue
        lines = [
            f"上次运行的命令 {str(evt.get('command') or '').strip()} 已经完成"
            for evt in items
        ]
        text = "\n".join(lines)
        task_id = "proc_" + uuid.uuid4().hex[:12]
        mark_thread_task(thread_id, task_id)
        if mode.is_bound_thread(thread_id):
            task = asyncio.create_task(_wakeup_group(thread_id, text, task_id))
        else:
            task = asyncio.create_task(_wakeup_web(web_agent, store, thread_id, text, task_id))
        register_wakeup_task(task_id, task)
