"""schedule 工具 —— agent 创建/查看/取消定时任务。

创建挂在当前对话。list 看全部任务，用 current 标明哪条是本对话的。
cancel 按 timer_id 取消，不限对话。到点仍只写回创建时的那个对话。
会话删除时，该对话下的定时任务一并取消。

三种模式:
  mode="agent"(默认) 到点用 message 当提示词把 agent 唤起，跑完整一轮。
  mode="program" 到点只把 message 写进本对话的程序定时卡片，不唤起 agent。
                 message 是给用户看的正文，{now} 会换成当时的时间。
  mode="exec" 到点在工作区执行 command，不唤起 agent。跑完把退出码和输出写进本对话。
"""
from __future__ import annotations

import json
import time

from ..maf_tools.timer_registry import timer_registry

from ._context import current_turn
from ._registry import registry

_AGENT_NOTE = (
    "到点时系统会用 message 当提示词把你唤起,你那一轮的回复才是用户看到的内容;"
    "message 本身只作为触发卡片显示,不是发给用户的文案。"
)
_PROGRAM_NOTE = (
    "到点时只把 message 写进本对话的程序定时卡片，不会唤起你。"
    "message 里的 {now} 会换成当时的时间。这条记录会进上下文，之后的对话你能看见。"
)
_EXEC_NOTE = (
    "到点时在工作区执行 command，不会唤起你。"
    "interval 从上次触发再等 every_minutes 分钟，不对齐整点。"
    "跑完的退出码和输出会写进本对话。"
)

DESCRIPTION = "管理定时任务"

SCHEDULE_DOC = """管理定时任务。创建挂在本对话；list 能看到全部任务，current=true 表示本对话的。
cancel 按 timer_id 取消，其他对话的也能取消。到点仍写回创建时的那个对话。
会话被删除时，该对话下的定时任务一并取消。

## mode 决定到点做什么

mode="agent"(默认):用 message 当提示词把你唤起，跑一轮对话。用户看到的是你那一轮的回复。
message 写给「到点时的你自己」，写清楚要做什么:
  "提醒用户吃药,顺便看一眼今天日程有没有冲突"

mode="program":不唤起你。到点只把 message 写进本对话的程序定时卡片，并计入上下文。
message 就是用户看到的正文。需要当时时间就写 {now}:
  用户说"每小时发送一次当前时间"
  → mode="program", repeat="hourly", at="00", message="当前时间：{now}"

mode="exec":不唤起你。到点在工作区根目录执行 command。
interval 从上次触发再等 N 分钟，23/30/60 分钟都一样，不对齐整点。
  用户说"每 23 分钟跑一次报表脚本"
  → mode="exec", repeat="interval", every_minutes=23, command="python anamana_report/run_report.py"
  用户说"每天早上 9 点跑备份"
  → mode="exec", repeat="daily", at="09:00", command="python backup.py"
  当前对话已绑定 webhook 且用户说纯程序/固定程序定时发群
  → mode="exec"。command 对提示词里的 webhook 地址发 HTTP JSON，msg_type 为 text。
  不要用 mode="program"（只写本对话卡片，群里收不到），不要用 mode="agent"。

## schedule(action="create", repeat=..., message="...", mode=..., command=...)

repeat 决定时机,按 repeat 填对应字段:
  repeat="daily"     + at="09:00"                  每天 09:00
  repeat="weekly"    + at="09:00" + weekday="mon"  每周一 09:00(mon/tue/wed/thu/fri/sat/sun)
  repeat="hourly"    + at="05"                     每小时第 5 分钟
  repeat="interval"  + every_minutes=10            每 10 分钟一次
  repeat="once"      + date="2026-08-12" + at="14:30"   只在这个时间点跑一次,跑完自动消失
at 一律 24 小时制 "HH:MM"(下午 3 点 = "15:00")。

例: 用户说"每天早上9点提醒我吃药"
  → mode="agent", repeat="daily", at="09:00", message="提醒用户吃药,顺便看一眼今天日程有没有冲突"
例: 用户说"每10分钟检查一次服务状态,有问题提醒我"
  → mode="agent", repeat="interval", every_minutes=10, message="检查服务是否正常运行,有异常立即说明问题并给出处理建议"

## schedule(action="list") —— 列出全部定时任务，current=true 的是本对话
## schedule(action="cancel", timer_id="xxx") —— 按 id 取消，不限对话"""


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def _require_thread() -> str:
    ctx = current_turn()
    if ctx is None:
        raise RuntimeError("当前不在一轮对话里,无法确定定时任务属于哪个会话")
    return ctx.thread_id


