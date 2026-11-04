from __future__ import annotations

import json
from typing import Any, AsyncIterator

from .chunks import iter_sse_data, make_chunk, new_chunk_id, tool_call_delta, usage_dict
from .reasoning import pop_reasoning


def _text_type(role: str) -> str:
    return "output_text" if role == "assistant" else "input_text"


def _parts(content: Any, role: str) -> list[dict]:
    text_type = _text_type(role)
    if isinstance(content, str):
        return [{"type": text_type, "text": content}] if content else []
    if not isinstance(content, list):
        raise ValueError("消息 content 既不是字符串也不是数组")
    out: list[dict] = []
    for item in content:
        if not isinstance(item, dict):
            raise ValueError("content 数组里出现了非对象元素")
        t = item.get("type")
        if t in ("text", "input_text", "output_text"):
            out.append({"type": text_type, "text": item.get("text", "")})
        elif t == "image_url":
            url = item.get("image_url")
            if isinstance(url, dict):
                url = url.get("url", "")
            out.append({"type": "input_image", "image_url": url})
        elif t == "input_image":
            out.append(item)
        elif t == "file":
            f = item.get("file") or {}
            part: dict[str, Any] = {"type": "input_file"}
            if f.get("file_data"):
                part["file_data"] = f["file_data"]
            if f.get("filename"):
                part["filename"] = f["filename"]
            out.append(part)
        elif t == "refusal" and role == "assistant":
            out.append({"type": "refusal", "refusal": item.get("refusal", item.get("text", ""))})
        else:
            raise ValueError("Responses 接口不支持这种内容类型: " + str(t))
    return out


def _message_items(msg: dict) -> list[dict]:
    role = str(msg.get("role") or "user")
    if role == "tool":
        return [{
            "type": "function_call_output",
            "call_id": str(msg.get("tool_call_id") or ""),
            "output": str(msg.get("content") or ""),
        }]
    items: list[dict] = []
    parts = _parts(msg.get("content"), role)
    if parts:
        items.append({"type": "message", "role": role, "content": parts})
    if role == "assistant":
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments", "")
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            items.append({
                "type": "function_call",
                "call_id": str(tc.get("id") or ""),
                "name": str(fn.get("name") or ""),
                "arguments": args,
            })
    return items


def _tool(tool: dict) -> dict:
    if tool.get("type") != "function":
        return tool
    fn = tool.get("function")
    if not isinstance(fn, dict):
        return tool
    out: dict[str, Any] = {"type": "function", "name": fn["name"]}
    if "description" in fn:
        out["description"] = fn["description"]
    if "parameters" in fn:
        out["parameters"] = fn["parameters"]
    if "strict" in fn:
        out["strict"] = fn["strict"]
    return out


def _tool_choice(tc: Any) -> Any:
    if isinstance(tc, dict) and tc.get("type") == "function":
        fn = tc.get("function") or {}
        return {"type": "function", "name": fn.get("name") or tc.get("name")}
    return tc


def encode(body: dict[str, Any]) -> dict[str, Any]:
    body = dict(body)
    enabled, effort = pop_reasoning(body)
    payload: dict[str, Any] = {"model": body["model"], "stream": True}
    payload["input"] = [item for m in body.get("messages") or [] for item in _message_items(m)]
    if enabled is True:
        payload["reasoning"] = {"effort": effort, "summary": "auto"}
    elif enabled is False:
        payload["reasoning"] = {"effort": "none"}
    if body.get("tools"):
        payload["tools"] = [_tool(t) for t in body["tools"]]
    if body.get("tool_choice") is not None:
        payload["tool_choice"] = _tool_choice(body["tool_choice"])
    if "parallel_tool_calls" in body:
        payload["parallel_tool_calls"] = bool(body["parallel_tool_calls"])
    for key in ("temperature", "top_p", "metadata", "user"):
        if key in body:
            payload[key] = body[key]
    if "max_completion_tokens" in body:
        payload["max_output_tokens"] = body["max_completion_tokens"]
    elif "max_tokens" in body:
        payload["max_output_tokens"] = body["max_tokens"]
    payload["store"] = False
    return payload


def _usage(resp: dict) -> dict:
    u = resp.get("usage") or {}
    details = u.get("input_tokens_details") or {}
    return usage_dict(
        int(u.get("input_tokens") or 0),
        int(u.get("output_tokens") or 0),
        int(details.get("cached_tokens") or 0),
    )


