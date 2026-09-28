from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .procguard import ListenerJob, graceful_stop, is_dws_listener, kill_pid, kill_stale
from .protocol import InboundMention


def _dws_bin() -> str:
    path = shutil.which("dws")
    if not path:
        raise RuntimeError("未检测到钉钉 CLI (dws)")
    return path


def _dws_json(
    args: list[str],
    timeout: int,
    cwd: Path | None = None,
    identity: str = "",
) -> dict[str, Any]:
    cmd = [_dws_bin()]
    ident = (identity or "").strip()
    if ident:
        cmd.extend(["--profile", ident])
    completed = subprocess.run(
        [*cmd, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
        cwd=str(cwd) if cwd is not None else None,
    )
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or f"exit {completed.returncode}").strip()
        raise RuntimeError(err)
    raw = (completed.stdout or "").strip()
    if not raw:
        raise RuntimeError("dws 无输出")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("dws 返回不是对象")
    return data


def _dws_logged_in() -> bool:
    path = shutil.which("dws")
    if not path:
        raise RuntimeError("未检测到钉钉 CLI (dws)")
    completed = subprocess.run(
        [path, "auth", "status", "--format", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    raw = (completed.stdout or "").strip()
    if raw:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise RuntimeError("dws 返回不是对象")
        return data.get("authenticated") is True
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or f"exit {completed.returncode}").strip())
    raise RuntimeError("dws 无输出")


def _org_identities() -> list[dict[str, str]]:
    data = _dws_json(["profile", "list", "--format", "json"], 30)
    rows = data.get("profiles")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("没有已登录的钉钉企业")
    by_corp: dict[str, list[dict[str, Any]]] = {}
    for item in rows:
        if not isinstance(item, dict):
            raise RuntimeError("profile 不是对象: " + json.dumps(item, ensure_ascii=False))
        corp = str(item.get("corpId") or "").strip()
        if not corp:
            raise RuntimeError("profile 缺少 corpId: " + json.dumps(item, ensure_ascii=False))
        by_corp.setdefault(corp, []).append(item)
    out: list[dict[str, str]] = []
    for corp, items in by_corp.items():
        currents = [item for item in items if item.get("isOrgCurrent") is True]
        if len(currents) != 1:
            raise RuntimeError("企业缺少唯一当前账号: " + corp)
        item = currents[0]
        selector = str(item.get("profile") or "").strip()
        if not selector:
            raise RuntimeError("profile 缺少选择器: " + json.dumps(item, ensure_ascii=False))
        auth = _dws_json(["auth", "status", "--format", "json"], 30, identity=selector)
        if auth.get("authenticated") is not True:
            raise RuntimeError("企业未登录: " + str(item.get("corpName") or selector))
        out.append(
            {
                "identity": selector,
                "corp_name": str(item.get("corpName") or "").strip(),
                "user_name": str(item.get("userName") or "").strip(),
            }
        )
    return out


def _current_identity() -> str:
    data = _dws_json(["profile", "list", "--format", "json"], 30)
    rows = data.get("profiles")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("没有已登录的钉钉企业")
    currents = [item for item in rows if isinstance(item, dict) and item.get("isCurrent") is True]
    if len(currents) != 1:
        raise RuntimeError("没有唯一当前企业")
    selector = str(currents[0].get("profile") or "").strip()
    if not selector:
        raise RuntimeError("当前企业缺少选择器")
    return selector


class _OrgListen:
    def __init__(self, identity: str, corp_name: str) -> None:
        self.identity = identity
        self.corp_name = corp_name
        self.proc: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.ready = False
        self.subscribe_id = ""
        self.bus_pid = 0
        self.ready_at_ms = 0
        self.last_audit = 0.0
        self.error = ""


_AUDIT_INTERVAL_S = 60
_AUDIT_GRACE_MS = 30_000
_SEEN_LIMIT = 2000