@registry.register(
    toolset="ask",
    name="schedule",
    summary="管理定时任务:可查看全部,创建挂在本对话",
    description=DESCRIPTION,
    doc=SCHEDULE_DOC,
)
def schedule(
    action: str,
    repeat: str = "",
    at: str = "",
    weekday: str = "",
    date: str = "",
    every_minutes: int = 0,
    message: str = "",
    timer_id: str = "",
    mode: str = "agent",
    command: str = "",
) -> str:
    """管理定时任务。list 看全部，创建挂在本对话。

    Args:
        action: create / list / cancel
        repeat: 触发频率,create 时必填。once / hourly / daily / weekly / interval
        at: 时间点 "HH:MM"(24 小时制)。daily/weekly/once 必填;hourly 填分钟如 "05"
        weekday: 星期,repeat="weekly" 时必填。mon/tue/wed/thu/fri/sat/sun
        date: 日期 "YYYY-MM-DD",repeat="once" 时必填
        every_minutes: 间隔分钟数(1-1440),repeat="interval" 时必填
        message: agent 模式是唤起自己的指令;program 模式是写进本对话的正文,可用 {now}
        timer_id: 定时任务 id,cancel 时必填,不限对话
        mode: agent=到点唤起你(默认); program=到点只写程序定时卡片; exec=到点执行 command
        command: exec 模式必填,到点时在工作区根目录执行的命令
    """
    action = str(action or "").strip().lower()
    thread_id = _require_thread()

    if action == "list":
        items = []
        for t in timer_registry.list_all():
            items.append({
                "id": t.id,
                "thread_id": t.thread_id,
                "current": t.thread_id == thread_id,
                "when": t.when_text,
                "message": t.message,
                "mode": t.mode,
                "command": t.command,
                "next_at": _fmt_ts(t.next_at),
                "last_at": _fmt_ts(t.last_at),
            })
        return json.dumps({"timers": items}, ensure_ascii=False)

    if action == "cancel":
        tid = str(timer_id or "").strip()
        if not tid:
            return _err("timer_id 不能为空 —— 先用 action=\"list\" 查出要取消哪一个")
        if not timer_registry.cancel(tid):
            return _err(f"定时任务 {tid} 不存在(可能已经取消,或者只跑一次的任务已经跑完了)")
        return json.dumps({"ok": True, "action": "cancelled", "timer_id": tid}, ensure_ascii=False)

    if action == "create":
        msg = str(message or "").strip()
        cmd = str(command or "").strip()
        if str(mode or "agent").strip().lower() == "exec":
            if not cmd:
                return _err("command 不能为空")
        elif not msg:
            return _err("message 不能为空")
        try:
            n = int(every_minutes or 0)
        except (TypeError, ValueError):
            raise RuntimeError(f"every_minutes={every_minutes!r} 要是整数分钟,例 every_minutes=10")
        t = timer_registry.create(
            thread_id=thread_id, message=msg,
            repeat=repeat, at=at, weekday=weekday, date=date, every_minutes=n,
            mode=mode,
            command=cmd,
        )
        if t.mode == "exec":
            note = _EXEC_NOTE
        elif t.mode == "program":
            note = _PROGRAM_NOTE
        else:
            note = _AGENT_NOTE
        return json.dumps({
            "ok": True,
            "timer": {
                "id": t.id,
                "when": t.when_text,
                "message": t.message,
                "mode": t.mode,
                "command": t.command,
                "next_at": _fmt_ts(t.next_at),
            },
            "note": note,
        }, ensure_ascii=False)

    raise RuntimeError(f"未知 action: {action!r}。可用: create, list, cancel")
