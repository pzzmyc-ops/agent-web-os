"""terminal 工具 —— 在 webos 上打开系统终端并操作它。

终端是共用的。有人无人由 agent 用 mark 自己标，跑命令不会自动改这块牌子。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from fm.backend.pathutil import from_agent_path, resolve
from fm.backend.terminal import (
    REGISTRY,
    close_session,
    focus_session,
    open_on_desktop,
    set_occupancy,
)

from ..maf_tools.interrupt import is_interrupted, worker_thread_of_task

from ._context import current_turn
from ._registry import registry
from ._spill import OUTPUT_HEAD_CHARS, OUTPUT_TAIL_CHARS, spill_text

DESCRIPTION = (
    "在桌面终端里执行命令。"
    "有人无人用 mark 自己标。开始用这间终端时填有人。"
    "只有你认为用户的这次任务已经完成，才填无人。"
    "一条命令结束、终端变空闲、还要继续查或改，都不是任务完成，mark 留空。"
    "无人的可以进，有人的不要进。没有能进的再新开。"
)

TERMINAL_DOC = """在 webos 的终端窗口里操作一个系统 shell。终端是共用的。

每间终端有两块牌子：
- 有人·某对话 / 无人：你用参数 mark 填写。
- 空闲 / 忙碌：里面有没有命令在跑，这条会自己变，不用你填。命令结束变成空闲，不代表任务完成。

mark=有人：你开始用这间终端、接下来还要在里面做事时填。
mark=无人：只有你自己认为用户的这次任务已经完成，才填。下面这些都不是任务完成，不要标无人：
- 这一条命令跑完了
- 终端从忙碌变回空闲
- 这一小步做完了，但任务还有后续
- 你还可能要再进这间终端

标了有人之后，继续打命令时 mark 留空，牌子保持有人，直到任务完成。

用法：
- 看到无人：可以传它的 session_id 进去。决定占用就 mark=有人。
- 看到有人（不是你）：不要进。
- 没有无人的：不传 session_id，新开一间，需要占住再 mark=有人。
- 可以同时用不同的终端。任务没完成就不要把它们标成无人。

不带 session_id：新开一个终端。workdir 留空则用工作区目录。
带 session_id：进这间终端。不要再传 workdir，换目录就在终端里 cd。
mark：有人、无人，或留空。留空表示不改牌子。
command 留空：只改牌子，或只打开窗口，不打命令。

background 为 false：打完这一行后等到它结束再返回。到点还没结束就不杀，窗口留着，status 为 running，之后用 process 接着看。
background 为 true：打完立刻返回 session_id，窗口留着继续跑。

人在同一个窗口里打的字会和你交错。这是同一条终端。

process 工具用来看、等、往正在跑的程序里写、关掉：
  process(action='poll')    看状态和最近输出
  process(action='log')     翻这个窗口里已经打出来的内容
  process(action='wait')    等到当前这行结束
  process(action='write')   原样写入，不加回车
  process(action='submit')  在提示符上再提交一行
  process(action='close')   发送 Ctrl+D，不关窗口
  process(action='kill')    关掉窗口并停掉 shell
  process(action='list')    列出还开着的终端，包括人打开的

对话里点停止只会打断你这次等待，不会关掉终端。要停掉里面的程序就 write 一个中断，或 kill 关掉这个终端。

输出超过 1 万字符时只返回开头和结尾各 5000 字符，完整内容存进 output_file，elided_chars 是省略的字符数。
要看中间的内容就用 grep 搜 output_file。不要用 file_access_read 读它，那会把整个文件读进来。
会刷大量日志的命令，先在命令里重定向到文件。

不要用 shell 干文件的活，那些有专门的工具：
  读文件用 file_access_read，搜内容用 grep，找文件名用 glob，列目录用 file_access_ls，
  改文件用 file_access_replace，建文件用 file_access_write。
