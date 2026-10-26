from __future__ import annotations

import secrets
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.datastructures import Headers

COOKIE_NAME = "fm_auth"
COOKIE_MAX_AGE = 30 * 24 * 3600

_PUBLIC_PATHS = frozenset({
    "/login",
    "/api/auth/login",
    "/api/auth/status",
    "/api/v1/media/invoke",
    "/api/onlyoffice/file",
    "/api/onlyoffice/callback",
})
_PUBLIC_PREFIXES = (
    "/api/llm-proxy",
    "/hermes",
    "/deepseek",
    "/comfyui",
    "/remote",
    "/onlyoffice-ds",
)

_LOGIN_PAGE = Path(__file__).resolve().parent / "fm" / "frontend" / "login.html"

_password = ""
_tokens: set[str] = set()

router = APIRouter()


def is_public(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    for prefix in _PUBLIC_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _token_from_headers(headers: Headers) -> str:
    raw = headers.get("cookie", "")
    if not raw:
        return ""
    jar = SimpleCookie()
    jar.load(raw)
    morsel = jar.get(COOKIE_NAME)
    return morsel.value if morsel is not None else ""


def is_authenticated(headers: Headers) -> bool:
    token = _token_from_headers(headers)
    return bool(token) and token in _tokens


class AuthMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        headers = Headers(scope=scope)
        if is_public(path) or is_authenticated(headers):
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        accept = headers.get("accept", "")
        if scope["method"] == "GET" and "text/html" in accept:
            query = scope.get("query_string") or b""
            target = path + ("?" + query.decode("utf-8") if query else "")
            response = RedirectResponse("/login?next=" + quote(target, safe=""), status_code=303)
        else:
            response = JSONResponse({"ok": False, "error": "未登录"}, status_code=401)
        await response(scope, receive, send)


@router.get("/login")
async def login_page():
    response = HTMLResponse(_LOGIN_PAGE.read_text(encoding="utf-8"))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/api/auth/status")
async def auth_status(request: Request):
    return JSONResponse({"ok": True, "authenticated": is_authenticated(request.headers)})


@router.post("/api/auth/login")
async def auth_login(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise RuntimeError("请求体不是对象")
    password = str(body.get("password") or "")
    if not secrets.compare_digest(password.encode("utf-8"), _password.encode("utf-8")):
        return JSONResponse({"ok": False, "error": "密码错误"}, status_code=401)
    token = secrets.token_urlsafe(32)
    _tokens.add(token)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/api/auth/logout")
async def auth_logout(request: Request):
    token = _token_from_headers(request.headers)
    _tokens.discard(token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


def install_webauth(app: FastAPI, password: str) -> None:
    global _password
    if not password:
        raise RuntimeError("web_password 为空,不能启动登录门")
    _password = password
    app.add_middleware(AuthMiddleware)
    app.include_router(router)
