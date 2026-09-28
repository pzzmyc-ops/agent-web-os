"""把 events.jsonl 的上下文区翻成框架要的消息列表。

每次取都读文件,不在框架内存里另存一份对话。
写盘由 stream_adapter 追加事件,这里不写回。
"""
from __future__ import annotations

import json
from typing import Any, Sequence

from agent_framework import Content, HistoryProvider, Message

from . import session_log
from .store import Store


#: 主 agent 在群聊里显示的名字。落盘的 assistant 事件用它当 name,投影时也靠它认出
#: 「哪些历史发言是我自己说的」。
MAIN_AGENT_DISPLAY = "助手"

#: 给模型说明历史里的发言人标注是什么。
#:
#: 多人协作的历史必须标出谁说的,否则模型分不清。试过用 OpenAI 消息的 name 字段:
#: 网关那一跳之后模型收不到 —— claude 和 kimi 都明确回答「两条发言都没有署名」。
#: 所以只能把名字写进正文。
#:
#: 只靠这段说明拦不住模型模仿前缀,实测它照样会输出「[助手] xxx」。真正起作用的是
#: _assistant_contents 里那条「自己的发言不加前缀」—— 模型看不到自己带前缀说话的
#: 示范,就不会照着写。这段说明留给群聊 seed:那份上下文是所有参与者共用的,没法
#: 按人裁掉前缀。
SPEAKER_PREFIX_NOTE = (
    "## 历史里的发言人标注\n"
    "多人协作会话的历史里,assistant 消息开头的 `[名字]` 是转录时加的发言人标注,"
    "不是正文的一部分。你自己的回答绝对不要加这种前缀,直接说内容。"
)


def _user_message(ev: dict[str, Any]) -> Message | None:
    content = str(ev.get("content") or "")
    hints = [
        h for h in (session_log.attachment_hint(a) for a in (ev.get("attachments") or [])) if h
    ]
    if hints:
        content = content + "\n" + "\n".join(hints) if content else "\n".join(hints)
    if not content:
        return None
    # contents 必须是「列表」:传裸字符串会被当成 Content 序列逐字符拆开,
    # 一句话就变成几十条单字消息,上下文直接被搞烂。
    return Message(role="user", contents=[content])


def _assistant_contents(ev: dict[str, Any], me: str) -> list[Content]:
    """一条 assistant_message → 思考 + 正文。

    别人的发言带上 `[名字]`,模型才分得清谁说过什么;`me` 自己的发言不带 —— assistant
    角色在这份历史里本来就是「我」,再给自己贴个第三人称标签是错的,而且模型会把它
    当成格式规范照着模仿,在新回答前面也加一个 `[助手]`,那个前缀跟着落盘、下一轮
    又被它自己看见,越滚越像真的。
    """
    out: list[Content] = []
    reasoning = str(ev.get("reasoning") or "")
    if reasoning:
        out.append(Content.from_text_reasoning(text=reasoning))
    content = str(ev.get("content") or "")
    if content:
        name = str(ev.get("name") or "")
        out.append(Content.from_text(f"[{name}] {content}" if name and name != me else content))
    return out


def _call_content(ev: dict[str, Any]) -> Content:
    extra = ev.get("extra_content")
    props = {"extra_content": extra} if extra else None
    return Content.from_function_call(
        str(ev.get("call_id") or ""),
        str(ev.get("name") or ""),
        arguments=ev.get("arguments") or {},
        additional_properties=props,
    )