async def decode(lines: AsyncIterator[str], model: str) -> AsyncIterator[dict]:
    chunk_id = new_chunk_id()
    role_sent = False
    rows: dict[int, dict[str, Any]] = {}
    item_to_idx: dict[str, int] = {}

    def role_chunk():
        nonlocal role_sent
        if role_sent:
            return None
        role_sent = True
        return make_chunk(chunk_id, model, {"role": "assistant"})

    def idx_for(output_index: Any, item_id: Any) -> int:
        key = str(item_id or "")
        if key and key in item_to_idx:
            return item_to_idx[key]
        idx = int(output_index) if output_index is not None else max(rows.keys(), default=-1) + 1
        if key:
            item_to_idx[key] = idx
        if idx not in rows:
            rows[idx] = {"id": "", "name": "", "arguments": "", "announced": False}
        return idx

    def apply_item(idx: int, item: dict) -> None:
        row = rows[idx]
        call_id = str(item.get("call_id") or "")
        if call_id:
            row["id"] = call_id
        if item.get("name"):
            row["name"] = str(item["name"])
        args = item.get("arguments")
        if args and not row["arguments"]:
            row["arguments"] = str(args)
        if item.get("id"):
            item_to_idx[str(item["id"])] = idx

    def flush_row(idx: int, piece: str):
        row = rows[idx]
        if not row["name"]:
            return None
        first = not row["announced"]
        if not first and not piece:
            return None
        row["announced"] = True
        return make_chunk(chunk_id, model, tool_call_delta(idx, row["id"], row["name"], piece, announce=first))

    async for ev in iter_sse_data(lines):
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get("type") or "")
        if kind in ("error", "response.failed"):
            raise RuntimeError("Responses 上游报错: " + json.dumps(ev, ensure_ascii=False)[:2000])
        if kind == "response.incomplete":
            resp = ev.get("response") or {}
            detail = resp.get("incomplete_details") or {}
            rc = role_chunk()
            if rc:
                yield rc
            reason = str(detail.get("reason") or "")
            yield make_chunk(chunk_id, model, {}, "length" if reason == "max_output_tokens" else "stop", _usage(resp))
            return
        if kind in ("response.completed", "response.done"):
            resp = ev.get("response") or {}
            finish = "stop"
            for i, item in enumerate(resp.get("output") or []):
                if not isinstance(item, dict) or item.get("type") != "function_call":
                    continue
                finish = "tool_calls"
                idx = idx_for(i, item.get("id") or item.get("call_id"))
                apply_item(idx, item)
                rc = role_chunk()
                if rc:
                    yield rc
                ch = flush_row(idx, "" if rows[idx]["announced"] else rows[idx]["arguments"])
                if ch:
                    yield ch
            rc = role_chunk()
            if rc:
                yield rc
            yield make_chunk(chunk_id, model, {}, finish, _usage(resp))
            return
        delta: dict[str, Any] = {}
        tool_chunk = None
        if kind == "response.output_text.delta":
            delta["content"] = ev.get("delta", "")
        elif kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
            delta["reasoning_content"] = ev.get("delta", "")
        elif kind == "response.output_item.added":
            item = ev.get("item") or {}
            if item.get("type") == "function_call":
                idx = idx_for(ev.get("output_index"), item.get("id"))
                apply_item(idx, item)
                tool_chunk = flush_row(idx, "")
        elif kind == "response.function_call_arguments.delta":
            idx = idx_for(ev.get("output_index"), ev.get("item_id"))
            piece = str(ev.get("delta") or "")
            rows[idx]["arguments"] += piece
            tool_chunk = flush_row(idx, piece)
        elif kind == "response.output_item.done":
            item = ev.get("item") or {}
            if item.get("type") == "function_call":
                idx = idx_for(ev.get("output_index"), item.get("id"))
                apply_item(idx, item)
                row = rows[idx]
                tool_chunk = flush_row(idx, "" if row["announced"] else row["arguments"])
        else:
            continue
        rc = role_chunk()
        if rc:
            yield rc
        if tool_chunk is not None:
            yield tool_chunk
        elif delta:
            yield make_chunk(chunk_id, model, delta)
    raise RuntimeError("Responses 上游流在 response.completed 之前结束了")
