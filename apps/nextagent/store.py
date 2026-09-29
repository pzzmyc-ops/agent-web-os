"""mafagent 会话持久化(JSONL)。

sessions/<thread_id>/events.jsonl 按区存放:
第一行 ui 区,第二行 plan 区,第三行 memory 区,第四行 ctx 区,其后是上下文事件。
上下文只追加;ui、plan、memory、ctx 各只有一份,写入时覆盖该区。
例外是压缩:它会把上下文区整段重写成摘要 + 尾段(见 compaction.py)。
事件词汇表和投影规则在 session_log.py。
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import session_log
from .maf_tools.timer_registry import timer_registry

APP_FEISHU_WEBHOOK = "feishu_webhook"
_WEBHOOK_PREFIX = "https://open.feishu.cn/open-apis/bot/v2/hook/"


def _app_name(app: str) -> str:
    name = str(app or "").strip()
    if name and name != APP_FEISHU_WEBHOOK:
        raise RuntimeError("未知应用: " + name)
    return name


def _now_ms() -> int:
    return int(time.time() * 1000)


def _thread_rank(thread: dict) -> int:
    sort = thread.get("sort")
    if isinstance(sort, int) and not isinstance(sort, bool):
        return sort
    return int(thread.get("updated_at") or thread.get("created_at") or 0)


def _new_id() -> str:
    return uuid.uuid4().hex


def _is_ui_record(obj: dict) -> bool:
    return str(obj.get("zone") or "") == "ui" or str(obj.get("type") or "") == session_log.EV_SESSION_UI


def _is_plan_record(obj: dict) -> bool:
    return str(obj.get("zone") or "") == "plan" or str(obj.get("type") or "") == session_log.EV_PLAN_DOC


def _is_memory_record(obj: dict) -> bool:
    return str(obj.get("zone") or "") == "memory" or str(obj.get("type") or "") == session_log.EV_SESSION_MEMORY


def _is_ctx_record(obj: dict) -> bool:
    return str(obj.get("zone") or "") == "ctx" or str(obj.get("type") or "") == session_log.EV_SESSION_CTX


def _is_context_record(obj: dict) -> bool:
    return not (
        _is_ui_record(obj) or _is_plan_record(obj) or _is_memory_record(obj) or _is_ctx_record(obj)
    )


def _ctx_state(rec: dict | None) -> dict:
    """ctx 区 → {window, used, compactions}。

    window 是用户为这个对话设定的上下文窗口(token);0 表示还没设过,用全局默认。
    used 是上一次调用时上游回报的真实输入 token 数,压缩阈值就按它判。
    """
    if rec is None:
        return {"window": 0, "used": 0, "compactions": 0}
    return {
        "window": int(rec.get("window") or 0),
        "used": int(rec.get("used") or 0),
        "compactions": int(rec.get("compactions") or 0),
    }


def _memory_text(rec: dict | None) -> str:
    if rec is None:
        return ""
    if "text" not in rec:
        return ""
    text = rec["text"]
    if not isinstance(text, str):
        raise RuntimeError("session memory text must be a string")
    return text


def _plan_slot(rec: dict | None) -> dict | None:
    if rec is None:
        return None
    text = str(rec.get("plan") or "").strip()
    todos = rec.get("todos")
    if not text or not isinstance(todos, list):
        return None
    pid = str(rec.get("plan_id") or rec.get("id") or "").strip()
    if not pid:
        raise RuntimeError("plan_doc missing plan_id")
    return {
        "plan": text,
        "todos": list(todos),
        "phase": str(rec.get("phase") or ""),
        "plan_id": pid,
        "review_block_id": str(rec.get("review_block_id") or ""),
        "source": str(rec.get("source") or "agent"),
        "updated_at": int(rec.get("updated_at") or rec.get("created_at") or _now_ms()),
        "task_id": str(rec.get("task_id") or ""),
    }


def _split_session(rows: list[dict]) -> tuple[dict, dict | None, str, dict, list[dict]]:
    ui: dict = {}
    plan = None
    memory = ""
    ctx = _ctx_state(None)
    events: list[dict] = []
    for obj in rows:
        if not isinstance(obj, dict):
            raise RuntimeError("session record must be an object")
        if _is_ui_record(obj):
            raw = obj.get("ui")
            if raw is None:
                ui = {}
            elif isinstance(raw, dict):
                ui = dict(raw)
            else:
                raise RuntimeError("session ui must be an object")
            continue
        if _is_plan_record(obj):
            slot = _plan_slot(obj)
            if slot is not None:
                plan = slot
            continue
        if _is_memory_record(obj):
            memory = _memory_text(obj)
            continue
        if _is_ctx_record(obj):
            ctx = _ctx_state(obj)
            continue
        events.append(obj)
    return ui, plan, memory, ctx, events


class Store:
    DEFAULT_ROLE_NAME = "助手"
    DEFAULT_ROLE_AVATAR = "助"

    def __init__(self, sessions_dir: str = "sessions"):
        self._root = Path(sessions_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # 下一个事件 id。只追加写就不能靠「读全文取最大值」来定 id,那等于每次追加
        # 都读一遍全文。首次追加时扫一次,之后在内存里递增;重写文件的两条路径负责失效。
        self._next_ids: dict[str, int] = {}
        self._checkpoints = None

    @property
    def root(self) -> Path:
        return self._root

    @property
    def checkpoints(self):
        """文件检查点,存在 sessions/<thread_id>/checkpoints/ 下,删会话时随目录一起删。"""
        if self._checkpoints is None:
            from .checkpoints import Checkpoints

            self._checkpoints = Checkpoints(self._root)
        return self._checkpoints

    # ── helpers ──

    def _index_path(self) -> Path:
        return self._root / "index.json"

    def _roles_path(self) -> Path:
        return self._root / "roles.json"

    def _thread_dir(self, thread_id: str) -> Path:
        return self._root / thread_id

    def _events_path(self, thread_id: str) -> Path:
        return self._thread_dir(thread_id) / "events.jsonl"

    def _read_index(self) -> dict:
        p = self._index_path()
        if not p.is_file():
            return {"current": "", "threads": [], "folders": []}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {"current": "", "threads": [], "folders": []}
        if not isinstance(data.get("folders"), list):
            data["folders"] = []
        return data

    def _write_index(self, data: dict) -> None:
        self._index_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _read_roles(self) -> dict:
        p = self._roles_path()
        if not p.is_file():
            return {"roles": [], "active": {}}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {"roles": [], "active": {}}

    def _write_roles(self, data: dict) -> None:
        self._roles_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _read_records(self, thread_id: str) -> list[dict]:
        p = self._events_path(thread_id)
        if not p.is_file():
            return []
        rows: list[dict] = []
        for n, line in enumerate(p.read_text(encoding="utf-8").split("\n"), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"事件日志第 {n} 行不是合法 JSON: {p}") from exc
        return rows

    def _session_state(self, thread_id: str) -> tuple[dict, dict | None, str, dict, list[dict]]:
        return _split_session(self._read_records(thread_id))

    def load_events(self, thread_id: str) -> list[dict]:
        return self._session_state(thread_id)[4]

    def _write_session(
        self, thread_id: str, ui: dict, plan: dict | None, memory: str, ctx: dict, events: list[dict]
    ) -> None:
        self._require_thread(thread_id)
        if not isinstance(ui, dict):
            raise RuntimeError("session ui must be an object")
        if not isinstance(memory, str):
            raise RuntimeError("session memory must be a string")
        if not isinstance(ctx, dict):
            raise RuntimeError("session ctx must be an object")
        self._thread_dir(thread_id).mkdir(parents=True, exist_ok=True)
        slot = _plan_slot(plan) if plan is not None else None
        plan_rec = {"zone": "plan"}
        if slot is not None:
            plan_rec = {"zone": "plan", **slot}
        mem_rec = {"zone": "memory"}
        if memory:
            mem_rec["text"] = memory
        rows = [{"zone": "ui", "ui": dict(ui)}, plan_rec, mem_rec, {"zone": "ctx", **_ctx_state(ctx)}]
        for ev in events:
            if not isinstance(ev, dict):
                raise RuntimeError("context event must be an object")
            if not _is_context_record(ev):
                raise RuntimeError("context event cannot be a session zone record")
            rows.append(ev)
        lines = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in rows)
        self._events_path(thread_id).write_text(lines, encoding="utf-8")
        self._next_ids.pop(thread_id, None)

    def _thread_listed(self, thread_id: str) -> bool:
        return any(t["id"] == thread_id for t in self._read_index()["threads"])

    def _require_thread(self, thread_id: str) -> None:
        if not self._thread_listed(thread_id):
            raise FileNotFoundError(f"会话已删除: {thread_id}")

    def _rewrite_events(self, thread_id: str, events: list[dict]) -> None:
        ui, plan, memory, ctx, _ = self._session_state(thread_id)
        self._write_session(thread_id, ui, plan, memory, ctx, events)

    def _touch_thread(self, thread_id: str) -> None:
        idx = self._read_index()
        for t in idx["threads"]:
            if t["id"] == thread_id:
                t["updated_at"] = _now_ms()
                break
        self._write_index(idx)

    # ── threads ──

    def list_threads(self) -> list[dict]:
        idx = self._read_index()
        threads = sorted(idx["threads"], key=_thread_rank, reverse=True)
        return [
            {
                "id": t["id"],
                "title": t["title"],
                "updatedAt": t.get("updated_at") or t.get("created_at") or 0,
                "folderId": t.get("folder_id") or "",
                "app": str(t.get("app") or ""),
                "feishuWebhookBound": self._webhook_bound(t["id"], str(t.get("app") or "")),
            }
            for t in threads
        ]

    def thread_exists(self, thread_id: str) -> bool:
        return self._events_path(thread_id).is_file() or self._thread_dir(thread_id).is_dir()

    def _webhook_path(self, thread_id: str) -> Path:
        return self._thread_dir(thread_id) / "feishu_webhook.json"

    def _webhook_bound(self, thread_id: str, app: str) -> bool:
        if app != APP_FEISHU_WEBHOOK:
            return False
        return bool(self.feishu_webhook_url(thread_id))

    def thread_app(self, thread_id: str) -> str:
        idx = self._read_index()
        for t in idx["threads"]:
            if t["id"] == thread_id:
                return str(t.get("app") or "")
        raise RuntimeError("对话不存在")

    def feishu_webhook_url(self, thread_id: str) -> str:
        path = self._webhook_path(thread_id)
        if not path.is_file():
            return ""
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise RuntimeError("飞书 webhook 文件损坏")
        url = str(raw.get("url") or "").strip()
        if not url:
            raise RuntimeError("飞书 webhook 文件没有地址")
        return url

    def set_feishu_webhook(self, thread_id: str, url: str) -> str:
        if self.thread_app(thread_id) != APP_FEISHU_WEBHOOK:
            raise RuntimeError("当前对话不是飞书 webhook 应用")
        text = str(url or "").strip()
        if not text:
            raise RuntimeError("飞书 webhook 地址不能为空")
        if not text.startswith(_WEBHOOK_PREFIX):
            raise RuntimeError("飞书 webhook 地址格式不对")
        token = text[len(_WEBHOOK_PREFIX):]
        if not token or "/" in token or " " in token:
            raise RuntimeError("飞书 webhook 地址格式不对")
        path = self._webhook_path(thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"url": text}, ensure_ascii=False), encoding="utf-8")
        self._touch_thread(thread_id)
        return text

    def create_thread(self, title: str = "新对话", folder_id: str = "", app: str = "") -> str:
        tid = _new_id()
        now = _now_ms()
        idx = self._read_index()
        fid = str(folder_id or "").strip()
        app_name = _app_name(app)
        if fid:
            folder = next((f for f in idx["folders"] if f["id"] == fid), None)
            if folder is None:
                raise RuntimeError("文件夹不存在")
            if str(folder.get("app") or "") != app_name:
                raise RuntimeError("文件夹不属于当前应用")
        row = {"id": tid, "title": title, "created_at": now, "updated_at": now}
        if fid:
            row["folder_id"] = fid
        if app_name:
            row["app"] = app_name
        idx["threads"].append(row)
        if not idx["current"]:
            idx["current"] = tid
        self._write_index(idx)
        self._thread_dir(tid).mkdir(parents=True, exist_ok=True)
        lines = (
            json.dumps({"zone": "ui", "ui": {}}, ensure_ascii=False)
            + "\n"
            + json.dumps({"zone": "plan"}, ensure_ascii=False)
            + "\n"
            + json.dumps({"zone": "memory"}, ensure_ascii=False)
            + "\n"
            + json.dumps({"zone": "ctx", **_ctx_state(None)}, ensure_ascii=False)
            + "\n"
        )
        self._events_path(tid).write_text(lines, encoding="utf-8")
        self._next_ids[tid] = 1
        # 卡槽留空:新对话默认走单聊(run_turn)。要群聊由用户自己把角色拉进来,
        # 卡槽非空才会切到 handoff 编排。
        return tid

    def rename_thread(self, thread_id: str, title: str) -> None:
        text = str(title or "").strip()
        if not text:
            raise RuntimeError("对话名称为空")
        idx = self._read_index()
        for t in idx["threads"]:
            if t["id"] == thread_id:
                t["title"] = text
                t["updated_at"] = _now_ms()
                self._write_index(idx)
                return
        raise RuntimeError("对话不存在")

    def delete_thread(self, thread_id: str) -> None:
        timer_registry.cancel_for_thread(thread_id)
        idx = self._read_index()
        idx["threads"] = [t for t in idx["threads"] if t["id"] != thread_id]
        if idx["current"] == thread_id:
            remaining = sorted(
                idx["threads"],
                key=lambda t: (t.get("updated_at", 0), t.get("created_at", 0)),
                reverse=True,
            )
            idx["current"] = remaining[0]["id"] if remaining else ""
        self._write_index(idx)
        roles = self._read_roles()
        roles["active"].pop(thread_id, None)
        self._write_roles(roles)
        td = self._thread_dir(thread_id)
        if td.is_dir():
            shutil.rmtree(str(td))

    def thread_title(self, thread_id: str) -> str:
        idx = self._read_index()
        for t in idx["threads"]:
            if t["id"] == thread_id:
                return t.get("title", "")
        return ""

    def count_threads(self) -> int:
        return len(self._read_index()["threads"])

    # ── current thread pointer ──

    def current_thread(self) -> str:
        idx = self._read_index()
        cur = idx.get("current", "")
        if cur and any(t["id"] == cur for t in idx["threads"]):
            return cur
        # fallback
        threads = sorted(
            idx["threads"],
            key=lambda t: (t.get("updated_at", 0), t.get("created_at", 0)),
            reverse=True,
        )
        return threads[0]["id"] if threads else ""

    def set_current(self, thread_id: str) -> None:
        idx = self._read_index()
        idx["current"] = thread_id
        self._write_index(idx)

    def ensure_initial_thread(self) -> str:
        if self.count_threads() == 0:
            return self.create_thread()
        cur = self.current_thread()
        return cur or self.create_thread()

    def list_folders(self) -> list[dict]:
        folders = sorted(
            self._read_index()["folders"],
            key=lambda f: (f.get("created_at", 0), f.get("name", "")),
        )
        return [
            {
                "id": f["id"],
                "name": f["name"],
                "updatedAt": f.get("updated_at") or f.get("created_at") or 0,
                "app": str(f.get("app") or ""),
            }
            for f in folders
        ]

    def create_folder(self, name: str, app: str = "") -> dict:
        text = self._folder_name(name)
        app_name = _app_name(app)
        now = _now_ms()
        fid = _new_id()
        row = {"id": fid, "name": text, "created_at": now, "updated_at": now}
        if app_name:
            row["app"] = app_name
        idx = self._read_index()
        idx["folders"].append(row)
        self._write_index(idx)
        return {"id": fid, "name": text, "updatedAt": now, "app": app_name}

    def rename_folder(self, folder_id: str, name: str) -> dict:
        text = self._folder_name(name)
        idx = self._read_index()
        for folder in idx["folders"]:
            if folder["id"] == folder_id:
                folder["name"] = text
                folder["updated_at"] = _now_ms()
                self._write_index(idx)
                return {
                    "id": folder["id"],
                    "name": folder["name"],
                    "updatedAt": folder["updated_at"],
                    "app": str(folder.get("app") or ""),
                }
        raise RuntimeError("文件夹不存在")

    def delete_folder(self, folder_id: str) -> None:
        idx = self._read_index()
        before = len(idx["folders"])
        idx["folders"] = [f for f in idx["folders"] if f["id"] != folder_id]
        if len(idx["folders"]) == before:
            raise RuntimeError("文件夹不存在")
        for thread in idx["threads"]:
            if thread.get("folder_id") == folder_id:
                thread.pop("folder_id", None)
        self._write_index(idx)

    def set_thread_folder(self, thread_id: str, folder_id: str) -> None:
        idx = self._read_index()
        fid = str(folder_id or "").strip()
        folder = next((f for f in idx["folders"] if f["id"] == fid), None) if fid else None
        if fid and folder is None:
            raise RuntimeError("文件夹不存在")
        found = False
        for thread in idx["threads"]:
            if thread["id"] != thread_id:
                continue
            if fid and str(folder.get("app") or "") != str(thread.get("app") or ""):
                raise RuntimeError("不能把对话放到其他应用的分组")
            if fid:
                thread["folder_id"] = fid
            else:
                thread.pop("folder_id", None)
            thread["updated_at"] = _now_ms()
            found = True
            break
        if not found:
            raise FileNotFoundError(f"会话已删除: {thread_id}")
        self._write_index(idx)

    def place_thread(self, thread_id: str, anchor_id: str, *, after: bool) -> None:
        if thread_id == anchor_id:
            return
        idx = self._read_index()
        moving = next((t for t in idx["threads"] if t["id"] == thread_id), None)
        anchor = next((t for t in idx["threads"] if t["id"] == anchor_id), None)
        if moving is None or anchor is None:
            raise FileNotFoundError(f"会话已删除: {thread_id if moving is None else anchor_id}")
        if str(moving.get("app") or "") != str(anchor.get("app") or ""):
            raise RuntimeError("不能把对话放到其他应用的分组")
        fid = str(anchor.get("folder_id") or "")
        if fid:
            folder = next((f for f in idx["folders"] if f["id"] == fid), None)
            if folder is None:
                raise RuntimeError("文件夹不存在")
            if str(folder.get("app") or "") != str(moving.get("app") or ""):
                raise RuntimeError("不能把对话放到其他应用的分组")
            moving["folder_id"] = fid
        else:
            moving.pop("folder_id", None)
        app = str(moving.get("app") or "")
        siblings = [
            t for t in idx["threads"]
            if str(t.get("folder_id") or "") == fid and str(t.get("app") or "") == app
        ]
        siblings.sort(key=_thread_rank, reverse=True)
        siblings = [t for t in siblings if t["id"] != moving["id"]]
        pos = next(i for i, t in enumerate(siblings) if t["id"] == anchor_id)
        if after:
            pos += 1
        siblings.insert(pos, moving)
        top = max(_thread_rank(t) for t in siblings) + len(siblings)
        for i, thread in enumerate(siblings):
            thread["sort"] = top - i
        self._write_index(idx)

    def _folder_name(self, name: str) -> str:
        text = str(name or "").strip()
        if not text:
            raise RuntimeError("文件夹名称为空")
        if "/" in text or "\\" in text:
            raise RuntimeError("文件夹名称不能包含路径分隔符")
        return text

    # ── events / messages ──

    def append_event(self, thread_id: str, event_type: str, *, task_id: str = "", **fields) -> int:
        if event_type in (
            session_log.EV_PLAN_DOC,
            session_log.EV_SESSION_UI,
            session_log.EV_SESSION_MEMORY,
            session_log.EV_SESSION_CTX,
        ):
            raise RuntimeError("ui, plan, memory and ctx belong in session zones, not context events")
        with self._lock:
            self._require_thread(thread_id)
            eid = self._next_ids.get(thread_id)
            if eid is None:
                eid = max(
                    (int(e.get("id") or 0) for e in self.load_events(thread_id)), default=0
                ) + 1
            self._next_ids[thread_id] = eid + 1
            event = {
                "id": eid,
                "type": event_type,
                "task_id": task_id or "",
                "created_at": _now_ms(),
                **fields,
            }
            if not _is_context_record(event):
                raise RuntimeError("context event cannot be a ui, plan or memory record")
            self._thread_dir(thread_id).mkdir(parents=True, exist_ok=True)
            with self._events_path(thread_id).open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._touch_thread(thread_id)
            return eid

    def first_user_text(self, thread_id: str) -> str:
        for e in self.load_events(thread_id):
            if e.get("type") == session_log.EV_USER:
                return e.get("content") or ""
        return ""

    def delete_events_after(self, thread_id: str, after_event_id: int) -> int:
        """截掉 id 大于 after_event_id 的事件。编辑重发用它把后续对话砍掉。"""
        with self._lock:
            events = self.load_events(thread_id)
            keep = [e for e in events if int(e.get("id") or 0) <= after_event_id]
            self._rewrite_events(thread_id, keep)
            self._touch_thread(thread_id)
            return len(events) - len(keep)

    def event_id_by_checkpoint(self, thread_id: str, checkpoint_id: str) -> int:
        """带这个 checkpoint_id 的事件(用户消息或压缩摘要)的 id。找不到就抛。"""
        for e in self.load_events(thread_id):
            if str(e.get("checkpoint_id") or "") == checkpoint_id:
                return int(e.get("id") or 0)
        raise RuntimeError(f"没有事件挂着检查点 {checkpoint_id}")

    def update_event_content(self, thread_id: str, event_id: int, content: str) -> bool:
        """改写一条事件的正文。编辑重发改提问用它 —— 这是明确的破坏性改写。"""
        with self._lock:
            events = self.load_events(thread_id)
            for e in events:
                if int(e.get("id") or 0) == event_id:
                    e["content"] = content or ""
                    self._rewrite_events(thread_id, events)
                    return True
            return False

    def nth_record_of_role(self, thread_id: str, role: str, n: int) -> dict | None:
        return session_log.nth_record_of_role(self.load_events(thread_id), role, n)

    def recover_unpaired_calls(self, thread_id: str) -> int:
        """给「有调用没结果」的工具补一条说明中断的 tool_result 事件。

        进程在回合中途被杀时,close_open 没机会跑,日志里就留下半边调用。半边调用发回
        模型上游会直接 400,所以必须补齐。补的是一条真实事件 —— 「这次调用没有结果」
        本来就发生过,它该在日志里占一行,而不是每次读历史的时候临时圆一遍。
        """
        orphans = session_log.unpaired_calls(self.load_events(thread_id))
        for ev in orphans:
            self.append_event(
                thread_id,
                session_log.EV_TOOL_RESULT,
                task_id=str(ev.get("task_id") or ""),
                call_id=str(ev.get("call_id") or ""),
                name=str(ev.get("name") or ""),
                result="未收到工具结果(服务进程在回合中途退出)",
                status="error",
                duration_ms=None,
                completed_at=_now_ms(),
            )
        return len(orphans)

    def task_payload(self, thread_id: str, task_id: str) -> dict:
        """一个 task 已落盘的正文/思考/工具块。前端拉快照用。"""
        return session_log.task_snapshot(self.load_events(thread_id), task_id)

    # ── roles ──

    def list_roles(self) -> list[dict]:
        return list(self._read_roles()["roles"])

    def get_role(self, role_id: str) -> dict | None:
        for r in self._read_roles()["roles"]:
            if r["id"] == role_id:
                return dict(r)
        return None

    def create_role(self, name: str, avatar: str = "", system_prompt: str = "") -> str:
        rid = _new_id()
        now = _now_ms()
        role = {
            "id": rid,
            "name": name,
            "avatar": avatar,
            "system_prompt": system_prompt,
            "created_at": now,
            "updated_at": now,
        }
        data = self._read_roles()
        data["roles"].append(role)
        self._write_roles(data)
        return rid

    def ensure_default_role(self, default_prompt: str = "") -> str:
        """确保角色库中至少有一个默认角色,返回其 id。
        如果已有匹配 DEFAULT_ROLE_NAME 的就复用,否则新建。"""
        for r in self.list_roles():
            if (r.get("name") or "").strip() == self.DEFAULT_ROLE_NAME:
                return r["id"]
        return self.create_role(self.DEFAULT_ROLE_NAME, self.DEFAULT_ROLE_AVATAR, default_prompt)

    def update_role(self, role_id: str, name: str, avatar: str = "", system_prompt: str = "") -> bool:
        data = self._read_roles()
        for r in data["roles"]:
            if r["id"] == role_id:
                r["name"] = name
                r["avatar"] = avatar
                r["system_prompt"] = system_prompt
                r["updated_at"] = _now_ms()
                self._write_roles(data)
                return True
        return False

    def delete_role(self, role_id: str) -> bool:
        data = self._read_roles()
        before = len(data["roles"])
        data["roles"] = [r for r in data["roles"] if r["id"] != role_id]
        # 也从所有线程的活跃角色中移除
        for tid in list(data.get("active", {}).keys()):
            data["active"][tid] = [rid for rid in data["active"][tid] if rid != role_id]
            if not data["active"][tid]:
                del data["active"][tid]
        self._write_roles(data)
        return len(data["roles"]) < before

    # ── active roles (per thread) ──

    def add_active_role(self, thread_id: str, role_id: str) -> None:
        data = self._read_roles()
        data.setdefault("active", {})
        data["active"].setdefault(thread_id, [])
        if role_id not in data["active"][thread_id]:
            data["active"][thread_id].append(role_id)
        self._write_roles(data)

    def remove_active_role(self, thread_id: str, role_id: str) -> bool:
        """从对话中移除一个角色。卡槽允许完全清空(返回 True)。
        返回 False 表示角色本来就不在场。"""
        data = self._read_roles()
        if thread_id not in data.get("active", {}):
            return False
        if role_id not in data["active"][thread_id]:
            return False
        data["active"][thread_id] = [rid for rid in data["active"][thread_id] if rid != role_id]
        if not data["active"][thread_id]:
            del data["active"][thread_id]
        self._write_roles(data)
        return True

    def get_active_roles(self, thread_id: str) -> list[dict]:
        data = self._read_roles()
        active_ids = data.get("active", {}).get(thread_id, [])
        all_roles = {r["id"]: r for r in data["roles"]}
        result = []
        for rid in active_ids:
            r = all_roles.get(rid)
            if r:
                result.append({
                    "id": r["id"],
                    "name": r["name"],
                    "avatar": r.get("avatar", ""),
                    "system_prompt": r.get("system_prompt", ""),
                })
        return sorted(result, key=lambda r: r["name"])

    # ── frontend history events ──

    def load_plan_doc(self, thread_id: str) -> dict | None:
        from .plan_mode import public_plan_doc

        return public_plan_doc(self._session_state(thread_id)[1])

    def save_plan_doc(self, thread_id: str, rec: dict) -> None:
        slot = _plan_slot(rec)
        if slot is None:
            raise RuntimeError("save_plan_doc requires plan and todos")
        with self._lock:
            ui, _, memory, ctx, events = self._session_state(thread_id)
            self._write_session(thread_id, ui, slot, memory, ctx, events)
            self._touch_thread(thread_id)

    def clear_plan_doc(self, thread_id: str) -> None:
        with self._lock:
            ui, _, memory, ctx, events = self._session_state(thread_id)
            self._write_session(thread_id, ui, None, memory, ctx, events)
            self._touch_thread(thread_id)

    def load_plan_slot(self, thread_id: str) -> dict | None:
        """plan 区原样的槽位(不经 public 投影),检查点拍快照用。"""
        return self._session_state(thread_id)[1]

    def restore_plan_slot(self, thread_id: str, slot: dict | None) -> None:
        """把 plan 区整个换成检查点里记的那份(None 表示当时没有计划)。"""
        with self._lock:
            ui, _, memory, ctx, events = self._session_state(thread_id)
            self._write_session(thread_id, ui, slot, memory, ctx, events)
            self._touch_thread(thread_id)

    def load_session_ui(self, thread_id: str) -> dict:
        return dict(self._session_state(thread_id)[0])

    def agent_speak_on(self, thread_id: str) -> bool:
        return self.load_session_ui(thread_id).get("agentSpeak", True) is not False

    def media_passthrough_on(self, thread_id: str) -> bool:
        return bool(self.load_session_ui(thread_id).get("mediaPassthrough"))

    def selected_chat_model(self, thread_id: str) -> str:
        from .config import resolve_default_chat_model

        mid = str(self.load_session_ui(thread_id).get("agentProfile") or "").strip()
        resolved = resolve_default_chat_model(mid)[0]
        if resolved != mid:
            self.apply_selected_chat_model(thread_id, resolved)
        return resolved

    def apply_selected_chat_model(self, thread_id: str, model: str) -> None:
        from .config import resolve_default_chat_model

        mid = resolve_default_chat_model((model or "").strip())[0]
        ui = self.load_session_ui(thread_id)
        if str(ui.get("agentProfile") or "").strip() == mid:
            return
        next_ui = dict(ui)
        next_ui["agentProfile"] = mid
        self.save_session_ui(thread_id, next_ui)

    def save_session_ui(self, thread_id: str, ui: dict) -> None:
        if not isinstance(ui, dict):
            raise RuntimeError("session_ui must be an object")
        with self._lock:
            _, plan, memory, ctx, events = self._session_state(thread_id)
            self._write_session(thread_id, dict(ui), plan, memory, ctx, events)
            self._touch_thread(thread_id)

    def load_memory(self, thread_id: str) -> str:
        return self._session_state(thread_id)[2]

    def save_memory(self, thread_id: str, text: str) -> None:
        if not isinstance(text, str):
            raise RuntimeError("session memory must be a string")
        with self._lock:
            ui, plan, _, ctx, events = self._session_state(thread_id)
            self._write_session(thread_id, ui, plan, text, ctx, events)
            self._touch_thread(thread_id)

    # ── ctx 区:上下文窗口设置 + 上游回报的真实用量 ──

    def load_ctx_state(self, thread_id: str) -> dict:
        return dict(self._session_state(thread_id)[3])

    def save_ctx_state(self, thread_id: str, **fields) -> dict:
        """局部更新 ctx 区。只传要改的字段,其余保持原值。"""
        with self._lock:
            ui, plan, memory, ctx, events = self._session_state(thread_id)
            for key in ("window", "used", "compactions"):
                if key in fields:
                    ctx[key] = int(fields[key])
            self._write_session(thread_id, ui, plan, memory, ctx, events)
            self._touch_thread(thread_id)
            return dict(ctx)

    def replace_context_events(self, thread_id: str, events: list[dict]) -> None:
        """把整个上下文区换成新的一批事件。压缩专用,旧事件不再保留。"""
        with self._lock:
            self._rewrite_events(thread_id, events)
            self._touch_thread(thread_id)

    def to_history_events(self, thread_id: str) -> list[dict]:
        return session_log.to_frontend_events(self.load_events(thread_id))


