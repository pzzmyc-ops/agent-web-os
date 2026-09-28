"""会话事件日志的词汇表与投影。

以前落盘的是「加工好的结果」:一条 assistant 记录里塞 content + reasoning + blocks
(工具块的 JSON 字符串),工具和正文的先后顺序靠约定还原。这带来三个必然后果:

- 工具调用先落盘、结果后到达,结果就再也写不回去 —— 历史里那次调用永远没有结果,
  模型下一轮只知道自己调过,不知道拿回了什么;
- 中断的调用只剩半边,为了不让上游 400 只能整块丢掉,模型下一轮会把活重做一遍;
- 发请求前还得重排一次消息顺序,把配不上的成对丢弃。

现在 tool_call 与 tool_result 是两条独立事件,各带 call_id。投影按事件顺序直接生成
消息,不需要猜谁在谁前面。

一条事件的公共字段:id(线程内单调递增)、type、task_id、created_at。
"""
from __future__ import annotations

import json
from typing import Any

EV_USER = "user_message"
EV_ASSISTANT = "assistant_message"
EV_TOOL_CALL = "tool_call"
EV_TOOL_RESULT = "tool_result"
EV_SYSTEM = "system_notice"
EV_RECALL = "recall"
EV_PROGRAM_TIMER = "program_timer"
EV_TURN_END = "turn_end"
EV_PLAN_DOC = "plan_doc"
EV_SESSION_UI = "session_ui"
EV_SESSION_MEMORY = "session_memory"
EV_SESSION_CTX = "session_ctx"
EV_COMPACTION = "compaction"
EV_COMPACTED_TOOL = "compacted_tool"

#: 压缩后的工具记录在前端卡片上的名字。
COMPACTED_TOOL_LABEL = "中段压缩结果"

#: 参与投影的事件类型。turn_end 只是结构标记,不进任何一侧的历史。
_PROJECTED = frozenset({
    EV_USER, EV_ASSISTANT, EV_TOOL_CALL, EV_TOOL_RESULT,
    EV_SYSTEM, EV_RECALL, EV_PROGRAM_TIMER, EV_COMPACTION, EV_COMPACTED_TOOL,
})

#: 前端历史里的角色名 → 事件类型。编辑重发按角色计数,两边必须用同一套。
ROLE_TO_TYPE = {
    "user": EV_USER,
    "assistant": EV_ASSISTANT,
    "system": EV_SYSTEM,
    "recall": EV_RECALL,
}


def is_orchestration_tool(name: str) -> bool:
    n = str(name or "")
    return n in ("task_complete", "round_complete") or n.startswith("handoff_to_")


def attachment_hint(att: dict[str, Any]) -> str | None:
    """附件 → 写进模型消息的路径提示。

    不写模型就不知道文件在哪,file_access_read 也无从读起。
    """
    path = str(att.get("path") or att.get("media_id") or "").replace("\\", "/")
    if not path:
        return None
    name = str(att.get("filename") or path)
    mime = str(att.get("mime_type") or "").lower()
    if mime.startswith("image/"):
        return f"[用户提供了图片 {name},路径: {path},需要再看时用 view_image 查看]"
    return f"[用户提供了文件 {name},路径: {path},请用 file_access_read 读取]"


def logical_records(events: list[dict]) -> list[dict]:
    """事件流 → 逻辑记录。相邻的同一发言人 assistant 事件合并成一条。

    为什么要合并:一个人的发言会被工具和自续循环切成好几段,每段都是一条独立事件
    (这是对的,顺序信息全在里面)。但展示和计数要按「一次发言」算,否则自续空转一次
    历史里就多一个「我继续待命」的碎片气泡。

    合并只在投影时做,日志本身保持只追加 —— 以前是靠回写上一条消息的 content 来合并,
    那需要重写整个文件,而且并发时会写坏。

    每条记录带 `ids`(来源事件 id 列表)。编辑重发要按记录截断,截到最后一个来源 id。
    """
    out: list[dict] = []
    for ev in events:
        etype = str(ev.get("type") or "")
        if etype not in _PROJECTED:
            continue
        rec = dict(ev)
        rec["ids"] = [int(ev.get("id") or 0)]
        if etype == EV_ASSISTANT and out:
            prev = out[-1]
            same_speaker = (
                prev.get("type") == EV_ASSISTANT
                and str(prev.get("name") or "") == str(rec.get("name") or "")
            )
            if same_speaker:
                prev["content"] = str(prev.get("content") or "") + str(rec.get("content") or "")
                prev["reasoning"] = str(prev.get("reasoning") or "") + str(rec.get("reasoning") or "")
                prev["ids"].append(int(ev.get("id") or 0))
                continue
        out.append(rec)
    return out


def nth_record_of_role(events: list[dict], role: str, n: int) -> dict | None:
    """第 n 条某角色的逻辑记录(n 从 1 开始)。编辑重发用它定位目标。"""
    want = ROLE_TO_TYPE.get(role)
    if not want:
        return None
    count = 0
    for rec in logical_records(events):
        if rec.get("type") != want:
            continue
        count += 1
        if count == n:
            return rec
    return None


