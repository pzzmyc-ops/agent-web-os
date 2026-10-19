from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from openai._models import construct_type
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk

from apps.nextagent.llm import MafChatClient


def _client() -> MafChatClient:
    return MafChatClient.__new__(MafChatClient)


class UsageChunkTests(unittest.TestCase):
    def test_stream_usage_without_created_keeps_usage(self) -> None:
        chunk = construct_type(
            type_=ChatCompletionChunk,
            value={
                "choices": [],
                "object": "chat.completion.chunk",
                "usage": {
                    "completion_tokens": 111,
                    "cost": 3.2445e-05,
                    "prompt_tokens": 166,
                    "total_tokens": 336,
                },
            },
        )
        update = _client()._parse_response_update_from_openai(chunk)
        self.assertIsNone(update.created_at)
        usage = [c for c in update.contents if getattr(c, "type", None) == "usage"]
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0].usage_details["input_token_count"], 166)
        self.assertEqual(usage[0].usage_details["output_token_count"], 111)

    def test_stream_chunk_with_created_keeps_timestamp(self) -> None:
        chunk = construct_type(
            type_=ChatCompletionChunk,
            value={
                "id": "c1",
                "created": 1792036672,
                "model": "kimi-k3",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": "好"}, "finish_reason": None}],
            },
        )
        update = _client()._parse_response_update_from_openai(chunk)
        self.assertEqual(
            update.created_at,
            datetime.fromtimestamp(1792036672, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )

    def test_response_without_created_keeps_usage(self) -> None:
        response = construct_type(
            type_=ChatCompletion,
            value={
                "id": "c1",
                "object": "chat.completion",
                "model": "kimi-k3",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "好"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            },
        )
        parsed = _client()._parse_response_from_openai(response, {})
        self.assertIsNone(parsed.created_at)
        self.assertEqual(parsed.usage_details["input_token_count"], 10)
        self.assertEqual(parsed.messages[0].text, "好")
