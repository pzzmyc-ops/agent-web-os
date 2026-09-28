"""WebSocket 聊天端点:复刻 nextagent 前端所需的握手与回合流。

流程:accept → 收 auth → 回 auth_response(带对话列表 + currentId)→ 循环收帧。
收 text → 起一个异步任务用 stream_adapter.run_turn 驱动 Agent 并流式发块协议。
支持 ping / task_control(cancel,协作式)/ thread_focus。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import session_log
from .identity import current_user
from .live_conns import broadcast, register_conn, unregister_conn
from .maf_tools.interrupt import interrupt_task
from .stream_adapter import TurnEmitter, _log_user_message, run_turn, run_handoff_turn
from .turn_event_bus import bus as event_bus
from .turn_protocol import TextBlocks, turn_frame

router = APIRouter()

DEFAULT_TITLE = "新对话"
ASK_DEEPSEEK_KEY = (
    "你选中了 DeepSeek 官方模型（deepseek-official-flash / deepseek-official-pro），"
    "还没有官方 API Key，这条链路还不能调用。\n\n"
    "请到 https://platform.deepseek.com 创建 API Key，把完整的 Key 作为下一条消息发过来。"
    "程序会写入本机配置，这条消息不会当聊天内容保存。\n\n"
    "Key 写入后即可用官方 DeepSeek。其它已接入的模型不受影响。"
)
DEEPSEEK_KEY_SAVED = (
    "DeepSeek 官方 API Key 已写入。现在可以用官方 DeepSeek 对话。"
    "若要加新模型，直接让 Agent 添加即可。"
)
_awaiting_deepseek_key = False

#: 进程级单回合守卫:同一 thread 同一时刻只允许一个回合(task)。
#: 前端流式期间本就禁发,但刷新/重连会重置前端状态;没有这个守卫时,回合黑屏期的
#: 二次发送会再起一个 task,而 handoff 路径在回合开头就写 user —— 历史里就会出现
#: 两条一模一样的用户消息。
_thread_active_tasks: dict[str, str] = {}

#: 协作式取消标记(进程级)。取消来自当前连接,但回合可能是上一个连接发起的
#: (刷新后回合仍在跑),所以不能只挂在连接局部变量上,否则刷新后「停止」失效。
_cancelled_tasks: set[str] = set()
_wakeup_tasks: dict[str, asyncio.Task] = {}

#: thread_id → 该回合的 asyncio.Task。定时任务要打断当前回合时得拿到 task 对象,
#: 而 ws 循环里的 tasks 表是连接局部的,server 级的 ticker 看不见。
_thread_runner_tasks: dict[str, asyncio.Task] = {}


def mark_thread_task(thread_id: str, task_id: str) -> None:
    _thread_active_tasks[thread_id] = task_id


def unmark_thread_task(thread_id: str, task_id: str) -> None:
    if _thread_active_tasks.get(thread_id) == task_id:
        _thread_active_tasks.pop(thread_id, None)


def thread_is_busy(thread_id: str) -> bool:
    return thread_id in _thread_active_tasks


def task_is_cancelled(task_id: str) -> bool:
    return task_id in _cancelled_tasks


async def interrupt_thread(thread_id: str, *, timeout: float = 30.0) -> bool:
    """打断 *thread_id* 正在跑的那一轮,等它真的收完尾再返回。

    定时任务优先级最高:到点时不排队也不跳过,直接按用户点「停止」的同一条路径把
    当前回合取消掉。必须等回合结束 —— 不等就会两轮同时往一个 events.jsonl 里写。
    """
    tid = _thread_active_tasks.get(thread_id)
    if not tid:
        return False
    _cancelled_tasks.add(tid)
    interrupt_task(tid)
    from .web_server import dingtalk_bot

    dingtalk_bot.cancel_if_match(tid, thread_id)
    task = _thread_runner_tasks.get(thread_id) or _wakeup_tasks.get(tid)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    deadline = time.monotonic() + timeout
    while _thread_active_tasks.get(thread_id) == tid:
        if time.monotonic() >= deadline:
            raise RuntimeError(f"打断超时,回合仍在跑: thread={thread_id} task={tid}")
        await asyncio.sleep(0.1)
    return True


def clear_cancelled_task(task_id: str) -> None:
    _cancelled_tasks.discard(task_id)


def register_wakeup_task(task_id: str, task: asyncio.Task) -> None:
    _wakeup_tasks[task_id] = task


def unregister_wakeup_task(task_id: str) -> None:
    _wakeup_tasks.pop(task_id, None)


def register_runner_task(thread_id: str, task: asyncio.Task) -> None:
    _thread_runner_tasks[thread_id] = task


def unregister_runner_task(thread_id: str, task: asyncio.Task) -> None:
    if _thread_runner_tasks.get(thread_id) is task:
        _thread_runner_tasks.pop(thread_id, None)


def drop_thread_completions(thread_id: str) -> None:
    from .maf_tools.process_registry import process_registry

    process_registry.drop_notifications(thread_id)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _thread_model(store, thread_id: str, data: dict) -> str:
    raw = str(data.get("agentProfile") or data.get("model") or "").strip()
    if raw:
        store.apply_selected_chat_model(thread_id, raw)
    return store.selected_chat_model(thread_id)


def _reasoning_params(data: dict) -> tuple[bool | None, str | None]:
    """取前端的思考开关。模型不支持思考时前端两个字段都不发,这里返回 (None, None),
    表示「不干预」,由模型 adapter 自己的默认值决定。"""
    raw = data.get("thinking")
    thinking = bool(raw) if raw is not None else None
    effort = str(data.get("reasoning_effort") or "").strip() or None
    return thinking, effort


async def _plain_assistant_turn(store, thread_id: str, message_id: str, text: str, *, user_content: str = "") -> None:
    task_id = "task_" + uuid.uuid4().hex[:12]
    emitter = TurnEmitter(broadcast, task_id=task_id, thread_id=thread_id)
    blocks = TextBlocks(emitter, store, task_id=task_id, thread_id=thread_id)
    _thread_active_tasks[thread_id] = task_id
    try:
        await event_bus.open(task_id, thread_id=thread_id, message_id=message_id)
        await emitter.control(
            "message_accepted",
            {
                "taskId": task_id,
                "threadId": thread_id,
                "messageId": message_id,
                "eventSeq": 0,
                "status": "queued",
            },
            status="queued",
        )
        await emitter.turn(
            "stream_start",
            {
                "taskType": "chat",
                "content": user_content,
                "isAgentRecall": False,
                "recallSource": "",
            },
        )
        await emitter.turn("turn_start", {})
        if user_content:
            await _log_user_message(store, emitter, content=user_content, attachments=[])
        await blocks.text(text)
        await blocks.finalize()
        store.append_event(
            thread_id,
            session_log.EV_TURN_END,
            task_id=task_id,
            interrupted=False,
            error="",
        )
        await emitter.turn(
            "turn_complete",
            {
                "content": text,
                "thinking": "",
                "compressed": False,
                "compressionCount": 0,
                "interrupted": False,
            },
            status="ok",
        )
        await event_bus.close(task_id)
    finally:
        if _thread_active_tasks.get(thread_id) == task_id:
            _thread_active_tasks.pop(thread_id, None)


async def _plain_notice_turn(store, thread_id: str, message_id: str, text: str) -> None:
    task_id = "task_" + uuid.uuid4().hex[:12]
    emitter = TurnEmitter(broadcast, task_id=task_id, thread_id=thread_id)
    blocks = TextBlocks(emitter, store, task_id=task_id, thread_id=thread_id)
    _thread_active_tasks[thread_id] = task_id
    try:
        await event_bus.open(task_id, thread_id=thread_id, message_id=message_id)
        await emitter.control(
            "message_accepted",
            {
                "taskId": task_id,
                "threadId": thread_id,
                "messageId": message_id,
                "eventSeq": 0,
                "status": "queued",
            },
            status="queued",
        )
        await emitter.turn(
            "stream_start",
            {
                "taskType": "chat",
                "content": "",
                "isAgentRecall": False,
                "recallSource": "",
            },
        )
        await emitter.turn("turn_start", {})
        await blocks.notice(text)
        await blocks.finalize()
        store.append_event(
            thread_id,
            session_log.EV_TURN_END,
            task_id=task_id,
            interrupted=False,
            error="",
        )
        await emitter.turn(
            "turn_complete",
            {
                "content": "",
                "thinking": "",
                "compressed": False,
                "compressionCount": 0,
                "interrupted": False,
            },
            status="ok",
        )
        await event_bus.close(task_id)
    finally:
        if _thread_active_tasks.get(thread_id) == task_id:
            _thread_active_tasks.pop(thread_id, None)


def _apply_deepseek_api_key(text: str) -> None:
    from appconfig import write_deepseek_api_key
    from gateway.adapters.registry import reload_adapters
    import apps.nextagent.config as agent_config
    from server import ensure_keyed_embedded

    write_deepseek_api_key(text)
    agent_config._cfg_cache = None
    reload_adapters()
    ensure_keyed_embedded()


def _control(ev_type: str, data: dict, *, status: str = "ok") -> dict:
    return {
        "type": ev_type,
        "messageId": "",
        "timestamp": _now_ms(),
        "status": status,
        "topic": "chat",
        "user": current_user(),
        "data": data,
    }


def _history_snapshot(store, thread_id: str, mid: str) -> dict:
    return {
        "type": "history_snapshot",
        "messageId": mid,
        "timestamp": _now_ms(),
        "status": "ok",
        "topic": "chat",
        "user": current_user(),
        "data": {
            "events": store.to_history_events(thread_id),
            "threadId": thread_id,
            "checkpoints": store.checkpoints.state(thread_id),
            "planDoc": store.load_plan_doc(thread_id),
        },
    }


async def _apply_checkpoint(store, thread_id: str, checkpoint_id: str, *, from_submit: bool) -> dict:
    """改盘 + 计划槽回写 + 通知文件管理器刷新。from_submit 见 Checkpoints.restore。"""
    from fm.backend.desktop import notify_fs_changed

    plan_now = store.load_plan_slot(thread_id)
    report = await asyncio.to_thread(
        store.checkpoints.restore, thread_id, checkpoint_id,
        from_submit=from_submit, current_plan=plan_now,
    )
    store.restore_plan_slot(thread_id, report["plan"])
    if report["changed"]:
        notify_fs_changed(report["changed"])
    return report


async def _redo_checkpoint(store, thread_id: str) -> dict:
    from fm.backend.desktop import notify_fs_changed

    plan_now = store.load_plan_slot(thread_id)
    report = await asyncio.to_thread(store.checkpoints.redo, thread_id, current_plan=plan_now)
    store.restore_plan_slot(thread_id, report["plan"])
    if report["changed"]:
        notify_fs_changed(report["changed"])
    return report


def _truncate_to_cursor(store, thread_id: str) -> None:
    """回退状态下发新消息:光标所指那条消息起的历史全部砍掉,头部快照作废。"""
    cursor = store.checkpoints.cursor(thread_id)
    if not cursor:
        return
    eid = store.event_id_by_checkpoint(thread_id, cursor)
    store.delete_events_after(thread_id, eid - 1)
    store.checkpoints.clear_head(thread_id)


async def _active_tasks_payload() -> list[dict]:
    """此刻在跑的回合。以 _thread_active_tasks 为准:受理即算在跑,不等总线开日志。

    前端拿它对齐本地任务表 —— 不在名单里的一律清掉,发送 / 停止键就按这份名单算。
    """
    out: list[dict] = []
    for thread_id, task_id in list(_thread_active_tasks.items()):
        snap = await event_bus.snapshot(task_id)
        out.append({
            "taskId": task_id,
            "threadId": thread_id,
            "status": "running",
            "taskKind": "chat",
            "snapshotSeq": snap.last_seq if snap is not None else 0,
        })
    return out


def _turn_resume_frame(snap, mid: str) -> dict:
    return {
        "v": 1,
        "type": "turn_resume",
        "messageId": mid,
        "timestamp": _now_ms(),
        "status": "streaming",
        "topic": "chat",
        "user": current_user(),
        "seq": 0,
        "taskId": snap.task_id,
        "threadId": snap.thread_id,
        "data": {
            "taskId": snap.task_id,
            "threadId": snap.thread_id,
            "taskKind": "chat",
            "status": "running",
            "content": snap.content,
            "snapshotContent": snap.content,
            "snapshotReasoning": snap.reasoning,
            "snapshotSeq": snap.last_seq,
            "blocks": snap.blocks,
            "reconnect": True,
        },
    }


def _cancel_task(tid: str, thread_id: str, local_tasks: dict[str, asyncio.Task]) -> None:
    """按用户点「停止」的路径取消一个回合。

    回合的 asyncio.Task 登记在进程级的 _thread_runner_tasks 里,不在发起它的那条连接上:
    别的窗口、刷新后的新连接点停止,也要能真的把它 cancel 掉,而不是只挂一个协作标记。
    """
    if tid:
        _cancelled_tasks.add(tid)
        interrupt_task(tid)
    task = None
    if thread_id and _thread_active_tasks.get(thread_id) == tid:
        task = _thread_runner_tasks.get(thread_id)
    if task is None:
        task = local_tasks.get(tid) or _wakeup_tasks.get(tid)
    if task is not None:
        task.cancel()


@router.websocket("/api/v1/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    store = websocket.app.state.store
    web_agent = websocket.app.state.web_agent

    send_lock = asyncio.Lock()
    ws_alive = True

    conn_id = "conn_" + uuid.uuid4().hex[:12]

    async def send(obj: dict) -> None:
        nonlocal ws_alive
        if not ws_alive:
            return
        async with send_lock:
            try:
                await websocket.send_json(obj)
            except Exception:
                # 连接已断开:回合继续跑完并落库(生成权在后端)。这条连接从广播表摘掉,
                # 事件仍在总线里,前端重连后靠 turn_resume / resume 续传把画面补齐。
                ws_alive = False
                unregister_conn(conn_id)

    register_conn(conn_id, send)

    tasks: dict[str, asyncio.Task] = {}

    async def maybe_title(thread_id: str, content: str) -> None:
        if store.thread_title(thread_id) != DEFAULT_TITLE:
            return
        title = content.strip().replace("\n", " ")[:20] or DEFAULT_TITLE
        if title == DEFAULT_TITLE:
            return
        store.rename_thread(thread_id, title)
        await send(_control("title_updated", {"threadId": thread_id, "title": title}))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")
            data = msg.get("data") or {}
            mid = msg.get("messageId", "")

            if mtype == "auth":
                cur = store.ensure_initial_thread()
                from .web_server import dingtalk_bot

                convs = dingtalk_bot.annotate_conversations(
                    [
                        {
                            "id": t["id"],
                            "title": t["title"],
                            "updatedAt": t.get("updatedAt", 0),
                            "folderId": t.get("folderId") or "",
                            "active": (t["id"] == cur),
                            "app": t.get("app") or "",
                            "feishuWebhookBound": bool(t.get("feishuWebhookBound")),
                        }
                        for t in store.list_threads()
                    ]
                )
                await send(
                    _control(
                        "auth_response",
                        {
                            "threadId": (data.get("threadId") or cur),
                            "connectionId": conn_id,
                            "conversations": convs,
                            "folders": store.list_folders(),
                            "currentId": cur,
                        },
                    )
                )
                await send(_control("tasks_snapshot", {"tasks": await _active_tasks_payload()}))
                # 只对真正在跑的 task 发 turn_resume(已完成的靠历史恢复)
                for task_id in event_bus.active_task_ids:
                    snap = await event_bus.snapshot(task_id)
                    if snap is None:
                        continue
                    await send(_turn_resume_frame(snap, mid))

            elif mtype == "ping":
                await send(_control("pong", {"serverTime": _now_ms(), "connectionId": data.get("connectionId", "")}))

            elif mtype == "thread_focus":
                await send(_control("tasks_snapshot", {"tasks": await _active_tasks_payload()}))
                focus_thread = str(data.get("threadId") or "")
                for task_id in event_bus.active_task_ids:
                    snap = await event_bus.snapshot(task_id)
                    if snap is None:
                        continue
                    if focus_thread and snap.thread_id != focus_thread:
                        continue
                    await send(_turn_resume_frame(snap, mid))

            elif mtype == "resume":
                # 前端重连后报上它见过的最后一个 seq,把它没见过的事件按原序号补发。
                # 差得太多(总线环已经把那段挤掉了)就退回整份快照。
                tid = str(data.get("taskId") or "")
                after_seq = int(data.get("lastSeq") or 0)
                log = await event_bus.get(tid)
                if log is not None:
                    if log.has_gap(after_seq):
                        await send(_turn_resume_frame(log.snapshot(), mid))
                    else:
                        for ev in log.events_after(after_seq):
                            await send(turn_frame(ev))

            elif mtype == "task_control":
                if data.get("action") == "cancel":
                    tid = str(data.get("taskId") or "")
                    thread_id = str(data.get("threadId") or "").strip()
                    if not thread_id:
                        for th, active in _thread_active_tasks.items():
                            if active == tid:
                                thread_id = th
                                break
                    if not tid and thread_id:
                        tid = _thread_active_tasks.get(thread_id, "")
                    _cancel_task(tid, thread_id, tasks)
                    from .web_server import dingtalk_bot

                    dingtalk_bot.cancel_if_match(tid, thread_id)
                    if thread_id:
                        from .plan_mode import abandon_plan

                        abandon_plan(store, thread_id)
                        drop_thread_completions(thread_id)

            elif mtype == "clarify_respond":
                # 用户回答了 clarify 的提问(点了选项或自己打字)。
                # toolCallId 就是那个工具块的 blockId —— 前端按块渲染问题,
                # 所以块 id 天然就是「这个问题」的标识,不用另造一套 id。
                # resolve 返回 False 表示没人在等(重复提交、或已超时),忽略即可。
                from .toolkit.clarify import clarify_registry

                block_id = str(data.get("toolCallId") or data.get("tool_call_id") or "").strip()
                if block_id:
                    clarify_registry.resolve(block_id, str(data.get("answer") or ""))

            elif mtype == "plan_review_respond":
                from .plan_mode import (
                    commit_plan_approval,
                    followup_for_decision,
                    plan_review_registry,
                    wait_review_tool_result,
                )

                block_id = str(data.get("toolCallId") or data.get("tool_call_id") or "").strip()
                plan_id = str(data.get("planId") or "").strip()
                decision = str(data.get("decision") or "").strip()
                thread_id = str(data.get("threadId") or "").strip()
                if not block_id and not plan_id:
                    raise RuntimeError("plan_review_respond missing toolCallId and planId")
                if decision == "dismiss":
                    if block_id:
                        plan_review_registry.resolve(block_id, {"dismissed": True})
                    tid = _thread_active_tasks.get(thread_id) if thread_id else None
                    if tid:
                        _cancel_task(tid, thread_id, tasks)
                    from .web_server import dingtalk_bot

                    dingtalk_bot.cancel_if_match(tid or "", thread_id)
                elif decision in ("approve", "decline"):
                    user_modified = False
                    if decision == "approve":
                        if not thread_id:
                            raise RuntimeError("plan_review_respond approve missing threadId")
                        tid = _thread_active_tasks.get(thread_id) or ""
                        approved = commit_plan_approval(store, thread_id, task_id=str(tid), plan_id=plan_id)
                        user_modified = bool(approved.get("userModified"))
                        payload = {"approved": True}
                    else:
                        payload = {"approved": False}
                    followup = followup_for_decision(decision, user_modified=user_modified)
                    if not block_id:
                        raise RuntimeError("plan_review_respond missing toolCallId")
                    if not thread_id:
                        raise RuntimeError("plan_review_respond missing threadId")
                    plan_review_registry.resolve(block_id, payload)
                    await wait_review_tool_result(store, thread_id, block_id)
                    tid = _thread_active_tasks.get(thread_id) if thread_id else None
                    if tid:
                        task = _thread_runner_tasks.get(thread_id)
                        _cancel_task(tid, thread_id, tasks)
                        if task is not None:
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass
                    await send(_control("plan_decision_idle", {
                        "threadId": thread_id,
                        "decision": decision,
                        "followup": followup,
                    }))
                else:
                    raise RuntimeError("plan_review_respond unknown decision: " + decision)

            elif mtype == "text":
                content = str(data.get("content") or "").strip()
                thread_id = str(data.get("threadId") or "").strip() or store.current_thread()
                attachments = data.get("attachments") or []
                if (not content and not attachments) or not thread_id:
                    continue
                if not store.thread_exists(thread_id):
                    thread_id = store.ensure_initial_thread()
                from appconfig import load_config
                from gateway.adapters.registry import requires_official_deepseek_key

                model = _thread_model(store, thread_id, data)
                keyed = bool(str(load_config().deepseek_api_key or "").strip())
                global _awaiting_deepseek_key
                if not keyed and _awaiting_deepseek_key and requires_official_deepseek_key(model):
                    if thread_id in _thread_active_tasks:
                        await send(_control(
                            "message_rejected",
                            {"threadId": thread_id, "messageId": mid, "reason": "task_active"},
                            status="rejected",
                        ))
                        continue
                    if not content:
                        await send(_control(
                            "message_rejected",
                            {"threadId": thread_id, "messageId": mid, "reason": "empty_key"},
                            status="rejected",
                        ))
                        continue
                    _apply_deepseek_api_key(content)
                    _awaiting_deepseek_key = False
                    await send(_control("deepseek_key_saved", {"threadId": thread_id}))
                    await _plain_notice_turn(store, thread_id, mid, DEEPSEEK_KEY_SAVED)
                    continue
                if not keyed and _awaiting_deepseek_key:
                    _awaiting_deepseek_key = False
                if not keyed and requires_official_deepseek_key(model):
                    if thread_id in _thread_active_tasks:
                        await send(_control(
                            "message_rejected",
                            {"threadId": thread_id, "messageId": mid, "reason": "task_active"},
                            status="rejected",
                        ))
                        continue
                    _awaiting_deepseek_key = True
                    _truncate_to_cursor(store, thread_id)
                    await _plain_assistant_turn(
                        store, thread_id, mid, ASK_DEEPSEEK_KEY, user_content=content,
                    )
                    continue
                from .store import APP_FEISHU_WEBHOOK

                if store.thread_app(thread_id) == APP_FEISHU_WEBHOOK and not store.feishu_webhook_url(thread_id):
                    await send(_control(
                        "message_rejected",
                        {"threadId": thread_id, "messageId": mid, "reason": "feishu_webhook_missing"},
                        status="rejected",
                    ))
                    continue
                if thread_id in _thread_active_tasks:
                    import logging
                    logging.getLogger("mafagent.ws").warning(
                        "reject text: thread %s has active task %s", thread_id, _thread_active_tasks[thread_id]
                    )
                    await send(_control(
                        "message_rejected",
                        {"threadId": thread_id, "messageId": mid, "reason": "task_active"},
                        status="rejected",
                    ))
                    continue
                _truncate_to_cursor(store, thread_id)
                media_passthrough = bool(data.get("mediaPassthrough"))
                thinking, effort = _reasoning_params(data)
                plan_mode = bool(data.get("planMode"))
                from .web_server import dingtalk_bot

                bound = dingtalk_bot.is_bound_thread(thread_id)
                webhook_app = store.thread_app(thread_id) == APP_FEISHU_WEBHOOK
                if bound:
                    plan_mode = False
                task_id = "task_" + uuid.uuid4().hex[:12]
                # 回合事件广播给所有在线连接,不绑发起它的这一条:同一对话开了几个窗口
                # 就几个窗口同时看到流;这条连接断了,别的窗口和重连后的窗口照样收。
                emitter = TurnEmitter(broadcast, task_id=task_id, thread_id=thread_id)

                active_roles = store.get_active_roles(thread_id)

                async def runner(
                    em=emitter,
                    tid=task_id,
                    th=thread_id,
                    ct=content,
                    md=model,
                    m_id=mid,
                    att=attachments,
                    mp=media_passthrough,
                    ar=active_roles,
                    tk=thinking,
                    ef=effort,
                    pm=plan_mode,
                    skip_handoff=bound or webhook_app,
                ):
                    try:
                        if ar and not skip_handoff:
                            await run_handoff_turn(
                                web_agent, em, store,
                                content=ct, message_id=m_id, model=md, roles=ar,
                                is_cancelled=lambda: tid in _cancelled_tasks,
                                attachments=att, media_passthrough=mp,
                                thinking=tk, reasoning_effort=ef,
                                plan_mode=pm,
                            )
                        else:
                            await run_turn(
                                web_agent, em,
                                content=ct, message_id=m_id, model=md,
                                is_cancelled=lambda: tid in _cancelled_tasks,
                                attachments=att, media_passthrough=mp,
                                thinking=tk, reasoning_effort=ef,
                                plan_mode=pm,
                                group_chat=False,
                            )
                    finally:
                        tasks.pop(tid, None)
                        _cancelled_tasks.discard(tid)
                        _thread_active_tasks.pop(th, None)
                        _thread_runner_tasks.pop(th, None)

                _thread_active_tasks[thread_id] = task_id
                asyncio.create_task(maybe_title(thread_id, content))
                tasks[task_id] = asyncio.create_task(runner())
                _thread_runner_tasks[thread_id] = tasks[task_id]

            elif mtype == "add_role":
                thread_id = str(data.get("threadId") or "").strip() or store.current_thread()
                role_id = str(data.get("role_id") or "").strip()
                if thread_id and role_id:
                    store.add_active_role(thread_id, role_id)

            elif mtype == "remove_role":
                thread_id = str(data.get("threadId") or "").strip() or store.current_thread()
                role_id = str(data.get("role_id") or "").strip()
                if thread_id and role_id:
                    store.remove_active_role(thread_id, role_id)

            elif mtype == "edit_and_regenerate":
                # 编辑用户消息并重新生成:回退到该消息→替换内容→重新跑轮
                user_idx_raw = data.get("userMessageIndex")
                thread_id = str(data.get("threadId") or "").strip() or store.current_thread()
                new_content = str(data.get("newContent") or "").strip()
                attachments = data.get("attachments") or []
                if (not new_content and not attachments) or not thread_id:
                    continue
                if not store.thread_exists(thread_id):
                    thread_id = store.ensure_initial_thread()
                from .store import APP_FEISHU_WEBHOOK

                if store.thread_app(thread_id) == APP_FEISHU_WEBHOOK and not store.feishu_webhook_url(thread_id):
                    await send(_control(
                        "message_rejected",
                        {"threadId": thread_id, "messageId": mid, "reason": "feishu_webhook_missing"},
                        status="rejected",
                    ))
                    continue
                if thread_id in _thread_active_tasks:
                    import logging
                    logging.getLogger("mafagent.ws").warning(
                        "reject edit_and_regenerate: thread %s has active task %s",
                        thread_id, _thread_active_tasks[thread_id],
                    )
                    await send(_control(
                        "message_rejected",
                        {"threadId": thread_id, "messageId": mid, "reason": "task_active"},
                        status="rejected",
                    ))
                    continue
                try:
                    user_idx = int(user_idx_raw)
                except (TypeError, ValueError):
                    await send(_control(
                        "message_rejected",
                        {"threadId": thread_id, "messageId": mid, "reason": "invalid_index"},
                        status="rejected",
                    ))
                    continue
                target_msg = store.nth_record_of_role(thread_id, "user", user_idx)
                if target_msg is None:
                    await send(_control(
                        "message_rejected",
                        {"threadId": thread_id, "messageId": mid, "reason": "message_not_found"},
                        status="rejected",
                    ))
                    continue
                # 文件先回到这条提问之前的状态(有检查点才有得恢复;老会话的消息没有),
                # 再截掉这条提问之后的所有事件,把提问改成新内容。检查点留在事件上,
                # 重发不再另建点:它记的正是「这条消息之前」。
                cp_id = str(target_msg.get("checkpoint_id") or "")
                if cp_id and store.checkpoints.exists(thread_id, cp_id):
                    await _apply_checkpoint(store, thread_id, cp_id, from_submit=True)
                else:
                    store.checkpoints.clear_head(thread_id)
                store.delete_events_after(thread_id, target_msg["ids"][0])
                store.update_event_content(thread_id, target_msg["ids"][0], new_content)

                model = _thread_model(store, thread_id, data)
                media_passthrough = bool(data.get("mediaPassthrough"))
                thinking, effort = _reasoning_params(data)
                plan_mode = bool(data.get("planMode"))
                from .web_server import dingtalk_bot

                bound = dingtalk_bot.is_bound_thread(thread_id)
                webhook_app = store.thread_app(thread_id) == APP_FEISHU_WEBHOOK
                if bound:
                    plan_mode = False
                task_id = "task_" + uuid.uuid4().hex[:12]
                emitter = TurnEmitter(broadcast, task_id=task_id, thread_id=thread_id)
                active_roles = store.get_active_roles(thread_id)

                async def runner_edit_user(
                    em=emitter,
                    tid=task_id,
                    th=thread_id,
                    ct=new_content,
                    md=model,
                    m_id=mid,
                    att=attachments,
                    mp=media_passthrough,
                    ar=active_roles,
                    tk=thinking,
                    ef=effort,
                    pm=plan_mode,
                    skip_handoff=bound or webhook_app,
                ):
                    try:
                        if ar and not skip_handoff:
                            await run_handoff_turn(
                                web_agent, em, store,
                                content=ct, message_id=m_id, model=md, roles=ar,
                                is_cancelled=lambda: tid in _cancelled_tasks,
                                attachments=att, media_passthrough=mp,
                                skip_user_save=True,
                                thinking=tk, reasoning_effort=ef,
                                plan_mode=pm,
                            )
                        else:
                            await run_turn(
                                web_agent, em,
                                content=ct, message_id=m_id, model=md,
                                is_cancelled=lambda: tid in _cancelled_tasks,
                                attachments=att, media_passthrough=mp,
                                skip_user_save=True,
                                thinking=tk, reasoning_effort=ef,
                                plan_mode=pm,
                                group_chat=False,
                            )
                    finally:
                        tasks.pop(tid, None)
                        _cancelled_tasks.discard(tid)
                        _thread_active_tasks.pop(th, None)
                        _thread_runner_tasks.pop(th, None)

                _thread_active_tasks[thread_id] = task_id
                asyncio.create_task(maybe_title(thread_id, new_content))
                tasks[task_id] = asyncio.create_task(runner_edit_user())
                _thread_runner_tasks[thread_id] = tasks[task_id]

            elif mtype in ("checkpoint_restore", "checkpoint_redo"):
                # 文件检查点:restore 把工作区改回某条消息之前;redo 回到恢复前的头部。
                # 都不动对话事件,只在末尾追加一条说明,并推一份完整历史让前端重画
                # (已回退的那段气泡要变灰、光标位置要换)。
                thread_id = str(data.get("threadId") or "").strip() or store.current_thread()
                cp_id = str(data.get("checkpointId") or "").strip()
                if not thread_id or not store.thread_exists(thread_id):
                    continue
                if thread_id in _thread_active_tasks:
                    await send(_control(
                        "message_rejected",
                        {"threadId": thread_id, "messageId": mid, "reason": "task_active"},
                        status="rejected",
                    ))
                    continue
                if mtype == "checkpoint_restore" and not store.checkpoints.exists(thread_id, cp_id):
                    await send(_control(
                        "message_rejected",
                        {"threadId": thread_id, "messageId": mid, "reason": "checkpoint_not_found"},
                        status="rejected",
                    ))
                    continue
                from .checkpoints import restore_summary

                if mtype == "checkpoint_restore":
                    report = await _apply_checkpoint(store, thread_id, cp_id, from_submit=False)
                    summary = restore_summary(report)
                else:
                    report = await _redo_checkpoint(store, thread_id)
                    summary = "已撤销恢复,工作区回到恢复前的状态。\n" + restore_summary(report)
                store.append_event(thread_id, session_log.EV_SYSTEM, content=summary)
                await send(_history_snapshot(store, thread_id, mid))

            elif mtype == "edit_ai_message":
                # 编辑 AI 回复:回退到该消息→替换内容。不自动跑新轮,前端自行刷新历史。
                ai_idx_raw = data.get("aiMessageIndex")
                thread_id = str(data.get("threadId") or "").strip() or store.current_thread()
                new_content = str(data.get("newContent") or "").strip()
                if not thread_id:
                    continue
                try:
                    ai_idx = int(ai_idx_raw)
                except (TypeError, ValueError):
                    continue
                target_msg = store.nth_record_of_role(thread_id, "assistant", ai_idx)
                if target_msg is None:
                    continue
                # 一条发言可能由几个分段事件合并而来,截到它的第一个来源事件,
                # 后面的分段跟着一起删掉,改完就只剩一条 assistant 事件
                store.delete_events_after(thread_id, target_msg["ids"][0])
                store.update_event_content(thread_id, target_msg["ids"][0], new_content)
                # 刷新:让前端重载历史,这样 DOM 里的旧气泡会被新内容替换
                await send(_history_snapshot(store, thread_id, mid))

            # 其它类型(resume / clarify_respond 等本步忽略)
    except WebSocketDisconnect:
        pass
    finally:
        unregister_conn(conn_id)
