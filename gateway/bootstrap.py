from __future__ import annotations

from fastapi import FastAPI

from gateway.core.accounting import init_db


def install_gateway(app: FastAPI) -> None:
    """把 gateway 框架装进宿主 app。

    框架零对外调用:只挂契约路由(对话/媒体/模型目录/热加载)。
    所有实际的模型/媒体/OSS/转发调用都在 adapter(节点)里,框架不 import 任何 adapter 或厂商包。
    """
    if getattr(app.state, "gateway_installed", False):
        return
    init_db()
    from gateway.routes.media import router as media_router
    from gateway.routes.openai import router as openai_router

    app.include_router(openai_router, prefix="/api/llm-proxy/v1")
    app.include_router(media_router, prefix="/api/v1/media")
    app.state.gateway_installed = True
