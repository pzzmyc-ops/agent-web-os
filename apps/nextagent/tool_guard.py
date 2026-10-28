from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from agent_framework import Content, FunctionInvocationContext, FunctionMiddleware

from .toolkit._context import current_turn
from .toolkit._registry import ToolSpec, registry
from .toolkit._spill import OUTPUT_LIMIT_CHARS, spill_text

PARALLEL_SAFE_BUILTIN = frozenset({
    "file_access_read",
    "file_access_ls",
    "file_access_grep",
    "skills_list",
    "skill_view",
    "load_skill",
    "read_skill_resource",
    "plan_read",
})


class ToolTimeoutError(RuntimeError):
    pass


_call_id: ContextVar[str] = ContextVar("tool_guard_call_id", default="")


def current_call_id() -> str:
    return _call_id.get()


class _Gate:
    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self.readers = 0
        self.writer = False
        self.pending = 0

    async def acquire(self, shared: bool) -> None:
        async with self._cond:
            self.pending += 1
            try:
                if shared:
                    await self._cond.wait_for(lambda: not self.writer)
                    self.readers += 1
                else:
                    await self._cond.wait_for(lambda: not self.writer and self.readers == 0)
                    self.writer = True
            finally:
                self.pending -= 1

    async def release(self, shared: bool) -> None:
        async with self._cond:
            if shared:
                self.readers -= 1
            else:
                self.writer = False
            self._cond.notify_all()

    def idle(self) -> bool:
        return self.readers == 0 and not self.writer and self.pending == 0


_gates: dict[str, _Gate] = {}


def _gate_key() -> str:
    ctx = current_turn()
    if ctx is None or not ctx.task_id:
        raise RuntimeError("工具调用不在任何一轮对话里（没有 TurnContext），无法确定并发闸门归属")
    return ctx.task_id


def _gate_for(key: str) -> _Gate:
    gate = _gates.get(key)
    if gate is None:
        gate = _Gate()
        _gates[key] = gate
    return gate


def _drop_if_idle(key: str) -> None:
    gate = _gates.get(key)
    if gate is not None and gate.idle():
        del _gates[key]


def is_parallel_safe(name: str) -> bool:
    spec = registry.get(name)
    if spec is not None:
        return spec.concurrency_safe
    return name in PARALLEL_SAFE_BUILTIN


_presentations: dict[str, dict] = {}


def pop_presentation(call_id: str) -> dict | None:
    return _presentations.pop(call_id, None)


def _is_content_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(c, Content) for c in value)


def render_value(spec: ToolSpec | None, name: str, args: dict, raw: Any) -> Any:
    if isinstance(raw, Content):
        return raw
    if _is_content_list(raw):
        if any(c.type != "text" for c in raw):
            return raw
        return "\n".join(c.text or "" for c in raw)
    if isinstance(raw, str):
        return raw
    if raw is None:
        raise TypeError(f"工具 {name} 返回了 None")
    if spec is not None and spec.render is not None:
        text = spec.render(args, raw)
        if not isinstance(text, str):
            raise TypeError(f"工具 {name} 的 render 必须返回 str，收到 {type(text).__name__}")
        return text
    return json.dumps(raw, ensure_ascii=False)


def present_value(spec: ToolSpec | None, name: str, args: dict, raw: Any) -> dict | None:
    if spec is None or spec.present is None:
        return None
    if isinstance(raw, (str, Content)) or _is_content_list(raw):
        return None
    meta = spec.present(args, raw)
    if meta is not None and not isinstance(meta, dict):
        raise TypeError(f"工具 {name} 的 present 必须返回 dict 或 None，收到 {type(meta).__name__}")
    return meta


def _fit_result(result: Any, limit: int) -> Any:
    if not isinstance(result, str):
        return result
    spilled = spill_text(result, limit)
    if spilled is None:
        return result
    return spilled["text"]


class ToolGuardMiddleware(FunctionMiddleware):
    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        name = str(getattr(context.function, "name", "") or "")
        spec = registry.get(name)
        shared = is_parallel_safe(name)
        timeout = spec.timeout_seconds if spec is not None else None
        limit = spec.max_result_chars if spec is not None else OUTPUT_LIMIT_CHARS
        key = _gate_key()
        gate = _gate_for(key)
        call_id = str(context.metadata.get("call_id") or "")
        token = _call_id.set(call_id)
        await gate.acquire(shared)
        try:
            if timeout is None:
                await call_next()
            else:
                try:
                    await asyncio.wait_for(call_next(), timeout)
                except asyncio.TimeoutError:
                    raise ToolTimeoutError(f"工具 {name} 超过 {timeout:g} 秒未返回，已放弃等待") from None
        finally:
            await gate.release(shared)
            _drop_if_idle(key)
            _call_id.reset(token)
        args = dict(context.arguments) if isinstance(context.arguments, Mapping) else {}
        raw = context.result
        meta = present_value(spec, name, args, raw)
        if meta is not None and call_id:
            _presentations[call_id] = meta
        context.result = _fit_result(render_value(spec, name, args, raw), limit)
