"""process 工具 —— 看、等、输入、关掉桌面上的终端会话。"""
from __future__ import annotations

import json
import time

from fm.backend.terminal import REGISTRY, close_session

from ..maf_tools.interrupt import is_interrupted, worker_thread_of_task
from ..maf_tools.process_registry import _redact_process_result

from ._context import current_turn
from ._registry import registry
from .terminal import fit_output


PROCESS_DOC = """查看或操作桌面上的终端。有人无人要用 terminal 的 mark 改，这里改不了。只有你认为用户的这次任务已经完成，才用 terminal 标无人；命令结束、终端空闲都不要标。有人（不是你）的终端不要写、不要关。

Actions:
    list: 列出还开着的终端
    poll: 看状态和最近输出
    log:  翻这个窗口里的内容(支持 offset/limit)
    wait: 等到当前命令结束(可设 timeout 秒数)。已经停在提示符上会立刻返回
    kill: 关掉窗口并停掉 shell
    write: 原样写入，不加回车
    submit: 在提示符上提交一行
    close: 发送 Ctrl+D，不关窗口"""


def _dump(payload: dict) -> str:
    return json.dumps(fit_output(_redact_process_result(payload)), ensure_ascii=False)


@registry.register(
    toolset="terminal",
    name="process",
    summary="管理桌面终端: list/poll/log/wait/kill/write/submit/close",
    description="管理桌面终端",
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
    """管理桌面上打开的终端。

    Actions:
        list: 列出还开着的终端
        poll: 看状态和最近输出
        log:  翻这个窗口里的内容(支持 offset/limit)
        wait: 等到当前命令结束(可设 timeout 秒数)
        kill: 关掉窗口并停掉 shell
        write: 原样写入，不加回车
        submit: 在提示符上提交一行
        close: 发送 Ctrl+D，不关窗口
    """
    session_id = str(session_id) if session_id else ""
    ctx = current_turn()
    desktop_id = ctx.desktop_id if ctx is not None else ""
    from ..maf_tools.session_key import get_current_session_key

    chat_key = get_current_session_key(default="") or ""

    if action == "list":
        return json.dumps({"processes": REGISTRY.list()}, ensure_ascii=False)

    if action not in {"poll", "log", "wait", "kill", "write", "submit", "close"}:
        return json.dumps(
            {"error": f"Unknown process action: {action}. Use: list, poll, log, wait, kill, write, submit, close"},
            ensure_ascii=False,
        )
    if not session_id:
        return json.dumps({"error": f"session_id is required for {action}"}, ensure_ascii=False)

    try:
        session = REGISTRY.get(session_id)
    except KeyError:
        if action == "kill":
            try:
                return _dump(close_session(session_id, desktop_id))
            except KeyError:
                return json.dumps({"status": "not_found", "error": f"No process with ID {session_id}"}, ensure_ascii=False)
        return json.dumps({"status": "not_found", "error": f"No process with ID {session_id}"}, ensure_ascii=False)

    if action == "poll":
        visible = session.visible_text()
        result = {
            "session_id": session.id,
            "command": session.command,
            "status": session.state,
            "pid": session.pid,
            "uptime_seconds": int(time.time() - session.started_at),
            "output_preview": visible[-1000:],
        }
        if session.state != "running" and session.exit_code is not None:
            result["exit_code"] = session.exit_code
        return _dump(result)

    if action == "log":
        visible = session.visible_text()
        lines = visible.splitlines()
        total = len(lines)
        if offset == 0 and limit > 0:
            selected = lines[-limit:]
        else:
            selected = lines[offset:offset + limit]
        return _dump({
            "session_id": session.id,
            "command": session.command,
            "status": session.state,
            "output": "\n".join(selected),
            "total_lines": total,
            "showing": f"{len(selected)} lines",
        })

    if action == "wait":
        seconds = 180 if timeout is None else timeout

        def run_wait() -> str:
            result = session.wait_command(seconds, is_interrupted)
            return _dump(result)

        if ctx is None:
            return run_wait()
        with worker_thread_of_task(ctx.task_id):
            return run_wait()

    from fm.backend.terminal import focus_session

    try:
        if action in {"write", "submit", "close", "kill"}:
            if session.session_key and session.session_key != chat_key:
                who = session.owner or "别人"
                return json.dumps({"status": "error", "error": f"这个终端有人（{who}）"}, ensure_ascii=False)
        if action == "kill":
            return _dump(close_session(session_id, desktop_id))
        if action == "write":
            focus_session(session, desktop_id)
            session.write_raw(str(data))
            return json.dumps({"status": "ok", "bytes_written": len(str(data))}, ensure_ascii=False)
        if action == "submit":
            focus_session(session, desktop_id)
            session.submit_line(str(data))
            return json.dumps({"status": "ok"}, ensure_ascii=False)
        focus_session(session, desktop_id)
        session.send_eot()
        return json.dumps({"status": "ok", "message": "EOF sent"}, ensure_ascii=False)
    except (RuntimeError, ValueError) as exc:
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
