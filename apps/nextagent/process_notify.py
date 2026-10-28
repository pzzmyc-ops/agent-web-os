from __future__ import annotations

from typing import Awaitable, Callable

from agent_framework import FunctionInvocationContext, FunctionMiddleware


class ProcessNotifyMiddleware(FunctionMiddleware):
    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        await call_next()
        name = getattr(context.function, "name", "")
        if name in {"terminal", "process"}:
            from .toolkit.terminal import terminal_windows_note

            context.result = _append_note(context.result, terminal_windows_note())


def _append_note(result: object, note: str) -> str:
    if not isinstance(result, str):
        raise TypeError(f"终端工具结果应已被 ToolGuardMiddleware 渲染成 str，收到 {type(result).__name__}")
    return result + "\n\n" + note
