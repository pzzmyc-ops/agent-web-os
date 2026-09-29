"""合体入口:文件管理器(宿主)+ agent(app)+ gateway(内嵌),单进程单端口。

    python\\python.exe server.py    (见 start.bat,解释器随项目放在 python/ 目录)

三个组件共用一个 FastAPI app,URL 命名空间不重叠:

    /                       文件管理器桌面 shell(catch-all,必须最后挂)
    /api/*                  文件管理器:文件操作
    /api/transfer/*         文件管理器:上传下载(含 WS 进度)
    /api/onlyoffice/*       文件管理器:Office 在线编辑
    /api/desktop/*          文件管理器:桌面调用入口(含浏览器页面报到的 WS)
    /assets/kod, /plugins   文件管理器静态资源

    /agent                  agent 对话界面(在文件管理器里以 app 窗口打开)
    /api/v1/*               agent:会话/历史/角色/媒体
    /api/v1/ws              agent:对话 WebSocket
    /api/chat/models        agent:模型列表
    /workspace/*, /media/*  agent 前端的文件读写(与 /api/* 同一个根目录)
    /static/*               agent 前端静态资源

    /api/llm-proxy/v1       内嵌网关:OpenAI 兼容端点(config.base_url 指向它)
    /api/v1/media/*         内嵌网关:媒体模型调用

挂载顺序是硬约束:文件管理器前端是 "/" 上的 catch-all StaticFiles,任何在它
之后注册的路由都会被吞掉,所以 mount_fm_frontend() 必须最后调用。

agent 与文件管理器共用同一个文件根目录(config.json 的 fm_root),所以 agent
写的文件在资源管理器里立刻可见。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

# vendor/ 里的框架需要保持原有 import 路径(agent_framework 等)
_PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT / "vendor"))
sys.path.insert(0, str(_PROJECT))

from contextlib import asynccontextmanager

from apps.nextagent.config import load_config

cfg = load_config()

# fm/backend/settings.py 在 import 时会自己读 config.json;这里只需保证根目录已存在。
os.makedirs(cfg.fm_root_dir, exist_ok=True)

import copy

import uvicorn
from uvicorn.config import LOGGING_CONFIG
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from fm.backend.mount import install_fm, mount_fm_frontend, start_fm_background
from gateway.bootstrap import install_gateway
from apps.nextagent.web_server import install_agent
from embed_boot import wait_gateway
import comfyui_adapter
import deepseek_adapter
import hermes_adapter
import onlyoffice_adapter
import remote_adapter

_EMBEDDED_STOP: list = []
_EMBEDDED_STARTED: set[str] = set()
_EMBEDDED_CATALOG = (
    ("hermes", hermes_adapter),
    ("deepseek", deepseek_adapter),
    ("comfyui", comfyui_adapter),
    ("remote", remote_adapter),
    ("onlyoffice", onlyoffice_adapter),
)


def _list_embedded() -> list[dict]:
    apps = []
    for name, mod in _EMBEDDED_CATALOG:
        if not mod.available():
            continue
        apps.append({"name": name, "embed": mod.embed_url()})
    return apps


def _install_embedded(app) -> None:
    for _name, mod in _EMBEDDED_CATALOG:
        mod.install(app)


def _try_start_embedded(name, mod, workspace: str) -> None:
    if name in _EMBEDDED_STARTED:
        return
    if not mod.available():
        print(f"[{name}] skip", flush=True)
        return
    try:
        mod.start(workspace)
    except Exception as exc:
        print(f"[{name}] error    {exc}", flush=True)
        return
    _EMBEDDED_STARTED.add(name)
    _EMBEDDED_STOP.append(mod.stop)
    print(f"[{name}] embed     {mod.embed_url()}", flush=True)


def _start_embedded(workspace: str) -> None:
    for name, mod in _EMBEDDED_CATALOG:
        _try_start_embedded(name, mod, workspace)


def ensure_keyed_embedded() -> None:
    workspace = cfg.fm_root_dir
    _try_start_embedded("hermes", hermes_adapter, workspace)
    _try_start_embedded("deepseek", deepseek_adapter, workspace)


def _stop_embedded() -> None:
    for fn in reversed(_EMBEDDED_STOP):
        fn()
    _EMBEDDED_STOP.clear()
    _EMBEDDED_STARTED.clear()


def _embedded_task_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        raise exc


class _SkipDingtalkStatusAccess(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True
        path = str(args[2]).split("?", 1)[0]
        return path != "/api/v1/dingtalk-bot/status"


#: 走文件管理器响应封套({code,data,info})的路径前缀。agent 用的是 {ok,...},
#: 不能被一起改写 —— 原 fm/main.py 的全局异常处理器没有这个区分。
_FM_ENVELOPE_PREFIXES = ("/api/list", "/api/info", "/api/mkdir", "/api/mkfile",
                         "/api/rename", "/api/delete", "/api/copy", "/api/move",
                         "/api/upload", "/api/download", "/api/downloadZip",
                         "/api/media", "/api/thumbnail", "/api/preview", "/api/save", "/api/search",
                         "/api/zip", "/api/unzip", "/api/root",
                         "/api/transfer", "/api/onlyoffice",
                         "/api/embedded-apps")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_fm_background()

    # 定时任务:server 级后台任务,独立于 WS 连接 —— 没人连着也照样跑。
    # 到点 → claim_due 认领(同时把 next_at 推到下一次)→ recall_runner.run_recall
    # → run_turn(指令当提示词,工具照常可用)→ recall 卡片 + 回复落进 events.jsonl
    # → 在线连接实时收到(live_conns.broadcast),不在线的下次打开从历史恢复。
    # 定时任务优先级最高:到点时会话正忙就按用户点「停止」的同一条路径打断它,
    # 等那一轮真的收完尾再跑,避免两轮同时往一个 events.jsonl 里写。
    async def _run_agent_timer(thread_id: str, prompt: str, card: str):
        from apps.nextagent.recall_runner import run_recall
        from apps.nextagent.ws_chat import interrupt_thread

        log = logging.getLogger("mafagent.cron")
        try:
            if await interrupt_thread(thread_id):
                log.info("定时任务到点,已打断会话 %s 正在跑的回合", thread_id)
            await run_recall(
                app.state.web_agent, app.state.store, thread_id,
                prompt=prompt, card_text=card, source="timer",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("定时任务回合失败(%s): %s", thread_id, exc)

    async def _cron_ticker():
        from apps.nextagent.maf_tools.timer_registry import (
            build_wakeup_prompt,
            render_program_text,
            timer_registry,
        )
        from apps.nextagent.recall_runner import run_exec_timer, run_program_timer

        log = logging.getLogger("mafagent.cron")
        while True:
            try:
                due, skipped = timer_registry.claim_due()
                for t in skipped:
                    log.info(
                        "定时任务 %s(%s)错过了计划时间 %s,不补跑,已推到下一次",
                        t.id, t.when_text,
                        time.strftime("%Y-%m-%d %H:%M", time.localtime(t.next_at)),
                    )
                for t in due:
                    if t.mode == "program":
                        asyncio.create_task(run_program_timer(
                            app.state.store, t.thread_id, render_program_text(t),
                        ))
                    elif t.mode == "exec":
                        asyncio.create_task(run_exec_timer(
                            app.state.store, t.thread_id, t.command, t.when_text,
                        ))
                    elif t.mode == "agent":
                        prompt, card = build_wakeup_prompt(t)
                        asyncio.create_task(_run_agent_timer(t.thread_id, prompt, card))
                    else:
                        raise RuntimeError("unknown timer mode: " + repr(t.mode))
            except Exception as exc:  # noqa: BLE001 — ticker 必须活着
                logging.getLogger("mafagent.cron").warning("cron ticker 出错: %s", exc)
            await asyncio.sleep(20)

    _cron_task = asyncio.create_task(_cron_ticker())

    async def _process_ticker():
        from apps.nextagent.message_runner import dispatch_process_completions

        log = logging.getLogger("mafagent.process")
        while True:
            try:
                await dispatch_process_completions(app.state.web_agent, app.state.store)
            except Exception as exc:
                log.warning("process ticker 出错: %s", exc)
            await asyncio.sleep(1)

    _process_task = asyncio.create_task(_process_ticker())

    async def _start_embedded_when_gateway_up() -> None:
        url = f"http://127.0.0.1:{cfg.web_port}/api/llm-proxy/v1/models"
        await wait_gateway(url)
        await asyncio.to_thread(_start_embedded, cfg.fm_root_dir)

    _embedded_task = asyncio.create_task(_start_embedded_when_gateway_up())
    _embedded_task.add_done_callback(_embedded_task_done)

    yield

    _embedded_task.cancel()
    _stop_embedded()
    _process_task.cancel()
    _cron_task.cancel()
    try:
        await _process_task
    except asyncio.CancelledError:
        pass
    try:
        await _cron_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="mafagent + fm", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.exception_handler(Exception)
async def _error_envelope(request: Request, exc: Exception):
    """文件管理器前端要求 {code,data,info} 封套;其余路径保持 FastAPI 默认形状。"""
    is_fm = request.url.path.startswith(_FM_ENVELOPE_PREFIXES)
    status = exc.status_code if isinstance(exc, StarletteHTTPException) else 400
    detail = str(exc.detail) if isinstance(exc, StarletteHTTPException) else f"{type(exc).__name__}: {exc}"
    if is_fm:
        return JSONResponse(status_code=status, content={"code": False, "data": detail, "info": ""})
    return JSONResponse(status_code=status, content={"ok": False, "error": detail})


@app.exception_handler(StarletteHTTPException)
async def _http_error_envelope(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith(_FM_ENVELOPE_PREFIXES):
        return await _error_envelope(request, exc)
    return await http_exception_handler(request, exc)


# 顺序:agent → gateway → 文件管理器 API → 文件管理器前端("/" catch-all,最后)
install_agent(app)
install_gateway(app)
install_fm(app)
_install_embedded(app)
app.state.list_embedded = _list_embedded


@app.api_route("/api/{rest:path}",
               methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def _api_route_not_found(request: Request, rest: str):
    """必须在 mount_fm_frontend() 之前注册:"/" 上的 StaticFiles 会接住所有未匹配路径,
    而它对非 GET/HEAD 抛 405,拼错的端点会看起来像"路径对、方法错",调用方于是一直在
    错前缀下换后缀。这里明确回 404 并带上真实路由清单。
    """
    path = f"/api/{rest}"
    # 用 FastAPI 自己生成的 schema 取路由:include_router 的路由被包在 _IncludedRouter 里,
    # 手工遍历 app.routes 取不到它们。
    routes = sorted(
        p for p in app.openapi().get("paths", {})
        if p.startswith("/api/") and p != "/api/{rest}"
    )
    detail = f"no such API route: {request.method} {path}"
    is_fm = path.startswith(_FM_ENVELOPE_PREFIXES)
    if is_fm:
        return JSONResponse(status_code=404, content={"code": False, "data": detail, "info": ""})
    return JSONResponse(status_code=404, content={"ok": False, "error": detail, "available": routes})


mount_fm_frontend(app)


if __name__ == "__main__":
    port = cfg.web_port
    # 控制台一律用 ASCII —— cmd 默认 cp936,中文会变乱码。
    print(f"[fm]      desktop   http://127.0.0.1:{port}/", flush=True)
    print(f"[agent]   chat      http://127.0.0.1:{port}/agent", flush=True)
    print(f"[agent]   webhook   http://127.0.0.1:{port}/feishu-webhook", flush=True)
    print(f"[gateway] api       http://127.0.0.1:{port}/api/llm-proxy/v1", flush=True)
    print(f"[root]    files     {cfg.fm_root_dir}", flush=True)
    log_config = copy.deepcopy(LOGGING_CONFIG)
    log_config["filters"] = {
        "skip_dingtalk_status": {"()": _SkipDingtalkStatusAccess},
    }
    log_config["handlers"]["access"]["filters"] = ["skip_dingtalk_status"]
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        log_config=log_config,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
