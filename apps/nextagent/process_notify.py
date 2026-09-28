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
        from .maf_tools.process_registry import process_registry
        from .maf_tools.session_key import get_current_session_key

        session_key = get_current_session_key(default="")
        if not session_key:
            return
        pairs = process_registry.drain_notifications(session_key=session_key)
        if not pairs:
            return
        if any(str(evt.get("type") or "completion") == "completion" for evt, _text in pairs):
            from fm.backend.desktop import notify_fs_changed

            notify_fs_changed()
        note = "<system_notification>\n" + "\n\n".join(text for _evt, text in pairs) + "\n</system_notification>"
        context.result = _append_note(context.result, note)


def _append_note(result: Any, note: str) -> str:
    if result is None:
        return note
    if isinstance(result, str):
        return result + "\n\n" + note
    return json.dumps(result, ensure_ascii=False, default=str) + "\n\n" + note
