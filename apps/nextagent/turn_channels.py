"""工具 → 用户的交互通道:澄清提问、计划审阅。

两条通道都挂在「当前正在执行的工具块」上(ToolTracker.current_block),前端把问题
和方案渲染在那个块里;等待与超时也在这里,工具函数本身只管调 ask。
"""
from __future__ import annotations

import asyncio

from .tool_tracker import ToolTracker


class ClarifyChannel:
    def __init__(self, tracker: ToolTracker, *, group_chat: bool = False) -> None:
        self._tracker = tracker
        self._group_chat = group_chat

    def _timeout_text(self, timeout: float) -> str:
        return (
            f"[用户未在 {max(1, round(timeout))} 秒内回应,请自行做出最佳判断并继续,"
            "事后向用户说明你按什么假设做的]"
        )

    async def _ask_group(self, question: str, choices: list[str], *, timeout: float) -> str:
        from .groupchat.runtime import require_group_chat
        from .toolkit.clarify import group_clarify_registry

        lines = [question, ""]
        for i, choice in enumerate(choices, 1):
            lines.append(f"{i}. {choice}")
        lines.append("")
        lines.append("请在本群@我回复本题，不@不生效。")
        thread_id = self._tracker.thread_id
        fut = group_clarify_registry.register(thread_id)
        await asyncio.to_thread(require_group_chat().deliver_text, thread_id, "\n".join(lines))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return self._timeout_text(timeout)
        finally:
            group_clarify_registry.discard(thread_id)

    async def ask(self, question: str, choices: list[str], *, timeout: float) -> str:
        from .toolkit.clarify import clarify_registry

        if self._group_chat:
            return await self._ask_group(question, choices, timeout=300.0)

        call = self._tracker.current_block()
        if call is None:
            return "[无法建立提问通道,请自行做出最佳判断并继续,事后向用户说明你的假设]"

        block_id = call["blockId"]
        payload = {"question": question, "choices": list(choices)}
        fut = clarify_registry.register(block_id)
        await self._tracker.patch_block(block_id, {"clarify": payload})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = self._timeout_text(timeout)
            await self._tracker.patch_block(
                block_id, {"clarify": {**payload, "answer": timed_out}}
            )
            return timed_out
        finally:
            clarify_registry.discard(block_id)


class PlanReviewChannel:
    def __init__(self, tracker: ToolTracker) -> None:
        self._tracker = tracker
        self.approved = False

    async def ask(self, plan: str, todos: list) -> dict:
        from .plan_mode import persist_plan_doc, plan_review_registry

        call = self._tracker.current_block()
        if call is None:
            raise RuntimeError("exit_plan_mode missing tool block")
        block_id = call["blockId"]
        plan_id = persist_plan_doc(
            self._tracker.store,
            self._tracker.thread_id,
            task_id=self._tracker.task_id,
            plan=plan,
            todos=todos,
            phase="review",
            review_block_id=block_id,
        )
        fut = plan_review_registry.register(block_id)
        await self._tracker.patch_block(
            block_id,
            {"planReview": {"plan": plan, "todos": todos, "planId": plan_id}},
        )
        try:
            decision = await fut
        finally:
            plan_review_registry.discard(block_id)
        if decision.get("approved"):
            self.approved = True
        return decision
