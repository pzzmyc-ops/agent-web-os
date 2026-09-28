from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CHANNEL_LABEL = {
    "dingtalk": "钉钉",
    "feishu": "飞书",
}


def channel_label(channel: str) -> str:
    name = _CHANNEL_LABEL.get(str(channel or "").strip())
    if not name:
        raise RuntimeError("未知通道: " + str(channel or ""))
    return name


class BindingStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"bindings": {}}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("bindings"), dict):
            raise RuntimeError("群聊绑定文件损坏")
        return raw

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _row(self, conversation_id: str, item: dict[str, Any]) -> dict[str, Any]:
        channel = str(item.get("channel") or "").strip()
        if not channel:
            raise RuntimeError("绑定缺少通道")
        row = dict(item)
        row["conversation_id"] = conversation_id
        row["channel"] = channel
        return row

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        item = self._data["bindings"].get(conversation_id)
        if item is None:
            return None
        if not isinstance(item, dict):
            raise RuntimeError("绑定记录损坏")
        return self._row(conversation_id, item)

    def get_by_thread(self, thread_id: str) -> dict[str, Any] | None:
        for cid, item in self._data["bindings"].items():
            if not isinstance(item, dict):
                raise RuntimeError("绑定记录损坏")
            if str(item.get("thread_id") or "") == thread_id:
                return self._row(cid, item)
        return None

    def get_by_group_name(self, group_name: str) -> dict[str, Any] | None:
        name = (group_name or "").strip()
        if not name:
            raise RuntimeError("群名不能为空")
        found: list[dict[str, Any]] = []
        for cid, item in self._data["bindings"].items():
            if not isinstance(item, dict):
                raise RuntimeError("绑定记录损坏")
            if str(item.get("group_name") or "").strip() == name:
                found.append(self._row(cid, item))
        if len(found) > 1:
            raise RuntimeError("群名对应多个绑定: " + name)
        if not found:
            return None
        return found[0]

    def put(self, conversation_id: str, item: dict[str, Any]) -> None:
        if not str(item.get("channel") or "").strip():
            raise RuntimeError("绑定缺少通道")
        self._data["bindings"][conversation_id] = item
        self._save()

    def list_items(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for cid, item in self._data["bindings"].items():
            if not isinstance(item, dict):
                raise RuntimeError("绑定记录损坏")
            out.append(self._row(cid, item))
        return out

    def get_model(self) -> str:
        value = self._data.get("model")
        if value is None:
            return ""
        if not isinstance(value, str):
            raise RuntimeError("绑定文件模型字段损坏")
        return value.strip()

    def set_model(self, model: str) -> None:
        self._data["model"] = model
        self._save()

    def flags_for_thread(self, thread_id: str) -> dict[str, Any]:
        bind = self.get_by_thread(thread_id)
        if bind is None:
            return {"dingtalk": False, "group_chat": False}
        channel = str(bind.get("channel") or "")
        return {
            "dingtalk": channel == "dingtalk",
            "group_chat": True,
            "channel": channel,
            "source_label": channel_label(channel),
            "group_name": str(bind.get("group_name") or ""),
        }

    def annotate_conversations(self, convs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in convs:
            row = dict(item)
            row.update(self.flags_for_thread(str(item.get("id") or "")))
            out.append(row)
        return out