class _LostMentions(RuntimeError):
    def __init__(self, slot: _OrgListen, mentions: list[InboundMention]) -> None:
        brief = " | ".join(f"[{m.conversation_id}] {m.sender}: {m.content}" for m in mentions)
        super().__init__(slot.corp_name + f" 事件流漏掉 {len(mentions)} 条@，API 有而监听没收到: " + brief)
        self.mentions = mentions


def _at_time_ms(text: str) -> int:
    value = (text or "").strip()
    if not value:
        raise RuntimeError("@我 消息缺少时间")
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return int(parsed.timestamp() * 1000)


def _pick_title(obj: Any) -> str:
    if not isinstance(obj, dict):
        raise RuntimeError("会话信息不是对象")
    for key in ("title", "name", "conversationTitle", "conversation_title", "nick"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for nested_key in ("conversationInfo", "conversation", "data", "result"):
        nested = obj.get(nested_key)
        if isinstance(nested, dict):
            return _pick_title(nested)
    raise RuntimeError("会话信息没有群名: " + json.dumps(obj, ensure_ascii=False))


def _download_paths(data: dict[str, Any]) -> list[str]:
    ledger = data.get("resourceDownloads")
    if ledger is None:
        return []
    if not isinstance(ledger, dict):
        raise RuntimeError("resourceDownloads 不是对象: " + json.dumps(data, ensure_ascii=False))
    if ledger.get("ok") is not True:
        raise RuntimeError("文件下载失败: " + json.dumps(ledger, ensure_ascii=False))
    failures = ledger.get("failures")
    if failures:
        raise RuntimeError("文件下载失败: " + json.dumps(failures, ensure_ascii=False))
    items = ledger.get("downloads")
    if not isinstance(items, list):
        raise RuntimeError("downloads 不是数组: " + json.dumps(ledger, ensure_ascii=False))
    paths: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("下载记录不是对象: " + json.dumps(item, ensure_ascii=False))
        path = item.get("localPath")
        if not isinstance(path, str) or not path.strip():
            raise RuntimeError("下载记录没有 localPath: " + json.dumps(item, ensure_ascii=False))
        paths.append(path.strip())
    return paths


def _placed_path(raw: str, dest: Path) -> str:
    path = Path(raw)
    if not path.is_absolute():
        path = dest / path
    return str(path)


def _has_resource_refs(messages: list[Any]) -> bool:
    for item in messages:
        if not isinstance(item, dict):
            continue
        refs = item.get("resourceRefs")
        if isinstance(refs, list) and refs:
            return True
    return False


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"事件缺少 {key}")
    return value.strip()


def _project_messages(messages: list) -> list[dict]:
    out: list[dict] = []
    for item in messages:
        if not isinstance(item, dict):
            raise RuntimeError("消息不是对象: " + json.dumps(item, ensure_ascii=False))
        out.append(
            {
                "messageId": item.get("messageId") or item.get("message_id") or "",
                "sender": item.get("sender") or "",
                "text": item.get("text") or item.get("content") or "",
                "createTime": item.get("createTime") or item.get("create_time") or "",
                "messageType": item.get("messageType") or "",
                "resourceRefs": item.get("resourceRefs") or [],
            }
        )
    return out


def _send_args(cid: str, group: str) -> list[str]:
    if cid:
        return ["--group", cid]
    if group:
        return ["--chat-query", group]
    raise RuntimeError("当前对话没有绑定群，请说明群名")


