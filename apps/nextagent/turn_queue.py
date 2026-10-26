from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field


@dataclass
class UserItem:
    message_id: str
    content: str
    attachments: list
    model: str
    media_passthrough: bool
    thinking: bool | None
    effort: str | None
    plan_mode: bool
    active_roles: list
    skip_handoff: bool
    item_id: str = ""
    queued_at: int = 0


@dataclass
class ReportItem:
    content: str
    group_chat: bool
    item_id: str = ""
    queued_at: int = 0


_queues: dict[str, deque] = {}
_hold: set[str] = set()


def depth(thread_id: str) -> int:
    queued = _queues.get(thread_id)
    if queued is None:
        return 0
    return len(queued)


def enqueue(thread_id: str, item: UserItem | ReportItem) -> int:
    import time

    if not item.item_id:
        item.item_id = "q_" + uuid.uuid4().hex[:12]
    if not item.queued_at:
        item.queued_at = int(time.time() * 1000)
    queued = _queues.get(thread_id)
    if queued is None:
        queued = deque()
        _queues[thread_id] = queued
    queued.append(item)
    return len(queued)


def drain(thread_id: str) -> list[UserItem | ReportItem]:
    queued = _queues.pop(thread_id, None)
    if not queued:
        return []
    return list(queued)


def merge(items: list[UserItem | ReportItem]) -> UserItem | ReportItem:
    if not items:
        raise RuntimeError("merge called with empty queue")
    if len(items) == 1:
        return items[0]
    parts = [it.content for it in items if it.content]
    users = [it for it in items if isinstance(it, UserItem)]
    if not users:
        return ReportItem(content="\n\n".join(parts), group_chat=items[-1].group_chat)
    attachments: list = []
    for it in users:
        attachments.extend(it.attachments or [])
    last = users[-1]
    return UserItem(
        message_id=users[0].message_id,
        content="\n\n".join(parts),
        attachments=attachments,
        model=last.model,
        media_passthrough=last.media_passthrough,
        thinking=last.thinking,
        effort=last.effort,
        plan_mode=last.plan_mode,
        active_roles=last.active_roles,
        skip_handoff=last.skip_handoff,
    )


def remove(thread_id: str, item_id: str) -> bool:
    queued = _queues.get(thread_id)
    if not queued:
        return False
    for item in queued:
        if item.item_id == item_id:
            queued.remove(item)
            if not queued:
                del _queues[thread_id]
            return True
    return False


def _attachment_names(attachments: list) -> list[str]:
    names: list[str] = []
    for att in attachments or []:
        if isinstance(att, dict):
            names.append(str(att.get("filename") or att.get("path") or att.get("media_id") or ""))
        else:
            names.append(str(att))
    return names


def snapshot(thread_id: str) -> list[dict]:
    queued = _queues.get(thread_id)
    if not queued:
        return []
    out: list[dict] = []
    for item in queued:
        if isinstance(item, UserItem):
            out.append({
                "id": item.item_id,
                "kind": "user",
                "content": item.content,
                "attachments": _attachment_names(item.attachments),
                "model": item.model,
                "queuedAt": item.queued_at,
            })
        else:
            out.append({
                "id": item.item_id,
                "kind": "report",
                "content": item.content,
                "attachments": [],
                "model": "",
                "queuedAt": item.queued_at,
            })
    return out


def threads_with_items() -> list[str]:
    return [tid for tid, queued in _queues.items() if queued]


def hold(thread_id: str) -> None:
    _hold.add(thread_id)


def release(thread_id: str) -> None:
    _hold.discard(thread_id)


def held(thread_id: str) -> bool:
    return thread_id in _hold


def reset(thread_id: str) -> None:
    _queues.pop(thread_id, None)
    _hold.discard(thread_id)
