from __future__ import annotations

import asyncio
import re
from urllib.parse import quote

import httpx
import websockets
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, Response

HOP = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "origin",
    "referer",
})
DROP_RESP = HOP | frozenset({"content-encoding", "x-frame-options"})
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def _rewrite_location(value: str, prefix: str, upstream: str) -> str:
    alt = upstream.replace("127.0.0.1", "localhost")
    for base in (upstream, alt):
        if value.startswith(base):
            rest = value[len(base):]
            if not rest.startswith("/"):
                rest = "/" + rest
            return prefix + rest
    if value.startswith("/"):
        return prefix + value
    return value


def _is_text(ctype: str) -> bool:
    low = ctype.lower()
    return "text/" in low or "javascript" in low or "json" in low


def _disposition(value: str) -> str:
    try:
        value.encode("latin-1")
        return value
    except UnicodeEncodeError:
        pass
    found = re.search(r'filename="([^"]+)"', value)
    if not found:
        found = re.search(r"filename=([^;\s]+)", value)
    if not found:
        raise RuntimeError("无法转发非 ASCII 的 Content-Disposition")
    return "inline; filename*=UTF-8''" + quote(found.group(1), safe="")


def _response_headers(resp, request: Request, prefix: str, upstream: str) -> dict:
    parent = str(request.base_url).rstrip("/")
    out = {}
    for header_key, value in resp.headers.items():
        low = header_key.lower()
        if low == "content-length":
            continue
        if low in DROP_RESP:
            continue
        if low == "location":
            out[header_key] = _rewrite_location(value, prefix, upstream)
            continue
        if low == "content-security-policy":
            if parent and parent not in value:
                value = value.replace("frame-ancestors ", "frame-ancestors " + parent + " ")
            out[header_key] = value
            continue
        if low == "content-disposition":
            out[header_key] = _disposition(value)
            continue
        out[header_key] = value
    return out


async def proxy_http(request: Request, path: str, prefix: str, upstream: str, rewrite=None):
    url = upstream + "/" + path if path else upstream + "/"
    if request.url.query:
        url += "?" + request.url.query
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    body = None
    if request.method not in ("GET", "HEAD"):
        body = await request.body()
    client = httpx.AsyncClient(timeout=None)
    try:
        req = client.build_request(request.method, url, headers=headers, content=body)
        resp = await client.send(req, stream=True)
        raw = await resp.aread()
    except Exception:
        await client.aclose()
        raise
    await resp.aclose()
    await client.aclose()
    ctype = resp.headers.get("content-type") or ""
    if rewrite and _is_text(ctype):
        raw = rewrite(raw, ctype)
    out = _response_headers(resp, request, prefix, upstream)
    if request.method == "HEAD":
        length = resp.headers.get("content-length")
        if length:
            out["content-length"] = length
        return Response(content=b"", status_code=resp.status_code, headers=out)
    out["content-length"] = str(len(raw))
    return Response(content=raw, status_code=resp.status_code, headers=out)


def make_leaf_router(upstream: str, leaves: tuple[str, ...]) -> APIRouter:
    router = APIRouter()
    for leaf in leaves:
        path = leaf.strip("/")
        name = "leaf-" + path.replace("/", "-")

        async def rest(request: Request, _path: str = path):
            return await proxy_http(request, _path, "", upstream)

        router.add_api_route(leaf, rest, methods=METHODS, name=name)
    return router


def make_router(prefix: str, upstream: str, rewrite=None, rewrite_ws=None) -> APIRouter:
    router = APIRouter(prefix=prefix)
    key = prefix.strip("/")
    slash = prefix + "/"
    ws_upstream = "ws://" + upstream[len("http://"):]

    async def proxy(request: Request, path: str):
        return await proxy_http(request, path, prefix, upstream, rewrite)

    async def proxy_ws(websocket: WebSocket, path: str):
        requested = websocket.headers.get("sec-websocket-protocol")
        sub = None
        if requested:
            sub = requested.split(",")[0].strip()
            if not sub:
                raise RuntimeError("WebSocket 子协议为空")
        dest = ws_upstream + "/" + path
        if websocket.url.query:
            dest += "?" + websocket.url.query
        extra = []
        cookie = websocket.headers.get("cookie")
        if cookie:
            extra.append(("Cookie", cookie))
        connect_kw = {
            "max_size": None,
            "max_queue": None,
            "compression": None,
            "ping_interval": None,
        }
        if extra:
            connect_kw["additional_headers"] = extra
        if sub:
            connect_kw["subprotocols"] = [sub]
        async with websockets.connect(dest, **connect_kw) as up:
            if sub:
                await websocket.accept(subprotocol=sub)
            else:
                await websocket.accept()
            async def down():
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    if msg.get("text") is not None:
                        await up.send(msg["text"])
                    elif msg.get("bytes") is not None:
                        await up.send(msg["bytes"])

            async def up_loop():
                async for message in up:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        if rewrite_ws:
                            message = rewrite_ws(message)
                        await websocket.send_text(message)

            tasks = [asyncio.create_task(down()), asyncio.create_task(up_loop())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            first = next(iter(done))
            exc = first.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                raise exc
            await up.close()
            await websocket.close()

    router.add_api_route("", lambda: RedirectResponse(url=slash, status_code=307), methods=["GET", "HEAD"], name=key + "-slash")

    async def root(request: Request):
        return await proxy(request, "")

    async def rest(request: Request, path: str):
        return await proxy(request, path)

    router.add_api_route("/", root, methods=METHODS, name=key + "-root")
    router.add_api_route("/{path:path}", rest, methods=METHODS, name=key + "-path")
    router.add_api_websocket_route("/{path:path}", proxy_ws, name=key + "-ws")
    return router
