from __future__ import annotations

import json
import re
import uuid

from . import session_log

PLAN_POLICY = (
    "\n\n[计划模式]\n"
    "你正处于计划模式。一直留在计划模式，直到 exit_plan_mode 成功、用户把模式关掉，或你调用 plan_end_mode(end=true)。"
    "「去做吧」这类话等于去写方案，不是去改东西。"
    "口头同意、回答你刚才问的问题，都不算批准，也不能当作已经交了方案；把确认过的决定写进方案，再通过 exit_plan_mode 交出来。\n"
    "先摸清现状。只用只读查看、搜索、分析。不要改文件、改配置、跑会改仓库的格式化或生成、提交，也不要开始执行方案。"
    "当前没有终端，也没有写文件的能力。不要调用不存在的写入或终端工具。\n"
    "写计划的同时写待办。待办是当前这一份工作清单，每次交完整列表，新的盖旧的。"
    "能自己查清的不要问用户。不要把终稿当普通回复贴出来，不要问「要不要开始」。\n"
    "方案要能直接开工：目标、成功标准、按部分怎么改、接口和数据、边界、失败、测试、假设。"
    "短到能审，细到别人不用再做设计决策。\n"
    "准备好了就调用 exit_plan_mode，提交以 # 标题开头的完整 markdown 计划，以及完整待办列表。"
    "每个会话只有一份计划。需要正文或待办时调用 plan_read，不要把整份方案反复写进回复。"
    "本轮必须调用 plan_end_mode 和 plan_show_window。"
    "闲聊或用户说无关的话：plan_end_mode(end=true) 或 plan_show_window(show=false)，不要调用 exit_plan_mode。"
    "要交方案给人审：调用 exit_plan_mode，并 plan_show_window(show=true)。"
    "要关掉计划模式：plan_end_mode(end=true)。要再打开计划窗：plan_show_window(show=true)。"
    "被拒就改完再交。审不了就停着，不要自己开干。"
    "计划未被批准前不要调用 round_complete。"
)

EXIT_PLAN_MODE = "exit_plan_mode"
TODO_WRITE = "todo_write"
PLAN_READ = "plan_read"
PLAN_END_MODE = "plan_end_mode"
PLAN_SHOW_WINDOW = "plan_show_window"
TODO_STATUSES = ("pending", "in_progress", "completed")

APPROVE_FOLLOWUP = "用户接受了这个计划，请按照计划内容执行"
APPROVE_MODIFIED_FOLLOWUP = "用户接受并修改了这个计划，获取最新计划内容，请按照计划内容执行"
DECLINE_FOLLOWUP = "用户拒绝了这个计划，根据情况制定下一步计划"


def followup_for_decision(decision: str, *, user_modified: bool) -> str:
    if decision == "approve":
        if user_modified:
            return APPROVE_MODIFIED_FOLLOWUP
        return APPROVE_FOLLOWUP
    if decision == "decline":
        return DECLINE_FOLLOWUP
    raise RuntimeError("followup_for_decision unknown decision: " + decision)


async def wait_review_tool_result(store, thread_id: str, block_id: str) -> None:
    import asyncio

    block_id = str(block_id or "")
    thread_id = str(thread_id or "")
    if not block_id:
        raise RuntimeError("wait_review_tool_result missing block_id")
    if not thread_id:
        raise RuntimeError("wait_review_tool_result missing thread_id")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 15
    while loop.time() < deadline:
        for ev in store.load_events(thread_id):
            if str(ev.get("type") or "") == session_log.EV_TOOL_RESULT and str(ev.get("block_id") or "") == block_id:
                return
        await asyncio.sleep(0.02)
    raise RuntimeError("wait_review_tool_result timeout: " + block_id)


def abandon_plan(store, thread_id: str) -> None:
    thread_id = str(thread_id or "")
    if not thread_id:
        raise RuntimeError("abandon_plan missing thread_id")
    if store.load_plan_doc(thread_id) is not None:
        store.clear_plan_doc(thread_id)
    ui = store.load_session_ui(thread_id)
    if ui.get("planMode"):
        next_ui = dict(ui)
        next_ui["planMode"] = False
        store.save_session_ui(thread_id, next_ui)

