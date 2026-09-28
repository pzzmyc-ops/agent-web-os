from __future__ import annotations

import json
from pathlib import Path

from fm.backend.pathutil import from_agent_path, resolve

from ..groupchat.runtime import require_group_chat
from ._context import require_turn

GROUP_TASK_COMPLETE = "task_complete"


def make_group_task_complete():
    from agent_framework import tool

    @tool(
        name=GROUP_TASK_COMPLETE,
        description=(
            "群聊收尾检查，只有群里被@的这一轮才能用，不是多agent也不是桌面收尾。"
            "调用前先自己核对：这次@要你做的事是不是已经做完，结论和文件是不是已经送到群里了。"
            "summary 必填，写这轮的结论，它同时是本轮的收尾记录。"
            "send_summary 由你判断：群里还没有人看到这轮结论就填 true，把 summary 发到绑定群；"
            "结论已经在群里说过、或者按当前任务的规矩这个群不该收到这条结论，就填 false，只收尾不发言。"
            "files 只填这轮产出、且还没送到群里的文件真实绝对路径（正斜杠，相对路径按工作区目录解析），多个用逗号分隔，"
            "已经用别的方式发过的文件不要再填，填了就会重复发一次。没有要补发的就留空。"
            "调用后本轮立刻结束，不要再查群、不要再补发、不要再解释一遍。"
            "只写在对话窗口里不算完成，不要问要不要发。"
        ),
        approval_mode="never_require",
    )
    def task_complete(summary: str, send_summary: bool, files: str = "") -> str:
        body = (summary or "").strip()
        if not body:
            raise RuntimeError("总结不能为空")
        ctx = require_turn()
        flag = ctx.group_complete
        if flag is None:
            raise RuntimeError("task_complete 只能在群聊轮次使用")
        if flag.get("done"):
            raise RuntimeError("本轮已经收尾过，不要重复调用，也不要再补发内容")
        sent: list[str] = []
        raw = (files or "").strip()
        if send_summary or raw:
            gc = require_group_chat()
            bind = gc.bindings.get_by_thread(ctx.thread_id)
            if bind is None:
                raise RuntimeError("当前对话没有绑定群")
            cid = str(bind.get("conversation_id") or "").strip()
            if not cid:
                raise RuntimeError("绑定缺少会话ID")
            adapter = gc.adapter(str(bind["channel"]))
        if send_summary:
            adapter.send_text(cid, body)
        if raw:
            for part in raw.split(","):
                item = part.strip()
                if not item:
                    raise RuntimeError("files 含有空路径")
                rel = from_agent_path(item)
                full = Path(resolve(rel))
                if not full.is_file():
                    raise RuntimeError("文件不存在: " + rel)
                adapter.send_file(cid, str(full))
                sent.append(rel)
        flag["done"] = True
        return json.dumps(
            {
                "ok": True,
                "summary_sent": bool(send_summary),
                "files": sent,
                "note": "本轮已收尾，不要再发消息、不要再查群、不要再补做动作",
            },
            ensure_ascii=False,
        )

    return task_complete
