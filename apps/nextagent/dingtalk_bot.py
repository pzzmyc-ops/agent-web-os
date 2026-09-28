from __future__ import annotations

from pathlib import Path
from typing import Any

from .groupchat.dingtalk_cli import DingTalkCliAdapter
from .groupchat.feishu_cli import FeishuCliAdapter
from .groupchat.mode import GroupChatMode
from .groupchat.runtime import set_group_chat
from .groupchat.store import BindingStore

__all__ = ["BindingStore", "DingTalkBot"]


class DingTalkBot:
    def __init__(self, store, web_agent, bindings_path: Path, fm_root: Path, default_model: str) -> None:
        self._mode = GroupChatMode(store, web_agent, bindings_path, default_model)
        dingtalk = DingTalkCliAdapter(
            fm_root,
            log=self._mode.log,
            on_mention=self._mode.handle_mention,
            on_clarify=self._mode.take_clarify,
        )
        feishu = FeishuCliAdapter(
            fm_root,
            log=self._mode.log,
            on_mention=self._mode.handle_mention,
            on_clarify=self._mode.take_clarify,
        )
        self._mode.register("dingtalk", dingtalk)
        self._mode.register("feishu", feishu)
        self._dingtalk = dingtalk
        self._feishu = feishu
        set_group_chat(self._mode)

    def set_model(self, model: str) -> None:
        self._mode.set_model(model)

    def status(self) -> dict[str, Any]:
        return self._mode.status()

    def cancel_turn(self) -> None:
        self._mode.cancel_turn()

    def cancel_if_match(self, task_id: str = "", thread_id: str = "") -> None:
        self._mode.cancel_if_match(task_id, thread_id)

    def is_bound_thread(self, thread_id: str) -> bool:
        return self._mode.is_bound_thread(thread_id)

    async def inject_user_message(self, thread_id: str, content: str) -> str:
        return await self._mode.inject_user_message(thread_id, content)

    def annotate_conversations(self, convs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._mode.annotate_conversations(convs)

    async def start(self) -> None:
        await self._dingtalk.start()
        await self._feishu.start()

    async def stop(self) -> None:
        await self._dingtalk.stop()
        await self._feishu.stop()
