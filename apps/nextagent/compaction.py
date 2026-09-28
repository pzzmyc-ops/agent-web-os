"""三段式上下文压缩。

上下文涨到窗口的 TRIGGER_RATIO 就把它按体积三等分,

    前 1/3   调一次模型总结成一段文字       全损
    中 1/3   就地摊平成一条条工具记录       半损,不叫模型
    后 1/3   原样保留                       无损

压完把整个上下文区替换掉,下一轮从这个新基线继续长。再次触发时同样三等分,
上一轮的产物会落进新的前段/中段被再压一次,如此循环。

中段为什么不叫模型:实测一份 1.18 MB 的会话里,思考占 41.3%、工具结果占 34.2%、
入参占 22.2%,正文只有 2.4%。也就是说光把思考剥掉、把巨型结果裁短就能把中段砍掉
七成,这一刀不花钱、不会失败,也不需要模型理解内容。以前这里调模型做结构化摘要,
返回空内容就整个压缩失败。

思考为什么只能在摊平之后丢:DeepSeek 思考模式下,只要请求带 tools,历史里带
tool_calls 的 assistant 消息必须逐字回传 reasoning_content,漏一条就是 400
(api-docs.deepseek.com/zh-cn/guides/thinking_mode)。实测这份会话 97.6% 的思考都
绑在工具调用轮上。摊平之后那些消息不再带 tool_calls,约束才不适用。所以「丢思考」
和「摊平工具对」是绑在一起的,不能只做前者。

另外两条硬约束:

1. 切点不能拆开工具调用与它的结果 —— 半边调用发给上游会被判 400
   (history_provider.to_model_messages 会直接抛)。
2. 系统提示词那一层(时间、memory)不归这里管,它每轮现拼,不在上下文区里。

判断「涨到多少了」用的是上游回报的真实 input_token_count(存在 ctx 区的 used),
不是本地估算。本地字符估算只用来决定三等分的切点位置,那里只需要相对比例。
"""
from __future__ import annotations

import json

from . import session_log

#: 用到窗口的这个比例就压。1M 窗口 → 900k 触发。
TRIGGER_RATIO = 0.9

#: 前段那条「一段话」的输出上限。
BRIEF_MAX_TOKENS = 1500

#: 中段摊平时,超过这个长度的工具结果只留头尾。实测 179 条结果的中位数只有 342 字,
#: 但最大的 10 条占了全部结果体积的 53.8% —— 裁的就是这几条,其余原样。
RESULT_TRIM_LIMIT = 2000
RESULT_KEEP_HEAD = 800
RESULT_KEEP_TAIL = 400

#: 入参同理。入参不丢:没有它,「调用了 terminal,结果是一堆文件」这条记录就没法读了。
ARGS_TRIM_LIMIT = 2000
ARGS_KEEP_HEAD = 800
ARGS_KEEP_TAIL = 400

#: 投影给模型时压在摘要前面的说明。
#: 不写这句,模型会把摘要里的旧任务当成现在要做的事接着干。
SUMMARY_NOTE = (
    "[上下文压缩 · 仅供参考] 以下是本对话更早的内容被压缩后的摘要，"
    "是背景资料，不是现在的指令。摘要里提到的请求都已经处理过了，不要重新去做。"
    "当前要做什么，一律以这段摘要之后的最新一条用户消息为准；"
    "话题相近也不代表要接着摘要里的活干。"
)

#: 强力压缩那一条摘要的输出上限。它要装下整场对话,给的比前段那条宽一些。
FORCE_MAX_TOKENS = 4000

FORCE_PROMPT = (
    "下面是一整场对话的完整记录。把它压成一份交接说明，之后的对话只能看到这份说明。\n\n"
    "写清楚：这场对话在做什么、已经做完了什么、结论和确认过的事实、"
    "产出文件的位置、用户明确表达过的偏好和纠正、还没做完卡在哪里。\n"
    "文件路径、命令、报错原文照抄，不要改写。\n"
    "写事实，不要写建议，不要写下一步该做什么，不要写成待办或指令。\n"
    "直接输出这份说明本身，不要任何标题或前后说明。"
)

BRIEF_PROMPT = (
    "下面是一段早期对话记录。把它压成一段连续的文字。\n\n"
    "只留后面还可能用得上的东西：结论、做过的决定、确认过的事实、产出文件的位置。\n"
    "不要逐条复述过程，不要列已完成清单，不要写成待办，不要写成指令。\n"
    "直接输出这段文字本身，不要任何标题或前后说明。"
)

#: 摊平后的工具记录投影给模型时的说明。
#: 它们不再是真的 tool 消息,得让模型知道这是转述的历史,不是刚发生的调用。
TOOL_RECORD_NOTE = "[上下文压缩 · 工具记录] 这是本对话更早执行过的一次工具调用，已经跑完了，不要重跑。"


