"""后台召回 —— 不是用户发起的那一轮对话怎么跑。

来源:
  - 定时任务到点(server._cron_ticker → timer_registry.claim_due)

关键是**必须走 stream_adapter.run_turn**,而不是自己 agent.run()。run_turn 才会:
  - set_turn() 绑上 TurnContext —— 否则这一轮里 schedule / clarify 这些
    调 require_turn() 的工具全部抛异常,agent 会得出「当前环境没有这个工具」的结论
  - set_current_session_key() —— 这一轮里起的后台进程才知道完成通知该投给谁
  - 工具块进 event_bus + 随 assistant 消息落盘 —— 刷新后历史能还原这一轮的工具调用
  - finally 里落盘,取消/异常也不丢

推送用 live_conns.broadcast:在线的连接实时看到流,一个都没有也照样跑完 —— recall 卡片
和回复都在 events.jsonl 里,用户下次打开这个会话就能看到。
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
import uuid

from . import session_log
from .identity import current_user
from .live_conns import broadcast
from .stream_adapter import TurnEmitter, run_turn

_log = logging.getLogger("mafagent.recall")

#: 每个会话一把锁:同一个会话的两次召回排队跑,不让两轮同时往一个 AgentSession 里写。
#: 按会话数量增长(几十条量级),不做淘汰 —— 淘汰的竞态比这点内存更麻烦。
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(thread_id: str) -> asyncio.Lock:
    lock = _locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[thread_id] = lock
    return lock


async def run_recall(
    web_agent,
    store,
    thread_id: str,
    *,
    prompt: str,
    card_text: str,
    source: str = "system",
) -> None:
    """在 *thread_id* 里跑一轮由后台触发的对话。

    prompt: 真正发给模型的提示词(定时任务里它是 message 外面套了一层交代的版本)。
    card_text: 前端「Agent 召回」卡片显示的正文,也是落盘的 recall 事件内容 ——
        用户该看到的是原始指令,不是那层包裹交代。
    source: 召回来源,前端拿它显示标签。"timer" = 定时任务,"system" = 后台进程通知。
    """
    if not thread_id or not store.thread_exists(thread_id):
        _log.warning("recall skipped: thread %r 不存在(会话可能已被删除)", thread_id)
        return

    task_id = "recall_" + uuid.uuid4().hex[:12]
    async with _lock_for(thread_id):
        # recall 卡片先落盘,历史里的顺序才是「触发 → 回复」
        store.append_event(
            thread_id, session_log.EV_RECALL, task_id=task_id, content=card_text
        )

        emitter = TurnEmitter(broadcast, task_id=task_id, thread_id=thread_id)
        try:
            await run_turn(
                web_agent, emitter,
                content=prompt,
                message_id="",
                model=store.selected_chat_model(thread_id),
                media_passthrough=store.media_passthrough_on(thread_id),
                skip_user_save=True,      # 触发内容已经作为 recall 事件落盘了
                is_agent_recall=True,
            )
        except Exception as exc:  # noqa: BLE001 — 后台轮次失败不该把 ticker/watcher 带崩
            _log.warning("recall turn 失败(%s, source=%s): %s", thread_id, source, exc)


async def run_exec_timer(store, thread_id: str, command: str, when_text: str) -> None:
    from .config import load_config

    cwd = load_config().fm_root_dir
    env = os.environ.copy()
    env.pop("HERMES_HOME", None)
    env["PYTHONIOENCODING"] = "utf-8"

    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            env=env,
        )

    retried = False
    try:
        completed = await asyncio.to_thread(_run)
        if completed.returncode != 0:
            await asyncio.sleep(5)
            completed = await asyncio.to_thread(_run)
            retried = True
    except subprocess.TimeoutExpired:
        text = f"{when_text}\n命令: {command}\n超时 600 秒"
    else:
        out = ((completed.stdout or "") + (completed.stderr or "")).strip()
        if len(out) > 8000:
            out = out[-8000:]
        text = f"{when_text}\n命令: {command}\n退出码: {completed.returncode}"
        if retried:
            text += "\n已重试 1 次"
        if out:
            text += "\n" + out
    if not thread_id or not store.thread_exists(thread_id):
        _log.warning("exec timer 会话不存在,命令已跑完: %s", command)
        return
    await run_program_timer(store, thread_id, text)


async def run_program_timer(store, thread_id: str, text: str) -> None:
    if not thread_id or not store.thread_exists(thread_id):
        raise RuntimeError("program timer thread missing: " + repr(thread_id))
    task_id = "ptimer_" + uuid.uuid4().hex[:12]
    created_at = int(time.time() * 1000)
    eid = store.append_event(
        thread_id,
        session_log.EV_PROGRAM_TIMER,
        task_id=task_id,
        content=text,
        created_at=created_at,
    )
    await broadcast({
        "type": "program_timer",
        "messageId": "",
        "timestamp": created_at,
        "status": "ok",
        "topic": "chat",
        "user": current_user(),
        "data": {
            "threadId": thread_id,
            "taskId": task_id,
            "content": text,
            "createdAt": created_at,
            "id": eid,
        },
    })
