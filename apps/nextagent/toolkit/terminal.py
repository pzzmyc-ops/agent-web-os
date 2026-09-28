"""terminal 工具 —— agent 的通用执行能力延伸。

比直接暴露 maf_tools.terminal_tool 多做三件事:

1. **默认工作目录改成工作区目录**。terminal_tool 默认跑在服务进程的 cwd(mafagent 项目目录),
   而 file_access_* 默认落在工作区目录 —— 同一个 agent 的两个工具对「工作区」的理解不一致,
   模型写 `ls` 看到的是项目源码,和它用 file_access_ls 看到的完全不是一回事。
2. **暴露 workdir 与 timeout**。
3. **workdir 走 pathutil**,和 file_access 的约定一致:真实绝对路径,相对路径按工作区解析。

工具描述(DESCRIPTION)是给模型看的,所以里面提到的替代工具必须是**这个项目真实存在的**
框架工具名(file_access_read / _grep / _ls / _write / _replace),不是 hermes 的
read_file / search_files / patch —— 后者在这里不存在,写进描述等于给模型指错路。
"""
from __future__ import annotations

import json
from pathlib import Path

from fm.backend.pathutil import from_agent_path, resolve

from ..maf_tools.interrupt import worker_thread_of_task

from ._context import current_turn
from ._registry import registry

#: 给模型的工具描述。核心意图:别用 shell 干文件的活 —— 文件操作走框架的
#: file_access_* 工具(它们受工作区沙箱约束、有审批规则、输出更省 token)。
DESCRIPTION = "执行 shell 命令"

TERMINAL_DOC = """在工作区里执行 shell 命令。文件系统、当前目录、导出的环境变量在多次调用之间保持。

不要用 shell 干文件的活 —— 这些都有专门的工具,更省 token 且受工作区约束:
  读文件      用 file_access_read,不要 cat/head/tail
  搜内容      用 grep,不要 shell 的 grep/rg
  找文件名    用 glob,不要 find/dir /s
  按含义找    用 semantic_search
  列目录      用 file_access_ls,不要 ls
  改文件      用 file_access_replace / file_access_replace_lines,不要 sed/awk
  建文件      用 file_access_write,不要 echo 重定向或 heredoc
  移动/复制   用 file_access_move / file_access_copy,不要 mv/cp/move/copy
  重命名      用 file_access_rename,不要 mv/ren
  建目录      用 file_access_mkdir,不要 mkdir
  删文件      用 file_access_delete,不要 rm/del

terminal 留给这些:构建、安装依赖、git、进程管理、跑脚本、网络请求、包管理器,
以及任何真的需要 shell 的事。

环境变量会保持,所以激活 venv 或 export 只需做一次,不要每条命令前都重新 source。

前台(默认):命令一结束就立刻返回,即使 timeout 设得很大。长任务把 timeout 设大即可,
跑得快照样秒回。短命令优先用前台。
前台到点还没结束的命令不会被杀:进程原样转成后台会话,返回 status=backgrounded、session_id
和到那一刻为止的输出,之后用 process 工具跟进。所以「超时」不等于「失败」,更不等于「没结果」。
如果转后台是因为命令本身选错了(比如拿 find 扫整块磁盘),先 process(action='kill') 停掉,
再换 glob / grep / file_access_ls 重做,不要原样重跑一遍。

后台(background=true):立刻返回一个 session_id,用 process 工具查询:
  process(action='poll')  看状态和新输出
  process(action='wait')  阻塞等它结束
  process(action='log')   取完整输出
适合永不退出的进程(服务器、watcher)和耗时很长的任务(测试、构建、部署)。
起服务后不要盲目 sleep —— 用健康检查或日志信号确认就绪,再另起一次 terminal 调用跑测试。
前台模式下不要用 nohup / disown / setsid / 结尾 `&` 这类 shell 级后台化,用 background=true,
否则进程脱离管理、输出也收不到。

不要在没有伪终端的情况下用 vim/nano 这类交互式工具 —— 它们会挂住。
git 输出可能分页时管给 cat。"""

def _resolve_workdir(rel: str) -> Path:
    virt = from_agent_path(rel, is_directory=True)
    return Path(resolve(virt))


@registry.register(
    toolset="terminal",
    name="terminal",
    summary="在工作区里执行 shell 命令",
    description=DESCRIPTION,
    doc=TERMINAL_DOC,
)
def terminal(
    command: str,
    workdir: str = "",
    timeout: int | None = None,
    background: bool = False,
    notify_on_complete: bool = False,
    watch_patterns: list[str] | None = None,
    pty: bool = False,
) -> str:
    """在工作区里执行一条 shell 命令,返回输出与退出码。

    Args:
        command: 要执行的命令。
        workdir: 工作目录的真实绝对路径(正斜杠,如 D:/proj);相对路径按工作区目录解析;留空表示工作区目录。
        timeout: 秒数上限,留空表示用默认上限。
        background: True 表示后台执行,立即返回 task_id;False 表示等命令完成。
        notify_on_complete: True 且 background=True 时,进程退出后自动通知你。
            适合测试、构建、部署等有明确终点的长任务。不要和 watch_patterns 同时设。
        watch_patterns: 后台输出的关键字列表,命中时通知你(每进程每 15 秒最多一次)。
            适合服务器就绪信号等中途单次事件,不要用于循环/批量任务中会反复出现的模式。
            和 notify_on_complete 互斥,不要同时设。
        pty: True 表示使用伪终端,适合需要交互式终端的工具(如 Python REPL、Codex CLI)。
    """
    from ..maf_tools.terminal_tool import terminal_tool

    try:
        cwd = _resolve_workdir(workdir)
    except ValueError as exc:
        return json.dumps({"output": "", "exit_code": -1, "error": str(exc)}, ensure_ascii=False)

    if not cwd.is_dir():
        return json.dumps(
            {"output": "", "exit_code": -1, "error": f"workdir 不存在: {workdir or str(cwd)}"},
            ensure_ascii=False,
        )

    kwargs = {
        "command": command, "background": background, "workdir": str(cwd),
        "notify_on_complete": notify_on_complete,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    if watch_patterns is not None:
        kwargs["watch_patterns"] = watch_patterns
    if pty:
        kwargs["pty"] = pty

    # 登记本线程,好让用户点停止时能把这条命令的子进程真正杀掉。没有当前轮
    # (单测、CLI 直接调)就没有可取消的对象。
    ctx = current_turn()
    if ctx is None:
        return str(terminal_tool(**kwargs))
    with worker_thread_of_task(ctx.task_id):
        return str(terminal_tool(**kwargs))
