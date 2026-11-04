from __future__ import annotations

import json
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from .chunks import iter_sse_data
from .reasoning import pop_reasoning


def _deepseek_host(base_url: str) -> bool:
    return (urlparse(base_url).hostname or "").lower() == "api.deepseek.com"


def encode(body: dict[str, Any], base_url: str) -> dict[str, Any]:
    body = dict(body)
    enabled, effort = pop_reasoning(body)
    body["stream"] = True
    body["stream_options"] = {"include_usage": True}
    if _deepseek_host(base_url):
        if enabled is True:
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = effort
        elif enabled is False:
            body["thinking"] = {"type": "disabled"}
        return body
    if enabled is True:
        body["reasoning_effort"] = effort
    return body


async def decode(lines: AsyncIterator[str], model: str) -> AsyncIterator[dict]:
    del model
    async for ev in iter_sse_data(lines):
        if not isinstance(ev, dict):
            continue
        if ev.get("error"):
            raise RuntimeError("chat 上游在流里报错: " + json.dumps(ev["error"], ensure_ascii=False)[:2000])
        if ev.get("created") is None:
            ev["created"] = 0
        yield ev