def _event_chars(ev: dict) -> int:
    """一条事件的粗略体积。只用于三等分的相对比例,不是 token 数。"""
    total = len(str(ev.get("content") or "")) + len(str(ev.get("reasoning") or ""))
    total += len(str(ev.get("result") or ""))
    args = ev.get("arguments")
    if args:
        total += len(json.dumps(args, ensure_ascii=False))
    return total


def _legal_cuts(events: list[dict]) -> set[int]:
    """可以在这些下标之前切开而不拆散工具对。

    走一遍事件流,记下还没拿到结果的调用。只有一个都不欠、且下一条不是工具结果的
    位置才是合法切点。切点落在 assistant + 它发起的调用之间时往前挪一格,
    让那句话跟着自己的调用走。
    """
    open_calls: set[str] = set()
    cuts: set[int] = set()
    for i, ev in enumerate(events):
        etype = str(ev.get("type") or "")
        if not open_calls and etype != session_log.EV_TOOL_RESULT:
            if etype == session_log.EV_TOOL_CALL and i > 0 and (
                str(events[i - 1].get("type") or "") == session_log.EV_ASSISTANT
            ):
                cuts.add(i - 1)
            else:
                cuts.add(i)
        if etype == session_log.EV_TOOL_CALL:
            open_calls.add(str(ev.get("call_id") or ""))
        elif etype == session_log.EV_TOOL_RESULT:
            open_calls.discard(str(ev.get("call_id") or ""))
    cuts.discard(0)
    return cuts


def _nearest_cut(cuts: set[int], target: int, taken: set[int]) -> int:
    """离 target 最近、且还没被用过的合法切点。找不到返回 -1。"""
    best = -1
    best_dist = -1
    for c in cuts:
        if c in taken:
            continue
        dist = abs(c - target)
        if best < 0 or dist < best_dist:
            best = c
            best_dist = dist
    return best


