from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from apps.nextagent import session_log
from apps.nextagent.store import Store
from apps.nextagent.turn_event_bus import bus as event_bus
from apps.nextagent.turn_lifecycle import TurnRun
from apps.nextagent.turn_protocol import TurnEmitter


class _Blocks:
    async def split_segment(self) -> None:
        return None

    async def persist_segment(self) -> None:
        return None


class _Usage:
    def __init__(self, count: int) -> None:
        self.type = "usage"
        self.usage_details = {"input_token_count": count}


class _Update:
    def __init__(self, count: int) -> None:
        self.contents = [_Usage(count)]
        self.additional_properties = None


class ContextUsageTests(unittest.TestCase):
    def test_usage_is_saved_and_pushed_as_soon_as_it_grows(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                store = Store(tmp)
                thread_id = store.create_thread("用量")
                store.append_event(thread_id, session_log.EV_USER, task_id="t", content="你好")
                sent: list[dict] = []

                async def send(frame: dict) -> None:
                    sent.append(frame)

                task_id = "task_usage_test"
                await event_bus.open(task_id, thread_id=thread_id, message_id="m1")
                try:
                    emitter = TurnEmitter(send, task_id=task_id, thread_id=thread_id)
                    web_agent = type("Agent", (), {"store": store, "default_window": 1_000_000})()
                    run = TurnRun(
                        web_agent,
                        emitter,
                        content="你好",
                        message_id="m1",
                        policy=None,
                        blocks=_Blocks(),
                    )
                    await run.consume(_Update(0))
                    self.assertEqual(store.load_ctx_state(thread_id)["used"], 0)
                    self.assertEqual(sent, [])
                    await run.consume(_Update(1200))
                    self.assertEqual(store.load_ctx_state(thread_id)["used"], 1200)
                    self.assertEqual(len(store.load_events(thread_id)), 1)
                    self.assertEqual([frame["type"] for frame in sent], ["context_usage"])
                    self.assertEqual(sent[0]["data"]["usage"]["used"], 1200)
                    self.assertEqual(sent[0]["data"]["threadId"], thread_id)
                    await run.consume(_Update(1200))
                    self.assertEqual(len(sent), 1)
                    await run.consume(_Update(3400))
                    self.assertEqual(store.load_ctx_state(thread_id)["used"], 3400)
                    self.assertEqual(len(store.load_events(thread_id)), 1)
                    self.assertEqual(sent[-1]["data"]["usage"]["used"], 3400)
                    self.assertEqual(len(sent), 2)
                finally:
                    await event_bus.discard(task_id)

        asyncio.run(run())

    def test_context_panel_updates_in_place(self) -> None:
        source = Path(__file__).resolve().parent.joinpath("static", "js", "context-window.js").read_text(encoding="utf-8")
        self.assertIn('messageBus.on("context_usage"', source)
        self.assertIn("paintUsage(body, data.usage)", source)
        handler = source[source.index('messageBus.on("context_usage"'):]
        self.assertNotIn("fillWindow()", handler)
        self.assertNotIn("disarmForce()", handler)


if __name__ == "__main__":
    unittest.main()
