from __future__ import annotations

import json

from ._context import require_turn
from ._registry import registry

ENTRY_DELIMITER = "\n§\n"

DESCRIPTION = (
    "保存本会话记忆。每轮会注入系统提示词。"
    "一次调用用 operations 做完全部改动；单条用 action=add/replace/remove。"
    "replace/remove 必须带 old_text（能唯一定位该条的子串）。"
    "用户偏好、纠正、稳定事实才写入；过程进度和琐事不要写。"
)

MEMORY_DOC = """保存本会话的记忆。每个对话一份，群聊角色共用。每轮调用都会写入系统提示词。

条目用 § 分隔。人可以在顶栏记忆窗口里直接改整份文本。

## memory(action="add", content="...")
追加一条。content 不能为空，不能和已有条目完全相同。

## memory(action="replace", old_text="...", content="...")
用 old_text 子串定位一条，整条换成 content。匹配不到或多条不同内容同时命中会报错。

## memory(action="remove", old_text="...")
用 old_text 子串定位一条并删除。

## memory(operations=[{action, content?, old_text?}, ...])
一次做完多步，整批成功才写入。多条改动或腾位置时用这个。
"""


def _parse(raw: str) -> list[str]:
    if not isinstance(raw, str):
        raise RuntimeError("memory text must be a string")
    if not raw.strip():
        return []
    return [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]


def _join(entries: list[str]) -> str:
    return ENTRY_DELIMITER.join(entries)


def _apply_op(working: list[str], action: str, content: str, old_text: str, pos: str) -> None:
    act = str(action or "").strip().lower()
    content = str(content or "").strip()
    old_text = str(old_text or "").strip()
    if act == "add":
        if not content:
            raise RuntimeError(f"{pos}: content 不能为空")
        if content in working:
            raise RuntimeError(f"{pos}: 条目已存在")
        working.append(content)
        return
    if act == "replace":
        if not old_text:
            raise RuntimeError(f"{pos}: old_text 不能为空")
        if not content:
            raise RuntimeError(f"{pos}: content 不能为空")
        matches = [i for i, e in enumerate(working) if old_text in e]
        if not matches:
            raise RuntimeError(f"{pos}: 没有条目匹配 '{old_text}'")
        if len({working[i] for i in matches}) > 1:
            raise RuntimeError(f"{pos}: '{old_text}' 匹配到多条不同条目")
        working[matches[0]] = content
        return
    if act == "remove":
        if not old_text:
            raise RuntimeError(f"{pos}: old_text 不能为空")
        matches = [i for i, e in enumerate(working) if old_text in e]
        if not matches:
            raise RuntimeError(f"{pos}: 没有条目匹配 '{old_text}'")
        if len({working[i] for i in matches}) > 1:
            raise RuntimeError(f"{pos}: '{old_text}' 匹配到多条不同条目")
        working.pop(matches[0])
        return
    raise RuntimeError(f"{pos}: action 必须是 add / replace / remove")


async def _notify(thread_id: str) -> None:
    from ..live_conns import broadcast

    await broadcast({"type": "memory_changed", "data": {"threadId": thread_id}})


@registry.register(
    toolset="ask",
    name="memory",
    summary="读写本会话记忆",
    description=DESCRIPTION,
    doc=MEMORY_DOC,
)
async def memory(
    action: str = "",
    content: str = "",
    old_text: str = "",
    operations: list | None = None,
) -> str:
    ctx = require_turn()
    store = ctx.store
    if store is None:
        raise RuntimeError("memory missing store")
    working = _parse(store.load_memory(ctx.thread_id))
    if operations is not None:
        if not isinstance(operations, list):
            raise RuntimeError("operations 必须是数组")
        if not operations:
            raise RuntimeError("operations 不能为空")
        for i, op in enumerate(operations):
            if not isinstance(op, dict):
                raise RuntimeError(f"Operation {i + 1}: 必须是对象")
            _apply_op(
                working,
                str(op.get("action") or ""),
                str(op.get("content") or ""),
                str(op.get("old_text") or ""),
                f"Operation {i + 1}",
            )
        message = "batch applied"
    else:
        act = str(action or "").strip().lower()
        if not act:
            raise RuntimeError("必须提供 action 或 operations")
        _apply_op(working, act, content, old_text, act)
        message = f"{act} ok"
    store.save_memory(ctx.thread_id, _join(working))
    await _notify(ctx.thread_id)
    return json.dumps(
        {
            "success": True,
            "done": True,
            "entry_count": len(working),
            "message": message,
        },
        ensure_ascii=False,
    )
