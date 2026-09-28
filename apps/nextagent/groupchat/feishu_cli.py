from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from .procguard import ListenerJob, graceful_stop, is_lark_listener, kill_stale
from .protocol import InboundMention

_EVENT_KEY = "im.message.receive_v1"


def _lark_bin() -> str:
    path = shutil.which("lark-cli")
    if not path:
        raise RuntimeError("未检测到飞书 CLI (lark-cli)")
    return path


def _lark_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("HERMES_HOME", None)
    return env


def _lark_run(args: list[str], timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_lark_bin(), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
        cwd=str(cwd) if cwd is not None else None,
        env=_lark_env(),
    )


def _lark_json(args: list[str], timeout: int, cwd: Path | None = None) -> dict[str, Any]:
    completed = _lark_run(args, timeout, cwd)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or f"exit {completed.returncode}").strip()
        raise RuntimeError(err)
    raw = (completed.stdout or "").strip()
    if not raw:
        raise RuntimeError("lark-cli 无输出")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("lark-cli 返回不是对象")
    return data


def _lark_ok(args: list[str], timeout: int, cwd: Path | None = None) -> dict[str, Any]:
    data = _lark_json(args, timeout, cwd)
    if data.get("ok") is not True:
        raise RuntimeError("飞书 CLI 失败: " + json.dumps(data, ensure_ascii=False))
    return data


def _auth_status() -> dict[str, Any]:
    return _lark_json(["auth", "status", "--json"], 30)


def _lark_logged_in() -> bool:
    path = shutil.which("lark-cli")
    if not path:
        raise RuntimeError("未检测到飞书 CLI (lark-cli)")
    data = _auth_status()
    identities = data.get("identities")
    if not isinstance(identities, dict):
        raise RuntimeError("auth status 没有 identities: " + json.dumps(data, ensure_ascii=False))
    bot = identities.get("bot")
    if not isinstance(bot, dict):
        raise RuntimeError("auth status 没有 identities.bot: " + json.dumps(data, ensure_ascii=False))
    return bot.get("available") is True and bot.get("status") == "ready"


def _app_id() -> str:
    data = _auth_status()
    value = data.get("appId")
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("auth status 没有 appId: " + json.dumps(data, ensure_ascii=False))
    return value.strip()


def _bot_open_id(app_id: str) -> str:
    listed = _lark_ok(["im", "+chat-list", "--as", "bot", "--format", "json"], 60)
    payload = listed.get("data")
    if not isinstance(payload, dict):
        raise RuntimeError("chat-list 没有 data: " + json.dumps(listed, ensure_ascii=False))
    chats = payload.get("chats")
    if not isinstance(chats, list) or not chats:
        raise RuntimeError("飞书机器人未加入任何群")
    first = chats[0]
    if not isinstance(first, dict):
        raise RuntimeError("chat-list 群不是对象: " + json.dumps(first, ensure_ascii=False))
    chat_id = first.get("chat_id")
    if not isinstance(chat_id, str) or not chat_id.strip():
        raise RuntimeError("chat-list 没有 chat_id: " + json.dumps(first, ensure_ascii=False))
    members = _lark_ok(
        [
            "im",
            "+chat-members-list",
            "--as",
            "bot",
            "--chat-id",
            chat_id.strip(),
            "--member-types",
            "bot",
            "--page-all",
            "--format",
            "json",
        ],
        60,
    )
    body = members.get("data")
    if not isinstance(body, dict):
        raise RuntimeError("chat-members-list 没有 data: " + json.dumps(members, ensure_ascii=False))
    bots = body.get("bots")
    if not isinstance(bots, list):
        raise RuntimeError("chat-members-list 没有 bots: " + json.dumps(members, ensure_ascii=False))
    found: list[str] = []
    for item in bots:
        if not isinstance(item, dict):
            raise RuntimeError("机器人成员不是对象: " + json.dumps(item, ensure_ascii=False))
        if str(item.get("app_id") or "") != app_id:
            continue
        member_id = item.get("member_id")
        if not isinstance(member_id, str) or not member_id.strip():
            raise RuntimeError("机器人成员没有 member_id: " + json.dumps(item, ensure_ascii=False))
        found.append(member_id.strip())
    if len(found) != 1:
        raise RuntimeError("无法解析当前应用的机器人 ID: " + json.dumps(members, ensure_ascii=False))
    return found[0]


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"事件缺少 {key}")
    return value.strip()


