from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class InboundMention:
    channel: str
    event_id: str
    conversation_id: str
    message_id: str
    sender: str
    sender_id: str
    content: str
    identity: str = ""


class GroupChatAdapter(Protocol):
    name: str

    def logged_in(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def status(self) -> dict[str, Any]: ...

    def group_name(self, conversation_id: str) -> str: ...

    def send_text(self, conversation_id: str, text: str, group: str = "") -> None: ...

    def send_file(self, conversation_id: str, path: str, group: str = "") -> None: ...

    def list_messages(
        self,
        conversation_id: str,
        start: str,
        end: str,
        page_all: bool,
    ) -> dict[str, Any]: ...

    def download_files(
        self,
        conversation_id: str,
        start: str,
        end: str,
        output_dir: str,
    ) -> dict[str, Any]: ...

    def resolve_sender(self, mention: InboundMention) -> tuple[str, str]: ...

    def owner_person(self, identity: str = "") -> tuple[str, str]: ...
