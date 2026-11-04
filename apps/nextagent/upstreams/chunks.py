from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator


def new_chunk_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def make_chunk(
    chunk_id: str,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
    usage: dict | None = None,
) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        obj["usage"] = usage
    return obj


def usage_dict(prompt: int, completion: int, cached: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "total_tokens": int(prompt or 0) + int(completion or 0),
    }
    if cached:
        out["prompt_tokens_details"] = {"cached_tokens": int(cached)}
    return out


def tool_call_delta(idx: int, call_id: str, name: str, arguments: str, *, announce: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"index": idx, "type": "function", "function": {}}
    if announce:
        entry["id"] = call_id
        entry["function"]["name"] = name
    if arguments:
        entry["function"]["arguments"] = arguments
    return {"tool_calls": [entry]}


async def iter_sse_data(lines: AsyncIterator[str]) -> AsyncIterator[Any]:
    async for raw in lines:
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError("上游返回了不是 JSON 的 SSE 数据: " + payload[:500]) from exc


async def collect_chunks(chunks: AsyncIterator[dict]) -> dict[str, Any]:
    text = ""
    reasoning = ""
    reasoning_details: list = []
    tool_rows: dict[int, dict[str, Any]] = {}
    finish = None
    usage = None
    chunk_id = ""
    model = ""
    created = int(time.time())
    async for ch in chunks:
        chunk_id = chunk_id or str(ch.get("id") or "")
        model = model or str(ch.get("model") or "")
        if ch.get("created"):
            created = int(ch["created"])
        if ch.get("usage") is not None:
            usage = ch["usage"]
        for choice in ch.get("choices") or []:
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                text += str(delta["content"])
            if delta.get("reasoning_content"):
                reasoning += str(delta["reasoning_content"])
            if delta.get("reasoning_details"):
                reasoning_details.extend(list(delta["reasoning_details"]))
            for tc in delta.get("tool_calls") or []:
                idx = int(tc.get("index") or 0)
                row = tool_rows.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    row["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    row["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    row["function"]["arguments"] += fn["arguments"]
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if reasoning:
        message["reasoning_content"] = reasoning
    if reasoning_details:
        message["reasoning_details"] = reasoning_details
    if tool_rows:
        message["tool_calls"] = [tool_rows[k] for k in sorted(tool_rows)]
    out: dict[str, Any] = {
        "id": chunk_id or new_chunk_id(),
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish or ("tool_calls" if tool_rows else "stop")}],
    }
    if usage is not None:
        out["usage"] = usage
    return out
