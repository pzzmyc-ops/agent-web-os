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


def pop(thread_id: str) -> UserItem | ReportItem | None:
    queued = _queues.get(thread_id)
    if not queued:
        return None
    item = queued.popleft()
    if not queued:
        del _queues[thread_id]
    return item


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
