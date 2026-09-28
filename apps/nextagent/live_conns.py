"""在线连接表 —— 后台发起的一轮对话往哪儿推。

用户发消息时,那一轮的事件走「发消息的那条 WS 连接」回去,天然有归属。但定时任务和
后台进程通知不是任何一条连接发起的:它们由 server 级的 ticker / watcher 触发,却仍然
要让**正在看这个会话的所有连接**实时看到流。

所以这里维护一张 conn_id → send 的表:

    register_conn(conn_id, send)     WS 建连时登记
    unregister_conn(conn_id)         断开时摘掉
    broadcast(obj)                   后台那一轮的事件推给所有在线连接

一条都没有(用户把浏览器关了)时 broadcast 静默丢弃 —— **这不影响那一轮照常跑完**:
recall 卡片和 agent 的回复由 run_turn 落进 events.jsonl,用户下次打开会话时从历史恢复。
广播只是给「此刻正在看」的连接补上实时性,不是持久化路径。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

SendFn = Callable[[dict[str, Any]], Awaitable[None]]

_conns: dict[str, SendFn] = {}

_log = logging.getLogger("mafagent.live_conns")


def register_conn(conn_id: str, send: SendFn) -> None:
    _conns[conn_id] = send


def unregister_conn(conn_id: str) -> None:
    _conns.pop(conn_id, None)


def conn_count() -> int:
    return len(_conns)


async def broadcast(obj: dict[str, Any]) -> None:
    """把一帧推给所有在线连接。单条连接失败不影响其他连接,也不影响调用方那一轮。"""
    for conn_id, send in list(_conns.items()):
        try:
            await send(obj)
        except Exception as exc:  # noqa: BLE001 — 死 socket 很常见,不值得中断一轮对话
            _log.debug("broadcast to %s failed: %s", conn_id, exc)
            _conns.pop(conn_id, None)
