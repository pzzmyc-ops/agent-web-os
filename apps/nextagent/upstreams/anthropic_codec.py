from __future__ import annotations

import json
from typing import Any, AsyncIterator

from .chunks import iter_sse_data, make_chunk, new_chunk_id, tool_call_delta, usage_dict
from .reasoning import pop_reasoning

VERSION = "2023-06-01"
HIDDEN_THINKING = "该模型上游不透露思考过程"
_BUDGET = {"low": 2048, "medium": 8192, "high": 32768}
_STOP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
    "pause_turn": "stop",
}


def _split_data_url(url: str) -> tuple[str, str]:
    if not url.startswith("data:"):
        raise ValueError("不是 data URL")
    head, _, data = url.partition(",")
    media = head[5:].split(";", 1)[0]
    return media, data


def _image_block(url: str) -> dict:
    if url.startswith("data:"):
        media, data = _split_data_url(url)
        return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
    return {"type": "image", "source": {"type": "url", "url": url}}


def _user_blocks(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        raise ValueError("消息 content 既不是字符串也不是数组")
    out: list[dict] = []
    for item in content:
        if not isinstance(item, dict):
            raise ValueError("content 数组里出现了非对象元素")
        t = item.get("type")
        if t == "text":
            if item.get("text"):
                out.append({"type": "text", "text": item["text"]})
        elif t == "image_url":
            url = item.get("image_url")
            if isinstance(url, dict):
                url = url.get("url", "")
            out.append(_image_block(str(url or "")))
        elif t == "file":
            f = item.get("file") or {}
            media, data = _split_data_url(str(f.get("file_data") or ""))
            if media != "application/pdf":
                raise ValueError("Anthropic 接口的文件内容只支持 PDF,收到: " + media)
            out.append({"type": "document", "source": {"type": "base64", "media_type": media, "data": data}})
        else:
            raise ValueError("Anthropic 接口不支持这种内容类型: " + str(t))
    return out


def _assistant_blocks(msg: dict) -> list[dict]:
    out: list[dict] = []
    for detail in msg.get("reasoning_details") or []:
        if not isinstance(detail, dict):
            continue
        if detail.get("type") == "thinking" and detail.get("signature"):
            out.append({"type": "thinking", "thinking": str(detail.get("thinking") or ""), "signature": detail["signature"]})
        elif detail.get("type") == "redacted_thinking" and detail.get("data"):
            out.append({"type": "redacted_thinking", "data": detail["data"]})
    content = msg.get("content")
    if isinstance(content, str):
        if content:
            out.append({"type": "text", "text": content})
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                out.append({"type": "text", "text": item["text"]})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments", "")
        if isinstance(args, str):
            parsed = json.loads(args) if args.strip() else {}
        else:
            parsed = args
        if not isinstance(parsed, dict):
            raise ValueError("工具调用参数不是 JSON 对象: " + str(args)[:200])
        out.append({"type": "tool_use", "id": str(tc.get("id") or ""), "name": str(fn.get("name") or ""), "input": parsed})
    return out


def _tool_result_block(msg: dict) -> dict:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": str(msg.get("tool_call_id") or "")}
    text = msg.get("content")
    if isinstance(text, list):
        text = "\n".join(str(p.get("text") or "") for p in text if isinstance(p, dict))
    text = str(text or "")
    if text:
        block["content"] = text
    return block


def _append(messages: list[dict], role: str, blocks: list[dict]) -> None:
    if not blocks:
        return
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"].extend(blocks)
        return
    messages.append({"role": role, "content": list(blocks)})


def _convert_messages(raw: list[dict]) -> tuple[list[dict], list[dict]]:
    system: list[dict] = []
    messages: list[dict] = []
    for msg in raw:
        role = str(msg.get("role") or "user")
        if role in ("system", "developer"):
            blocks = _user_blocks(msg.get("content"))
            if not messages:
                system.extend(blocks)
            else:
                _append(messages, "user", blocks)
        elif role == "user":
            _append(messages, "user", _user_blocks(msg.get("content")))
        elif role == "assistant":
            _append(messages, "assistant", _assistant_blocks(msg))
        elif role == "tool":
            _append(messages, "user", [_tool_result_block(msg)])
        else:
            raise ValueError("未知消息角色: " + role)
    if not messages or messages[0]["role"] != "user":
        raise ValueError("Anthropic 接口要求第一条消息是 user")
    return system, messages


def _tools(tools: list[dict]) -> list[dict]:
    out: list[dict] = []
    for t in tools:
        if t.get("type") != "function":
            raise ValueError("Anthropic 接口只支持 function 类型工具,收到: " + str(t.get("type")))
        fn = t.get("function") or {}
        item: dict[str, Any] = {
            "name": fn["name"],
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        }
        if fn.get("description"):
            item["description"] = fn["description"]
        out.append(item)
    return out


def _tool_choice(tc: Any, parallel: Any) -> dict | None:
    out: dict[str, Any] | None = None
    if tc is None or tc == "auto":
        out = {"type": "auto"}
    elif tc == "required":
        out = {"type": "any"}
    elif tc == "none":
        out = {"type": "none"}
    elif isinstance(tc, dict) and tc.get("type") == "function":
        fn = tc.get("function") or {}
        out = {"type": "tool", "name": fn.get("name") or tc.get("name")}
    else:
        raise ValueError("无法翻译的 tool_choice: " + str(tc))
    if parallel is False and out["type"] in ("auto", "any"):
        out["disable_parallel_tool_use"] = True
    return out


def _uses_adaptive(model: str) -> bool:
    name = str(model or "").lower().replace("_", "-")
    if "fable" in name or "mythos" in name:
        return True
    marked = "-" + name
    for family in ("opus", "sonnet", "haiku"):
        if f"-{family}-5" in marked and f"-{family}-4-5" not in marked and f"-{family}-4.5" not in marked:
            return True
    return False


def encode(body: dict[str, Any], default_max_tokens: int) -> dict[str, Any]:
    body = dict(body)
    enabled, effort = pop_reasoning(body)
    system, messages = _convert_messages(body.get("messages") or [])
    max_tokens = int(body.get("max_completion_tokens") or body.get("max_tokens") or default_max_tokens)
    payload: dict[str, Any] = {
        "model": body["model"],
        "max_tokens": max_tokens,
        "messages": messages,
        "stream": True,
    }
    if system:
        payload["system"] = system
    if body.get("tools"):
        payload["tools"] = _tools(body["tools"])
        payload["tool_choice"] = _tool_choice(body.get("tool_choice"), body.get("parallel_tool_calls"))
    if enabled is True:
        if _uses_adaptive(str(body.get("model") or "")):
            payload["thinking"] = {"type": "adaptive", "display": "summarized"}
            payload["output_config"] = {"effort": effort}
        else:
            budget = _BUDGET[effort]
            if payload["max_tokens"] <= budget:
                payload["max_tokens"] = budget + 4096
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
    else:
        if "temperature" in body:
            payload["temperature"] = body["temperature"]
        if "top_p" in body:
            payload["top_p"] = body["top_p"]
    stop = body.get("stop")
    if stop:
        payload["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    return payload


def headers(api_key: str) -> dict[str, str]:
    return {"x-api-key": api_key, "anthropic-version": VERSION}


async def decode(lines: AsyncIterator[str], model: str) -> AsyncIterator[dict]:
    chunk_id = new_chunk_id()
    prompt_tokens = 0
    cached_tokens = 0
    completion_tokens = 0
    finish = "stop"
    blocks: dict[int, dict[str, Any]] = {}
    tool_count = 0
    async for ev in iter_sse_data(lines):
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get("type") or "")
        if kind == "error":
            raise RuntimeError("Anthropic 上游报错: " + json.dumps(ev, ensure_ascii=False)[:2000])
        if kind == "message_start":
            msg = ev.get("message") or {}
            u = msg.get("usage") or {}
            prompt_tokens = int(u.get("input_tokens") or 0)
            cached_tokens = int(u.get("cache_read_input_tokens") or 0)
            prompt_tokens += cached_tokens + int(u.get("cache_creation_input_tokens") or 0)
            yield make_chunk(chunk_id, model, {"role": "assistant"})
        elif kind == "content_block_start":
            index = int(ev.get("index") or 0)
            block = ev.get("content_block") or {}
            btype = str(block.get("type") or "")
            info: dict[str, Any] = {"type": btype, "thinking": "", "signature": ""}
            blocks[index] = info
            if btype == "tool_use":
                info["tool_index"] = tool_count
                tool_count += 1
                yield make_chunk(
                    chunk_id, model,
                    tool_call_delta(info["tool_index"], str(block.get("id") or ""), str(block.get("name") or ""), "", announce=True),
                )
            elif btype == "text" and block.get("text"):
                yield make_chunk(chunk_id, model, {"content": block["text"]})
            elif btype == "redacted_thinking":
                yield make_chunk(chunk_id, model, {"reasoning_content": HIDDEN_THINKING})
                yield make_chunk(chunk_id, model, {"reasoning_details": [{"type": "redacted_thinking", "data": block.get("data") or ""}]})
        elif kind == "content_block_delta":
            index = int(ev.get("index") or 0)
            info = blocks[index]
            d = ev.get("delta") or {}
            dtype = str(d.get("type") or "")
            if dtype == "text_delta":
                if d.get("text"):
                    yield make_chunk(chunk_id, model, {"content": d["text"]})
            elif dtype == "input_json_delta":
                piece = str(d.get("partial_json") or "")
                if piece:
                    yield make_chunk(chunk_id, model, tool_call_delta(info["tool_index"], "", "", piece, announce=False))
            elif dtype == "thinking_delta":
                piece = str(d.get("thinking") or "")
                info["thinking"] += piece
                if piece:
                    yield make_chunk(chunk_id, model, {"reasoning_content": piece})
            elif dtype == "signature_delta":
                info["signature"] += str(d.get("signature") or "")
        elif kind == "content_block_stop":
            index = int(ev.get("index") or 0)
            info = blocks.get(index) or {}
            if info.get("type") == "thinking":
                if not str(info.get("thinking") or "").strip():
                    yield make_chunk(chunk_id, model, {"reasoning_content": HIDDEN_THINKING})
                if info.get("signature"):
                    yield make_chunk(chunk_id, model, {"reasoning_details": [
                        {"type": "thinking", "thinking": info["thinking"], "signature": info["signature"]},
                    ]})
        elif kind == "message_delta":
            d = ev.get("delta") or {}
            if d.get("stop_reason"):
                finish = _STOP.get(str(d["stop_reason"]), "stop")
            u = ev.get("usage") or {}
            if u.get("output_tokens") is not None:
                completion_tokens = int(u["output_tokens"])
        elif kind == "message_stop":
            yield make_chunk(chunk_id, model, {}, finish, usage_dict(prompt_tokens, completion_tokens, cached_tokens))
            return
    raise RuntimeError("Anthropic 上游流在 message_stop 之前结束了")
