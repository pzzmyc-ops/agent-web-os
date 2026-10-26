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
        ws_chat.bind_queue_runtime(None, None)
        ws_chat._thread_active_tasks.pop("thread-q", None)

    def tearDown(self) -> None:
        reset("thread-q")
        ws_chat._thread_active_tasks.pop("thread-q", None)

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

    def test_pump_sends_whole_queue_as_one_turn(self) -> None:
        sent = []
        launched = []

        async def fake_broadcast(obj):
            sent.append(obj)

        def fake_launch_user(web_agent, store, thread_id, item, local_tasks):
            launched.append(item)

        async def run() -> None:
            enqueue("thread-q", _user("m1", "先发"))
            enqueue("thread-q", ReportItem(content="命令跑完了", group_chat=False))
            enqueue("thread-q", _user("m2", "后发"))
            with mock.patch.object(ws_chat, "broadcast", fake_broadcast), mock.patch.object(
                ws_chat, "_launch_user", fake_launch_user
            ), mock.patch.object(ws_chat, "thread_is_busy", return_value=False):
                await ws_chat._pump("thread-q")
                await asyncio.sleep(0)
            self.assertEqual(len(launched), 1)
            self.assertEqual(
                launched[0].content,
                "先发\n\n" + turn_queue.REPORT_PREFACE + "\n\n命令跑完了\n\n后发",
            )
            self.assertEqual(launched[0].message_id, "m1")
            self.assertEqual(len(launched[0].attachments), 2)
            self.assertEqual(turn_queue.depth("thread-q"), 0)
            states = [o for o in sent if o["type"] == "queue_state"]
            self.assertEqual(len(states), 1)
            self.assertEqual(states[0]["data"]["items"], [])

        asyncio.run(run())

    def test_pump_reports_only_become_one_report_turn(self) -> None:
        launched = []

        def fake_launch_report(web_agent, store, thread_id, content, group_chat):
            launched.append((content, group_chat))

        async def fake_broadcast(obj):
            return None

        async def run() -> None:
            enqueue("thread-q", ReportItem(content="第一条完成", group_chat=False))
            enqueue("thread-q", ReportItem(content="第二条完成", group_chat=False))
            with mock.patch.object(ws_chat, "broadcast", fake_broadcast), mock.patch.object(
                ws_chat, "_launch_report", fake_launch_report
            ), mock.patch.object(ws_chat, "thread_is_busy", return_value=False):
                await ws_chat._pump("thread-q")
                await asyncio.sleep(0)
            self.assertEqual(launched, [(turn_queue.REPORT_PREFACE + "\n\n第一条完成\n\n第二条完成", False)])
            self.assertEqual(turn_queue.depth("thread-q"), 0)

        asyncio.run(run())

    def test_pump_drops_reports_already_seen(self) -> None:
        launched = []

        def fake_launch_report(web_agent, store, thread_id, content, group_chat):
            launched.append(content)

        def fake_launch_user(web_agent, store, thread_id, item, local_tasks):
            launched.append(item.content)

        async def fake_broadcast(obj):
            return None

        async def run() -> None:
            enqueue("thread-q", ReportItem(content="已看过", group_chat=False, session_id="s1", command_seq=1, completion=True))
            enqueue("thread-q", ReportItem(content="没看过", group_chat=False, session_id="s2", command_seq=1, completion=True))
            with mock.patch.object(ws_chat, "broadcast", fake_broadcast), mock.patch.object(
                ws_chat, "_launch_report", fake_launch_report
            ), mock.patch.object(
                ws_chat, "_launch_user", fake_launch_user
            ), mock.patch.object(ws_chat, "thread_is_busy", return_value=False), mock.patch(
                "apps.nextagent.message_runner.report_is_stale",
                side_effect=lambda it: it.session_id == "s1",
            ):
                await ws_chat._pump("thread-q")
                await asyncio.sleep(0)
            self.assertEqual(launched, [turn_queue.REPORT_PREFACE + "\n\n没看过"])

        asyncio.run(run())

    def test_pump_with_only_seen_reports_launches_nothing(self) -> None:
        launched = []

        async def fake_broadcast(obj):
            return None

        async def run() -> None:
            enqueue("thread-q", ReportItem(content="已看过", group_chat=False, session_id="s1", command_seq=1, completion=True))
            with mock.patch.object(ws_chat, "broadcast", fake_broadcast), mock.patch.object(
                ws_chat, "_launch_report", lambda *a: launched.append(a)
            ), mock.patch.object(ws_chat, "thread_is_busy", return_value=False), mock.patch(
                "apps.nextagent.message_runner.report_is_stale",
                return_value=True,
            ):
                await ws_chat._pump("thread-q")
                await asyncio.sleep(0)
            self.assertEqual(launched, [])
            self.assertEqual(turn_queue.depth("thread-q"), 0)

        asyncio.run(run())

    def test_single_user_item_is_passed_through_unchanged(self) -> None:
        item = _user("m1", "单条")
        self.assertIs(turn_queue.merge([item]), item)
        report = ReportItem(content="单条通知", group_chat=True)
        merged = turn_queue.merge([report])
        self.assertEqual(merged.content, turn_queue.REPORT_PREFACE + "\n\n单条通知")
        self.assertTrue(merged.group_chat)

    def test_unmark_thread_task_pumps_the_queue(self) -> None:
        launched = []

        def fake_launch_user(web_agent, store, thread_id, item, local_tasks):
            launched.append(item.content)

        async def fake_broadcast(obj):
            return None

        async def run() -> None:
            ws_chat.mark_thread_task("thread-q", "task-1")
            enqueue("thread-q", _user("m1", "停止后该发出"))
            with mock.patch.object(ws_chat, "broadcast", fake_broadcast), mock.patch.object(
                ws_chat, "_launch_user", fake_launch_user
            ):
                ws_chat.unmark_thread_task("thread-q", "task-1")
                for _ in range(4):
                    await asyncio.sleep(0)
            self.assertEqual(launched, ["停止后该发出"])
            self.assertEqual(turn_queue.depth("thread-q"), 0)

        asyncio.run(run())

    def test_unmark_with_other_task_does_not_pump(self) -> None:
        launched = []

        def fake_launch_user(web_agent, store, thread_id, item, local_tasks):
            launched.append(item.content)

        async def run() -> None:
            ws_chat.mark_thread_task("thread-q", "task-1")
            enqueue("thread-q", _user("m1", "还在忙"))
            with mock.patch.object(ws_chat, "_launch_user", fake_launch_user):
                ws_chat.unmark_thread_task("thread-q", "task-other")
                for _ in range(4):
                    await asyncio.sleep(0)
            self.assertEqual(launched, [])
            self.assertEqual(turn_queue.depth("thread-q"), 1)

        asyncio.run(run())

    def test_every_turn_exit_goes_through_unmark(self) -> None:
        src = (Path(__file__).resolve().parent / "ws_chat.py").read_text(encoding="utf-8")
        self.assertNotIn("_thread_active_tasks.pop(th, None)", src)
        self.assertEqual(src.count("schedule_pump("), 2)

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
