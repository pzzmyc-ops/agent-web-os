from __future__ import annotations

from typing import Any

_MODE: Any = None


def set_group_chat(mode: Any) -> None:
    global _MODE
    _MODE = mode


def require_group_chat() -> Any:
    if _MODE is None:
        raise RuntimeError("群聊模式未启动")
    return _MODE