class DingTalkCliAdapter:
    name = "dingtalk"

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
        self._listens: dict[str, _OrgListen] = {}
        self._cid_identity: dict[str, str] = {}
        self._keep_task: asyncio.Task | None = None
        self._stopping = True
        self._error = ""
        self._logged_in = False
        self._start_lock = asyncio.Lock()
        self._job = ListenerJob()
        self._seen_msgs: dict[str, int] = {}

    def _mark_seen(self, message_id: str) -> bool:
        if message_id in self._seen_msgs:
            return False
        self._seen_msgs[message_id] = int(time.time() * 1000)
        if len(self._seen_msgs) > _SEEN_LIMIT:
            for old in list(self._seen_msgs)[: len(self._seen_msgs) - _SEEN_LIMIT]:
                self._seen_msgs.pop(old, None)
        return True

    def _identity_for(self, conversation_id: str, group: str = "") -> str:
        cid = (conversation_id or "").strip()
        if cid:
            cached = str(self._cid_identity.get(cid) or "").strip()
            if cached:
                return cached
        from .runtime import require_group_chat

        bind = require_group_chat().bindings.get(cid) if cid else None
        if bind is None and (group or "").strip():
            bind = require_group_chat().bindings.get_by_group_name(group.strip())
        if bind is not None:
            ident = str(bind.get("identity") or "").strip()
            if ident:
                return ident
        return _current_identity()

    def logged_in(self) -> bool:
        return _dws_logged_in()

    def status(self) -> dict[str, Any]:
        orgs: list[dict[str, Any]] = []
        running = True
        ready = True
        if not self._listens:
            running = False
            ready = False
        for slot in self._listens.values():
            alive = slot.proc is not None and slot.proc.returncode is None
            running = running and alive
            ready = ready and slot.ready and alive
            orgs.append(
                {
                    "identity": slot.identity,
                    "corp_name": slot.corp_name,
                    "running": alive,
                    "ready": slot.ready and alive,
                    "subscribe_id": slot.subscribe_id,
                    "error": slot.error,
                }
            )
        first = next(iter(self._listens.values()), None)
        return {
            "running": running,
            "ready": ready,
            "error": self._error,
            "subscribe_id": first.subscribe_id if first is not None else "",
            "logged_in": self._logged_in,
            "orgs": orgs,
        }

    def group_name(self, conversation_id: str) -> str:
        data = _dws_json(
            [
                "chat",
                "conversation-info",
                "--conversation-id",
                conversation_id,
                "--format",
                "json",
            ],
            30,
            self._fm_root,
            identity=self._identity_for(conversation_id),
        )
        return _pick_title(data)

    def send_text(self, conversation_id: str, text: str, group: str = "") -> None:
        body = (text or "").strip()
        if not body:
            raise RuntimeError("发送内容不能为空")
        data = _dws_json(
            [
                "chat",
                "+messages-send",
                "--as",
                "user",
                *_send_args(conversation_id, group),
                "--markdown",
                body,
                "--yes",
                "--format",
                "json",
            ],
            60,
            self._fm_root,
            identity=self._identity_for(conversation_id, group),
        )
        if data.get("ok") is not True:
            raise RuntimeError("发回群失败: " + json.dumps(data, ensure_ascii=False))

    def send_file(self, conversation_id: str, path: str, group: str = "") -> None:
        data = _dws_json(
            [
                "chat",
                "+messages-send",
                "--as",
                "user",
                *_send_args(conversation_id, group),
                "--file",
                path,
                "--yes",
                "--format",
                "json",
            ],
            120,
            self._fm_root,
            identity=self._identity_for(conversation_id, group),
        )
        if data.get("ok") is not True:
            raise RuntimeError("发送文件失败: " + json.dumps(data, ensure_ascii=False))

    def resolve_sender(self, mention: InboundMention) -> tuple[str, str]:
        name = (mention.sender or "").strip()
        sid = (mention.sender_id or "").strip()
        if not name or not sid:
            raise RuntimeError("事件缺少发送者")
        return name, sid

    def owner_person(self, identity: str = "") -> tuple[str, str]:
        me = _dws_json(["contact", "+me", "--format", "json"], 30, identity=identity)
        name = str(me.get("name") or "").strip()
        uid = str(me.get("userId") or "").strip()
        if not name or not uid:
            raise RuntimeError("钉钉当前用户缺少姓名或ID")
        data = _dws_json(
            ["contact", "user", "search", "--query", name, "--format", "json"],
            30,
            identity=identity,
        )
        if data.get("success") is not True:
            raise RuntimeError("钉钉查自己失败: " + json.dumps(data, ensure_ascii=False))
        rows = data.get("result")
        if not isinstance(rows, list):
            raise RuntimeError("钉钉查自己结果不是列表: " + json.dumps(data, ensure_ascii=False))
        hits: list[str] = []
        for item in rows:
            if not isinstance(item, dict):
                raise RuntimeError("钉钉查自己条目不是对象: " + json.dumps(item, ensure_ascii=False))
            if str(item.get("userId") or "").strip() != uid:
                continue
            oid = str(item.get("openDingTalkId") or "").strip()
            if not oid:
                raise RuntimeError("钉钉当前用户缺少开放ID: " + json.dumps(item, ensure_ascii=False))
            hits.append(oid)
        if len(hits) != 1:
            raise RuntimeError("钉钉当前用户开放ID对不上: " + json.dumps(data, ensure_ascii=False))
        return name, hits[0]

    def _fetch(
        self,
        conversation_id: str,
        start: str,
        end: str,
        page_all: bool,
        download: bool,
        output_dir: str,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        args = [
            "chat",
            "+chat-messages",
            "--group",
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
            args.extend(["--download-resources", "--output-dir", output_dir])
        data = _dws_json(
            args,
            180,
            cwd if cwd is not None else self._fm_root,
            identity=self._identity_for(conversation_id),
        )
        if page_all and data.get("complete") is not True:
            raise RuntimeError("群消息读取不完整: " + json.dumps(data, ensure_ascii=False))
        messages = data.get("messages")
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
        data = self._fetch(conversation_id, start, end, page_all, False, "")
        return {
            "conversation_id": conversation_id,
            "complete": data.get("complete"),
            "hasMore": data.get("hasMore"),
            "messages": _project_messages(data["messages"]),
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
        data = self._fetch(conversation_id, start, end, False, True, ".", dest)
        files = [_placed_path(p, dest) for p in _download_paths(data)]
        if _has_resource_refs(data["messages"]) and not files:
            raise RuntimeError("消息含附件但没有下载到文件: " + json.dumps(data, ensure_ascii=False))
        return {
            "conversation_id": conversation_id,
            "output_dir": str(dest),
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
        killed = await asyncio.to_thread(kill_stale, is_dws_listener, "钉钉")
        if killed:
            self._log("info", f"已清理 {len(killed)} 个残留的钉钉监听/总线进程: {killed}")
        await self._supervisor()

    async def _wait_login(self) -> None:
        told = False
        while not self._stopping:
            try:
                ok = await asyncio.to_thread(_dws_logged_in)
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
                    self._log("info", "钉钉 CLI 已登录，启动监听")
                return
            self._logged_in = False
            self._error = "钉钉 CLI 未登录，登录后自动启动"
            if not told:
                self._log("info", "钉钉 CLI 未登录，等待登录后再启动")
                told = True
            await asyncio.sleep(10)

    async def _spawn_one(self, slot: _OrgListen) -> None:
        slot.error = ""
        slot.ready = False
        slot.subscribe_id = ""
        slot.bus_pid = 0
        slot.proc = await self._job.spawn(
            _dws_bin(),
            "--profile",
            slot.identity,
            "event",
            "+listen-im",
            "--kind",
            "at-me",
            "--duration",
            "0",
            "-f",
            "ndjson",
            "--yes",
        )
        await self._wait_ready(slot)
        slot.ready_at_ms = int(time.time() * 1000)
        slot.last_audit = time.monotonic()
        slot.reader = asyncio.create_task(self._read_events(slot))
        self._log("info", f"开始监听 {slot.corp_name} 的@ (bus {slot.bus_pid}, {slot.subscribe_id})")

    async def _sync_listens(self) -> None:
        items = await asyncio.to_thread(_org_identities)
        wanted = {item["identity"]: item for item in items}
        for ident in list(self._listens):
            if ident not in wanted:
                await self._teardown_one(self._listens.pop(ident))
        for ident, meta in wanted.items():
            slot = self._listens.get(ident)
            alive = (
                slot is not None
                and slot.proc is not None
                and slot.proc.returncode is None
                and slot.ready
            )
            if alive:
                continue
            if slot is not None:
                await self._teardown_one(slot)
            slot = _OrgListen(ident, meta["corp_name"])
            await self._spawn_one(slot)
            self._listens[ident] = slot

    async def _assert_alive(self, slot: _OrgListen) -> None:
        if slot.proc is None or slot.proc.returncode is not None:
            raise RuntimeError(slot.corp_name + " 监听进程已退出")
        if not slot.subscribe_id:
            raise RuntimeError(slot.corp_name + " 没有 subscribe_id")
        data = await asyncio.to_thread(
            _dws_json,
            [
                "event",
                "status",
                "--event",
                "user_im_message_receive_at",
                "--subscribe-id",
                slot.subscribe_id,
                "--format",
                "json",
            ],
            30,
            None,
            slot.identity,
        )
        live = (data.get("bus") or {}).get("live")
        if not isinstance(live, dict):
            raise RuntimeError("event status 没有 bus.live: " + json.dumps(data, ensure_ascii=False))
        source = live.get("source_state")
        if not isinstance(source, dict):
            raise RuntimeError("event status 没有 source_state: " + json.dumps(live, ensure_ascii=False))
        state = str(source.get("state") or "")
        if state != "connected":
            raise RuntimeError(slot.corp_name + " 事件总线未连接: " + json.dumps(source, ensure_ascii=False))
        consumers = live.get("consumers")
        if not isinstance(consumers, list):
            raise RuntimeError("event status 没有 consumers: " + json.dumps(live, ensure_ascii=False))
        sid = slot.subscribe_id
        if not any(isinstance(c, dict) and str(c.get("subscribe_id") or "") == sid for c in consumers):
            raise RuntimeError(slot.corp_name + " 订阅不在 consumers 里: " + json.dumps(live, ensure_ascii=False))
        if time.monotonic() - slot.last_audit >= _AUDIT_INTERVAL_S:
            lost = await self._audit(slot)
            if lost:
                raise _LostMentions(slot, lost)

    async def _audit(self, slot: _OrgListen) -> list[InboundMention]:
        slot.last_audit = time.monotonic()
        data = await asyncio.to_thread(
            _dws_json,
            ["chat", "+at-me", "--days", "1", "--format", "json"],
            60,
            None,
            slot.identity,
        )
        items = data.get("items")
        if not isinstance(items, list):
            raise RuntimeError("at-me 没有 items: " + json.dumps(data, ensure_ascii=False))
        now_ms = int(time.time() * 1000)
        pending: list[tuple[int, dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                raise RuntimeError("at-me 记录不是对象: " + json.dumps(item, ensure_ascii=False))
            mid = str(item.get("messageId") or "").strip()
            if not mid:
                raise RuntimeError("at-me 记录缺少 messageId: " + json.dumps(item, ensure_ascii=False))
            ts = _at_time_ms(str(item.get("time") or ""))
            if ts <= slot.ready_at_ms or ts > now_ms - _AUDIT_GRACE_MS:
                continue
            if mid in self._seen_msgs:
                continue
            pending.append((ts, item))
        pending.sort(key=lambda pair: pair[0])
        mentions: list[InboundMention] = []
        for ts, item in pending:
            mentions.append(await self._lost_mention(slot, item))
        for mention in mentions:
            self._mark_seen(mention.message_id)
        return mentions

    async def _lost_mention(self, slot: _OrgListen, item: dict[str, Any]) -> InboundMention:
        mid = str(item.get("messageId") or "").strip()
        cid = str(item.get("conversationId") or "").strip()
        if not cid:
            raise RuntimeError("at-me 记录缺少 conversationId: " + json.dumps(item, ensure_ascii=False))
        when = str(item.get("time") or "").strip()
        start = datetime.strptime(when, "%Y-%m-%d %H:%M:%S")
        end = start + timedelta(seconds=1)
        data = await asyncio.to_thread(
            _dws_json,
            [
                "chat",
                "+chat-messages",
                "--group",
                cid,
                "--start",
                start.strftime("%Y-%m-%d %H:%M:%S"),
                "--end",
                end.strftime("%Y-%m-%d %H:%M:%S"),
                "--order",
                "asc",
                "--format",
                "json",
            ],
            60,
            None,
            slot.identity,
        )
        messages = data.get("messages")
        if not isinstance(messages, list):
            raise RuntimeError("群消息缺少 messages: " + json.dumps(data, ensure_ascii=False))
        for msg in messages:
            if not isinstance(msg, dict) or str(msg.get("messageId") or "") != mid:
                continue
            sender_id = str(msg.get("senderId") or "").strip()
            sender = str(msg.get("sender") or "").strip()
            if not sender_id or not sender:
                raise RuntimeError("群消息缺少发送者: " + json.dumps(msg, ensure_ascii=False))
            identity = self._identity_for(cid)
            self._cid_identity[cid] = identity
            return InboundMention(
                channel=self.name,
                event_id="catchup:" + mid,
                conversation_id=cid,
                message_id=mid,
                sender=sender,
                sender_id=sender_id,
                content=str(msg.get("text") or "").strip(),
                identity=identity,
            )
        raise RuntimeError(f"群消息里找不到 {mid}: " + json.dumps(data, ensure_ascii=False))

    async def _recycle_one(self, slot: _OrgListen, reason: str) -> None:
        ident = slot.identity
        self._listens.pop(ident, None)
        self._log("error", f"重建 {slot.corp_name} 的监听: {reason}")
        await self._teardown_one(slot)
        if slot.subscribe_id:
            try:
                await asyncio.to_thread(
                    _dws_json,
                    ["event", "stop", slot.subscribe_id, "--yes", "--format", "json"],
                    30,
                    None,
                    ident,
                )
            except Exception as exc:
                self._log("error", f"{slot.corp_name} 注销订阅 {slot.subscribe_id} 失败: {exc}")
        if slot.bus_pid:
            await asyncio.to_thread(kill_pid, slot.bus_pid, slot.corp_name + " 总线")
        fresh = _OrgListen(ident, slot.corp_name)
        await self._spawn_one(fresh)
        self._listens[ident] = fresh
        self._log("info", f"{slot.corp_name} 监听已重建")

    async def _supervisor(self) -> None:
        self._log("info", "保活已启动")
        try:
            while not self._stopping:
                try:
                    async with self._start_lock:
                        if self._stopping:
                            return
                        await self._sync_listens()
                        for slot in list(self._listens.values()):
                            try:
                                await self._assert_alive(slot)
                            except _LostMentions as exc:
                                slot.error = str(exc)
                                owners = {m.identity for m in exc.mentions}
                                if not owners & set(self._listens):
                                    owners = {slot.identity}
                                for ident in owners:
                                    victim = self._listens.get(ident)
                                    if victim is not None:
                                        await self._recycle_one(victim, str(exc))
                                for mention in exc.mentions:
                                    self._log("info", f"补处理漏掉的@ {mention.sender}: {mention.content or '[无正文]'}")
                                    asyncio.create_task(self._deliver(mention))
                            except Exception as exc:
                                slot.error = str(exc)
                                await self._recycle_one(slot, str(exc))
                        self._error = ""
                    await asyncio.sleep(15)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._error = str(exc)
                    self._log("error", f"保活失败: {exc}")
                    if self._stopping:
                        return
                    await asyncio.sleep(3)
        except asyncio.CancelledError:
            raise

    async def _wait_ready(self, slot: _OrgListen) -> None:
        proc = slot.proc
        if proc is None or proc.stderr is None:
            raise RuntimeError("监听进程没有 stderr")
        deadline = time.monotonic() + 30
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError(slot.corp_name + " 监听未在 30 秒内 ready")
            line = await asyncio.wait_for(proc.stderr.readline(), timeout=30)
            if not line:
                code = proc.returncode
                raise RuntimeError(f"{slot.corp_name} 监听进程在 ready 前退出: {code}")
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text:
                self._log("stderr", text)
            if "[event] ready" in text:
                marker = "subscribe_id="
                if marker not in text:
                    raise RuntimeError(slot.corp_name + " ready 行没有 subscribe_id: " + text)
                slot.subscribe_id = text.split(marker, 1)[1].split()[0]
                bus_marker = "bus_pid="
                if bus_marker not in text:
                    raise RuntimeError(slot.corp_name + " ready 行没有 bus_pid: " + text)
                slot.bus_pid = int(text.split(bus_marker, 1)[1].split()[0])
                slot.ready = True
                asyncio.create_task(self._drain_stderr(slot))
                return

    async def _drain_stderr(self, slot: _OrgListen) -> None:
        proc = slot.proc
        if proc is None or proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text:
                self._log("stderr", text)

    def _mention(self, event: dict[str, Any], identity: str) -> InboundMention:
        mention = InboundMention(
            channel=self.name,
            event_id=_require_str(event, "event_id"),
            conversation_id=_require_str(event, "conversation_id"),
            message_id=_require_str(event, "message_id"),
            sender=_require_str(event, "sender"),
            sender_id=_require_str(event, "sender_open_dingtalk_id"),
            content=str(event.get("content") or "").strip(),
            identity=identity,
        )
        self._cid_identity[mention.conversation_id] = identity
        return mention

    async def _read_events(self, slot: _OrgListen) -> None:
        proc = slot.proc
        if proc is None or proc.stdout is None:
            raise RuntimeError("监听进程没有 stdout")
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    code = proc.returncode
                    raise RuntimeError(f"{slot.corp_name} 监听进程结束: {code}")
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text:
                    continue
                event = json.loads(text)
                if not isinstance(event, dict):
                    raise RuntimeError("事件不是对象: " + text)
                mid = str(event.get("message_id") or "").strip()
                if mid and not self._mark_seen(mid):
                    self._log("info", f"{slot.corp_name} 事件重复，已处理过: {mid}")
                    continue
                asyncio.create_task(self._dispatch(event, slot.identity))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            slot.error = str(exc)
            slot.ready = False
            self._error = str(exc)
            self._log("error", str(exc))

    async def _dispatch(self, event: dict[str, Any], identity: str) -> None:
        try:
            mention = self._mention(event, identity)
        except RuntimeError as exc:
            self._log("error", str(exc))
            return
        await self._deliver(mention)

    async def _deliver(self, mention: InboundMention) -> None:
        try:
            if self._on_clarify(mention):
                return
            await self._on_mention(mention)
        except asyncio.CancelledError:
            self._log("info", "已打断当前任务")
        except Exception as exc:
            self._log("error", str(exc))

    async def _teardown_one(self, slot: _OrgListen) -> None:
        reader = slot.reader
        slot.reader = None
        if reader is not None:
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass
        proc = slot.proc
        slot.proc = None
        slot.ready = False
        if proc is not None:
            await graceful_stop(proc, slot.corp_name + " 监听")

    async def _teardown_all(self) -> None:
        for ident in list(self._listens):
            await self._teardown_one(self._listens.pop(ident))

    async def stop(self) -> None:
        self._stopping = True
        keep = self._keep_task
        self._keep_task = None
        if keep is not None:
            keep.cancel()
            try:
                await keep
            except asyncio.CancelledError:
                pass
        async with self._start_lock:
            await self._teardown_all()
            self._log("info", "已停止监听")
