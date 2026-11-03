"""回合名单(single source of truth)的推送与形状。

「某 thread 此刻有没有回合在跑」只有 _thread_active_tasks 一份账。前端不再自己
推断回合结束,全靠后端推的 tasks_snapshot 收敛 —— 所以这份名单必须在每次变更时
立刻推出去,且帧里的字段就是前端要读的那几个。
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from apps.nextagent import ws_chat


def _collect_frames():
    frames = []

    async def fake_broadcast(obj):
        frames.append(obj)

    return frames, fake_broadcast


async def _settle(times: int = 6) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


class MarkUnmarkPublishTests(unittest.TestCase):
    def setUp(self) -> None:
        ws_chat._thread_active_tasks.clear()

    def tearDown(self) -> None:
        ws_chat._thread_active_tasks.clear()

    def test_mark_publishes_snapshot_with_the_task(self) -> None:
        frames, fake = _collect_frames()

        async def run() -> None:
            with mock.patch.object(ws_chat, "broadcast", fake):
                ws_chat.mark_thread_task("thread-a", "task-1")
                await _settle()

        asyncio.run(run())
        snaps = [f for f in frames if f["type"] == "tasks_snapshot"]
        self.assertEqual(len(snaps), 1)
        tasks = snaps[0]["data"]["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["taskId"], "task-1")
        self.assertEqual(tasks[0]["threadId"], "thread-a")
        self.assertEqual(tasks[0]["status"], "running")

    def test_unmark_publishes_snapshot_without_the_task(self) -> None:
        frames, fake = _collect_frames()

        async def run() -> None:
            with mock.patch.object(ws_chat, "broadcast", fake):
                ws_chat.mark_thread_task("thread-a", "task-1")
                await _settle()
                frames.clear()
                ws_chat.unmark_thread_task("thread-a", "task-1")
                await _settle()

        asyncio.run(run())
        snaps = [f for f in frames if f["type"] == "tasks_snapshot"]
        self.assertEqual(len(snaps), 1, "回合结束必须推一次空名单,前端靠它收尾")
        self.assertEqual(snaps[0]["data"]["tasks"], [])
        self.assertNotIn("thread-a", ws_chat._thread_active_tasks)

    def test_unmark_from_a_stale_task_is_a_noop(self) -> None:
        """旧回合迟到的收尾不能把新回合的名单抹掉。"""
        frames, fake = _collect_frames()

        async def run() -> None:
            with mock.patch.object(ws_chat, "broadcast", fake):
                ws_chat.mark_thread_task("thread-a", "task-new")
                await _settle()
                frames.clear()
                ws_chat.unmark_thread_task("thread-a", "task-old")
                await _settle()

        asyncio.run(run())
        self.assertEqual(ws_chat._thread_active_tasks.get("thread-a"), "task-new")
        self.assertEqual([f for f in frames if f["type"] == "tasks_snapshot"], [])

    def test_payload_mirrors_the_authority_exactly(self) -> None:
        ws_chat._thread_active_tasks["thread-a"] = "task-1"
        ws_chat._thread_active_tasks["thread-b"] = "task-2"
        payload = asyncio.run(ws_chat._active_tasks_payload())
        by_thread = {row["threadId"]: row for row in payload}
        self.assertEqual(set(by_thread), {"thread-a", "thread-b"})
        self.assertEqual(by_thread["thread-a"]["taskId"], "task-1")
        self.assertEqual(by_thread["thread-b"]["taskId"], "task-2")

    def test_payload_carries_the_fields_the_client_reads(self) -> None:
        """前端的 reconcile 只读这几个键,少一个就判不出「在跑」。"""
        ws_chat._thread_active_tasks["thread-a"] = "task-1"
        payload = asyncio.run(ws_chat._active_tasks_payload())
        row = payload[0]
        for key in ("taskId", "threadId", "status", "taskKind", "snapshotSeq"):
            self.assertIn(key, row)


class SyncStateContractTests(unittest.TestCase):
    def test_backend_serves_the_sync_state_probe(self) -> None:
        """前端靠 sync_state 自愈,这个入口不能丢。"""
        src = (Path(__file__).resolve().parent / "ws_chat.py").read_text(encoding="utf-8")
        self.assertIn('mtype == "sync_state"', src)
        self.assertIn('_control("tasks_snapshot"', src)

    def test_turn_end_is_not_the_only_way_out(self) -> None:
        """名单推送必须挂在 mark/unmark 上,不能只靠 turn_complete 那一帧。"""
        src = (Path(__file__).resolve().parent / "ws_chat.py").read_text(encoding="utf-8")
        mark_body = src.split("def mark_thread_task(", 1)[1].split("def unmark_thread_task(", 1)[0]
        unmark_body = src.split("def unmark_thread_task(", 1)[1].split("def thread_is_busy(", 1)[0]
        self.assertIn("publish_tasks()", mark_body)
        self.assertIn("publish_tasks()", unmark_body)


if __name__ == "__main__":
    unittest.main()