terminal 留给构建、安装、git、跑脚本，以及真的需要 shell 的事。"""


def _chat_name(ctx, session_key: str) -> str:
    title = ""
    if ctx is not None and getattr(ctx, "store", None) is not None and ctx.thread_id:
        title = str(ctx.store.thread_title(ctx.thread_id) or "").strip()
    return title or session_key or "未命名对话"


def terminal_windows_note() -> str:
    from fm.backend.terminal import sign_text

    rows = REGISTRY.list()
    lines = ["终端牌子（有人无人只在你填 mark 时改。只有你认为这次任务已经完成才能标无人，命令结束不要标）："]
    if not rows:
        lines.append("现在没有终端。需要时新开一间。")
        return "\n".join(lines)
    for row in rows:
        sign = sign_text(row.get("sessionKey") or "", row.get("owner") or "", row.get("state") or "")
        lines.append(f"- {sign} session_id={row['id']} 目录={row['path']}")
    return "\n".join(lines)


def _resolve_workdir(rel: str) -> Path:
    virt = from_agent_path(rel, is_directory=True)
    return Path(resolve(virt))


def fit_output(payload: dict) -> dict:
    output = payload.get("output")
    if not isinstance(output, str):
        return payload
    spilled = spill_text(output)
    if spilled is None:
        return payload
    payload["output"] = spilled["text"]
    payload["elided_chars"] = spilled["elided_chars"]
    payload["output_file"] = spilled["output_file"]
    payload["output_lines"] = spilled["output_lines"]
    payload["warning"] = "输出太长，只保留了开头和结尾。要看中间的内容就用 grep 搜 output_file，不要用 file_access_read 读它。"
    return payload


def _dump(payload: dict, command: str = "") -> str:
    from ..maf_tools.agent.redact import redact_sensitive_text, redact_terminal_output

    if payload.get("output"):
        payload["output"] = redact_terminal_output(payload["output"], command)
    if payload.get("command"):
        payload["command"] = redact_sensitive_text(payload["command"], code_file=True)
    return json.dumps(fit_output(payload), ensure_ascii=False)


def _fail(message: str) -> str:
    return json.dumps(
        {"output": "", "exit_code": None, "session_id": "", "status": "error", "error": message},
        ensure_ascii=False,
    )


@registry.register(
    toolset="terminal",
    name="terminal",
    summary="在桌面终端里执行命令",
    description=DESCRIPTION,
    doc=TERMINAL_DOC,
)
def terminal(
    command: str = "",
    session_id: str = "",
    workdir: str = "",
    timeout: int | None = None,
    background: bool = False,
    notify_on_complete: bool = False,
    watch_patterns: list[str] | None = None,
    pty: bool = True,
    mark: Annotated[
        Literal["", "有人", "无人"],
        "有人、无人，或留空。留空不改牌子。开始占用填有人。只有你认为用户的这次任务已经完成才填无人。命令结束不是任务完成。",
    ] = "",
) -> str:
    """在桌面终端里执行一条命令，或只改有人无人牌子。

    Args:
        command: 要打进终端的一行。留空表示不打命令。
        session_id: 要进入的终端。无人的可以进。都有人或都在忙碌时留空，新开一间。
        workdir: 新终端的目录。留空表示工作区。已有终端上不能传。
        timeout: 前台等待的秒数。留空为 180。到点不杀进程。
        background: True 表示打完就返回，窗口继续跑。
        notify_on_complete: 后台命令结束时通知你。不要和 watch_patterns 同时设。
        watch_patterns: 后台输出里出现这些字时通知你。同一终端至少隔 15 秒。
        pty: 必须为 True。这个终端始终是系统终端。
        mark: 有人、无人，或留空。只有任务完成才填无人。
    """
    mark = str(mark or "").strip()
    if mark not in {"", "有人", "无人"}:
        return _fail("mark 只能是有人、无人，或留空")
    if pty is False:
        return _fail("这个终端始终是系统终端")
    ctx = current_turn()
    desktop_id = ctx.desktop_id if ctx is not None else ""

    note = ""
    patterns = watch_patterns
    if background and notify_on_complete and patterns:
        patterns = None
        note = "watch_patterns 已忽略，因为同时设置了 notify_on_complete"

    from ..maf_tools.session_key import get_current_session_key

    session_key = get_current_session_key(default="") or ""
    if background and (notify_on_complete or patterns) and not session_key:
        return _fail("当前没有会话，无法投递完成通知")
    wait_seconds = 180 if timeout is None else timeout
    if wait_seconds <= 0:
        return _fail("timeout 必须大于 0")

    def run() -> str:
        sid = str(session_id or "")
        if sid:
            if workdir:
                return _fail("已经打开的终端不能再传 workdir，要换目录就在里面 cd")
            try:
                session = REGISTRY.get(sid)
            except KeyError:
                return _fail(f"没有这个终端: {sid}")
            if session.session_key and session.session_key != session_key:
                who = session.owner or "别人"
                return _fail(f"这个终端有人（{who}）")
        else:
            try:
                cwd = _resolve_workdir(workdir)
            except ValueError as exc:
                return _fail(str(exc))
            if not cwd.is_dir():
                return _fail(f"workdir 不存在: {workdir or str(cwd)}")
            session = open_on_desktop(str(cwd), desktop_id)

        if mark == "有人":
            set_occupancy(session, "有人", session_key, _chat_name(ctx, session_key))

        if not str(command or "").strip():
            if background:
                return _fail("后台运行需要一条命令")
            if mark == "无人":
                set_occupancy(session, "无人", session_key, _chat_name(ctx, session_key))
            payload = {
                "output": "",
                "exit_code": None,
                "session_id": session.id,
                "status": session.state if session.state != "starting" else "idle",
                "error": None,
            }
            if note:
                payload["note"] = note
            return _dump(payload)

        focus_session(session, desktop_id)
        session.submit_line(
            command,
            notify=bool(background and notify_on_complete),
            patterns=patterns if background else None,
            session_key=session_key,
        )
        if background:
            output, _code = session.command_view()
            payload = {
                "output": output,
                "exit_code": None,
                "session_id": session.id,
                "status": "running",
                "error": None,
            }
            if note:
                payload["note"] = note
            if mark == "无人":
                set_occupancy(session, "无人", session_key, _chat_name(ctx, session_key))
            return _dump(payload, command)

        result = session.wait_command(wait_seconds, is_interrupted)
        payload = {
            "output": result["output"],
            "exit_code": result["exit_code"],
            "session_id": session.id,
            "status": result["status"],
            "error": None,
        }
        if note:
            payload["note"] = note
        if mark == "无人":
            set_occupancy(session, "无人", session_key, _chat_name(ctx, session_key))
        return _dump(payload, command)

    with worker_thread_of_task(ctx.task_id):
        try:
            return run()
        except Exception as exc:
            return _fail(str(exc))
