from __future__ import annotations

from collections import deque
from dataclasses import dataclass


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


@dataclass
class ReportItem:
    content: str
    group_chat: bool


_queues: dict[str, deque] = {}
_stop_ahead: dict[str, int] = {}
_hold: set[str] = set()


def depth(thread_id: str) -> int:
    queued = _queues.get(thread_id)
    if queued is None:
        return 0
    return len(queued)


def enqueue(thread_id: str, item: UserItem | ReportItem) -> int:
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


def note_stop(thread_id: str, current_is_running: bool) -> str:
    if current_is_running:
        return "current"
    if arm_next_stop(thread_id):
        return "next"
    return "none"


def arm_next_stop(thread_id: str) -> bool:
    armed = _stop_ahead.get(thread_id, 0)
    if armed >= depth(thread_id):
        return False
    _stop_ahead[thread_id] = armed + 1
    return True


def take_armed_stop(thread_id: str) -> bool:
    armed = _stop_ahead.get(thread_id, 0)
    if armed <= 0:
        return False
    if armed == 1:
        del _stop_ahead[thread_id]
    else:
        _stop_ahead[thread_id] = armed - 1
    return True


def hold(thread_id: str) -> None:
    _hold.add(thread_id)


def release(thread_id: str) -> None:
    _hold.discard(thread_id)


def held(thread_id: str) -> bool:
    return thread_id in _hold


def reset(thread_id: str) -> None:
    _queues.pop(thread_id, None)
    _stop_ahead.pop(thread_id, None)
    _hold.discard(thread_id)