def result_call_ids(events: list[dict]) -> set[str]:
    """日志里已经有结果的 call_id。"""
    return {
        str(ev.get("call_id") or "")
        for ev in events
        if str(ev.get("type") or "") == EV_TOOL_RESULT and ev.get("call_id")
    }


def unpaired_calls(events: list[dict]) -> list[dict]:
    """有调用、没有结果的 tool_call 事件。

    正常收尾的回合不会留下这种事件 —— 中断时 ToolTracker.close_open 会补一条
    说明中断的 tool_result。留下来只有一个原因:进程在回合中途被杀,来不及补。
    进程下次启动时由 recover_unpaired 补齐,而不是在投影时临时圆过去:
    「这次调用没有结果」是真实发生过的事,它该在日志里有一行,而不是每次读的时候
    重新编一遍。
    """
    have = result_call_ids(events)
    return [
        ev
        for ev in events
        if str(ev.get("type") or "") == EV_TOOL_CALL
        and str(ev.get("call_id") or "") not in have
    ]


def _tool_call_public(ev: dict) -> dict:
    """tool_call 事件 → 前端 assistant 事件里的 toolCalls 项(OpenAI 三件套形状)。

    前端认的是 block_id(直播时 block_open 用的那个),不是模型给的 call_id:模型的
    call_id 只保证一次调用里唯一,有些模型直接发 "0"、"1",跨回合会在 DOM 里撞车。
    block_id 带 task_id,天生不会撞。模型侧的配对用 call_id,见 history_provider。
    """
    return {
        "id": str(ev.get("block_id") or ""),
        "type": "function",
        "function": {
            "name": str(ev.get("name") or ""),
            "arguments": json.dumps(ev.get("arguments") or {}, ensure_ascii=False),
        },
    }


def _tool_result_public(call: dict, result: dict | None) -> dict:
    """tool_call + tool_result → 前端的 tool 事件。"""
    ev = result or {}
    out = {
        "type": "tool",
        "toolCallId": str(call.get("block_id") or ""),
        "name": str(call.get("name") or ""),
        "content": str(ev.get("result") or ""),
        "taskId": str(call.get("task_id") or ""),
        "createdAt": call.get("created_at"),
        "completedAt": ev.get("completed_at"),
        "durationMs": ev.get("duration_ms"),
    }
    # render_media 之类挂在工具结果上的媒体载荷:前端 history.js 读它把图渲回来
    if ev.get("media"):
        out["media"] = ev["media"]
    return out


