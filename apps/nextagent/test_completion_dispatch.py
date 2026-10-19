import asyncio
import queue
import sys
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from apps.nextagent.maf_tools.process_registry import process_registry
from apps.nextagent.maf_tools.session_key import (
    reset_current_session_key,
    set_current_session_key,
)
from apps.nextagent.message_runner import dispatch_process_completions
from apps.nextagent.turn_queue import UserItem, depth, remove, reset, snapshot
from apps.nextagent.toolkit._context import TurnContext, bind_turn
from apps.nextagent.toolkit.terminal import terminal
from fm.backend.terminal import HostedSession


def _completion(session_id: str, session_key: str, command: str, output: str) -> dict:
    return {
        "type": "completion",
        "session_id": session_id,
        "session_key": session_key,
        "command": command,
        "exit_code": 0,
        "completion_reason": "exited",
        "termination_source": "",
        "output": output,
        "started_at": 1,
    }


def _drain_queue() -> list:
    found = []
    while True:
        try:
            found.append(process_registry.completion_queue.get_nowait())
        except queue.Empty:
            return found


@contextmanager
def isolated_queue():
    saved = _drain_queue()
    try:
        yield
    finally:
        _drain_queue()
        for evt in saved:
            process_registry.completion_queue.put(evt)


def _bare_session() -> HostedSession:
    session = HostedSession.__new__(HostedSession)
    session.id = "sid"
    session.lock = threading.Lock()
    session.cond = threading.Condition(session.lock)
    session.state = "idle"
    session.text = "echo hi\nhi\n"
    session.command = ""
    session.command_start = 0
    session.exit_code = None
    session.notify_on_complete = False
    session.watch_patterns = []
    session.started_at = 1
    session.session_key = ""
    session.owner = ""
    session.desktop_id = ""
    session._notified = False
    session.result_ready = False
    session.result_taken = False
    session._latched_exit = None
    session._settle_gen = 0
    session._watch_next = 0.0
    session._command_session_key = ""
    session.shell = mock.Mock()
    return session


class Mode:
    def is_bound_thread(self, thread_id: str) -> bool:
        return False


async def _flush() -> None:
    for _ in range(8):
        await asyncio.sleep(0)


class DispatchTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset("thread-a")
        reset("thread-b")
    def test_command_chat_is_on_the_completion_notice(self) -> None:
        session = _bare_session()
        delivered = []
        session._deliver = delivered.append
        session.submit_line("echo hi", notify=True, session_key="thread-a")
        session.text += "hi\n"
        session._latched_exit = 0
        session._settle(session._settle_gen)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["session_key"], "thread-a")
        self.assertEqual(delivered[0]["command"], "echo hi")
        self.assertIn("hi", delivered[0]["output"])

    def test_watch_notice_uses_command_chat(self) -> None:
        session = _bare_session()
        session.submit_line("serve", notify=False, patterns=["READY"], session_key="thread-a")
        notices = session._watch("READY now")
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["session_key"], "thread-a")
        self.assertEqual(notices[0]["type"], "watch_match")

    def test_other_session_does_not_take_unaddressed_notice(self) -> None:
        with isolated_queue():
            process_registry.completion_queue.put(
                _completion("s-none", "", "echo lost", "lost-out")
            )
            process_registry.completion_queue.put(
                _completion("s-a", "thread-a", "echo kept", "kept-out")
            )
            taken = process_registry.drain_notifications(session_key="thread-b")
            self.assertEqual(taken, [])
            left = _drain_queue()
            keys = [evt.get("session_key") for evt in left]
            self.assertEqual(keys, ["", "thread-a"])

    def test_one_bad_notice_does_not_drop_the_rest(self) -> None:
        seen = []

        async def fake_web(web_agent, store, thread_id, content, task_id):
            seen.append((thread_id, content))

        async def run() -> None:
            with isolated_queue(), mock.patch(
                "apps.nextagent.groupchat.runtime.require_group_chat",
                return_value=Mode(),
            ), mock.patch(
                "apps.nextagent.message_runner._wakeup_web",
                fake_web,
            ), mock.patch(
                "apps.nextagent.ws_chat.mark_thread_task",
            ), mock.patch(
                "apps.nextagent.ws_chat.register_wakeup_task",
            ), mock.patch(
                "fm.backend.desktop.notify_fs_changed",
            ):
                process_registry.completion_queue.put(
                    _completion("s-a", "thread-a", "echo good", "good-out")
                )
                process_registry.completion_queue.put(
                    _completion("s-bad", "", "echo bad", "bad-out")
                )
                with self.assertRaises(RuntimeError):
                    await dispatch_process_completions(None, None)
                await _flush()

        asyncio.run(run())
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "thread-a")
        self.assertIn("echo good", seen[0][1])
        self.assertIn("good-out", seen[0][1])

    def test_watch_notice_is_delivered(self) -> None:
        seen = []

        async def fake_web(web_agent, store, thread_id, content, task_id):
            seen.append((thread_id, content))

        async def run() -> None:
            with isolated_queue(), mock.patch(
                "apps.nextagent.groupchat.runtime.require_group_chat",
                return_value=Mode(),
            ), mock.patch(
                "apps.nextagent.message_runner._wakeup_web",
                fake_web,
            ), mock.patch(
                "apps.nextagent.ws_chat.mark_thread_task",
            ), mock.patch(
                "apps.nextagent.ws_chat.register_wakeup_task",
            ), mock.patch(
                "fm.backend.desktop.notify_fs_changed",
            ):
                process_registry.completion_queue.put({
                    "type": "watch_match",
                    "session_id": "s-w",
                    "session_key": "thread-a",
                    "command": "serve",
                    "pattern": "READY",
                    "output": "READY now",
                    "suppressed": 0,
                })
                await dispatch_process_completions(None, None)
                await _flush()

        asyncio.run(run())
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "thread-a")
        self.assertIn("READY", seen[0][1])
        self.assertIn("serve", seen[0][1])

    def test_group_chat_not_ready_leaves_the_queue(self) -> None:
        async def run() -> None:
            with isolated_queue(), mock.patch(
                "apps.nextagent.groupchat.runtime.require_group_chat",
                side_effect=RuntimeError("群聊模式未启动"),
            ):
                process_registry.completion_queue.put(
                    _completion("s-a", "thread-a", "echo good", "good-out")
                )
                with self.assertRaises(RuntimeError):
                    await dispatch_process_completions(None, None)
                left = _drain_queue()
                self.assertEqual([evt["session_id"] for evt in left], ["s-a"])

        asyncio.run(run())

    def test_terminal_tool_passes_chat_id(self) -> None:
        session = mock.Mock()
        session.session_key = ""
        session.id = "s1"
        session.state = "idle"
        session.command_view.return_value = ("", None)
        ctx = TurnContext(thread_id="thread-a", task_id="task-1")
        token = set_current_session_key("thread-a")
        try:
            with bind_turn(ctx), mock.patch(
                "apps.nextagent.toolkit.terminal.REGISTRY"
            ) as registry, mock.patch(
                "apps.nextagent.toolkit.terminal.focus_session",
            ):
                registry.get.return_value = session
                terminal(
                    command="echo hi",
                    session_id="s1",
                    background=True,
                    notify_on_complete=True,
                )
        finally:
            reset_current_session_key(token)
        session.submit_line.assert_called_once()
        self.assertEqual(session.submit_line.call_args.kwargs["session_key"], "thread-a")
        self.assertEqual(session.submit_line.call_args.kwargs["notify"], True)

    def test_terminal_tool_refuses_notify_without_chat(self) -> None:
        ctx = TurnContext(thread_id="", task_id="task-1")
        with bind_turn(ctx), mock.patch(
            "apps.nextagent.maf_tools.session_key.get_current_session_key",
            return_value="",
        ), mock.patch(
            "apps.nextagent.toolkit.terminal.REGISTRY"
        ) as registry:
            text = terminal(
                command="echo hi",
                session_id="s1",
                background=True,
                notify_on_complete=True,
            )
        registry.get.assert_not_called()
        self.assertIn("会话", text)

    def test_busy_report_waits_in_the_session_queue(self) -> None:
        seen = []

        async def fake_web(web_agent, store, thread_id, content, task_id):
            seen.append((thread_id, content))

        async def run() -> None:
            with isolated_queue(), mock.patch(
                "apps.nextagent.groupchat.runtime.require_group_chat",
                return_value=Mode(),
            ), mock.patch(
                "apps.nextagent.message_runner._wakeup_web",
                fake_web,
            ), mock.patch(
                "apps.nextagent.ws_chat.thread_is_busy",
                return_value=True,
            ), mock.patch(
                "fm.backend.desktop.notify_fs_changed",
            ):
                process_registry.completion_queue.put(
                    _completion("s-a", "thread-a", "echo good", "good-out")
                )
                await dispatch_process_completions(None, None)
                await _flush()
                self.assertEqual(seen, [])
                self.assertEqual(depth("thread-a"), 1)
                self.assertEqual(_drain_queue(), [])

        asyncio.run(run())

    def test_stop_does_not_drop_queued_items(self) -> None:
        first = UserItem(
            message_id="m1",
            content="one",
            attachments=[],
            model="m",
            media_passthrough=False,
            thinking=None,
            effort=None,
            plan_mode=False,
            active_roles=[],
            skip_handoff=True,
        )
        second = UserItem(
            message_id="m2",
            content="two",
            attachments=[],
            model="m",
            media_passthrough=False,
            thinking=None,
            effort=None,
            plan_mode=False,
            active_roles=[],
            skip_handoff=True,
        )
        from apps.nextagent.turn_queue import enqueue

        enqueue("thread-a", first)
        enqueue("thread-a", second)
        self.assertEqual(depth("thread-a"), 2)
        items = snapshot("thread-a")
        self.assertEqual([it["content"] for it in items], ["one", "two"])
        self.assertEqual([it["kind"] for it in items], ["user", "user"])
        self.assertTrue(items[0]["id"])
        self.assertNotEqual(items[0]["id"], items[1]["id"])
        self.assertFalse(remove("thread-a", "missing"))
        self.assertTrue(remove("thread-a", items[0]["id"]))
        self.assertEqual([it["content"] for it in snapshot("thread-a")], ["two"])
        self.assertEqual(depth("thread-a"), 1)
