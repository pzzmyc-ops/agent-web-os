from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from agent_framework import ChatResponse, Content
from openai._models import construct_type
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk

from apps.nextagent.tool_tracker import ToolTracker
from apps.nextagent.upstreams.anthropic_codec import decode as decode_anthropic
from apps.nextagent.upstreams.chat_codec import decode as decode_chat
from apps.nextagent.upstreams.responses_codec import decode

ARGS = {"file_name": "/root/a.png", "offset": 1, "limit": 120}
ARGS_TEXT = json.dumps(ARGS, ensure_ascii=False)


def _item(arguments):
    return {
        "id": "fc_1",
        "type": "function_call",
        "name": "file_access_read",
        "call_id": "call_1",
        "arguments": arguments,
    }


async def _events(events: list[dict]):
    async def lines():
        for ev in events:
            yield "data: " + json.dumps(ev, ensure_ascii=False)

    out = []
    async for chunk in decode(lines(), "gpt-6"):
        out.append(chunk)
    return out


def _tool_arguments(chunks: list[dict]) -> str:
    text = ""
    for chunk in chunks:
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            for call in delta.get("tool_calls") or []:
                fn = call.get("function") or {}
                if fn.get("arguments"):
                    text += fn["arguments"]
    return text


def _parsed(chunks: list[dict]) -> dict:
    text = _tool_arguments(chunks)
    return Content.from_function_call(
        call_id="call_1",
        name="file_access_read",
        arguments=text,
    ).parse_arguments()


class ResponsesToolArgsTests(unittest.IsolatedAsyncioTestCase):
    async def test_done_item_arguments_are_one_object(self) -> None:
        chunks = await _events([
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": _item(""),
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": _item(ARGS_TEXT),
            },
            {
                "type": "response.completed",
                "response": {"id": "resp_1", "output": [_item(ARGS_TEXT)]},
            },
        ])
        self.assertEqual(_parsed(chunks), ARGS)

    async def test_deltas_are_not_appended_to_the_done_arguments(self) -> None:
        chunks = await _events([
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": _item(""),
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "item_id": "fc_1",
                "delta": ARGS_TEXT,
            },
            {
                "type": "response.function_call_arguments.done",
                "output_index": 0,
                "item_id": "fc_1",
                "arguments": ARGS_TEXT,
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": _item(ARGS_TEXT),
            },
            {
                "type": "response.completed",
                "response": {"id": "resp_1", "output": [_item(ARGS_TEXT)]},
            },
        ])
        self.assertEqual(_tool_arguments(chunks), ARGS_TEXT)
        self.assertEqual(_parsed(chunks), ARGS)

    async def test_object_arguments_stay_json(self) -> None:
        chunks = await _events([
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": _item(ARGS),
            },
            {
                "type": "response.completed",
                "response": {"id": "resp_1", "output": [_item(ARGS)]},
            },
        ])
        self.assertEqual(_parsed(chunks), ARGS)

    async def test_double_encoded_arguments_unwrap_to_object(self) -> None:
        chunks = await _events([
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": _item(json.dumps(ARGS_TEXT, ensure_ascii=False)),
            },
            {
                "type": "response.completed",
                "response": {"id": "resp_1", "output": [_item(json.dumps(ARGS_TEXT, ensure_ascii=False))]},
            },
        ])
        parsed = _parsed(chunks)
        self.assertNotIn("raw", parsed)
        self.assertEqual(parsed, ARGS)


class _Log:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append_event(self, thread_id: str, kind: str, **payload) -> None:
        self.events.append({"thread_id": thread_id, "type": kind, **payload})


class _Emitter:
    async def turn(self, *args, **kwargs) -> None:
        return None


def _contents_of(chunk: dict) -> list:
    parsed = construct_type(type_=ChatCompletionChunk, value=chunk)
    contents = []
    for choice in parsed.choices:
        delta = choice.delta
        if delta is None or not delta.tool_calls:
            continue
        for tool in delta.tool_calls:
            fn = tool.function
            if fn is None:
                continue
            contents.append(Content.from_function_call(
                call_id=tool.id or "",
                name=fn.name or "",
                arguments=fn.arguments or "",
            ))
    return contents


