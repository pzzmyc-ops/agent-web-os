import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from agent_framework import Message

from appconfig import normalize_upstreams
from apps.nextagent.upstreams.catalog import model_id
from apps.nextagent.upstreams.client import build_upstream_client

RAW = [
    {"name": "公司网关", "base_url": "http://gw.local/v1", "api_key": "k1", "models": ["gpt-6-sol"]},
    {"name": "白湾", "base_url": "https://whitebay.click/v1", "api_key": "k2", "models": ["gpt-6-sol"]},
]


def _config():
    return SimpleNamespace(upstreams=normalize_upstreams(RAW))


class UpstreamClientModelTests(unittest.TestCase):
    def test_request_goes_to_the_chosen_upstream_with_the_raw_model_name(self):
        seen = []

        async def fake_stream(up, body, default_max_tokens):
            seen.append((up.name, body["model"]))
            yield {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "model": body["model"],
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            }

        client = build_upstream_client(1024)
        with patch("apps.nextagent.upstreams.catalog.load_config", _config), \
             patch("apps.nextagent.upstreams.client.stream_chat", fake_stream):
            response = asyncio.run(client.get_response(
                [Message("user", ["hi"])],
                options={"model": model_id("白湾", "gpt-6-sol")},
            ))
        self.assertEqual(seen, [("白湾", "gpt-6-sol")])
        self.assertEqual(response.text, "ok")


if __name__ == "__main__":
    unittest.main()