PLAN_HIDE_TOOLS = frozenset({
    "file_access_write",
    "file_access_delete",
    "file_access_replace",
    "file_access_replace_lines",
    "terminal",
    "process",
    "clarify",
    "schedule",
    "skill_manage",
    "skills_hub",
})

_HEADING = re.compile(r"^#\s+\S")


def normalize_todos(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise RuntimeError("todos must be a list")
    todos: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise RuntimeError("each todo must be an object")
        content = str(item.get("content") or "").strip()
        status = str(item.get("status") or "").strip()
        if not content:
            raise RuntimeError("todo content must be a non-empty string")
        if content in seen:
            raise RuntimeError("duplicate todo content: " + content)
        if status not in TODO_STATUSES:
            raise RuntimeError("todo status must be pending, in_progress or completed")
        seen.add(content)
        todos.append({"content": content, "status": status})
    return todos


def format_plan_pointer(*, phase: str, user_modified: bool = False) -> str:
    if phase == "review":
        lines = [
            "[计划模式]",
            "本会话当前有一份待审计划。方案正文不在提示词里。需要看方案或待办时调用 plan_read。",
        ]
    elif phase == "active":
        lines = [
            "[执行约束]",
            "本会话当前有一份计划。必须按最新计划执行，不要偏离。",
            "方案正文不在提示词里。需要看方案或待办时调用 plan_read。",
            "做完一项就调用 todo_write 提交完整待办清单，新的盖旧的。",
        ]
    else:
        raise RuntimeError("unknown plan phase: " + phase)
    if user_modified:
        lines.append("用户已经改过这份计划和待办。先调用 plan_read 取得最新正文，不要沿用你记忆里的旧版。")
    return "\n".join(lines)


def persist_plan_doc(
    store,
    thread_id: str,
    *,
    task_id: str = "",
    plan: str,
    todos: list[dict],
    phase: str,
    plan_id: str = "",
    review_block_id: str = "",
    source: str = "agent",
) -> str:
    if source not in ("agent", "user"):
        raise RuntimeError("plan_doc source must be agent or user")
    current = store.load_plan_doc(thread_id)
    pid = str(plan_id or "").strip()
    if not pid and current:
        pid = str(current["planId"])
    if not pid:
        pid = uuid.uuid4().hex
    store.save_plan_doc(
        thread_id,
        {
            "plan": plan,
            "todos": todos,
            "phase": phase,
            "plan_id": pid,
            "review_block_id": review_block_id or "",
            "source": source,
            "task_id": task_id,
        },
    )
    return pid


def public_plan_doc(rec: dict | None) -> dict | None:
    if rec is None:
        return None
    plan = str(rec.get("plan") or "").strip()
    todos = rec.get("todos")
    if not plan or not isinstance(todos, list):
        return None
    pid = str(rec.get("plan_id") or rec.get("id") or rec.get("planId") or "").strip()
    if not pid:
        raise RuntimeError("plan_doc missing plan_id")
    return {
        "plan": plan,
        "todos": list(todos),
        "phase": str(rec.get("phase") or ""),
        "planId": pid,
        "reviewBlockId": str(rec.get("review_block_id") or rec.get("reviewBlockId") or ""),
        "userModified": str(rec.get("source") or "") == "user",
    }


def fold_plan_doc(events: list[dict]) -> dict | None:
    doc = None
    for ev in events:
        zone = str(ev.get("zone") or "")
        etype = str(ev.get("type") or "")
        if zone == "plan" or etype == session_log.EV_PLAN_DOC:
            doc = ev
    return public_plan_doc(doc)


def commit_plan_approval(store, thread_id: str, *, task_id: str = "", plan_id: str = "") -> dict:
    doc = store.load_plan_doc(thread_id)
    if doc is None:
        raise RuntimeError("commit_plan_approval missing plan_doc")
    want = str(plan_id or "").strip()
    if want and want != doc["planId"]:
        raise RuntimeError("commit_plan_approval plan_id mismatch")
    persist_plan_doc(
        store,
        thread_id,
        task_id=task_id,
        plan=doc["plan"],
        todos=doc["todos"],
        phase="active",
        plan_id=doc["planId"],
        review_block_id=str(doc.get("reviewBlockId") or ""),
        source="user" if doc.get("userModified") else "agent",
    )
    return store.load_plan_doc(thread_id)


def apply_user_plan_update(store, thread_id: str, *, plan_id: str, plan: str, todos) -> dict:
    text = (plan or "").strip()
    if not _HEADING.match(text):
        raise RuntimeError("plan must start with a # heading")
    items = normalize_todos(todos)
    current = store.load_plan_doc(thread_id)
    if current is None:
        raise RuntimeError("apply_user_plan_update missing plan_doc")
    want = str(plan_id or "").strip()
    if not want:
        raise RuntimeError("apply_user_plan_update missing plan_id")
    if want != current["planId"]:
        raise RuntimeError("apply_user_plan_update plan_id mismatch")
    persist_plan_doc(
        store,
        thread_id,
        plan=text,
        todos=items,
        phase=str(current["phase"] or ""),
        plan_id=current["planId"],
        review_block_id=str(current.get("reviewBlockId") or ""),
        source="user",
    )
    doc = store.load_plan_doc(thread_id)
    if doc is None:
        raise RuntimeError("apply_user_plan_update failed to fold")
    return doc


class PlanReviewRegistry:
    def __init__(self) -> None:
        self._waiters: dict[str, object] = {}

    def register(self, block_id: str):
        import asyncio

        block_id = str(block_id or "")
        old = self._waiters.pop(block_id, None)
        if old is not None and not old.done():
            old.cancel()
        fut = asyncio.get_running_loop().create_future()
        self._waiters[block_id] = fut
        return fut

    def resolve(self, block_id: str, decision: dict) -> bool:
        fut = self._waiters.pop(str(block_id or ""), None)
        if fut is None or fut.done():
            return False
        fut.set_result(decision)
        return True

    def discard(self, block_id: str) -> None:
        fut = self._waiters.pop(str(block_id or ""), None)
        if fut is not None and not fut.done():
            fut.cancel()


plan_review_registry = PlanReviewRegistry()


def make_exit_plan_mode_tool():
    from agent_framework import tool

    from .toolkit._context import require_turn

    @tool(
        name=EXIT_PLAN_MODE,
        description=(
            "仅在计划模式使用。把完整 markdown 计划和完整待办清单交给用户审阅。"
            "计划必须以 # 标题开头。待办每条含 content 与 status（pending / in_progress / completed）。"
            "批准后离开计划模式，从用户的下一轮开始按方案执行。"
        ),
        approval_mode="never_require",
    )
    async def exit_plan_mode(plan: str, todos: list[dict]) -> str:
        text = (plan or "").strip()
        if not _HEADING.match(text):
            raise RuntimeError("exit_plan_mode requires a non-empty markdown plan starting with a # heading")
        items = normalize_todos(todos)
        if not items:
            raise RuntimeError("todos must be a non-empty list")
        ctx = require_turn()
        store = getattr(ctx, "store", None)
        if store is None:
            raise RuntimeError("exit_plan_mode missing store")
        channel = getattr(ctx, "plan_review", None)
        if channel is None:
            raise RuntimeError("no plan-review channel is available")
        decision = await channel.ask(text, items)
        if decision.get("dismissed"):
            raise RuntimeError("用户关闭了审计划要自己说话；留在计划模式，停在这里等他们的下一条消息。")
        if not decision.get("approved"):
            feedback = str(decision.get("feedback") or "").strip()
            if feedback:
                raise RuntimeError("用户选择继续规划；他们的反馈：" + feedback)
            raise RuntimeError("用户选择继续规划；请修改计划和待办后再交。")
        doc = store.load_plan_doc(ctx.thread_id)
        if doc is None:
            raise RuntimeError("exit_plan_mode missing stored plan_doc")
        return json.dumps(
            {"approved": True, "plan": text, "todos": items},
            ensure_ascii=False,
        )

    return exit_plan_mode


def make_todo_write_tool():
    from agent_framework import tool

    from .toolkit._context import require_turn

    @tool(
        name=TODO_WRITE,
        description=(
            "更新当前待办清单。每次提交完整列表，新的盖旧的。"
            "每条含 content 与 status（pending / in_progress / completed）。"
            "规划时和交计划一起用；执行时用来勾掉已完成的项。"
        ),
        approval_mode="never_require",
    )
    async def todo_write(todos: list[dict]) -> str:
        items = normalize_todos(todos)
        if not items:
            raise RuntimeError("todos must be a non-empty list")
        ctx = require_turn()
        store = getattr(ctx, "store", None)
        if store is None:
            raise RuntimeError("todo_write missing store")
        current = store.load_plan_doc(ctx.thread_id)
        if current is None:
            raise RuntimeError("todo_write requires an approved plan; submit plan and todos through exit_plan_mode first")
        if current.get("phase") != "active":
            raise RuntimeError("todo_write requires an approved plan; submit plan and todos through exit_plan_mode first")
        persist_plan_doc(
            store,
            ctx.thread_id,
            task_id=ctx.task_id,
            plan=current["plan"],
            todos=items,
            phase="active",
            plan_id=current["planId"],
            review_block_id=str(current.get("reviewBlockId") or ""),
        )
        return json.dumps({"todos": items}, ensure_ascii=False)

    return todo_write


def make_plan_read_tool():
    from agent_framework import tool

    from .toolkit._context import require_turn

    @tool(
        name=PLAN_READ,
        description=(
            "读取本会话当前这一份计划的最新正文和待办。每个会话只有一份计划，不需要编号。"
            "正文以这次返回为准。用户改过之后必须再调一次，不要沿用旧内容。"
        ),
        approval_mode="never_require",
    )
    async def plan_read() -> str:
        ctx = require_turn()
        store = getattr(ctx, "store", None)
        if store is None:
            raise RuntimeError("plan_read missing store")
        doc = store.load_plan_doc(ctx.thread_id)
        if doc is None:
            raise RuntimeError("plan_read missing plan_doc")
        return json.dumps(
            {
                "phase": doc["phase"],
                "userModified": bool(doc.get("userModified")),
                "plan": doc["plan"],
                "todos": doc["todos"],
            },
            ensure_ascii=False,
        )

    return plan_read


def make_plan_end_mode_tool():
    from agent_framework import tool

    from .toolkit._context import require_turn

    @tool(
        name=PLAN_END_MODE,
        description=(
            "申报本轮是否结束计划模式。"
            "闲聊或用户说无关的话传 end=true。"
            "还在规划、还要交方案传 end=false。"
        ),
        approval_mode="never_require",
    )
    async def plan_end_mode(end: bool) -> str:
        ctx = require_turn()
        st = getattr(ctx, "plan_status", None)
        if st is None:
            raise RuntimeError("plan_end_mode missing plan_status")
        st["end_called"] = True
        st["end"] = bool(end)
        emit = getattr(ctx, "emit_plan_status", None)
        if emit is not None:
            await emit()
        return json.dumps({"endPlanMode": bool(end)}, ensure_ascii=False)

    return plan_end_mode


def make_plan_show_window_tool():
    from agent_framework import tool

    from .toolkit._context import require_turn

    @tool(
        name=PLAN_SHOW_WINDOW,
        description=(
            "申报本轮是否打开计划窗口。"
            "要给人审计划或重开窗口传 show=true。"
            "闲聊、不要弹窗传 show=false。"
        ),
        approval_mode="never_require",
    )
    async def plan_show_window(show: bool) -> str:
        ctx = require_turn()
        st = getattr(ctx, "plan_status", None)
        if st is None:
            raise RuntimeError("plan_show_window missing plan_status")
        st["show_called"] = True
        st["show"] = bool(show)
        emit = getattr(ctx, "emit_plan_status", None)
        if emit is not None:
            await emit()
        return json.dumps({"showWindow": bool(show)}, ensure_ascii=False)

    return plan_show_window