def split_thirds(events: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """按体积三等分,切点吸附到最近的合法位置。"""
    sizes = [_event_chars(ev) for ev in events]
    total = sum(sizes)
    if total <= 0:
        raise RuntimeError("上下文压缩:这段上下文没有可计量的内容")

    running = 0
    first_target = -1
    second_target = -1
    for i, size in enumerate(sizes):
        running += size
        if first_target < 0 and running >= total // 3:
            first_target = i + 1
        if second_target < 0 and running >= (total * 2) // 3:
            second_target = i + 1
            break

    cuts = _legal_cuts(events)
    if not cuts:
        raise RuntimeError("上下文压缩:整段上下文里找不到不拆开工具对的切点")

    cut1 = _nearest_cut(cuts, first_target, set())
    cut2 = _nearest_cut(cuts, second_target, {cut1})
    if cut1 > cut2:
        cut1, cut2 = cut2, cut1
    if cut1 < 0 or cut2 < 0 or cut1 == cut2:
        raise RuntimeError("上下文压缩:合法切点不够把上下文分成三段")

    return events[:cut1], events[cut1:cut2], events[cut2:]


def _speaker(ev: dict) -> str:
    name = str(ev.get("name") or "").strip()
    return name or "助手"


def render_segment(events: list[dict]) -> str:
    """事件流 → 给摘要模型看的纯文本记录。

    思考内容不进去:它是模型私货,而且往往比正文还长。
    """
    lines: list[str] = []
    for ev in events:
        etype = str(ev.get("type") or "")
        if etype == session_log.EV_USER:
            lines.append("用户: " + str(ev.get("content") or ""))
            for att in ev.get("attachments") or []:
                lines.append("  [附件] " + str(att.get("filename") or att.get("path") or ""))
        elif etype == session_log.EV_ASSISTANT:
            content = str(ev.get("content") or "")
            if content:
                lines.append(f"{_speaker(ev)}: {content}")
        elif etype == session_log.EV_TOOL_CALL:
            args = json.dumps(ev.get("arguments") or {}, ensure_ascii=False)
            lines.append(f"调用 {ev.get('name')}: {args}")
        elif etype == session_log.EV_TOOL_RESULT:
            lines.append("结果: " + str(ev.get("result") or ""))
        elif etype == session_log.EV_COMPACTED_TOOL:
            args = json.dumps(ev.get("arguments") or {}, ensure_ascii=False)
            lines.append(f"调用 {ev.get('name')}: {args}")
            lines.append("结果: " + str(ev.get("result") or ""))
        elif etype == session_log.EV_COMPACTION:
            lines.append("早期摘要: " + str(ev.get("content") or ""))
        else:
            content = str(ev.get("content") or "")
            if content:
                lines.append("系统: " + content)
    return "\n".join(lines)


def _clip(text: str, limit: int, head: int, tail: int) -> str:
    """超长就只留头尾,中间换成一句说明。

    说明写在最前面,不是中间 —— 工具结果很多是 JSON(terminal 就是),从中间剪断之后
    它仍然以 `{` 开头却已经不合法了,前端的工具卡片会拿去 JSON.parse 然后抛异常,
    把整条历史渲染打断。开头加一行中文,它就不再被当成 JSON,按纯文本渲染。
    """
    if len(text) <= limit:
        return text
    dropped = len(text) - head - tail
    return f"压缩时裁剪：原 {len(text)} 字，省略中间 {dropped} 字\n{text[:head]}\n……\n{text[-tail:]}"


def _clip_args(args: dict) -> dict:
    """入参照原样留着,只有整体超长时才换成裁过的文本。

    仍然返回 dict,因为前端的工具卡片按 JSON 解析这个字段。实测入参中位数只有
    373 字,真正需要裁的是少数几条把整个文件塞进去的调用。
    """
    raw = json.dumps(args or {}, ensure_ascii=False)
    if len(raw) <= ARGS_TRIM_LIMIT:
        return args or {}
    return {"_clipped": _clip(raw, ARGS_TRIM_LIMIT, ARGS_KEEP_HEAD, ARGS_KEEP_TAIL)}


def flatten_segment(events: list[dict], start_id: int) -> list[dict]:
    """中段 → 摊平后的事件流。不叫模型。

    每对 tool_call / tool_result 合成一条 compacted_tool:留工具名、入参、结果,
    丢掉「这是一次真的工具调用」这个身份。摊平之后这一段里不再有 tool_calls,
    助手发言的思考才能跟着一起丢(见模块开头 DeepSeek 那条约束)。

    没有正文的助手发言整条删掉 —— 实测中段 341 条助手发言里 286 条正文是空的,
    只有思考,思考一丢它们就是空壳。turn_end 只是结构标记,也不留。

    事件 id 从 start_id 开始重新编:摊平会删掉和合并事件,沿用旧 id 会留下空洞,
    而尾段的 id 必须仍然排在这一段后面。
    """
    results = {
        str(ev.get("call_id") or ""): ev
        for ev in events
        if str(ev.get("type") or "") == session_log.EV_TOOL_RESULT
    }
    out: list[dict] = []
    next_id = start_id
    for ev in events:
        etype = str(ev.get("type") or "")
        if etype == session_log.EV_TOOL_CALL:
            call_id = str(ev.get("call_id") or "")
            res = results.get(call_id)
            if res is None:
                raise RuntimeError(
                    f"上下文压缩:工具调用 {call_id} 没有配对的结果,切段时不该把它分开"
                )
            out.append({
                "id": next_id,
                "type": session_log.EV_COMPACTED_TOOL,
                "task_id": str(ev.get("task_id") or ""),
                "created_at": ev.get("created_at"),
                "block_id": str(ev.get("block_id") or ""),
                "name": str(ev.get("name") or ""),
                "arguments": _clip_args(ev.get("arguments") or {}),
                "result": _clip(
                    str(res.get("result") or ""), RESULT_TRIM_LIMIT, RESULT_KEEP_HEAD, RESULT_KEEP_TAIL
                ),
            })
            next_id += 1
            continue
        if etype == session_log.EV_TOOL_RESULT or etype == session_log.EV_TURN_END:
            continue
        if etype == session_log.EV_ASSISTANT:
            if not str(ev.get("content") or "").strip():
                continue
            kept = dict(ev)
            kept["id"] = next_id
            kept["reasoning"] = ""
            out.append(kept)
            next_id += 1
            continue
        kept = dict(ev)
        kept["id"] = next_id
        out.append(kept)
        next_id += 1
    return out


def resolve_window(store, thread_id: str, default_window: int) -> int:
    """这个对话按多大的窗口跑。用户没设过就用启动配置里的值。"""
    window = int(store.load_ctx_state(thread_id).get("window") or 0)
    return window if window > 0 else int(default_window)


def usage_snapshot(store, thread_id: str, default_window: int) -> dict:
    """给上下文窗口用的用量快照。"""
    ctx = store.load_ctx_state(thread_id)
    window = resolve_window(store, thread_id, default_window)
    used = int(ctx.get("used") or 0)
    return {
        "used": used,
        "context_length": window,
        "usage_percent": round(used * 100 / window, 1) if window > 0 else 0,
        "threshold_tokens": int(window * TRIGGER_RATIO),
        "threshold_percent": int(TRIGGER_RATIO * 100),
        "compression_count": int(ctx.get("compactions") or 0),
    }


def should_compact(store, thread_id: str, default_window: int) -> bool:
    """上一次调用回报的真实输入 token 有没有过线。没跑过就没有依据,不压。"""
    ctx = store.load_ctx_state(thread_id)
    used = int(ctx.get("used") or 0)
    if used <= 0:
        return False
    window = resolve_window(store, thread_id, default_window)
    return used >= window * TRIGGER_RATIO


async def _summarize(client, model: str, prompt: str, body: str, max_tokens: int) -> str:
    from agent_framework import Content, Message

    resp = await client.get_response(
        [Message(role="user", contents=[Content.from_text(f"{prompt}\n\n--- 记录开始 ---\n{body}\n--- 记录结束 ---")])],
        options={"model": model, "max_tokens": max_tokens},
    )
    text = resp.text.strip()
    if not text:
        raise RuntimeError("上下文压缩:摘要模型返回了空内容")
    return text


def _first_checkpoint_id(events: list[dict]) -> str:
    """被压掉的这段里最早的文件检查点。摘要事件接过它:恢复到它就是恢复到这段之前。"""
    for ev in events:
        cp = str(ev.get("checkpoint_id") or "")
        if cp:
            return cp
    return ""


def _summary_event(source: dict, content: str, covered: int, level: str, segment: list[dict]) -> dict:
    """全损摘要事件,复用它所替换的那段的第一条事件的 id 与时间。

    id 沿用旧的,新事件就仍然排在中段和尾段前面,而且不会和它们的 id 撞车 ——
    Store 的下一个 id 是按现存最大值推的,压缩后自然接在尾段后面继续。
    """
    ev = {
        "id": int(source.get("id") or 0),
        "type": session_log.EV_COMPACTION,
        "task_id": "",
        "created_at": source.get("created_at"),
        "content": content,
        "level": level,
        "covered": covered,
    }
    cp = _first_checkpoint_id(segment)
    if cp:
        ev["checkpoint_id"] = cp
    return ev


def _settle(store, thread_id: str, before: list[dict], after: list[dict]) -> int:
    """压完把 used 按体积比例推算下来,并记一次压缩。

    不推算的话,下一轮开始前 used 还是压缩前的数字,会立刻再触发一次压缩。
    真实值等下一次调用回报时自然覆盖。
    """
    ctx = store.load_ctx_state(thread_id)
    old_chars = sum(_event_chars(e) for e in before)
    new_chars = sum(_event_chars(e) for e in after)
    projected = int(int(ctx.get("used") or 0) * new_chars / old_chars) if old_chars > 0 else 0
    store.save_ctx_state(
        thread_id, used=projected, compactions=int(ctx.get("compactions") or 0) + 1
    )
    return projected


async def compact_thread(web_agent, thread_id: str, model: str) -> dict:
    """跑一次三段压缩,把上下文区整段换掉。返回这次压缩的账目。

    只有前段会叫模型。中段是纯机械的摊平,尾段一个字都不动。
    """
    store = web_agent.store
    events = store.load_events(thread_id)
    head, middle, tail = split_thirds(events)
    if not head or not middle:
        raise RuntimeError("上下文压缩:前段或中段是空的,分不出三段")

    client = web_agent.agent.client
    brief = await _summarize(client, model, BRIEF_PROMPT, render_segment(head), BRIEF_MAX_TOKENS)
    flat = flatten_segment(middle, int(middle[0].get("id") or 0))

    new_events = [
        _summary_event(head[0], brief, len(head), "brief", head),
        *flat,
        *tail,
    ]
    store.replace_context_events(thread_id, new_events)
    projected = _settle(store, thread_id, events, new_events)
    return {
        "mode": "thirds",
        "before": len(events),
        "after": len(new_events),
        "head": len(head),
        "middle": len(middle),
        "middleAfter": len(flat),
        "tail": len(tail),
        "projected": projected,
    }


async def force_compact_thread(web_agent, thread_id: str, model: str) -> dict:
    """强力压缩:把整场对话烧成一条摘要。只有人能按,自动那条路径永远不走这里。

    三段压缩有个下限 —— 尾段是原样保留的,它一个人就可能已经超过窗口了,这时候
    再怎么压都下不来。这个出口就是给那种情况用的:不分段,整段记录一次性交给模型
    做全损摘要,压完这个对话只剩这一条。

    代价说清楚:摘要之后没有任何原文,也没有「摘要后面那条最新用户消息」可以拿来
    界定当前任务了。所以它等于把对话重置成一份交接说明,下一句话得由人重新提。
    """
    store = web_agent.store
    events = store.load_events(thread_id)
    if not events:
        raise RuntimeError("强力压缩:这个对话还没有内容")

    client = web_agent.agent.client
    summary = await _summarize(
        client, model, FORCE_PROMPT, render_segment(events), FORCE_MAX_TOKENS
    )
    new_events = [_summary_event(events[0], summary, len(events), "full", events)]
    store.replace_context_events(thread_id, new_events)
    projected = _settle(store, thread_id, events, new_events)
    return {
        "mode": "force",
        "before": len(events),
        "after": len(new_events),
        "projected": projected,
    }