def to_model_messages(
    events: list[dict], *, thread_id: str = "", me: str = MAIN_AGENT_DISPLAY
) -> list[Message]:
    """事件流 → 模型看到的消息列表。

    `me` 是这份历史给谁看的显示名:他自己的历史发言不加发言人前缀(见 _assistant_contents)。

    两条投影规则值得单独说:

    - 紧挨着 tool_call 的那条 assistant_message 会被折进同一条 assistant 消息。
      日志里「说一句话 → 发起调用」是同一次模型输出(_persist_segment 在调用到达的
      那一刻落盘),拆成两条消息会让 reasoning 落在一条没有 tool_calls 的消息上,
      而 deepseek / kimi 这类模型只在带工具调用的那一轮回传思考内容,拆开就丢了。
    - 相邻的 tool_call 攒成一组再一起吐结果。顺序执行时是「调用-结果-调用-结果」,
      并行时是「调用-调用-结果-结果」,攒成组两种都能保证每条 tool 消息紧跟在
      声明它的 assistant 消息之后 —— 这是上游的硬要求。

    走的是 logical_records 而不是原始事件:自续循环会把一次发言切成好几条 assistant
    事件,直接投影就变成连续几条 assistant 消息,白占上下文。合并只在相邻且中间没有
    工具时发生,所以不会把工具前后的话粘到一起。
    """
    results = {
        str(ev.get("call_id") or ""): ev
        for ev in events
        if str(ev.get("type") or "") == session_log.EV_TOOL_RESULT
    }
    records = session_log.logical_records(events)
    out: list[Message] = []
    pending_calls: list[dict] = []
    pending_assistant: dict[str, Any] | None = None

    def flush_assistant() -> None:
        nonlocal pending_assistant
        if pending_assistant is None:
            return
        contents = _assistant_contents(pending_assistant, me)
        if contents:
            out.append(Message(role="assistant", contents=contents))
        pending_assistant = None

    def flush_calls() -> None:
        nonlocal pending_assistant
        if not pending_calls:
            return
        head = _assistant_contents(pending_assistant, me) if pending_assistant is not None else []
        pending_assistant = None
        out.append(Message(role="assistant", contents=head + [_call_content(c) for c in pending_calls]))
        for call in pending_calls:
            call_id = str(call.get("call_id") or "")
            res = results.get(call_id)
            if res is None:
                raise ValueError(
                    f"工具调用没有配对的结果: thread={thread_id} call_id={call_id} "
                    f"name={call.get('name')} —— 半边调用发给上游会被判 400,"
                    "应该由 Store.recover_unpaired_calls 补一条中断说明"
                )
            out.append(Message(
                role="tool",
                contents=[Content.from_function_result(call_id, result=str(res.get("result") or ""))],
            ))
        pending_calls.clear()

    for ev in records:
        etype = str(ev.get("type") or "")
        if etype == session_log.EV_TOOL_CALL:
            pending_calls.append(ev)
            continue
        if etype == session_log.EV_TOOL_RESULT:
            flush_calls()
            continue
        if etype == session_log.EV_ASSISTANT:
            flush_calls()
            flush_assistant()
            pending_assistant = ev
            continue
        if etype == session_log.EV_USER:
            flush_calls()
            flush_assistant()
            msg = _user_message(ev)
            if msg is not None:
                out.append(msg)
            continue
        if etype == session_log.EV_SYSTEM:
            flush_calls()
            flush_assistant()
            content = str(ev.get("content") or "")
            if content:
                out.append(Message(role="system", contents=[content]))
            continue
        if etype == session_log.EV_COMPACTION:
            flush_calls()
            flush_assistant()
            content = str(ev.get("content") or "")
            if content:
                from .compaction import SUMMARY_NOTE

                out.append(Message(role="system", contents=[SUMMARY_NOTE + "\n\n" + content]))
            continue
        if etype == session_log.EV_COMPACTED_TOOL:
            # 中段压缩留下的工具记录。它已经不是真的工具对了,所以走 system 转述 ——
            # 一条孤立的 tool 消息发给上游会被判 400,而重新造一个 assistant tool_calls
            # 又会把 DeepSeek 那条「带 tool_calls 必须回传 reasoning_content」的约束
            # 拉回来,可是思考在压缩时已经丢了。
            flush_calls()
            flush_assistant()
            from .compaction import TOOL_RECORD_NOTE

            args = json.dumps(ev.get("arguments") or {}, ensure_ascii=False)
            out.append(Message(role="system", contents=[
                f"{TOOL_RECORD_NOTE}\n调用了 {ev.get('name')}，参数：{args}\n结果：{ev.get('result') or ''}"
            ]))
            continue
        if etype == session_log.EV_PROGRAM_TIMER:
            flush_calls()
            flush_assistant()
            content = str(ev.get("content") or "")
            if content:
                out.append(Message(role="system", contents=["[程序定时] " + content]))
            continue
        # recall / turn_end 之类不进模型历史
    flush_calls()
    flush_assistant()
    return out


class MafHistoryProvider(HistoryProvider):
    def __init__(self, store: Store, source_id: str = "maf_history", *, skip_excluded: bool = True):
        super().__init__(
            source_id,
            load_messages=True,
            store_inputs=False,
            store_outputs=False,
            store_context_messages=False,
        )
        self._store = store
        self.skip_excluded = skip_excluded

    def _from_log(self, session_id: str | None) -> list[Message]:
        if not session_id:
            return []
        return to_model_messages(self._store.load_events(session_id), thread_id=session_id)

    async def get_messages(
        self, session_id: str | None, *, state: dict[str, Any] | None = None, **kwargs: Any
    ) -> list[Message]:
        messages = self._from_log(session_id)
        if self.skip_excluded:
            messages = [m for m in messages if not m.additional_properties.get("_excluded", False)]
        return messages

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        return
