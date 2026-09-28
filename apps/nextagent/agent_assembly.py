"""按回合把主 agent 组装成本轮要跑的参与者:系统提示前缀、角色 agent、附件、历史。

这里只管「把一个 agent 拼出来」,不知道流式协议,也不决定本轮走哪种模式 ——
模式由 turn_policy 决定,这里只提供组装原料。
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from agent_framework import Content, Message

from fm.backend.pathutil import from_agent_path, resolve

from . import session_log
from .history_provider import MAIN_AGENT_DISPLAY, SPEAKER_PREFIX_NOTE, to_model_messages
from .toolkit import registry as tool_registry

# ── 多 agent 回合的收尾约定(与具体业务无关,只是框架层协议)──
# 「什么时候算做完」交给主 agent 判定,但判定要有结构化出口,不能靠猜模型有没有再交接:
# 主 agent 独有一个 round_complete 工具,调用它 = 声明本轮结束;
# 这个信号接到 HandoffBuilder.with_termination_condition 上,让工作流当场收工,
# 不用再多走一轮才发现没人接手。
ROUND_COMPLETE_TOOL = "round_complete"
ROUND_COMPLETE_DESCRIPTION = (
    "Declare this multi-agent round finished. Call it when the user's latest request is fully "
    "satisfied, so the round can wrap up immediately."
)
COORDINATOR_DESCRIPTION = (
    "总协调者:把任务分派给合适的成员、必要时自己动手、汇总结果,并在任务真正完成后结束本轮。"
)
COORDINATOR_PROTOCOL = (
    "\n\n[本轮协议]\n本轮对话由你负责收尾:任务真正完成时,"
    f"调用 {ROUND_COMPLETE_TOOL} 结束本轮;需要别人做事就交接给对应成员,他们做完会交回给你。"
)
# 注入其他参与者的协议说明。handoff 模式下控制权只能由 agent 自己调交接工具移交
# (框架的响应永远回给发起方),所以「做完了要交回协调者」必须说一次 —— 这是框架用法,
# 不是业务规则:任何场景下都一样。
MEMBER_PROTOCOL = (
    "\n\n[本轮协议]\n你是这次协作会话的成员之一:你负责的部分做完后,"
    "用交接工具把控制权交回总协调者(需要别的成员先做事时,也可以直接交接给他),"
    "本轮由协调者统一收尾。"
    f"\n\n{SPEAKER_PREFIX_NOTE}"
)

#: 交接工具的名字前缀。HandoffBuilder 按 f"handoff_to_{参与者名}" 生成它们。
HANDOFF_TOOL_PREFIX = "handoff_to_"


def _turn_origin_note(*, group_chat: bool) -> str:
    if group_chat:
        return "本轮从群里被@发起。"
    return "本轮从桌面发起。没有 task_complete 工具。"


def _turn_clock_line() -> str:
    now = datetime.datetime.now()
    return f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')} 星期{('一', '二', '三', '四', '五', '六', '日')[now.weekday()]}"


def _turn_memory_block(store, thread_id: str) -> str:
    text = store.load_memory(thread_id).strip()
    if not text:
        return ""
    return "MEMORY:\n" + text


def turn_system_prefix(*, group_chat: bool, store, thread_id: str) -> str:
    parts = [_turn_clock_line(), _turn_origin_note(group_chat=group_chat)]
    mem = _turn_memory_block(store, thread_id)
    if mem:
        parts.append(mem)
    return "\n".join(parts) + "\n"


def build_user_contents(
    web_agent,
    content: str,
    attachments: list[dict] | None,
    *,
    media_passthrough: bool,
) -> tuple[list, list[dict]]:
    """把提问和附件拼成给模型的用户消息内容,返回 (contents, 落盘用的附件列表)。

    单聊(run_turn)和群聊(run_handoff_turn)共用:附件处理只做在其中一条路径上,
    另一条就会完全看不见附件 —— 模型只收到光秃秃的提问,只能反问用户文件是哪个。

    图片、视频、音频是否内联只由 media_passthrough 开关决定:模型能不能读是 adapter 的
    media_caps 说了算,这里不再另存一份能力表去猜(猜错就是开关按了没反应)。
    选了不支持该媒体的模型又开着直传,让上游直接报错。

    其它附件(md / pdf / docx 等)把文件路径写进正文 —— 不写模型就不知道
    文件在哪,file_access_read 也无从读起。
    """
    if not attachments:
        return [Content.from_text(content)], []

    msg_content = content
    media: list = []

    for att in attachments:
        mime = str(att.get("mime_type") or "").lower()
        path = str(att.get("path") or att.get("media_id") or "")
        if (mime.startswith("image/") or mime.startswith("video/") or mime.startswith("audio/")) and media_passthrough:
            fp = Path(resolve(from_agent_path(path)))
            media.append(Content.from_data(fp.read_bytes(), media_type=mime))
            continue
        hint = session_log.attachment_hint(att)
        if hint and hint not in msg_content:
            msg_content += "\n" + hint

    return [Content.from_text(msg_content), *media], list(attachments)


def run_input_beyond_log(user_message: Message):
    extra = [c for c in (user_message.contents or []) if getattr(c, "type", None) == "data"]
    if extra:
        return Message(role="user", contents=extra)
    return []


def without_history_providers(providers):
    from agent_framework import HistoryProvider

    return [p for p in (providers or []) if not isinstance(p, HistoryProvider)]


def readonly_providers(agent):
    from agent_framework import FileAccessProvider

    out = []
    for p in list(agent.context_providers or []):
        if isinstance(p, FileAccessProvider):
            out.append(type(p)(
                p.store,
                source_id=p.source_id,
                instructions=p.instructions,
                disable_write_tools=True,
                disable_readonly_tool_approval=p.disable_readonly_tool_approval,
                disable_write_tool_approval=p.disable_write_tool_approval,
            ))
        else:
            out.append(p)
    return out


def reasoning_extra_body(thinking: bool | None, reasoning_effort: str | None) -> dict[str, Any] | None:
    """把前端的思考开关翻译成请求体附加字段。

    这两个键不是 OpenAI chat completions 的标准参数,SDK 的 create() 不接受未知关键字,
    只能走 extra_body 让 SDK 原样并进 body,再由网关的模型 adapter 解释。
    模型不支持思考时前端两个字段都不发,这里返回 None,请求体保持原样。
    """
    body: dict[str, Any] = {}
    if thinking is not None:
        body["thinking"] = bool(thinking)
    if reasoning_effort:
        body["reasoning_effort"] = str(reasoning_effort)
    return body or None


def round_complete_tool(finished: dict[str, bool]):
    from agent_framework import tool

    @tool(name=ROUND_COMPLETE_TOOL, description=ROUND_COMPLETE_DESCRIPTION, approval_mode="never_require")
    def round_complete(summary: str = "") -> str:
        finished["done"] = True
        return "round finished"

    return round_complete


def participant(
    agent,
    model: str | None,
    *,
    extra_instructions: str = "",
    replace_instructions: str | None = None,
    prefix_instructions: str = "",
    description: str | None = None,
    extra_tools: list | None = None,
    extra_body: dict[str, Any] | None = None,
    hide_tools: set[str] | None = None,
    context_providers=None,
):
    """把主 agent 变成 handoff 参与者(必要时换模型/加工具),保留它的工具与上下文管线。

    克隆而不是改原对象 —— 原 agent 还要服务普通单人对话。
    """
    from agent_framework import Agent

    opts = dict(agent.default_options)
    if replace_instructions is not None:
        opts.pop("instructions", None)
        body = replace_instructions
    else:
        body = opts.pop("instructions", None) or ""
    instructions = (prefix_instructions or "") + body + (extra_instructions or "")
    tools = list(opts.pop("tools", []) or []) + list(agent.mcp_tools or []) + list(extra_tools or [])
    if hide_tools:
        tools = [t for t in tools if getattr(t, "name", None) not in hide_tools]
    if model:
        opts["model"] = model
    if extra_body:
        opts["extra_body"] = extra_body
    opts["tools"] = tools
    return Agent(
        client=agent.client,
        instructions=instructions,
        id=agent.id,
        name=agent.name,
        description=description or agent.description,
        context_providers=context_providers if context_providers is not None else agent.context_providers,
        middleware=agent.middleware,
        require_per_service_call_history_persistence=True,
        default_options=opts,
    )


def role_agent_name(role: dict, index: int) -> str:
    """角色的参与者名。

    必须 ASCII 且唯一:handoff 工具名是 f"handoff_to_{name}",中文名会被模型服务拒绝,
    重名会让 HandoffBuilder 直接抛错。所以用序号 + id 前缀。
    """
    return f"role{index}_{str(role.get('id') or '')[:6]}"


def build_role_agent(
    web_agent,
    role: dict,
    model: str | None,
    safe_name: str,
    extra_body: dict[str, Any] | None = None,
    extra_instructions: str = "",
    extra_tools: list | None = None,
    hide_tools: set[str] | None = None,
    *,
    store,
    thread_id: str,
):
    """角色卡 → 参与者。

    instructions = 用户写的角色卡 + 一句协议说明(做完交回协调者),不含任何业务规则;
    description 给框架用来生成「交接到这个成员」的工具说明,别人据此知道他管什么。
    """
    from agent_framework import Agent

    opts: dict[str, Any] = {}
    if model:
        opts["model"] = model
    if extra_body:
        opts["extra_body"] = extra_body
    display = role.get("name") or safe_name
    prompt = (role.get("system_prompt") or "").strip()
    tools = list(tool_registry.function_tools()) + list(extra_tools or [])
    if hide_tools:
        tools = [t for t in tools if getattr(t, "name", None) not in hide_tools]
    origin = turn_system_prefix(group_chat=False, store=store, thread_id=thread_id)
    return Agent(
        client=web_agent.agent.client,
        instructions=f'{origin}你的名字是"{display}"。{prompt}{MEMBER_PROTOCOL}{extra_instructions}',
        name=safe_name,
        description=f"{display}:{prompt[:200]}" if prompt else display,
        require_per_service_call_history_persistence=True,
        tools=tools,
        middleware=web_agent.agent.middleware,
        default_options=opts or None,
    )


def history_messages(store, thread_id: str, *, skip_last_user: bool) -> list:
    events = list(store.load_events(thread_id))
    if skip_last_user:
        for i in range(len(events) - 1, -1, -1):
            if str(events[i].get("type") or "") == session_log.EV_USER:
                events = events[:i]
                break
    return to_model_messages(events, thread_id=thread_id)
