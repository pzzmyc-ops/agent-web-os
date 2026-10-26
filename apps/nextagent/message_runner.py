from __future__ import annotations

import asyncio
import logging
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


def _wakeup_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logging.getLogger("mafagent.process").error("完成通知投递失败: %s", exc)


def _report_body(evt: dict) -> tuple[str, str]:
    evt_type = str(evt.get("type") or "completion")
    thread_id = str(evt.get("session_key") or "").strip()
    if not thread_id:
        raise RuntimeError(
            f"process notification missing session_key type={evt_type} session_id={evt.get('session_id') or ''}"
        )
    command = str(evt.get("command") or "").strip()
    output = str(evt.get("output") or "").strip()
    if evt_type == "completion":
        if not command:
            raise RuntimeError("process notification missing command")
        body = f"上次运行的命令 {command} 已经完成，退出码 {evt.get('exit_code')}"
        if output:
            body = body + "\n" + output
        return thread_id, body
    if evt_type == "watch_match":
        pattern = str(evt.get("pattern") or "").strip()
        if not command or not pattern:
            raise RuntimeError("process notification missing watch fields")
        body = f"后台命令 {command} 的输出出现了 {pattern}"
        if output:
            body = body + "\n" + output
        return thread_id, body
    if evt_type == "watch_disabled":
        message = str(evt.get("message") or "").strip()
        if not message:
            raise RuntimeError("process notification missing message")
        return thread_id, message
    raise RuntimeError(f"process notification unsupported type: {evt_type}")


async def dispatch_process_completions(web_agent, store) -> None:
    from .groupchat.runtime import require_group_chat
    from .maf_tools.process_registry import process_registry

    mode = require_group_chat()
    evts = process_registry.drain_notifications(skip_poll_observed=False)
    if not evts:
        return
    from fm.backend.desktop import notify_fs_changed

    try:
        notify_fs_changed()
    except Exception:
        for evt, _text in evts:
            process_registry.completion_queue.put(evt)
        raise
    from .turn_queue import ReportItem, enqueue
    from .ws_chat import publish_queue, schedule_pump

    errors: list[str] = []
    by_thread: dict[str, list[str]] = {}
    for evt, _text in evts:
        try:
            thread_id, body = _report_body(evt)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        by_thread.setdefault(thread_id, []).append(body)
    for thread_id, bodies in by_thread.items():
        group_chat = mode.is_bound_thread(thread_id)
        for body in bodies:
            enqueue(thread_id, ReportItem(content=body, group_chat=group_chat))
        publish_queue(thread_id)
        schedule_pump(thread_id)
    if errors:
        raise RuntimeError("process notification 无法投递: " + "; ".join(errors))
