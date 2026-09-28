"""本轮走哪种模式,由策略对象决定;回合循环只调两个钩子。

借鉴 dsh 的做法:循环只负责「调模型、跑工具、重复」,模式相关的事情全部挂在钩子上 ——
prepare() 给出本轮 agent 的提示词与工具增删,after_step() 决定一步跑完后是收尾还是
追问模型继续。计划模式是横切能力(PlanFeature),单聊、飞书 webhook、多 agent 都能挂。
新增一种模式只加一个策略类,不碰 stream_adapter。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .agent_assembly import readonly_providers
from .tool_tracker import ToolTracker
from .turn_channels import PlanReviewChannel
from .turn_protocol import TurnEmitter

GROUP_COMPLETE_CONTINUE_PROMPT = (
    "本轮是群聊@，尚未调用 task_complete。"
    "必须调用群聊专属的 task_complete 收尾：先自己核对这次@要的事是不是已经做完、"
    "结论和文件是不是已经送到群里了。"
    "已经送出去的不要再送一遍：结论群里已经有了就 send_summary=false，只收尾不发言；"
    "只有这轮产出、且还没送到群里的文件才填 files，没有就不要填，不要乱发旧文件。"
    "不要只在对话窗口里写，不要问要不要发，不要用发消息工具代替。"
)
PLAN_STATUS_CONTINUE_PROMPT = (
    "本轮仍在计划模式，尚未同时调用 plan_end_mode 和 plan_show_window。"
    "闲聊或无关的话：plan_end_mode(end=true) 或 plan_show_window(show=false)，不要调用 exit_plan_mode。"
    "要交方案给人审：调用 exit_plan_mode，并 plan_show_window(show=true)。"
    "不要用文字代替这两个状态工具。"
)


@dataclass
class AgentSpec:
    extra_instructions: str = ""
    extra_tools: list = field(default_factory=list)
    replace_instructions: str | None = None
    hide_tools: set[str] | None = None
    context_providers: list | None = None


class PlanFeature:
    def __init__(self, *, enabled: bool, store, thread_id: str):
        self.enabled = enabled
        self._store = store
        self._thread_id = thread_id
        self.status: dict[str, bool] = {
            "end_called": False,
            "show_called": False,
            "end": False,
            "show": False,
        }
        self.channel: PlanReviewChannel | None = None

    def context_fields(self, tracker: ToolTracker, emitter: TurnEmitter) -> dict[str, Any]:
        if not self.enabled:
            return {}
        self.channel = PlanReviewChannel(tracker)

        async def emit_plan_status():
            await emitter.control(
                "plan_status",
                {
                    "threadId": self._thread_id,
                    "endCalled": bool(self.status["end_called"]),
                    "showCalled": bool(self.status["show_called"]),
                    "end": bool(self.status["end"]),
                    "show": bool(self.status["show"]),
                },
            )

        return {
            "plan_review": self.channel,
            "plan_status": self.status,
            "emit_plan_status": emit_plan_status,
        }

    def apply(self, extra_instructions: str, extra_tools: list | None) -> tuple[str, list]:
        instructions, tools = self._with_plan_mode(extra_instructions, extra_tools)
        return self._with_stored_plan(instructions, tools)

    def _with_plan_mode(self, extra_instructions: str, extra_tools: list | None) -> tuple[str, list]:
        tools = list(extra_tools or [])
        if not self.enabled:
            return extra_instructions, tools
        from .plan_mode import (
            PLAN_POLICY,
            make_exit_plan_mode_tool,
            make_plan_end_mode_tool,
            make_plan_show_window_tool,
        )

        return extra_instructions + PLAN_POLICY, tools + [
            make_exit_plan_mode_tool(),
            make_plan_end_mode_tool(),
            make_plan_show_window_tool(),
        ]

    def _with_stored_plan(self, extra_instructions: str, extra_tools: list | None) -> tuple[str, list]:
        from .plan_mode import format_plan_pointer, make_plan_read_tool, make_todo_write_tool

        tools = list(extra_tools or [])
        doc = self._store.load_plan_doc(self._thread_id)
        if doc is None:
            return extra_instructions, tools
        phase = str(doc.get("phase") or "")
        pointer = format_plan_pointer(
            phase=phase,
            user_modified=bool(doc.get("userModified")),
        )
        tools = tools + [make_plan_read_tool()]
        if phase == "active" and not self.enabled:
            tools = tools + [make_todo_write_tool()]
        return extra_instructions + "\n\n" + pointer, tools

    def role_extras(self) -> tuple[str, list]:
        from .plan_mode import format_plan_pointer, make_plan_read_tool

        doc = self._store.load_plan_doc(self._thread_id)
        if doc is None:
            return "", []
        pointer = "\n\n" + format_plan_pointer(
            phase=str(doc.get("phase") or ""),
            user_modified=bool(doc.get("userModified")),
        )
        return pointer, [make_plan_read_tool()]

    def providers(self, agent):
        return readonly_providers(agent) if self.enabled else None

    def continue_prompt(self, continues: int) -> str | None:
        status_ready = bool(self.status.get("end_called") and self.status.get("show_called"))
        if status_ready or continues >= 1:
            return None
        return PLAN_STATUS_CONTINUE_PROMPT


class SoloPolicy:
    group_chat = False

    def __init__(self, plan: PlanFeature, *, media_passthrough: bool):
        self.plan = plan
        self.media_passthrough = media_passthrough
        self.group_complete: dict | None = None

    def prepare(self, *, web_agent, store, thread_id: str) -> AgentSpec:
        from .tool_policy import hide_tools

        instructions, tools = self.plan.apply("", None)
        return AgentSpec(
            extra_instructions=instructions,
            extra_tools=tools,
            hide_tools=hide_tools(
                group_chat=False, plan_mode=self.plan.enabled, media_passthrough=self.media_passthrough,
            ) or None,
            context_providers=self.plan.providers(web_agent.agent),
        )

    def after_step(self, continues: int) -> str | None:
        if self.plan.enabled:
            return self.plan.continue_prompt(continues)
        return None


class FeishuWebhookPolicy(SoloPolicy):
    def prepare(self, *, web_agent, store, thread_id: str) -> AgentSpec:
        from .agent_core import feishu_webhook_instructions
        from .tool_policy import hide_tools

        hook = store.feishu_webhook_url(thread_id)
        if not hook:
            raise RuntimeError("当前对话没有填写飞书 webhook 地址")
        instructions, tools = self.plan.apply("", None)
        return AgentSpec(
            extra_instructions=instructions,
            extra_tools=tools,
            replace_instructions=feishu_webhook_instructions(hook),
            hide_tools=hide_tools(
                group_chat=False,
                plan_mode=self.plan.enabled,
                feishu_webhook=True,
                media_passthrough=self.media_passthrough,
            ) or None,
            context_providers=self.plan.providers(web_agent.agent),
        )


class GroupChatPolicy:
    group_chat = True

    def __init__(self, plan: PlanFeature, *, media_passthrough: bool):
        self.plan = plan
        self.media_passthrough = media_passthrough
        self.group_complete: dict | None = {"done": False}

    def prepare(self, *, web_agent, store, thread_id: str) -> AgentSpec:
        from .agent_core import group_chat_instructions
        from .groupchat.runtime import require_group_chat
        from .tool_policy import group_extra_tools, hide_tools

        bind = require_group_chat().bindings.get_by_thread(thread_id)
        if bind is None:
            raise RuntimeError("群聊轮次没有绑定")
        channel = str(bind.get("channel") or "").strip()
        if not channel:
            raise RuntimeError("绑定缺少通道")
        return AgentSpec(
            extra_instructions="",
            extra_tools=group_extra_tools(),
            replace_instructions=group_chat_instructions(channel),
            hide_tools=hide_tools(
                group_chat=True, channel=channel, media_passthrough=self.media_passthrough,
            ) or None,
            context_providers=self.plan.providers(web_agent.agent),
        )

    def after_step(self, continues: int) -> str | None:
        if self.plan.enabled:
            return self.plan.continue_prompt(continues)
        if self.group_complete is not None and self.group_complete.get("done"):
            return None
        if continues >= 1:
            raise RuntimeError("群聊轮次没有调用 task_complete")
        return GROUP_COMPLETE_CONTINUE_PROMPT


def build_policy(store, thread_id: str, *, plan_mode: bool, group_chat: bool, media_passthrough: bool):
    from .store import APP_FEISHU_WEBHOOK

    plan = PlanFeature(enabled=plan_mode, store=store, thread_id=thread_id)
    if group_chat:
        return GroupChatPolicy(plan, media_passthrough=media_passthrough)
    if store.thread_app(thread_id) == APP_FEISHU_WEBHOOK:
        return FeishuWebhookPolicy(plan, media_passthrough=media_passthrough)
    return SoloPolicy(plan, media_passthrough=media_passthrough)