def _sender_text(item: dict[str, Any]) -> str:
    sender = item.get("sender")
    if isinstance(sender, dict):
        name = sender.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        sid = sender.get("id")
        if isinstance(sid, str) and sid.strip():
            return sid.strip()
        return ""
    if isinstance(sender, str):
        return sender.strip()
    return ""


def _iter_messages(messages: list[Any]) -> Iterator[dict[str, Any]]:
    for item in messages:
        if not isinstance(item, dict):
            raise RuntimeError("消息不是对象: " + json.dumps(item, ensure_ascii=False))
        yield item
        replies = item.get("thread_replies")
        if replies is None:
            continue
        if not isinstance(replies, list):
            raise RuntimeError("thread_replies 不是数组: " + json.dumps(item, ensure_ascii=False))
        for reply in replies:
            if not isinstance(reply, dict):
                raise RuntimeError("话题回复不是对象: " + json.dumps(reply, ensure_ascii=False))
            yield reply


def _project_messages(messages: list) -> list[dict]:
    out: list[dict] = []
    for item in _iter_messages(messages):
        out.append(
            {
                "messageId": item.get("message_id") or "",
                "sender": _sender_text(item),
                "text": item.get("content") or "",
                "createTime": item.get("create_time") or "",
                "messageType": item.get("msg_type") or "",
                "resourceRefs": item.get("resources") or [],
            }
        )
    return out


def _has_downloadable(text: str) -> bool:
    return any(mark in text for mark in ("![Image](", "<file ", "<audio ", "<video ", "<media "))


def _download_paths(messages: list[Any], cwd: Path) -> list[str]:
    paths: list[str] = []
    saw_downloadable = False
    for item in _iter_messages(messages):
        content = str(item.get("content") or "")
        if _has_downloadable(content):
            saw_downloadable = True
        resources = item.get("resources")
        if resources is None:
            continue
        if not isinstance(resources, list):
            raise RuntimeError("resources 不是数组: " + json.dumps(item, ensure_ascii=False))
        for res in resources:
            if not isinstance(res, dict):
                raise RuntimeError("资源不是对象: " + json.dumps(res, ensure_ascii=False))
            if res.get("error") is True:
                raise RuntimeError("文件下载失败: " + json.dumps(res, ensure_ascii=False))
            local = res.get("local_path")
            if not isinstance(local, str) or not local.strip():
                raise RuntimeError("下载记录没有 local_path: " + json.dumps(res, ensure_ascii=False))
            path = Path(local.strip())
            if not path.is_absolute():
                path = cwd / path
            paths.append(str(path))
    if saw_downloadable and not paths:
        raise RuntimeError("消息含附件但没有下载到文件")
    return paths


def _chat_id_by_name(name: str) -> str:
    data = _lark_ok(
        ["im", "+chat-search", "--as", "bot", "--query", name, "--format", "json"],
        30,
    )
    payload = data.get("data")
    if not isinstance(payload, dict):
        raise RuntimeError("chat-search 没有 data: " + json.dumps(data, ensure_ascii=False))
    chats = payload.get("chats")
    if not isinstance(chats, list):
        raise RuntimeError("chat-search 没有 chats: " + json.dumps(data, ensure_ascii=False))
    exact: list[str] = []
    for item in chats:
        if not isinstance(item, dict):
            raise RuntimeError("群不是对象: " + json.dumps(item, ensure_ascii=False))
        if str(item.get("name") or "").strip() != name:
            continue
        cid = item.get("chat_id")
        if not isinstance(cid, str) or not cid.strip():
            raise RuntimeError("群没有 chat_id: " + json.dumps(item, ensure_ascii=False))
        exact.append(cid.strip())
    if len(exact) == 0:
        raise RuntimeError("没有找到同名群: " + name)
    if len(exact) > 1:
        raise RuntimeError("群名对应多个群: " + name)
    return exact[0]


