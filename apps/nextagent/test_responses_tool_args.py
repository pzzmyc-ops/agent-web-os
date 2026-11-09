from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from agent_framework import Content

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
