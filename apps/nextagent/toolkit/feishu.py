from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from fm.backend.pathutil import WORKSPACE, from_agent_path, resolve, to_fs

from ..groupchat.runtime import require_group_chat
from ..store import APP_FEISHU_WEBHOOK
from ._context import require_turn
from ._registry import registry

LIST_DOC = """查看当前绑定飞书群的消息。默认从现在往前看最近一页。

要看更早的内容时传 start / end（RFC3339 或 YYYY-MM-DD HH:mm:ss），需要整段拉完时把 page_all 设为 true。
不传 conversation_id 时使用当前对话绑定的群。
返回 messageId、发送人、正文、时间和附件引用。上面那张图通常就在最近几条里。"""

DOWNLOAD_DOC = """把当前绑定飞书群消息里的图片和文件下载到工作区。

默认下最近一页里的附件。要按时间范围下就传 start / end。
output_dir 是目录的真实绝对路径（正斜杠），相对路径按工作区目录解析；不传则写到工作区的 feishu-files/<当前对话>。
返回已下载文件的绝对路径。下载后用 file_access 或媒体能力处理，不要猜测路径。"""

SEND_FILE_DOC = """把本机文件发到飞书群。

path 是文件的真实绝对路径（正斜杠，如 D:/a/b.pdf），相对路径按工作区目录解析。
发到哪个群：当前对话已绑定飞书群就发到这个群；用户说了群名就发到那个群。两个都没有就不要猜。
群里被@时，只有调用这个工具文件才会出现在群里。只发结论需要的文件，不要发中间过程。"""

SEND_TEXT_DOC = """往飞书群发一段文字。

飞书 webhook 对话：发到当前对话绑定的 webhook，不要填 group，不要填 conversation_id。
普通飞书对话：当前对话已绑定飞书群就发到这个群；用户说了群名就发到那个群。两个都没有就不要猜。
只发结论，不要发中间过程、进度、已收到。"""


def _bindings():
    return require_group_chat().bindings


def _feishu():
    return require_group_chat().adapter("feishu")


def _require_feishu(bind: dict) -> None:
    if str(bind.get("channel") or "") != "feishu":
        raise RuntimeError("当前对话绑定的不是飞书群")


def _store():
    from ..web_server import store

    return store


def _webhook_thread() -> str:
    tid = require_turn().thread_id
    if _store().thread_app(tid) != APP_FEISHU_WEBHOOK:
        return ""
    return tid


def _post_webhook_text(url: str, text: str) -> None:
    payload = json.dumps({"msg_type": "text", "content": {"text": text}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError("飞书 webhook 发送失败: " + exc.read().decode("utf-8", "replace")) from exc
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("飞书 webhook 返回不是对象")
    if data.get("code") != 0:
        raise RuntimeError("飞书 webhook 发送失败: " + raw)


def conversation_id_for_thread(thread_id: str) -> str:
    bind = _bindings().get_by_thread(thread_id)
    if bind is None:
        raise RuntimeError("当前对话没有绑定飞书群")
    _require_feishu(bind)
    cid = str(bind.get("conversation_id") or "").strip()
    if not cid:
        raise RuntimeError("绑定缺少会话ID")
    return cid


def _conversation_id(explicit: str) -> str:
    value = (explicit or "").strip()
    if value:
        return value
    return conversation_id_for_thread(require_turn().thread_id)


def _target(conversation_id: str, group: str) -> tuple[str, str]:
    cid = (conversation_id or "").strip()
    name = (group or "").strip()
    if cid:
        return cid, ""
    if name:
        bind = _bindings().get_by_group_name(name)
        if bind is not None:
            _require_feishu(bind)
            out = str(bind.get("conversation_id") or "").strip()
            if not out:
                raise RuntimeError("绑定缺少会话ID")
            return out, name
        return "", name
    return _conversation_id(""), ""


def _returned_path(raw: str) -> str:
    p = Path(raw)
    if not p.is_absolute():
        p = Path(resolve(WORKSPACE)) / p
    return to_fs(str(p))


def _abs_file(raw: str) -> tuple[str, Path]:
    fs = from_agent_path(raw)
    return fs, Path(resolve(fs))


def _abs_dir(raw: str) -> tuple[str, Path]:
    fs = from_agent_path(raw, is_directory=True)
    full = Path(resolve(fs))
    full.mkdir(parents=True, exist_ok=True)
    return fs, full


@registry.register(
    toolset="skills",
    name="feishu_list_messages",
    summary="查看绑定飞书群的消息",
    description="查看飞书群消息",
    doc=LIST_DOC,
)
def feishu_list_messages(
    start: str = "",
    end: str = "",
    page_all: bool = False,
    conversation_id: str = "",
) -> str:
    if _webhook_thread():
        raise RuntimeError("飞书 webhook 对话不能获取群消息")
    cid = _conversation_id(conversation_id)
    payload = _feishu().list_messages(cid, start, end, page_all)
    return json.dumps(payload, ensure_ascii=False)


@registry.register(
    toolset="skills",
    name="feishu_download_files",
    summary="下载绑定飞书群的图片和文件",
    description="下载飞书群附件",
    doc=DOWNLOAD_DOC,
)
def feishu_download_files(
    start: str = "",
    end: str = "",
    output_dir: str = "",
    conversation_id: str = "",
) -> str:
    if _webhook_thread():
        raise RuntimeError("飞书 webhook 对话不能下载群文件")
    cid = _conversation_id(conversation_id)
    turn = require_turn()
    dest_fs, dest = _abs_dir(output_dir or f"feishu-files/{turn.thread_id}")
    payload = _feishu().download_files(cid, start, end, str(dest))
    files = [_returned_path(p) for p in payload["files"]]
    return json.dumps(
        {"conversation_id": cid, "output_dir": dest_fs, "files": files},
        ensure_ascii=False,
    )


@registry.register(
    toolset="skills",
    name="feishu_send_text",
    summary="立刻往绑定飞书群发文字",
    description="发送文字到飞书群",
    doc=SEND_TEXT_DOC,
)
def feishu_send_text(text: str, conversation_id: str = "", group: str = "") -> str:
    body = (text or "").strip()
    if not body:
        raise RuntimeError("发送内容不能为空")
    tid = _webhook_thread()
    if tid:
        if (conversation_id or "").strip() or (group or "").strip():
            raise RuntimeError("飞书 webhook 对话只能发到当前对话绑定的地址")
        url = _store().feishu_webhook_url(tid)
        if not url:
            raise RuntimeError("当前对话没有填写飞书 webhook 地址")
        _post_webhook_text(url, body)
        return json.dumps({"ok": True}, ensure_ascii=False)
    cid, name = _target(conversation_id, group)
    _feishu().send_text(cid, body, name)
    return json.dumps({"ok": True, "conversation_id": cid, "group": name}, ensure_ascii=False)


@registry.register(
    toolset="skills",
    name="feishu_send_file",
    summary="把工作区文件发到绑定飞书群",
    description="发送文件到飞书群",
    doc=SEND_FILE_DOC,
)
def feishu_send_file(path: str, conversation_id: str = "", group: str = "") -> str:
    if _webhook_thread():
        raise RuntimeError("飞书 webhook 不能发送文件")
    cid, name = _target(conversation_id, group)
    rel, full = _abs_file(path)
    if not full.is_file():
        raise RuntimeError("文件不存在: " + rel)
    _feishu().send_file(cid, str(full), name)
    return json.dumps({"ok": True, "path": rel, "conversation_id": cid, "group": name}, ensure_ascii=False)
