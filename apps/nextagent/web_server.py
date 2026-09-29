"""mafagent Web 层:REST + WebSocket,作为组件装进宿主 app。

范围:对话列表 + 对话框 + 角色。资产库/媒体/技能/工具不实现(前端对缺失容错)。
鉴权:自动登录单用户(见 identity.py)。

不再是独立入口 —— 由 server.py 调用 install_agent(app) 装进文件管理器的
FastAPI app,单进程运行。路由前缀 /api/v1/*、/api/chat/*、/workspace/*、
/media/*,与文件管理器的 /api/{list,transfer,onlyoffice} 不重叠;agent 的
首页在 /agent(根路径 "/" 属于文件管理器的桌面 shell)。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .agent_core import INSTRUCTIONS_BASE, build_web_agent
from .config import load_chat_models, load_config
from .dingtalk_bot import DingTalkBot
from .identity import current_user
from .store import Store
from .toolkit import describe_all, registry as tool_registry
from .turn_event_bus import bus as event_bus
from .ws_chat import router as ws_router

ROOT = Path(__file__).resolve().parent

cfg = load_config()
# sessions 存在文件管理器根目录下,和用户工作文件在一起
store = Store(str(Path(cfg.workspace_dir) / "sessions"))
store.ensure_default_role(INSTRUCTIONS_BASE)
web_agent = build_web_agent(cfg, store)
# 上次进程是不是死在某个回合中途:有调用没结果的工具在这里补齐说明中断的结果事件。
# 不补的话下一轮把半边调用发给上游会被判 400,整个会话从此发不出话。
for _t in store.list_threads():
    store.recover_unpaired_calls(_t["id"])

dingtalk_bot = DingTalkBot(
    store,
    web_agent,
    Path(cfg.workspace_dir) / "sessions" / "dingtalk_bot.json",
    Path(cfg.fm_root_dir),
    cfg.model,
)

router = APIRouter()


def install_agent(app: FastAPI) -> None:
    """把 agent 的 REST + WS + 静态资源装进宿主 app。

    必须在文件管理器挂 "/" catch-all 之前调用。
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(_no_cache)
    app.middleware("http")(_gateway_identity)
    app.state.store = store
    app.state.web_agent = web_agent
    app.state.cfg = cfg
    app.state.dingtalk_bot = dingtalk_bot
    app.include_router(router)
    app.include_router(ws_router)
    app.mount("/static", StaticFiles(directory=str(ROOT / "static"), html=False), name="static")
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def _lifespan(host):
        async with inner(host):
            await dingtalk_bot.start()
            try:
                yield
            finally:
                await dingtalk_bot.stop()

    app.router.lifespan_context = _lifespan


def _load_models() -> list[dict]:
    """前端模型下拉的数据。没有 adapter 时返回空,前端据此显示"无可用模型"。

    不再用 cfg.model 兜底:配置里的默认模型不等于「有一个能用的 adapter」,
    硬塞进下拉会让界面在根本没有可用模型时还显示一个(点了也会失败)。
    """
    return load_chat_models()


# ---------------- static ----------------

