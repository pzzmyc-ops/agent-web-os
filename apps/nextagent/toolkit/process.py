"""process 工具 —— 管理 terminal(background=true) 起的后台进程。

直接调 maf_tools.process_registry 的方法,输出统一过 _redact_process_result 打码
(后台输出和前台 terminal 走同一套密钥剥离,两个面不能各行其是)。
"""
from __future__ import annotations

import json

from ..maf_tools.interrupt import worker_thread_of_task
from ..maf_tools.process_registry import _redact_process_result
from ..maf_tools.process_registry import process_registry

from ._context import current_turn
from ._registry import registry


PROCESS_DOC = """管理 terminal(background=true) 启动的后台进程。

Actions:
    list: 列出所有后台进程
    poll: 检查状态 + 新输出
    log:  完整输出(支持 offset/limit 翻页)
    wait: 阻塞等待完成(可设 timeout 秒数)
    kill: 终止进程
    write: 发送原始 stdin 数据(不带换行)
    submit: 发送数据 + Enter(用于回答交互提示)
    close: 关闭 stdin / 发送 EOF"""


@registry.register(
    toolset="terminal",
    name="process",
    summary="管理后台进程: list/poll/log/wait/kill/write/submit/close",
    description="管理后台进程",
    doc=PROCESS_DOC,
)
def process(
    action: str,
    session_id: str = "",
    data: str = "",
    timeout: int | None = None,
    offset: int = 0,
    limit: int = 200,
) -> str:
    """管理 terminal(background=true) 启动的后台进程。

    Actions:
        list: 列出所有后台进程
        poll: 检查状态 + 新输出
        log:  完整输出(支持 offset/limit 翻页)
        wait: 阻塞等待完成(可设 timeout 秒数)
        kill: 终止进程
        write: 发送原始 stdin 数据(不带换行)
        submit: 发送数据 + Enter(用于回答交互提示)
        close: 关闭 stdin / 发送 EOF
    """
    from ..maf_tools.session_key import get_current_session_key

    # Coerce session_id to string (some models send it as integer)
    session_id = str(session_id) if session_id else ""

    if action == "list":
        try:
            session_key = get_current_session_key(default="") or ""
        except Exception:
            session_key = ""
        return json.dumps(
            {"processes": process_registry.list_sessions(task_id=None, session_key=session_key or None)},
            ensure_ascii=False,
        )

    if action in {"poll", "log", "wait", "kill", "write", "submit", "close"}:
        if not session_id:
            return json.dumps({"error": f"session_id is required for {action}"}, ensure_ascii=False)

        if action == "poll":
            return json.dumps(_redact_process_result(process_registry.poll(session_id)), ensure_ascii=False)
        elif action == "log":
            return json.dumps(
                _redact_process_result(process_registry.read_log(session_id, offset=offset, limit=limit)),
                ensure_ascii=False,
            )
        elif action == "wait":
            # wait 是这里唯一会长时间阻塞的动作,登记本线程好让停止键能中断它
            # (process_registry.wait 的轮询循环在看 is_interrupted)。
            ctx = current_turn()
            if ctx is None:
                result = process_registry.wait(session_id, timeout=timeout)
            else:
                with worker_thread_of_task(ctx.task_id):
                    result = process_registry.wait(session_id, timeout=timeout)
            return json.dumps(_redact_process_result(result), ensure_ascii=False)
        elif action == "kill":
            return json.dumps(
                _redact_process_result(process_registry.kill_process(session_id)),
                ensure_ascii=False,
            )
        elif action == "write":
            return json.dumps(process_registry.write_stdin(session_id, str(data)), ensure_ascii=False)
        elif action == "submit":
            return json.dumps(process_registry.submit_stdin(session_id, str(data)), ensure_ascii=False)
        elif action == "close":
            return json.dumps(process_registry.close_stdin(session_id), ensure_ascii=False)

    return json.dumps(
        {"error": f"Unknown process action: {action}. Use: list, poll, log, wait, kill, write, submit, close"},
        ensure_ascii=False,
    )
