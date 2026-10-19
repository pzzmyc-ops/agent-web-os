from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from apps.nextagent.ws_chat import DEFAULT_TITLE, _broadcast_title


class _Store:
    def __init__(self, title: str) -> None:
        self.title = title
        self.renamed = None

    def thread_title(self, thread_id: str) -> str:
        return self.title

    def rename_thread(self, thread_id: str, title: str) -> None:
        self.renamed = (thread_id, title)
        self.title = title


class BroadcastTitleTests(unittest.TestCase):
    def test_new_thread_takes_the_first_chars_of_the_question(self) -> None:
        async def run() -> None:
            store = _Store(DEFAULT_TITLE)
            question = "第一行\n第二行后面还有很多字用来超过二十个字"
            with mock.patch("apps.nextagent.ws_chat.broadcast", new=mock.AsyncMock()) as broadcast:
                await _broadcast_title(store, "t1", question)
            self.assertEqual(store.renamed, ("t1", "第一行 第二行后面还有很多字用来超过二十"))
            broadcast.assert_awaited_once()
            payload = broadcast.await_args.args[0]
            self.assertEqual(payload["type"], "title_updated")
            self.assertEqual(payload["data"]["threadId"], "t1")
            self.assertEqual(payload["data"]["title"], store.title)

        asyncio.run(run())

    def test_renamed_thread_stays(self) -> None:
        async def run() -> None:
            store = _Store("已经改过")
            with mock.patch("apps.nextagent.ws_chat.broadcast", new=mock.AsyncMock()) as broadcast:
                await _broadcast_title(store, "t1", "新的提问")
            self.assertIsNone(store.renamed)
            broadcast.assert_not_awaited()

        asyncio.run(run())

    def test_blank_question_does_not_rename(self) -> None:
        async def run() -> None:
            store = _Store(DEFAULT_TITLE)
            with mock.patch("apps.nextagent.ws_chat.broadcast", new=mock.AsyncMock()) as broadcast:
                await _broadcast_title(store, "t1", "  \n  ")
            self.assertIsNone(store.renamed)
            broadcast.assert_not_awaited()

        asyncio.run(run())

    def test_auto_title_has_one_route(self) -> None:
        text = Path(__file__).with_name("ws_chat.py").read_text(encoding="utf-8")
        self.assertNotIn("maybe_title", text)
        self.assertNotIn("first_user_text", text)
        self.assertEqual(text.count("_broadcast_title("), 3)