async def _no_cache(request: Request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p in ("/", "/agent", "/dingtalk-bot", "/feishu-webhook") or p.endswith((".html", ".js", ".css", ".mjs")):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


def _request_api_key(request: Request) -> str:
    key = request.headers.get("x-api-key", "").strip()
    if key:
        return key
    auth = request.headers.get("authorization", "").strip()
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return auth


async def _gateway_identity(request: Request, call_next):
    """把调用者身份交给 gateway 记账。

    gateway 的 extract_meta 只从 request.state 读这两项,自己不认识宿主的身份模块
    (gateway/core/metadata.py) —— 依赖是单向的,注入这一环归宿主。不装这个中间件
    的话 usage_events 的 username / api_key 两列恒为空。
    """
    request.state.gateway_username = current_user()["userName"]
    request.state.gateway_api_key = _request_api_key(request)
    return await call_next(request)


@router.get("/agent")
async def index_page():
    """agent 的对话界面。作为文件管理器里的一个 app,由 iframe 打开。"""
    return HTMLResponse((ROOT / "index.html").read_text(encoding="utf-8"))


@router.get("/dingtalk-bot")
async def dingtalk_bot_page():
    return RedirectResponse("/agent?feishu_webhook=1")


@router.get("/feishu-webhook")
async def feishu_webhook_page():
    return RedirectResponse("/agent?feishu_webhook=1")


@router.get("/api/v1/dingtalk-bot/status")
async def dingtalk_bot_status():
    return JSONResponse(dingtalk_bot.status())


@router.post("/api/v1/dingtalk-bot/model")
async def dingtalk_bot_model(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise RuntimeError("请求体不是对象")
    dingtalk_bot.set_model(str(body.get("model") or ""))
    return JSONResponse(dingtalk_bot.status())


@router.post("/api/v1/dingtalk-bot/cancel")
async def dingtalk_bot_cancel():
    dingtalk_bot.cancel_turn()
    return JSONResponse(dingtalk_bot.status())


# ---------------- 身份(单用户,无登录) ----------------

@router.get("/api/v1/auth/me")
async def auth_me():
    return JSONResponse({"ok": True, "user": current_user()})


# ---------------- conversations ----------------

def _conv_list(cur: str) -> list[dict]:
    return dingtalk_bot.annotate_conversations(
        [
            {
                "id": t["id"],
                "title": t["title"],
                "updatedAt": t.get("updatedAt", 0),
                "folderId": t.get("folderId") or "",
                "active": (t["id"] == cur),
                "app": t.get("app") or "",
                "feishuWebhookBound": bool(t.get("feishuWebhookBound")),
            }
            for t in store.list_threads()
        ]
    )


def _conv_payload(cur: str, **extra) -> dict:
    data = {
        "conversations": _conv_list(cur),
        "folders": store.list_folders(),
        "currentId": cur,
    }
    data.update(extra)
    return data


@router.get("/api/v1/conversations")
async def list_conversations():
    cur = store.ensure_initial_thread()
    return JSONResponse(_conv_payload(cur))


@router.post("/api/v1/conversations")
async def create_conversation(request: Request):
    folder_id = ""
    app = ""
    raw = await request.body()
    if raw:
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict):
            raise RuntimeError("请求体不是对象")
        folder_id = str(body.get("folderId") or "").strip()
        app = str(body.get("app") or "").strip()
    tid = store.create_thread(folder_id=folder_id, app=app)
    store.set_current(tid)
    return JSONResponse(_conv_payload(tid, id=tid, title=store.thread_title(tid)))


@router.put("/api/v1/conversations/{thread_id}/feishu-webhook")
async def set_feishu_webhook(thread_id: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise RuntimeError("请求体不是对象")
    store.set_feishu_webhook(thread_id, str(body.get("url") or ""))
    return JSONResponse(
        _conv_payload(
            store.current_thread(),
            ok=True,
            id=thread_id,
            feishuWebhookBound=True,
        )
    )


@router.post("/api/v1/conversations/switch")
async def switch_conversation(threadId: str):
    if store.thread_exists(threadId):
        store.set_current(threadId)
    return JSONResponse({"ok": True, "currentId": store.current_thread()})


@router.patch("/api/v1/conversations/{thread_id}")
async def rename_conversation(thread_id: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise RuntimeError("请求体不是对象")
    store.rename_thread(thread_id, str(body.get("title") or ""))
    return JSONResponse(
        _conv_payload(store.current_thread(), ok=True, id=thread_id, title=store.thread_title(thread_id))
    )


@router.delete("/api/v1/conversations/{thread_id}")
async def delete_conversation(thread_id: str):
    if store.count_threads() <= 1:
        return JSONResponse({"ok": False, "error": "cannot delete last conversation"}, status_code=400)
    was_current = store.current_thread() == thread_id
    store.delete_thread(thread_id)
    if was_current:
        store.set_current(store.current_thread())
    cur = store.current_thread()
    return JSONResponse(_conv_payload(cur, ok=True))


@router.post("/api/v1/conversation-folders")
async def create_conversation_folder(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise RuntimeError("请求体不是对象")
    folder = store.create_folder(str(body.get("name") or ""), app=str(body.get("app") or ""))
    return JSONResponse(_conv_payload(store.current_thread(), folder=folder))


@router.patch("/api/v1/conversation-folders/{folder_id}")
async def rename_conversation_folder(folder_id: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise RuntimeError("请求体不是对象")
    folder = store.rename_folder(folder_id, str(body.get("name") or ""))
    return JSONResponse(_conv_payload(store.current_thread(), folder=folder))


@router.delete("/api/v1/conversation-folders/{folder_id}")
async def delete_conversation_folder(folder_id: str):
    store.delete_folder(folder_id)
    return JSONResponse(_conv_payload(store.current_thread(), ok=True))


@router.post("/api/v1/conversations/{thread_id}/folder")
async def move_conversation_folder(thread_id: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise RuntimeError("请求体不是对象")
    store.set_thread_folder(thread_id, str(body.get("folderId") or ""))
    return JSONResponse(_conv_payload(store.current_thread(), ok=True))


@router.post("/api/v1/conversations/{thread_id}/place")
async def place_conversation(thread_id: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise RuntimeError("请求体不是对象")
    anchor = str(body.get("anchorId") or "").strip()
    if not anchor:
        raise RuntimeError("缺少目标对话")
    store.place_thread(thread_id, anchor, after=bool(body.get("after")))
    return JSONResponse(_conv_payload(store.current_thread(), ok=True))


# ---------------- history ----------------

async def _running_task(thread_id: str) -> dict | None:
    """这个对话此刻正在跑的回合快照。

    「正在跑」只有内存总线知道 —— 还没落盘的那一段正文只存在于总线里。已经跑完的回合
    不算 inflight,它们由 /api/v1/history 的事件投影负责。
    """
    for task_id in event_bus.active_task_ids:
        snap = await event_bus.snapshot(task_id)
        if snap is None or snap.thread_id != thread_id:
            continue
        return {
            "taskId": task_id,
            "threadId": thread_id,
            "status": "running",
            "taskKind": "chat",
            "snapshotContent": snap.content,
            "snapshotReasoning": snap.reasoning,
            "snapshotSeq": snap.last_seq,
            "blocks": snap.blocks,
        }
    return None


@router.get("/api/v1/session-ui")
async def session_ui_get(threadId: str):
    if not store.thread_exists(threadId):
        raise RuntimeError("session-ui thread not found")
    return JSONResponse({"ok": True, "sessionUi": store.load_session_ui(threadId)})


@router.put("/api/v1/session-ui")
async def session_ui_put(request: Request):
    body = await request.json()
    thread_id = str(body.get("threadId") or "").strip()
    if not thread_id:
        raise RuntimeError("session-ui missing threadId")
    if not store.thread_exists(thread_id):
        raise RuntimeError("session-ui thread not found")
    ui = body.get("sessionUi")
    if not isinstance(ui, dict):
        raise RuntimeError("session-ui must be an object")
    store.save_session_ui(thread_id, ui)
    return JSONResponse({"ok": True, "sessionUi": store.load_session_ui(thread_id)})


@router.get("/api/v1/session-memory")
async def session_memory_get(threadId: str):
    if not store.thread_exists(threadId):
        raise RuntimeError("session-memory thread not found")
    return JSONResponse({"ok": True, "text": store.load_memory(threadId)})


@router.put("/api/v1/session-memory")
async def session_memory_put(request: Request):
    from .live_conns import broadcast

    body = await request.json()
    thread_id = str(body.get("threadId") or "").strip()
    if not thread_id:
        raise RuntimeError("session-memory missing threadId")
    if not store.thread_exists(thread_id):
        raise RuntimeError("session-memory thread not found")
    text = body.get("text")
    if not isinstance(text, str):
        raise RuntimeError("session-memory text must be a string")
    store.save_memory(thread_id, text)
    await broadcast({"type": "memory_changed", "data": {"threadId": thread_id}})
    return JSONResponse({"ok": True, "text": store.load_memory(thread_id)})


@router.get("/api/v1/session-context")
async def session_context_get(threadId: str):
    from . import compaction

    if not store.thread_exists(threadId):
        raise RuntimeError("session-context thread not found")
    return JSONResponse({
        "ok": True,
        "usage": compaction.usage_snapshot(store, threadId, web_agent.default_window),
        "defaultWindow": web_agent.default_window,
    })


@router.put("/api/v1/session-context")
async def session_context_put(request: Request):
    from . import compaction

    body = await request.json()
    thread_id = str(body.get("threadId") or "").strip()
    if not thread_id:
        raise RuntimeError("session-context missing threadId")
    if not store.thread_exists(thread_id):
        raise RuntimeError("session-context thread not found")
    window = int(body.get("window"))
    if window <= 0:
        raise RuntimeError("上下文窗口必须是正整数")
    store.save_ctx_state(thread_id, window=window)
    return JSONResponse({
        "ok": True,
        "usage": compaction.usage_snapshot(store, thread_id, web_agent.default_window),
        "defaultWindow": web_agent.default_window,
    })


async def _compact_exclusively(request: Request, runner) -> JSONResponse:
    """跑一次手动压缩,期间独占这个对话,且可被定时任务打断。

    压缩必须独占:摘要那次模型调用要跑几十秒,而压完是用 replace_context_events
    把整个上下文区重写一遍。这期间要是有人发消息起了新回合,新回合刚落盘的事件会被
    这次重写直接抹掉。所以在 ws_chat 那份「谁在占用这个对话」的登记表里占个位 ——
    发消息的入口查的就是它,会走和回合中一样的拒绝路径。

    压缩跑在一个单独的 task 里并登记到 _thread_runner_tasks,定时任务到点时
    interrupt_thread 才能真正 cancel 它。取消只会落在摘要那步的 await 上,
    replace_context_events 还没执行,上下文原样保留 —— 打断不会留下压了一半的上下文。
    """
    from . import compaction
    from .live_conns import broadcast
    from .ws_chat import (
        clear_cancelled_task,
        mark_thread_task,
        register_runner_task,
        thread_is_busy,
        unmark_thread_task,
        unregister_runner_task,
    )

    body = await request.json()
    thread_id = str(body.get("threadId") or "").strip()
    if not thread_id:
        raise RuntimeError("session-context missing threadId")
    if not store.thread_exists(thread_id):
        raise RuntimeError("session-context thread not found")
    if thread_is_busy(thread_id) or await _running_task(thread_id) is not None:
        raise RuntimeError("这个对话正在忙，等它跑完再压缩")

    task_id = "compact_" + uuid.uuid4().hex[:12]
    mark_thread_task(thread_id, task_id)
    compact_task = asyncio.ensure_future(
        runner(web_agent, thread_id, store.selected_chat_model(thread_id))
    )
    register_runner_task(thread_id, compact_task)
    await broadcast({"type": "context_compacting", "data": {"threadId": thread_id, "active": True}})
    interrupted = False
    stat = None
    try:
        stat = await compact_task
    except asyncio.CancelledError:
        interrupted = True
    finally:
        unregister_runner_task(thread_id, compact_task)
        unmark_thread_task(thread_id, task_id)
        clear_cancelled_task(task_id)
        await broadcast({
            "type": "context_compacting",
            "data": {"threadId": thread_id, "active": False},
        })
    if interrupted:
        return JSONResponse({
            "ok": False,
            "interrupted": True,
            "usage": compaction.usage_snapshot(store, thread_id, web_agent.default_window),
            "defaultWindow": web_agent.default_window,
        })
    await broadcast({"type": "context_compacted", "data": {"threadId": thread_id}})
    return JSONResponse({
        "ok": True,
        "stat": stat,
        "usage": compaction.usage_snapshot(store, thread_id, web_agent.default_window),
        "defaultWindow": web_agent.default_window,
    })


@router.post("/api/v1/session-context/compact")
async def session_context_compact(request: Request):
    """手动跑一次三段压缩。"""
    from . import compaction

    return await _compact_exclusively(request, compaction.compact_thread)


@router.post("/api/v1/session-context/force-compact")
async def session_context_force_compact(request: Request):
    """强力压缩:整场对话烧成一条摘要。只有这个接口能触发,自动压缩永远不走它。"""
    from . import compaction

    return await _compact_exclusively(request, compaction.force_compact_thread)


@router.get("/api/v1/retrieval/status")
async def retrieval_status():
    from .retrieval import retrieval_service

    return JSONResponse({"ok": True, "status": retrieval_service.status()})


@router.post("/api/v1/retrieval/rebuild")
async def retrieval_rebuild():
    from .live_conns import broadcast
    from .retrieval import retrieval_service
    from .retrieval.service import IndexBusy, OllamaUnavailable

    try:
        info = await retrieval_service.start_rebuild(broadcast)
    except IndexBusy as exc:
        return JSONResponse({"ok": False, "error": str(exc), "status": retrieval_service.status()}, status_code=409)
    except OllamaUnavailable as exc:
        return JSONResponse({"ok": False, "error": str(exc), "status": retrieval_service.status()}, status_code=503)
    return JSONResponse({"ok": True, "ollama": info, "status": retrieval_service.status()})


@router.post("/api/v1/retrieval/cancel")
async def retrieval_cancel():
    from .retrieval import retrieval_service

    cancelled = retrieval_service.cancel()
    return JSONResponse({"ok": True, "cancelled": cancelled, "status": retrieval_service.status()})


@router.post("/api/v1/retrieval/sources")
async def retrieval_add_source(request: Request):
    from .live_conns import broadcast
    from .retrieval import retrieval_service
    from .retrieval.service import IndexBusy, OllamaUnavailable

    body = await request.json()
    try:
        info = await retrieval_service.add_source(str(body.get("path") or ""), broadcast)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc), "status": retrieval_service.status()}, status_code=400)
    except IndexBusy as exc:
        return JSONResponse({"ok": False, "error": str(exc), "status": retrieval_service.status()}, status_code=409)
    except OllamaUnavailable as exc:
        return JSONResponse({"ok": False, "error": str(exc), "status": retrieval_service.status()}, status_code=503)
    return JSONResponse({"ok": True, "ollama": info, "status": retrieval_service.status()})


@router.post("/api/v1/retrieval/sources/remove")
async def retrieval_remove_source(request: Request):
    from .retrieval import retrieval_service
    from .retrieval.service import IndexBusy

    body = await request.json()
    try:
        removed = retrieval_service.remove_source(str(body.get("path") or ""))
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc), "status": retrieval_service.status()}, status_code=400)
    except IndexBusy as exc:
        return JSONResponse({"ok": False, "error": str(exc), "status": retrieval_service.status()}, status_code=409)
    return JSONResponse({"ok": True, "removed": removed, "status": retrieval_service.status()})


@router.post("/api/v1/plan-doc")
async def plan_doc_update(request: Request):
    from .plan_mode import apply_user_plan_update

    body = await request.json()
    thread_id = str(body.get("threadId") or "").strip()
    if not thread_id:
        raise RuntimeError("plan-doc missing threadId")
    if not store.thread_exists(thread_id):
        raise RuntimeError("plan-doc thread not found")
    doc = apply_user_plan_update(
        store,
        thread_id,
        plan_id=str(body.get("planId") or ""),
        plan=str(body.get("plan") or ""),
        todos=body.get("todos"),
    )
    return JSONResponse({"ok": True, "planDoc": doc})


@router.get("/api/v1/history")
async def history(threadId: str):
    events = store.to_history_events(threadId) if store.thread_exists(threadId) else []
    inflight_row = await _running_task(threadId) if store.thread_exists(threadId) else None
    inflight = [inflight_row] if inflight_row else []
    plan_doc = store.load_plan_doc(threadId) if store.thread_exists(threadId) else None
    session_ui = store.load_session_ui(threadId) if store.thread_exists(threadId) else {}
    checkpoints = (
        store.checkpoints.state(threadId) if store.thread_exists(threadId) else {"cursor": "", "hasLatest": False}
    )
    return JSONResponse(
        {
            "events": events,
            "messages": [],
            "hasMore": False,
            "nextCursor": None,
            "inflight": inflight,
            "chatInflightTaskId": (inflight_row or {}).get("taskId") or "",
            "planDoc": plan_doc,
            "sessionUi": session_ui,
            "checkpoints": checkpoints,
        }
    )


@router.get("/api/v1/checkpoints")
async def checkpoints(threadId: str, taskId: str = "", from_seq: int = 0):
    """一个回合的当前快照。前端加载页面时用它把跑到一半的回合画回来。"""
    if not store.thread_exists(threadId):
        return JSONResponse({"ok": False, "error": "thread not found"}, status_code=404)
    running = await _running_task(threadId)
    if taskId and running is not None and running["taskId"] != taskId:
        running = None
    if running is not None:
        seq = int(running["snapshotSeq"])
        row = {
            "seq": seq,
            "content": running["snapshotContent"],
            "reasoning": running["snapshotReasoning"],
            "blocks": running["blocks"],
            "token_count": 0,
            "status": "streaming",
        }
        return JSONResponse({
            "task_id": running["taskId"],
            "checkpoints": [row] if seq > from_seq else [],
            "latest_seq": seq,
            "status": "streaming",
        })
    if not taskId:
        return JSONResponse(
            {"task_id": "", "checkpoints": [], "latest_seq": 0, "status": "completed"}
        )
    payload = store.task_payload(threadId, taskId)
    row = {
        "seq": 1,
        "content": payload["content"],
        "reasoning": payload["reasoning"],
        "blocks": payload["blocks"],
        "token_count": 0,
        "status": "completed",
    }
    return JSONResponse({
        "task_id": taskId,
        "checkpoints": [row] if from_seq < 1 else [],
        "latest_seq": 1,
        "status": "completed",
    })


# ---------------- models / skills / workspace ----------------

# 路径一律是真实绝对路径(正斜杠),解析走 fm.backend.pathutil;相对路径按工作区目录解析。

import mimetypes
import uuid
from pathlib import Path as _Path

from fastapi import File, Form, UploadFile
from fastapi.responses import FileResponse as _FileResponse

from fm.backend.pathutil import (
    WORKSPACE as _WORKSPACE_FS,
    from_agent_path as _from_agent_path,
    is_computer_root as _is_computer_root,
    list_roots as _list_roots,
    resolve as _resolve_fs,
    to_fs as _to_fs,
)

MIME_MAP: dict[str, str] = {}
for _ext, _mime in mimetypes.types_map.items():
    MIME_MAP[_ext] = _mime


def _asset_type(mime: str, name: str) -> str:
    m = (mime or "").lower()
    if m.startswith("image/"):
        return "image"
    if m.startswith("audio/"):
        return "audio"
    if m.startswith("video/"):
        return "video"
    return "other"


def _fs_path(raw: str, *, is_directory: bool) -> tuple[str, _Path]:
    fs = _from_agent_path(raw, is_directory=is_directory)
    return fs, _Path(_resolve_fs(fs))


def _node(fp: _Path) -> dict:
    fs = _to_fs(str(fp))
    is_dir = fp.is_dir()
    mime = MIME_MAP.get(fp.suffix.lower(), "application/octet-stream") if not is_dir else ""
    return {
        "id": fs,
        "path": fs,
        "name": fp.name or fs.rstrip("/"),
        "node_type": "directory" if is_dir else "file",
        "asset_type": _asset_type(mime, fp.name),
        "media_id": fs,
        "mime_type": mime,
        "size": fp.stat().st_size if fp.is_file() else 0,
        "metadata": {},
        "source": "local",
    }


def _root_node(root: str) -> dict:
    return {
        "id": root,
        "path": root,
        "name": root.rstrip("/"),
        "node_type": "directory",
        "asset_type": "other",
        "media_id": root,
        "mime_type": "",
        "size": 0,
        "metadata": {},
        "source": "local",
    }


_Path(_resolve_fs(_WORKSPACE_FS)).mkdir(parents=True, exist_ok=True)


@router.get("/workspace/info")
async def workspace_info():
    return JSONResponse({"workspace": _WORKSPACE_FS, "roots": _list_roots()})


@router.get("/workspace/list")
async def workspace_list(path: str = ""):
    try:
        fs = _from_agent_path(path, is_directory=True)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if _is_computer_root(fs):
        return JSONResponse({"path": "/", "nodes": [_root_node(r) for r in _list_roots()]})
    root = _Path(_resolve_fs(fs))
    if not root.is_dir():
        return JSONResponse({"ok": False, "error": "not a directory"}, status_code=404)
    nodes = [
        _node(entry)
        for entry in sorted(root.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    ]
    return JSONResponse({"path": fs, "nodes": nodes})


@router.get("/workspace/read")
async def workspace_read(path: str = ""):
    try:
        _fs, fp = _fs_path(path, is_directory=False)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if not fp.is_file():
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    mime = MIME_MAP.get(fp.suffix.lower(), "application/octet-stream")
    return _FileResponse(fp, media_type=mime)


@router.post("/workspace/upload")
async def workspace_upload(parent: str = "", file: UploadFile = File(...)):
    try:
        _fs, parent_dir = _fs_path(parent, is_directory=True)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    parent_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _Path(file.filename or "untitled").name
    dest = parent_dir / safe_name
    if dest.exists():
        stem, ext = _Path(safe_name).stem, _Path(safe_name).suffix
        dest = parent_dir / f"{stem}_{uuid.uuid4().hex[:6]}{ext}"
    data = await file.read()
    dest.write_bytes(data)
    nd = _node(dest)
    return JSONResponse(
        {
            "ok": True,
            "path": nd["path"],
            "media_id": nd["path"],
            "filename": safe_name,
            "mime_type": nd["mime_type"],
            "size": nd["size"],
            "node": nd,
        }
    )


@router.post("/workspace/mkdir")
async def workspace_mkdir(request: Request):
    body = await request.json()
    raw = str(body.get("path", "")).strip()
    if not raw:
        return JSONResponse({"ok": False, "error": "missing path"}, status_code=400)
    try:
        _fs, d = _fs_path(raw, is_directory=True)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    d.mkdir(parents=True, exist_ok=True)
    return JSONResponse({"ok": True, "node": _node(d)})


@router.post("/workspace/rename")
async def workspace_rename(request: Request):
    body = await request.json()
    src = str(body.get("old_path", "")).strip()
    dst = str(body.get("new_path", "")).strip()
    if not src or not dst:
        return JSONResponse({"ok": False, "error": "missing path"}, status_code=400)
    try:
        _s_fs, s = _fs_path(src, is_directory=False)
        _d_fs, d = _fs_path(dst, is_directory=False)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if not s.exists():
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    d.parent.mkdir(parents=True, exist_ok=True)
    s.rename(d)
    return JSONResponse({"ok": True, "node": _node(d)})


@router.post("/workspace/replace")
async def workspace_replace(request: Request):
    body = await request.json()
    raw = str(body.get("path", "")).strip()
    content = str(body.get("content", ""))
    if not raw:
        return JSONResponse({"ok": False, "error": "missing path"}, status_code=400)
    try:
        _fs, fp = _fs_path(raw, is_directory=False)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content, encoding="utf-8")
    return JSONResponse({"ok": True})


@router.post("/workspace/delete")
async def workspace_delete(request: Request):
    body = await request.json()
    raw = str(body.get("path", "")).strip()
    if not raw:
        return JSONResponse({"ok": False, "error": "missing path"}, status_code=400)
    try:
        _fs, fp = _fs_path(raw, is_directory=False)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if not fp.exists():
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if fp.is_dir():
        import shutil

        shutil.rmtree(fp)
    else:
        fp.unlink()
    return JSONResponse({"ok": True})


@router.post("/workspace/batch")
async def workspace_batch():
    return JSONResponse({"ok": True, "results": []})


@router.get("/media/{media_id:path}")
async def media_serve(media_id: str):
    """通过 /media/<真实路径> 提供文件内容。"""
    try:
        _fs, fp = _fs_path(media_id, is_directory=False)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if not fp.is_file():
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    mime = MIME_MAP.get(fp.suffix.lower(), "application/octet-stream")
    return _FileResponse(fp, media_type=mime)


# ---------------- media urls (for frontend asset panel) ----------------

from urllib.parse import quote


@router.get("/api/v1/media/{media_id:path}/url")
async def media_url(media_id: str):
    """前端资产面板通过此端点解析文件 URL。media_id 即文件的真实路径。"""
    return JSONResponse({"media_id": media_id, "url": f"/workspace/read?path={quote(media_id, safe='')}", "storage_kind": "workspace"})


@router.post("/api/v1/media/urls")
async def media_urls(request: Request):
    body = await request.json()
    ids = body.get("media_ids") or body.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    items = {}
    for mid in ids[:50]:
        key = str(mid)
        items[key] = {
            "media_id": key,
            "signed_url": f"/workspace/read?path={quote(key, safe='')}",
            "thumb_url": f"/workspace/read?path={quote(key, safe='')}",
        }
    return JSONResponse({"items": items})


@router.get("/api/chat/models")
async def chat_models():
    return JSONResponse({"models": _load_models()})


@router.get("/api/v1/skills")
async def skills():
    """返回可用技能列表(供前端技能下拉用)。

    前端 input.js:fetchSkills 读 data.skills 渲染下拉选项。
    skills = 从技能目录扫描的 SKILL.md(工作流包)
    tools  = 注册表中的原子工具(不在 UI 显示)
    """
    from .toolkit.skills_manage import scan_skills as _scan

    skill_items = [
        {
            "id": s["name"],
            "name": s["name"],
            "summary": s["description"],
        }
        for s in _scan()
    ]
    return JSONResponse({
        "skills": skill_items,
        "tools": describe_all(),
        "toolsets": tool_registry.toolsets(),
    })


# ---------------- roles ----------------

from pydantic import BaseModel as _BaseModel


class RoleBody(_BaseModel):
    name: str = ""
    avatar: str = ""
    system_prompt: str = ""


@router.get("/api/v1/roles")
async def list_roles():
    return JSONResponse({"roles": store.list_roles()})


@router.post("/api/v1/roles")
async def create_role(body: RoleBody):
    if not body.name.strip():
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    rid = store.create_role(body.name.strip(), body.avatar.strip(), body.system_prompt)
    role = store.get_role(rid)
    return JSONResponse({"ok": True, "role": role})


@router.put("/api/v1/roles/{role_id}")
async def update_role(role_id: str, body: RoleBody):
    ok = store.update_role(role_id, body.name.strip(), body.avatar.strip(), body.system_prompt)
    if not ok:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse({"ok": True})


@router.delete("/api/v1/roles/{role_id}")
async def delete_role(role_id: str):
    ok = store.delete_role(role_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse({"ok": True})


@router.get("/api/v1/threads/{thread_id}/roles")
async def thread_roles(thread_id: str):
    return JSONResponse({"roles": store.get_active_roles(thread_id)})


@router.post("/api/v1/threads/{thread_id}/roles")
async def thread_add_role(thread_id: str, request: Request):
    body = await request.json()
    role_id = str(body.get("role_id") or "").strip()
    if not role_id:
        return JSONResponse({"ok": False, "error": "missing role_id"}, status_code=400)
    store.add_active_role(thread_id, role_id)
    return JSONResponse({"ok": True})


@router.delete("/api/v1/threads/{thread_id}/roles/{role_id}")
async def thread_remove_role(thread_id: str, role_id: str):
    store.remove_active_role(thread_id, role_id)
    return JSONResponse({"ok": True})
    return JSONResponse({"ok": True})


# WS 路由与 /static 的挂载都在 install_agent()(见文件开头)。
# 入口是 server.py —— 本模块不再独立启动。
