from __future__ import annotations

from typing import Any

_EFFORTS = ("low", "medium", "high")


def pop_reasoning(body: dict[str, Any]) -> tuple[bool | None, str]:
    thinking = body.pop("thinking", None)
    effort = str(body.pop("reasoning_effort", "") or "").strip().lower()
    body.pop("reasoning", None)
    enabled: bool | None = None
    if isinstance(thinking, dict):
        kind = str(thinking.get("type") or "")
        if kind == "enabled":
            enabled = True
            effort = str(thinking.get("reasoning_effort") or effort or "").strip().lower()
        elif kind == "disabled":
            enabled = False
    elif isinstance(thinking, bool):
        enabled = thinking
    elif thinking == "disabled":
        enabled = False
    elif thinking == "enabled":
        enabled = True
    if enabled is True and effort not in _EFFORTS:
        effort = "medium"
    if enabled is not True:
        effort = ""
    return enabled, effort