async def _feed(chunks: list[dict]) -> tuple[dict, dict]:
    from agent_framework import ChatResponseUpdate

    updates = []
    for chunk in chunks:
        contents = _contents_of(chunk)
        if not contents:
            continue
        updates.append(ChatResponseUpdate(contents=contents, role="assistant"))
    response = ChatResponse.from_updates(updates)
    calls = [c for m in response.messages for c in m.contents if getattr(c, "type", None) == "function_call"]
    executed = calls[-1].parse_arguments() if calls else {}
    log = _Log()
    tracker = ToolTracker(_Emitter(), task_id="task", thread_id="thread", store=log)
    for update in updates:
        for content in update.contents:
            await tracker.on_content(content)
    logged = {}
    for event in log.events:
        if event.get("type") == "tool_call":
            logged = event.get("arguments") or {}
    return logged, executed


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_responses_done_and_completed_do_not_diverge(self) -> None:
        done = _item(ARGS_TEXT)
        completed = {
            "type": "function_call",
            "name": "file_access_read",
            "call_id": "call_1",
            "arguments": ARGS_TEXT,
        }
        chunks = await _events([
            {"type": "response.output_item.added", "output_index": 0, "item": _item("")},
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "item_id": "fc_1",
                "delta": ARGS_TEXT[:12],
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "item_id": "fc_1",
                "delta": ARGS_TEXT[12:],
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": done,
            },
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_1",
                    "output": [{"type": "reasoning", "id": "rs_1"}, completed],
                },
            },
        ])
        logged, executed = await _feed(chunks)
        self.assertEqual(logged, ARGS)
        self.assertEqual(executed, ARGS)

    async def test_responses_fragment_deltas_still_reach_the_tool(self) -> None:
        chunks = await _events([
            {"type": "response.output_item.added", "output_index": 0, "item": _item("")},
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "item_id": "fc_1",
                "delta": ARGS_TEXT[:12],
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "item_id": "fc_1",
                "delta": ARGS_TEXT[12:],
            },
            {"type": "response.output_item.done", "output_index": 0, "item": _item("")},
            {
                "type": "response.completed",
                "response": {"id": "resp_1", "output": [_item("")]},
            },
        ])
        logged, executed = await _feed(chunks)
        self.assertEqual(logged, ARGS)
        self.assertEqual(executed, ARGS)

    async def test_chat_fragments_reach_the_tool(self) -> None:
        body = json.dumps(ARGS, ensure_ascii=False)
        head, tail = body[:12], body[12:]

        async def lines():
            yield "data: " + json.dumps({
                "id": "c1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "deepseek-chat",
                "choices": [{
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [{
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "file_access_read", "arguments": head},
                        }],
                    },
                    "finish_reason": None,
                }],
            })
            yield "data: " + json.dumps({
                "id": "c1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "deepseek-chat",
                "choices": [{
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": tail}}]},
                    "finish_reason": "tool_calls",
                }],
            })

        chunks = [chunk async for chunk in decode_chat(lines(), "deepseek-chat")]
        logged, executed = await _feed(chunks)
        self.assertEqual(logged, ARGS)
        self.assertEqual(executed, ARGS)

    async def test_anthropic_fragments_reach_the_tool(self) -> None:
        body = json.dumps(ARGS, ensure_ascii=False)
        head, tail = body[:12], body[12:]

        async def lines():
            for ev in (
                {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "tool_use", "id": "toolu_1", "name": "file_access_read", "input": {}},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": head},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": tail},
                },
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 2}},
                {"type": "message_stop"},
            ):
                yield "data: " + json.dumps(ev)

        chunks = [chunk async for chunk in decode_anthropic(lines(), "claude")]
        logged, executed = await _feed(chunks)
        self.assertEqual(logged, ARGS)
        self.assertEqual(executed, ARGS)
