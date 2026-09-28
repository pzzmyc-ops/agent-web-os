"""会话身份 —— 「当前这条命令属于哪个会话」。

这是从 hermes 的 approval.py(3955 行)里剩下来的唯一还有用的东西。那个文件是命令
拦截层:硬阻断名单、危险模式匹配、审批等待、yolo、gateway 审批队列、smart approval。
mafagent 不拦截任何命令(产品取舍,见 terminal_tool.py 里的说明),所以整层已删,
只有会话身份这一小块被留下来 —— 它跟审批无关,是后台进程归属路由要用的:

- terminal_tool 起后台进程时把它写进 ProcessSession.session_key
- process_registry.drain_notifications 按它把完成通知投递给正确的会话
- toolkit/process 的 list 按它过滤出本会话的进程

用 ContextVar 而不是全局变量/环境变量:一个进程里同时有多个会话在跑(每条 WS 连接
一个 asyncio 任务),进程级的值会被最后一个写入者覆盖,导致 A 会话的后台进程完成
通知投递到 B 会话。ContextVar 天然按任务隔离。

绑定点:turn_lifecycle.TurnRun(它是唯一知道 thread_id 的地方)。没绑定时回落到
HERMES_SESSION_KEY 环境变量,再回落到调用方给的 default —— 单测和脚本直接调工具时
走这条路。
"""
from __future__ import annotations

import contextvars
import os

#: 当前会话标识。mafagent 里就是对话的 thread_id。
_session_key: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mafagent_session_key",
    default="",
)


def set_current_session_key(session_key: str) -> contextvars.Token[str]:
    """绑定当前会话标识,返回 token。必须在 finally 里 reset。"""
    return _session_key.set(session_key or "")


def reset_current_session_key(token: contextvars.Token[str]) -> None:
    """还原上一层的会话标识。"""
    try:
        _session_key.reset(token)
    except ValueError:
        pass  # 跨任务 reset 会抛,忽略即可(与 toolkit/_context.reset_turn 同处理)


def get_current_session_key(default: str = "default") -> str:
    """当前会话标识。

    顺序:ContextVar(run_turn 绑的)→ HERMES_SESSION_KEY 环境变量 → default。

    环境变量这一档保留是因为移植过来的代码在子进程里也读它(spawn 时会把
    HERMES_SESSION_* 注入子环境),去掉会让子进程失去身份。
    """
    key = _session_key.get()
    if key:
        return key
    return os.getenv("HERMES_SESSION_KEY", default) or default