def _send_chat_id(conversation_id: str, group: str) -> str:
    cid = (conversation_id or "").strip()
    if cid:
        return cid
    name = (group or "").strip()
    if name:
        return _chat_id_by_name(name)
    raise RuntimeError("当前对话没有绑定群，请说明群名")


def _mentions_bot(event: dict[str, Any], bot_id: str) -> bool:
    mentions = event.get("mentions")
    if mentions is None:
        return False
    if not isinstance(mentions, list):
        raise RuntimeError("事件 mentions 不是数组: " + json.dumps(event, ensure_ascii=False))
    for item in mentions:
        if not isinstance(item, dict):
            raise RuntimeError("mention 不是对象: " + json.dumps(item, ensure_ascii=False))
        if str(item.get("id") or "").strip() == bot_id:
            return True
    return False


class FeishuCliAdapter:
    name = "feishu"

    def __init__(
        self,
        fm_root: Path,
        *,
        log: Callable[[str, str], None],
        on_mention: Callable[[InboundMention], Any],
        on_clarify: Callable[[InboundMention], bool],
    ) -> None:
        self._fm_root = fm_root
        self._log = log
        self._on_mention = on_mention
        self._on_clarify = on_clarify
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._keep_task: asyncio.Task | None = None
        self._stopping = True
        self._ready = False
        self._error = ""
        self._logged_in = False
        self._app_id = ""
        self._bot_id = ""
        self._start_lock = asyncio.Lock()
        self._job = ListenerJob()

    def logged_in(self) -> bool:
        return _lark_logged_in()

    def status(self) -> dict[str, Any]:
        running = self._proc is not None and self._proc.returncode is None
        return {
            "running": running,
            "ready": self._ready and running,
            "error": self._error,
            "logged_in": self._logged_in,
            "app_id": self._app_id,
            "bot_id": self._bot_id,
        }

    def group_name(self, conversation_id: str) -> str:
        data = _lark_ok(
            [
                "im",
                "chats",
                "get",
                "--as",
                "bot",
                "--chat-id",
                conversation_id,
                "--format",
                "json",
            ],
            30,
            self._fm_root,
        )
        payload = data.get("data")
        if not isinstance(payload, dict):
            raise RuntimeError("chats.get 没有 data: " + json.dumps(data, ensure_ascii=False))
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError("会话信息没有群名: " + json.dumps(data, ensure_ascii=False))
        return name.strip()

    def send_text(self, conversation_id: str, text: str, group: str = "") -> None:
        body = (text or "").strip()
        if not body:
            raise RuntimeError("发送内容不能为空")
        chat_id = _send_chat_id(conversation_id, group)
        _lark_ok(
            [
                "im",
                "+messages-send",
                "--as",
                "bot",
                "--chat-id",
                chat_id,
                "--markdown",
                body,
                "--format",
                "json",
            ],
            60,
            self._fm_root,
        )

    def send_file(self, conversation_id: str, path: str, group: str = "") -> None:
        rel = Path(path)
        if not rel.is_absolute():
            raise RuntimeError("文件路径必须是绝对路径: " + path)
        chat_id = _send_chat_id(conversation_id, group)
        _lark_ok(
            [
                "im",
                "+messages-send",
                "--as",
                "bot",
                "--chat-id",
                chat_id,
                "--file",
                str(rel),
                "--format",
                "json",
            ],
            120,
            self._fm_root,
        )

    def resolve_sender(self, mention: InboundMention) -> tuple[str, str]:
        data = _lark_ok(
            [
                "im",
                "+messages-mget",
                "--as",
                "bot",
                "--message-ids",
                mention.message_id,
                "--no-reactions",
                "--format",
                "json",
            ],
            30,
        )
        payload = data.get("data")
        if not isinstance(payload, dict):
            raise RuntimeError("飞书消息没有 data: " + json.dumps(data, ensure_ascii=False))
        messages = payload.get("messages")
        if not isinstance(messages, list) or len(messages) != 1:
            raise RuntimeError("飞书消息条数不对: " + json.dumps(data, ensure_ascii=False))
        item = messages[0]
        if not isinstance(item, dict):
            raise RuntimeError("飞书消息不是对象: " + json.dumps(data, ensure_ascii=False))
        sender = item.get("sender")
        if not isinstance(sender, dict):
            raise RuntimeError("飞书消息没有发送者: " + json.dumps(item, ensure_ascii=False))
        name = str(sender.get("name") or "").strip()
        sid = str(sender.get("id") or "").strip()
        if not name or not sid:
            raise RuntimeError("飞书发送者缺少姓名或ID: " + json.dumps(sender, ensure_ascii=False))
        if sid != mention.sender_id:
            raise RuntimeError("飞书发送者ID与事件不一致")
        return name, sid

    def owner_person(self, identity: str = "") -> tuple[str, str]:
        data = _lark_ok(
            ["contact", "+search-user", "--user-ids", "me", "--as", "user", "--format", "json"],
            30,
        )
        payload = data.get("data")
        if not isinstance(payload, dict):
            raise RuntimeError("飞书当前用户没有 data: " + json.dumps(data, ensure_ascii=False))
        users = payload.get("users")
        if not isinstance(users, list) or len(users) != 1:
            raise RuntimeError("飞书当前用户不是唯一一条: " + json.dumps(data, ensure_ascii=False))
        item = users[0]
        if not isinstance(item, dict):
            raise RuntimeError("飞书当前用户不是对象: " + json.dumps(data, ensure_ascii=False))
        name = str(item.get("localized_name") or "").strip()
        sid = str(item.get("open_id") or "").strip()
        if not name or not sid:
            raise RuntimeError("飞书当前用户缺少姓名或ID: " + json.dumps(item, ensure_ascii=False))
        return name, sid

    def _fetch(
        self,
        conversation_id: str,
        start: str,
        end: str,
        page_all: bool,
        download: bool,
        cwd: Path,
    ) -> dict[str, Any]:
        args = [
            "im",
            "+chat-messages-list",
            "--as",
            "bot",
            "--chat-id",
            conversation_id,
            "--format",
            "json",
        ]
        if start.strip():
            args.extend(["--start", start.strip()])
        if end.strip():
            args.extend(["--end", end.strip()])
        if start.strip() or end.strip():
            args.extend(["--order", "asc"])
        if page_all:
            args.append("--page-all")
        if download:
            args.append("--download-resources")
        data = _lark_ok(args, 180, cwd)
        if page_all:
            meta = data.get("meta")
            if not isinstance(meta, dict):
                raise RuntimeError("群消息缺少 meta: " + json.dumps(data, ensure_ascii=False))
            pagination = meta.get("pagination")
            if not isinstance(pagination, dict) or pagination.get("complete") is not True:
                raise RuntimeError("群消息读取不完整: " + json.dumps(data, ensure_ascii=False))
        payload = data.get("data")
        if not isinstance(payload, dict):
            raise RuntimeError("群消息缺少 data")
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise RuntimeError("群消息缺少 messages")
        return data

    def list_messages(
        self,
        conversation_id: str,
        start: str,
        end: str,
        page_all: bool,
    ) -> dict[str, Any]:
        data = self._fetch(conversation_id, start, end, page_all, False, self._fm_root)
        payload = data["data"]
        meta = data.get("meta")
        complete = None
        if isinstance(meta, dict):
            pagination = meta.get("pagination")
            if isinstance(pagination, dict):
                complete = pagination.get("complete")
        return {
            "conversation_id": conversation_id,
            "complete": complete,
            "hasMore": payload.get("has_more"),
            "messages": _project_messages(payload["messages"]),
        }

    def download_files(
        self,
        conversation_id: str,
        start: str,
        end: str,
        output_dir: str,
    ) -> dict[str, Any]:
        dest = Path(output_dir)
        if not dest.is_absolute():
            raise RuntimeError("下载目录必须是绝对路径: " + output_dir)
        dest.mkdir(parents=True, exist_ok=True)
        data = self._fetch(conversation_id, start, end, False, True, dest)
        files = _download_paths(data["data"]["messages"], dest)
        return {
            "conversation_id": conversation_id,
            "output_dir": output_dir,
            "files": files,
        }

    async def start(self) -> None:
        async with self._start_lock:
            if self._keep_task is not None and not self._keep_task.done():
                return
            self._stopping = False
            self._keep_task = asyncio.create_task(self._boot())

    async def _boot(self) -> None:
        await self._wait_login()
        if self._stopping:
            return
        killed = await asyncio.to_thread(kill_stale, is_lark_listener, "飞书")
        if killed:
            self._log("info", f"已清理 {len(killed)} 个残留的飞书监听/总线进程: {killed}")
        await self._supervisor()

    async def _wait_login(self) -> None:
        told = False
        while not self._stopping:
            try:
                ok = await asyncio.to_thread(_lark_logged_in)
            except RuntimeError as exc:
                self._logged_in = False
                self._error = str(exc)
                if not told:
                    self._log("error", str(exc) + "，登录后自动启动")
                    told = True
                await asyncio.sleep(10)
                continue
            if ok:
                self._logged_in = True
                self._error = ""
                if told:
                    self._log("info", "飞书 CLI 已登录，启动监听")
                return
            self._logged_in = False
            self._error = "飞书 CLI 机器人身份未就绪，登录后自动启动"
            if not told:
                self._log("info", "飞书 CLI 机器人身份未就绪，等待就绪后再启动")
                told = True
            await asyncio.sleep(10)

    async def _spawn(self) -> None:
        self._error = ""
        self._ready = False
        try:
            if not await asyncio.to_thread(_lark_logged_in):
                raise RuntimeError("飞书 CLI 机器人身份未就绪")
            self._app_id = await asyncio.to_thread(_app_id)
            self._bot_id = await asyncio.to_thread(_bot_open_id, self._app_id)
            self._proc = await self._job.spawn(
                _lark_bin(),
                "event",
                "consume",
                _EVENT_KEY,
                "--as",
                "bot",
                env=_lark_env(),
            )
            await self._wait_ready()
        except Exception as exc:
            self._error = str(exc)
            await self._kill_proc()
            raise
        self._reader = asyncio.create_task(self._read_events())
        self._log("info", "开始监听飞书群@机器人")

    async def _assert_alive(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            raise RuntimeError("监听进程已退出")
        if not self._ready:
            raise RuntimeError("监听未 ready")
        if not self._app_id:
            raise RuntimeError("没有 app_id")
        data = await asyncio.to_thread(_lark_json, ["event", "status", "--json"], 30)
        apps = data.get("apps")
        if not isinstance(apps, list) or not apps:
            raise RuntimeError("event status 没有 apps: " + json.dumps(data, ensure_ascii=False))
        matched = [item for item in apps if isinstance(item, dict) and item.get("app_id") == self._app_id]
        if len(matched) != 1:
            raise RuntimeError("event status 对不上当前应用: " + json.dumps(data, ensure_ascii=False))
        app = matched[0]
        if app.get("running") is not True or app.get("status") != "running":
            raise RuntimeError("飞书事件总线未运行: " + json.dumps(app, ensure_ascii=False))
        consumers = app.get("consumers")
        if not isinstance(consumers, list):
            raise RuntimeError("event status 没有 consumers: " + json.dumps(app, ensure_ascii=False))
        live = [
            item
            for item in consumers
            if isinstance(item, dict) and item.get("event_key") == _EVENT_KEY
        ]
        if not live:
            raise RuntimeError("没有 im.message.receive_v1 消费者: " + json.dumps(app, ensure_ascii=False))
        for item in live:
            if item.get("dropped") != 0:
                raise RuntimeError("飞书事件被丢弃: " + json.dumps(item, ensure_ascii=False))

    async def _recover(self) -> None:
        async with self._start_lock:
            if self._stopping:
                return
            if self._proc is not None and self._proc.returncode is None and self._ready:
                try:
                    await self._assert_alive()
                    return
                except Exception:
                    pass
            try:
                await self._teardown_proc()
                await self._spawn()
                self._log("info", "已重新拉起飞书监听")
            except Exception as exc:
                self._error = str(exc)
                self._log("error", f"重新拉起飞书监听失败: {exc}")

    async def _supervisor(self) -> None:
        self._log("info", "飞书保活已启动")
        try:
            while not self._stopping:
                try:
                    dead = (
                        self._proc is None
                        or self._proc.returncode is not None
                        or not self._ready
                    )
                    if dead:
                        await self._recover()
                    else:
                        await self._assert_alive()
                    await asyncio.sleep(15)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._error = str(exc)
                    self._ready = False
                    self._log("error", f"飞书监听掉线: {exc}")
                    if self._stopping:
                        return
                    await self._recover()
                    await asyncio.sleep(3)
        except asyncio.CancelledError:
            raise

    async def _wait_ready(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            raise RuntimeError("监听进程没有 stderr")
        deadline = time.monotonic() + 30
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError("飞书监听未在 30 秒内 ready")
            line = await asyncio.wait_for(proc.stderr.readline(), timeout=30)
            if not line:
                code = proc.returncode
                raise RuntimeError(f"飞书监听进程在 ready 前退出: {code}")
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text:
                self._log("stderr", text)
            if "[event] ready" in text:
                self._ready = True
                asyncio.create_task(self._drain_stderr())
                return

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text:
                self._log("stderr", text)

    def _mention(self, event: dict[str, Any]) -> InboundMention | None:
        if str(event.get("chat_type") or "") != "group":
            return None
        if not self._bot_id:
            raise RuntimeError("没有机器人 ID")
        sender_id = _require_str(event, "sender_id")
        if str(event.get("sender_type") or "") == "bot" or sender_id == self._bot_id:
            return None
        if not _mentions_bot(event, self._bot_id):
            return None
        message_id = _require_str(event, "message_id")
        return InboundMention(
            channel=self.name,
            event_id=message_id,
            conversation_id=_require_str(event, "chat_id"),
            message_id=message_id,
            sender=sender_id,
            sender_id=sender_id,
            content=str(event.get("content") or "").strip(),
        )

    async def _read_events(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise RuntimeError("监听进程没有 stdout")
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    code = proc.returncode
                    raise RuntimeError(f"飞书监听进程结束: {code}")
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text:
                    continue
                event = json.loads(text)
                if not isinstance(event, dict):
                    raise RuntimeError("事件不是对象: " + text)
                asyncio.create_task(self._dispatch(event))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = str(exc)
            self._ready = False
            self._log("error", str(exc))
            if not self._stopping:
                asyncio.create_task(self._recover())

    async def _dispatch(self, event: dict[str, Any]) -> None:
        try:
            mention = self._mention(event)
        except RuntimeError as exc:
            self._log("error", str(exc))
            return
        if mention is None:
            return
        try:
            if self._on_clarify(mention):
                return
            await self._on_mention(mention)
        except asyncio.CancelledError:
            self._log("info", "已打断当前任务")
        except Exception as exc:
            self._log("error", str(exc))

    async def _teardown_proc(self) -> None:
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass
        await self._kill_proc()

    async def _kill_proc(self) -> None:
        proc = self._proc
        self._proc = None
        self._ready = False
        if proc is None:
            return
        await graceful_stop(proc, "飞书监听")

    async def stop(self) -> None:
        async with self._start_lock:
            self._stopping = True
            keep = self._keep_task
            self._keep_task = None
            if keep is not None:
                keep.cancel()
                try:
                    await keep
                except asyncio.CancelledError:
                    pass
            await self._teardown_proc()
            self._log("info", "已停止飞书监听")