def to_frontend_events(events: list[dict]) -> list[dict]:
    """事件流 → 前端历史事件。

    前端的契约是「assistant 事件带 toolCalls,后面跟着各自的 tool 事件」,渲染顺序是
    工具卡在正文之前(history.js 先 upsert 工具块再放文本)。所以一段工具要挂到它
    **后面**那段文字上 —— 直播时的顺序就是「工具跑完、然后接着说」。

    最后一段文字之后才出现的工具没有下一段可挂,单独发一条空正文的 assistant 事件
    带着它们;前端只要 toolCalls 非空就会渲染(history.js 的 etype === "assistant" 分支)。
    """
    results = {
        str(ev.get("call_id") or ""): ev
        for ev in events
        if str(ev.get("type") or "") == EV_TOOL_RESULT
    }
    out: list[dict] = []
    pending: list[dict] = []

    def flush_pending(task_id: str, created_at: Any) -> None:
        """把攒着的工具挂到一条空正文的 assistant 事件上(没有下一段文字时)。"""
        if not pending:
            return
        out.append({
            "id": pending[0].get("id"),
            "type": "assistant",
            "role": "assistant",
            "taskId": task_id,
            "createdAt": created_at,
            "content": "",
            "toolCalls": [_tool_call_public(c) for c in pending],
        })
        for call in pending:
            out.append(_tool_result_public(call, results.get(str(call.get("call_id") or ""))))
        pending.clear()

    for rec in logical_records(events):
        etype = str(rec.get("type") or "")
        task_id = str(rec.get("task_id") or "")
        created_at = rec.get("created_at")

        if etype == EV_TOOL_CALL:
            if is_orchestration_tool(str(rec.get("name") or "")):
                continue
            pending.append(rec)
            continue
        if etype == EV_TOOL_RESULT:
            continue

        if etype == EV_ASSISTANT:
            ev: dict[str, Any] = {
                "id": rec.get("id"),
                "type": "assistant",
                "role": "assistant",
                "taskId": task_id,
                "createdAt": created_at,
                "content": str(rec.get("content") or ""),
            }
            if rec.get("name"):
                ev["agentName"] = rec["name"]
            if rec.get("reasoning"):
                ev["thinking"] = rec["reasoning"]
            if pending:
                ev["toolCalls"] = [_tool_call_public(c) for c in pending]
            out.append(ev)
            for call in pending:
                out.append(_tool_result_public(call, results.get(str(call.get("call_id") or ""))))
            pending.clear()
            continue

        if etype == EV_COMPACTED_TOOL:
            # 中段压缩留下的工具记录。原始的 tool_call / tool_result 两条事件已经
            # 被合成一条,这里拆回前端要的「assistant 带 toolCalls + tool 结果」形状,
            # 复用工具卡片的渲染。卡片名字统一叫「中段压缩结果」而不是原工具名 ——
            # 它是压缩后的转述,不是一次真的调用,和上下那些真工具卡要能一眼分开;
            # 具体调了哪个工具写在卡片里面。
            if pending:
                flush_pending(
                    str(pending[0].get("task_id") or ""),
                    pending[0].get("created_at"),
                )
            block_id = str(rec.get("block_id") or ("compacted:" + str(rec.get("id") or "")))
            out.append({
                "id": rec.get("id"),
                "type": "assistant",
                "role": "assistant",
                "taskId": task_id,
                "createdAt": created_at,
                "content": "",
                "toolCalls": [{
                    "id": block_id,
                    "type": "function",
                    "function": {
                        "name": COMPACTED_TOOL_LABEL,
                        "arguments": json.dumps(
                            {"工具": str(rec.get("name") or ""), "参数": rec.get("arguments") or {}},
                            ensure_ascii=False,
                        ),
                    },
                }],
            })
            out.append({
                "type": "tool",
                "toolCallId": block_id,
                "name": COMPACTED_TOOL_LABEL,
                "content": str(rec.get("result") or ""),
                "taskId": task_id,
                "createdAt": created_at,
            })
            continue

        if pending:
            flush_pending(
                str(pending[0].get("task_id") or ""),
                pending[0].get("created_at"),
            )
        role = {
            EV_USER: "user",
            EV_SYSTEM: "system",
            EV_RECALL: "recall",
            EV_PROGRAM_TIMER: "program_timer",
            # 前段全损压缩的结果。原文已经被替换掉了,所以正文里放的是摘要本身,
            # 不是一句「已压缩」;前端拿它渲染成一张独立的压缩结果卡片。
            EV_COMPACTION: "compaction",
        }[etype]
        ev = {
            "id": rec.get("id"),
            "type": role,
            "role": role,
            "taskId": task_id,
            "createdAt": created_at,
            "content": str(rec.get("content") or ""),
        }
        if etype == EV_USER and rec.get("attachments"):
            ev["attachments"] = rec["attachments"]
        if etype == EV_COMPACTION:
            ev["covered"] = int(rec.get("covered") or 0)
            ev["level"] = str(rec.get("level") or "brief")
        # 文件检查点:用户消息带它自己的点;压缩摘要带被压掉那段里第一条消息的点,
        # 前端据此渲染「恢复到此之前」按钮。
        if rec.get("checkpoint_id"):
            ev["checkpointId"] = str(rec["checkpoint_id"])
        out.append(ev)

    flush_pending(
        str(pending[0].get("task_id") or "") if pending else "",
        pending[0].get("created_at") if pending else None,
    )
    return out


def task_snapshot(events: list[dict], task_id: str) -> dict:
    """一个 task 已落盘的正文、思考和工具块。

    形状和 TurnEventBus 的快照一致,所以前端不用区分「这份数据来自内存还是磁盘」。
    内存总线只保留活着的那一轮(3 分钟 TTL、8000 条上限),这里是它的持久版本。
    """
    results = {
        str(ev.get("call_id") or ""): ev
        for ev in events
        if str(ev.get("type") or "") == EV_TOOL_RESULT
    }
    content = ""
    reasoning = ""
    blocks: list[dict] = []
    for ev in events:
        if str(ev.get("task_id") or "") != task_id:
            continue
        etype = str(ev.get("type") or "")
        if etype == EV_ASSISTANT:
            content += str(ev.get("content") or "")
            reasoning += str(ev.get("reasoning") or "")
        elif etype == EV_TOOL_CALL:
            if is_orchestration_tool(str(ev.get("name") or "")):
                continue
            res = results.get(str(ev.get("call_id") or "")) or {}
            blk = {
                "blockId": str(ev.get("block_id") or ""),
                "kind": "tool",
                "name": str(ev.get("name") or ""),
                "input": ev.get("arguments") or {},
                "text": "",
                "result": str(res.get("result") or ""),
                "status": str(res.get("status") or "open"),
            }
            if ev.get("extra_content"):
                blk["extra_content"] = ev["extra_content"]
            if res.get("duration_ms") is not None:
                blk["durationMs"] = res["duration_ms"]
            if res.get("completed_at") is not None:
                blk["completedAt"] = res["completed_at"]
            if res.get("media"):
                blk["media"] = res["media"]
            blocks.append(blk)
    return {"content": content, "reasoning": reasoning, "blocks": blocks}
