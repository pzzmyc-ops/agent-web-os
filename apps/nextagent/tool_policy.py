from __future__ import annotations

from .plan_mode import PLAN_HIDE_TOOLS

# 群聊不给发消息工具，只留 task_complete，避免模型中途在群里啰嗦。
# task_complete 自己能发群，无需再调发送工具或钉钉 CLI。
# 同时模型调用 task_complete 时可自选是否把消息发到群里。
GROUP_HIDE_COMMON = frozenset({
    "clarify",
    "render_media",
    "render_text",
    "exit_plan_mode",
    "plan_read",
    "plan_end_mode",
    "plan_show_window",
    "todo_write",
    "dingtalk_send_text",
    "dingtalk_send_file",
    "feishu_send_text",
    "feishu_send_file",
})

GROUP_HIDE_DINGTALK = GROUP_HIDE_COMMON | frozenset({
    "feishu_list_messages",
    "feishu_download_files",
})

GROUP_HIDE_FEISHU = GROUP_HIDE_COMMON | frozenset({
    "dingtalk_list_messages",
    "dingtalk_download_files",
})

DESKTOP_HIDE = frozenset({
    "task_complete",
})

WEBHOOK_HIDE = DESKTOP_HIDE | frozenset({
    "feishu_list_messages",
    "feishu_download_files",
    "dingtalk_list_messages",
    "dingtalk_download_files",
    "dingtalk_send_text",
    "dingtalk_send_file",
})


MEDIA_OFF_HIDE = frozenset({
    "view_image",
})


def hide_tools(
    *,
    group_chat: bool,
    media_passthrough: bool,
    channel: str = "",
    plan_mode: bool = False,
    feishu_webhook: bool = False,
) -> set[str]:
    if group_chat:
        name = str(channel or "").strip()
        if name == "dingtalk":
            hide = set(GROUP_HIDE_DINGTALK)
        elif name == "feishu":
            hide = set(GROUP_HIDE_FEISHU)
        else:
            raise RuntimeError("未知通道: " + name)
    elif feishu_webhook:
        hide = set(WEBHOOK_HIDE)
        if plan_mode:
            hide |= set(PLAN_HIDE_TOOLS)
    else:
        hide = set(DESKTOP_HIDE)
        if plan_mode:
            hide |= set(PLAN_HIDE_TOOLS)
    if not media_passthrough:
        hide |= set(MEDIA_OFF_HIDE)
    return hide


def group_extra_tools() -> list:
    from .toolkit.group_complete import make_group_task_complete

    return [make_group_task_complete()]
