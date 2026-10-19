from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

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


def _append_note(result: Any, note: str) -> str:
    if result is None:
        return note
    if isinstance(result, str):
        return result + "\n\n" + note
    return json.dumps(result, ensure_ascii=False, default=str) + "\n\n" + note
