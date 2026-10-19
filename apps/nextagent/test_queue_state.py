import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from apps.nextagent import turn_queue, ws_chat
from apps.nextagent.turn_queue import ReportItem, UserItem, enqueue, reset


def _user(message_id: str, content: str) -> UserItem:
    return UserItem(
        message_id=message_id,
        content=content,
        attachments=[{"filename": "a.png", "media_id": "m1"}],
        model="kimi-k3",
        media_passthrough=False,
        thinking=None,
        effort=None,
        plan_mode=False,
        active_roles=[],
        skip_handoff=True,
    )


class QueueStateTest(unittest.TestCase):
    def setUp(self) -> None:
        reset("thread-q")

    def tearDown(self) -> None:
        reset("thread-q")

    def test_frame_lists_items_in_order_with_kind_and_attachments(self) -> None:
        enqueue("thread-q", _user("m1", "第一条"))
        enqueue("thread-q", ReportItem(content="命令跑完了", group_chat=False))
        frame = ws_chat._queue_state_frame("thread-q")
        self.assertEqual(frame["type"], "queue_state")
        self.assertEqual(frame["data"]["threadId"], "thread-q")
        items = frame["data"]["items"]
        self.assertEqual([it["kind"] for it in items], ["user", "report"])
        self.assertEqual(items[0]["content"], "第一条")
        self.assertEqual(items[0]["attachments"], ["a.png"])
        self.assertEqual(items[0]["model"], "kimi-k3")
        self.assertTrue(items[0]["queuedAt"] > 0)

    def test_pump_launches_head_and_publishes_rest(self) -> None:
        sent = []
        launched = []

        async def fake_broadcast(obj):
            sent.append(obj)

        def fake_launch_user(web_agent, store, thread_id, item, local_tasks):
            launched.append(item.content)

        async def run() -> None:
            enqueue("thread-q", _user("m1", "先发"))
            enqueue("thread-q", _user("m2", "后发"))
            with mock.patch.object(ws_chat, "broadcast", fake_broadcast), mock.patch.object(
                ws_chat, "_launch_user", fake_launch_user
            ), mock.patch.object(ws_chat, "thread_is_busy", return_value=False):
                await ws_chat._pump(None, None, "thread-q")
                await asyncio.sleep(0)
            self.assertEqual(launched, ["先发"])
            self.assertEqual(turn_queue.depth("thread-q"), 1)
            states = [o for o in sent if o["type"] == "queue_state"]
            self.assertEqual(len(states), 1)
            self.assertEqual([it["content"] for it in states[0]["data"]["items"]], ["后发"])

        asyncio.run(run())

    def test_remove_drops_only_that_item(self) -> None:
        enqueue("thread-q", _user("m1", "留下"))
        enqueue("thread-q", _user("m2", "去掉"))
        target = turn_queue.snapshot("thread-q")[1]["id"]
        self.assertTrue(turn_queue.remove("thread-q", target))
        self.assertEqual([it["content"] for it in turn_queue.snapshot("thread-q")], ["留下"])
        self.assertEqual(turn_queue.threads_with_items(), ["thread-q"])
        turn_queue.remove("thread-q", turn_queue.snapshot("thread-q")[0]["id"])
        self.assertEqual(turn_queue.threads_with_items(), [])

    def test_stop_path_no_longer_arms_queue(self) -> None:
        src = (Path(__file__).resolve().parent / "ws_chat.py").read_text(encoding="utf-8")
        self.assertNotIn("note_stop", src)
        self.assertNotIn("pre_stop", src)
        self.assertIn('mtype == "queue_remove"', src)


if __name__ == "__main__":
    unittest.main()
