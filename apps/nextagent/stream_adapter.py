"""回合驱动:把 Agent Framework 的流交给 ToolTracker 和块层,其余全部委托出去。

分层(借鉴 dsh 的「循环只写领域事件、载波另起一层、模式走钩子」):
- turn_protocol   前端块协议载波(TurnEmitter)与文本块 / 发言人块状态机
- tool_tracker    工具生命周期统一出口(function_call / function_result → 块 + 日志)
- turn_channels   澄清提问、计划审阅两条工具 → 用户通道
- agent_assembly  把主 agent 组装成本轮参与者、角色 agent、附件、历史
- turn_policy     本轮模式(单聊 / 飞书 webhook / 群聊)与计划模式钩子
- turn_lifecycle  回合骨架:开总线、绑上下文、受理 / 开始、收尾落盘、turn_complete

这里只剩两条驱动循环:run_turn(单 agent)与 run_handoff_turn(多 agent 交接)。
对外入口不变:TurnEmitter、run_turn、run_handoff_turn。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from agent_framework import Content, Message

from . import session_log
from .agent_assembly import (
    COORDINATOR_DESCRIPTION,
    COORDINATOR_PROTOCOL,
    build_role_agent,
    build_user_contents,
    history_messages,
    participant,
    readonly_providers,
    reasoning_extra_body,
    role_agent_name,
    round_complete_tool,
    run_input_beyond_log,
    turn_system_prefix,
    without_history_providers,
)
from .history_provider import MAIN_AGENT_DISPLAY
from .turn_lifecycle import TurnRun, iter_content_deltas
from .turn_policy import PlanFeature, SoloPolicy, build_policy
from .turn_protocol import SpeakerBlocks, TextBlocks, TurnEmitter

__all__ = ["TurnEmitter", "run_turn", "run_handoff_turn"]


async def _log_user_message(
    store, emitter: TurnEmitter, *, content: str, attachments: list[dict]
) -> None:
    """用户消息落盘,并在它上面挂一个文件检查点。

    先拍检查点再写事件:事件里的 checkpoint_id 必须指向一个已经存在的点。
    前端直播里的这条气泡此时还不知道自己的检查点,广播一条 checkpoint_created 补上。
    """
    thread_id = emitter.thread_id
    cp_id = await asyncio.to_thread(
        store.checkpoints.create, thread_id, plan=store.load_plan_slot(thread_id)
    )
    store.append_event(
        thread_id,
        session_log.EV_USER,
        task_id=emitter.task_id,
        content=content,
        attachments=attachments,
        checkpoint_id=cp_id,
    )
    await emitter.control(
        "checkpoint_created",
        {"taskId": emitter.task_id, "threadId": thread_id, "checkpointId": cp_id},
    )


async def run_turn(
    web_agent,
    emitter: TurnEmitter,
    *,
    content: str,
    message_id: str,
    model: str | None,
    is_cancelled: Callable[[], bool] | None = None,
    attachments: list[dict] | None = None,
    media_passthrough: bool = False,
    skip_user_save: bool = False,
    is_agent_recall: bool = False,
    thinking: bool | None = None,
    reasoning_effort: str | None = None,
    plan_mode: bool = False,
    group_chat: bool = False,
) -> None:
    """驱动一轮对话并把流翻译成块协议;user/assistant 落库在收尾阶段,保证取消/异常也持久化。

    attachments: 前端上传的附件列表 [{media_id, path, filename, mime_type}, ...]。
    media_passthrough: 是否把图片直接内联给模型(否则只给文本路径提示)。
    """
    task_id = emitter.task_id
    thread_id = emitter.thread_id
    store = web_agent.store
    policy = build_policy(
        store, thread_id, plan_mode=plan_mode, group_chat=group_chat, media_passthrough=media_passthrough,
    )
    blocks = TextBlocks(emitter, store, task_id=task_id, thread_id=thread_id)

    async with TurnRun(
        web_agent, emitter,
        content=content, message_id=message_id, policy=policy, blocks=blocks,
        is_agent_recall=is_agent_recall,
    ) as run:
        if not (model or "").strip():
            raise RuntimeError("未指定模型")
        model = str(model).strip()
        session = web_agent.session_for(thread_id)
        # 压缩要赶在这一轮的提问落盘之前:新问题不该被卷进被压的那三段里。
        await run.compact_if_due(model)
        options: dict[str, Any] = {"model": model}
        if extra_body := reasoning_extra_body(thinking, reasoning_effort):
            options["extra_body"] = extra_body
        parts, att_list = build_user_contents(
            web_agent, content, attachments, media_passthrough=media_passthrough
        )
        user_message = Message(role="user", contents=parts)
        # user 必须先落盘:下面的分段落盘会在流进行中就写 assistant,晚写 user 会让
        # 历史里「回答在提问之前」。附件一起落,前端历史和后续轮次的路径提示都靠它。
        if not skip_user_save:
            await _log_user_message(store, emitter, content=content, attachments=att_list)

        spec = policy.prepare(web_agent=web_agent, store=store, thread_id=thread_id)
        agent = participant(
            web_agent.agent,
            model,
            extra_instructions=spec.extra_instructions,
            extra_tools=spec.extra_tools,
            replace_instructions=spec.replace_instructions,
            prefix_instructions=turn_system_prefix(group_chat=group_chat, store=store, thread_id=thread_id),
            hide_tools=spec.hide_tools,
            context_providers=spec.context_providers,
        )
        pending = user_message if skip_user_save else run_input_beyond_log(user_message)
        continues = 0
        while pending is not None:
            stream = agent.run(pending, session=session, stream=True, options=options or None)
            async for update in stream:
                if run.cancelled_by(is_cancelled):
                    break
                await run.consume(update)
                tdelta, rdelta = iter_content_deltas(update)
                if rdelta:
                    await blocks.reasoning(rdelta)
                if tdelta:
                    await blocks.text(tdelta)
                await blocks.maybe_persist_due(run.tracker.has_open_calls)
            if run.interrupted:
                break
            await blocks.step_done()
            follow_up = policy.after_step(continues)
            if follow_up is None:
                break
            continues += 1
            pending = Message(role="user", contents=[Content.from_text(follow_up)])


async def run_handoff_turn(
    web_agent,
    emitter,
    store,
    *,
    content: str,
    message_id: str,
    model: str | None = None,
    roles: list[dict] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    attachments: list[dict] | None = None,
    media_passthrough: bool = False,
    skip_user_save: bool = False,
    thinking: bool | None = None,
    reasoning_effort: str | None = None,
    max_speaker_turns: int = 12,
    plan_mode: bool = False,
) -> None:
    """多 agent 协作(Handoff)的一个回合。

    回合的终止是结构化的,不依赖提示词约定:
    - 主 agent 独有 round_complete 工具,调用即置位完成标志;
      这个标志接到 HandoffBuilder.with_termination_condition 上,是工作流的终止条件。
    - 没调 round_complete 也会正常收尾:工作流走到「该问用户了」就停,我们不再把它推回去。
      追着不放只会让没活可干的协调者一句句「我继续待命」。
    - 兜底上限:max_speaker_turns(发言块数)。撞上限会给一条提示并收尾,不会无限跑。
    - 成员之间「谁负责什么」全部走框架的 description → 交接工具说明,不往提示词里塞名单。

    前端契约:
    - 整个回合只用 **一个 taskId**(emitter.task_id)。stream_start / turn_start 在跑模型之前
      就发出去,前端才能立刻把发送键切成停止键;结束时只有一个 turn_complete,前端据此收尾。
    - 每个发言人是一个 text 块(blockId=text:<taskId>:<n>,带 name=显示名);交接提示是
      notice 块。块都挂在同一条时间轴上,顺序 = 发言顺序。
    - 落库顺序:user 先写,之后每个发言按顺序写(assistant 带 name / system 是交接提示),
      刷新后历史与直播渲染一致。
    """
    from agent_framework import AgentResponseUpdate
    from agent_framework_orchestrations import HandoffBuilder

    task_id = emitter.task_id
    thread_id = emitter.thread_id
    if not (model or "").strip():
        raise RuntimeError("未指定模型")
    model = str(model).strip()
    roles = [r for r in (roles or []) if r.get("id")]

    user_parts, att_list = build_user_contents(
        web_agent, content, attachments, media_passthrough=media_passthrough
    )
    # user 事件先落盘,保证历史里「提问在前,回答在后」。
    # skip_user_save:编辑重发时这条 user 已经在日志里(内容被改过),再写一条就重复了。
    if not skip_user_save:
        await _log_user_message(store, emitter, content=content, attachments=att_list)

    plan = PlanFeature(enabled=plan_mode, store=store, thread_id=thread_id)
    policy = SoloPolicy(plan, media_passthrough=media_passthrough)
    blocks = SpeakerBlocks(emitter, store, task_id=task_id, thread_id=thread_id)

    async with TurnRun(
        web_agent, emitter,
        content=content, message_id=message_id, policy=policy, blocks=blocks,
    ) as run:
        # 组队之前先看上下文有没有到线。这一轮的提问已经落盘了,但它在最末尾,
        # 三等分时必然落在原样保留的尾段里,不会被压掉。
        await run.compact_if_due(model)

        # 参与者名必须 ASCII 且唯一(交接工具名 = f"handoff_to_{name}");
        # 「谁能交接给谁、对方负责什么」由框架从各自的 description 生成到工具说明里,
        # 所以这里不往提示词里塞成员名单,只把角色卡原样交给对应的 agent。
        main_name = web_agent.agent.name
        display_of: dict[str, str] = {main_name: MAIN_AGENT_DISPLAY}
        role_names: list[tuple[str, dict]] = []
        for i, r in enumerate(roles, 1):
            safe = role_agent_name(r, i)
            display_of[safe] = r.get("name") or safe
            role_names.append((safe, r))

        finished: dict[str, bool] = {"done": False}
        extra_body = reasoning_extra_body(thinking, reasoning_effort)
        coord_instructions, coord_tools = plan.apply(COORDINATOR_PROTOCOL, [round_complete_tool(finished)])
        from .tool_policy import hide_tools

        coord_providers = readonly_providers(web_agent.agent) if plan_mode else list(web_agent.agent.context_providers or [])
        hidden = hide_tools(group_chat=False, plan_mode=plan_mode, media_passthrough=media_passthrough) or None
        coordinator = participant(
            web_agent.agent,
            model,
            extra_instructions=coord_instructions,
            description=COORDINATOR_DESCRIPTION,
            extra_tools=coord_tools,
            extra_body=extra_body,
            prefix_instructions=turn_system_prefix(group_chat=False, store=store, thread_id=thread_id),
            hide_tools=hidden,
            context_providers=without_history_providers(coord_providers),
        )
        agents = [coordinator]
        role_instructions, role_tools = plan.role_extras()
        for safe, r in role_names:
            agents.append(build_role_agent(
                web_agent, r, model, safe, extra_body,
                extra_instructions=role_instructions,
                extra_tools=role_tools,
                hide_tools=hidden,
                store=store,
                thread_id=thread_id,
            ))

        # 不开 with_autonomous_mode:开了以后协调者说完话不交接,框架会反复追问它,
        # 而它没活可干时只会一句句「我继续待命」。让工作流照常在需要用户输入时停下来,
        # 由下面的循环收尾。round_complete 仍是提前收工的显式出口。
        workflow = (
            HandoffBuilder(participants=agents)
            .with_start_agent(coordinator)
            .with_termination_condition(lambda _conv: finished["done"])
            .build()
        )

        seed: list[Message] = history_messages(store, thread_id, skip_last_user=True)
        seed.append(Message(role="user", contents=user_parts))

        # 本轮有两个出口,都不需要我们再推:主 agent 调用 round_complete 让终止条件成立,
        # 或者工作流自己走到「该问用户了」而停下。硬上限 max_speaker_turns 兜底。
        events = workflow.run(seed, stream=True)
        # 必须显式关:上面每个出口都是 break,而 break 不会关掉异步生成器 —— 它要等
        # 事件循环的终结器另起一个任务去关,那个任务复制的是别人的上下文,而
        # workflow.run 的 tracing span 是在我们这个上下文里 attach 的,detach 于是抛
        # 「Token was created in a different Context」。登记到收尾钩子里就还在原上下文。
        run.add_closer(events.aclose)
        speaker_turns = 0
        async for event in events:
            if run.cancelled_by(is_cancelled):
                break

            etype = getattr(event, "type", "")

            if etype == "handoff_sent":
                src = getattr(event.data, "source", "") or ""
                tgt = getattr(event.data, "target", "") or ""
                await blocks.flush()
                await blocks.notice(f"{display_of.get(src, src)} → {display_of.get(tgt, tgt)}")
                continue

            if etype == "request_info":
                # 工作流要用户输入 = 该说的都说完了,本轮到此为止。这里不 break,
                # 让流自然收尾,避免留下没关掉的块。
                continue

            if etype in ("failed", "executor_failed"):
                run.error_msg = str(
                    getattr(event, "details", "") or getattr(event, "data", "") or "workflow failed"
                )
                break

            if etype not in ("output", "intermediate", "data"):
                continue

            update = event.data
            if not isinstance(update, AgentResponseUpdate):
                continue

            await run.consume(update)

            text = getattr(update, "text", None) or ""
            if not text:
                continue

            key = (
                getattr(event, "executor_id", None)
                or getattr(update, "author_name", None)
                or blocks.cur_key
                or "?"
            )
            if key != blocks.cur_key:
                await blocks.flush()
                speaker_turns += 1
                if speaker_turns > max_speaker_turns:
                    await blocks.notice(f"已达到本轮最多 {max_speaker_turns} 次发言,先交回给你")
                    break
                await blocks.start(key, display_of.get(key, key))

            await blocks.text(text)
            await blocks.maybe_persist_due(run.tracker.has_open_calls)

        await blocks.flush()
