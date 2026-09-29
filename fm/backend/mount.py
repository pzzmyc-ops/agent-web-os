"""把文件管理器装进一个已有的 FastAPI app。

拆成两步是因为挂载顺序有硬约束:FM 的前端是挂在 "/" 上的 catch-all
StaticFiles,必须最后挂,否则会吞掉 agent 和 gateway 的路由。

    install_fm(app)            # /api/* 路由 + /assets/* 静态资源
    ...其它组件的路由...
    mount_fm_frontend(app)     # "/" catch-all —— 必须最后

FM 自身的路由前缀:/api(文件操作)、/api/transfer(上传下载)、
/api/onlyoffice、/api/desktop(桌面调用入口)。与 agent 的 /api/v1/*、
/api/chat/* 不重叠。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .desktop import router as desktop_router
from .fileop import router as fileop_router
from .onlyoffice import router as onlyoffice_router
from .transfer import router as transfer_router

_NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}
_NO_CACHE_PREFIXES = (
    "/api/list",
    "/api/info",
    "/api/mkdir",
    "/api/mkfile",
    "/api/rename",
    "/api/delete",
    "/api/copy",
    "/api/move",
    "/api/upload",
    "/api/download",
    "/api/downloadZip",
    "/api/preview",
    "/api/browse",
    "/api/save",
    "/api/search",
    "/api/zip",
    "/api/unzip",
    "/api/archive",
    "/api/root",
    "/api/transfer",
    "/api/fileop",
    "/api/onlyoffice",
)

_FM = Path(__file__).resolve().parent.parent  # mafagent/fm
FRONTEND = _FM / "frontend"
ASSETS_STATIC = _FM / "assets" / "static"
ASSETS_PLUGINS = _FM / "assets" / "plugins"

#: FM 自己的路由前缀。server.py 的异常处理器据此决定是否套 FM 的响应封套
#: ({code,data,info}),避免把 agent 的 {ok,...} 一起改写。
FM_API_PREFIXES = ("/api/transfer", "/api/onlyoffice", "/api/")


def install_fm(app: FastAPI) -> None:
    """挂 FM 的 API 路由与静态资源(不含 "/" catch-all)。"""

    @app.middleware("http")
    async def fm_no_cache(request: Request, call_next):
        resp = await call_next(request)
        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in _NO_CACHE_PREFIXES):
            for k, v in _NO_CACHE.items():
                resp.headers[k] = v
        return resp

    app.include_router(api_router)
    app.include_router(desktop_router)
    app.include_router(onlyoffice_router)
    app.include_router(transfer_router)
    app.include_router(fileop_router)
    app.mount("/assets/kod", StaticFiles(directory=str(ASSETS_STATIC)), name="fm-kod")
    app.mount("/assets/plugins", StaticFiles(directory=str(ASSETS_PLUGINS)), name="fm-plugins")


def mount_fm_frontend(app: FastAPI) -> None:
    """挂 FM 前端(桌面 shell)到 "/"。必须在所有其它路由之后调用。"""
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="fm-frontend")


def start_fm_background() -> None:
    return
