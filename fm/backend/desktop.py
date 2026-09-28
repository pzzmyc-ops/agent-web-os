"""桌面调用入口 —— 文件管理器对外的被动接口。

两条 WebSocket,两个角色:

    /api/desktop/screen?desktopId=<id>   浏览器里的桌面页,报到并执行下发的命令
    /api/desktop/control                 调用方,发请求拿回执

谁都可以当调用方:文件管理器里的 agent、别的 agent、skill 带的一个脚本、调试用的一次性
连接。这里不认识任何 agent,也不去发现谁在等着用桌面,只做三件事:列出在线的桌面、把
命令送到指定的那一块、把回执原样返回。桌面能做什么(操作名、参数、是否只读)由浏览器里的
DesktopOS 自报,后端不留副本 —— 加一个操作只改 DesktopOS,这里零改动。

一个浏览器页面 = 一块桌面 = 一个 desktopId。窗口状态存在那个页面里,所以两个标签页
物理上就是两块不同的屏幕。id 由页面自己生成并在刷新后保持不变,但刷新会重建窗口,winId
会变 —— 调用方不能跨刷新记住 winId。命令必须指明打给哪一块,没有「默认那块屏」;指定的
那块不在线就立刻失败,不排队等它回来。

本接口与全站其余接口一样不做鉴权,按局域网内可信部署对待。
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .pathutil import parent_of, resolve, to_fs

#: 单条命令的等待上限。桌面操作多是本地 DOM 动作,但 Office 要跨 iframe 进编辑器内部
#: 执行,慢一些;后台标签页还会被浏览器降频。
CMD_TIMEOUT = 20.0

router = APIRouter(prefix="/api/desktop")

_loop: asyncio.AbstractEventLoop | None = None


class RefreshBody(BaseModel):
    paths: list[str] = []


def _classify_changed(paths: list[str] | None) -> tuple[list[str], list[str]]:
    dirs: list[str] = []
    files: list[str] = []
    for raw in paths or []:
        p = str(raw).strip()
        if not p:
            continue
        full = resolve(p)
        if os.path.isfile(full):
            rel = to_fs(full)
            files.append(rel)
            dirs.append(parent_of(rel))
        else:
            dirs.append(to_fs(full))
    return list(dict.fromkeys(dirs)), list(dict.fromkeys(files))


async def broadcast_fs_changed(paths: list[str] | None = None) -> int:
    dirs, files = _classify_changed(paths)
    payload = {"type": "fs_changed", "paths": dirs, "files": files}
    sent = 0
    for desktop_id, conn in list(_desktops.items()):
        try:
            await conn.ws.send_json(payload)
        except Exception:
            if _desktops.get(desktop_id) is conn:
                _desktops.pop(desktop_id, None)
            continue
        sent += 1
    return sent


def notify_fs_changed(paths: list[str] | None = None) -> None:
    if not _desktops:
        return
    if _loop is None:
        raise RuntimeError("桌面连接存在但事件循环未记录")
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _loop:
        _loop.create_task(broadcast_fs_changed(paths))
        return
    asyncio.run_coroutine_threadsafe(broadcast_fs_changed(paths), _loop)


@router.post("/refresh")
async def refresh(body: RefreshBody):
    dirs, files = _classify_changed(body.paths)
    sent = await broadcast_fs_changed(body.paths)
    return {"ok": True, "desktops": sent, "paths": dirs, "files": files}


class DesktopError(RuntimeError):
    """桌面不在线、没响应,或者那边明确回了失败。"""


class DesktopConn:
    """一块屏幕的连接。命令下去、按 reqId 等回执。"""

    def __init__(self, desktop_id: str, ws: WebSocket) -> None:
        self.desktop_id = desktop_id
        self.ws = ws
        self.connected_at = time.time()
        self._pending: dict[str, asyncio.Future] = {}

    async def call(self, op: str, params: dict[str, Any]) -> dict:
        req_id = "cmd_" + uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self.ws.send_json({"type": "cmd", "reqId": req_id, "op": op, "params": params})
            return await asyncio.wait_for(fut, timeout=CMD_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise DesktopError(
                f"桌面 {self.desktop_id} 没有在 {CMD_TIMEOUT:g} 秒内响应操作 {op}"
                "(页面可能已关闭,或编辑器正忙)"
            ) from exc
        finally:
            self._pending.pop(req_id, None)

    def resolve(self, msg: dict) -> None:
        fut = self._pending.get(str(msg.get("reqId") or ""))
        if fut is None or fut.done():
            return
        if msg.get("ok"):
            result = msg.get("result")
            fut.set_result(result if isinstance(result, dict) else {"result": result})
        else:
            fut.set_exception(DesktopError(str(msg.get("error") or "桌面操作失败")))

    def fail_all(self, reason: str) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(DesktopError(reason))
        self._pending.clear()


_desktops: dict[str, DesktopConn] = {}


def _conn(desktop_id: str) -> DesktopConn:
    desktop_id = str(desktop_id or "").strip()
    if not desktop_id:
        raise DesktopError("必须指明 desktopId —— 先发 {\"type\":\"list\"} 看有哪些屏幕在线")
    conn = _desktops.get(desktop_id)
    if conn is None:
        online = ", ".join(sorted(_desktops)) or "(无)"
        raise DesktopError(f"桌面 {desktop_id} 不在线;在线的是 {online}")
    return conn


def _online() -> list[dict]:
    return [
        {"desktopId": c.desktop_id, "connectedAt": c.connected_at}
        for c in sorted(_desktops.values(), key=lambda x: x.connected_at)
    ]


async def _handle(req: dict) -> dict:
    kind = str(req.get("type") or "")
    if kind == "list":
        return {"ok": True, "type": "list", "desktops": _online()}
    if kind == "exec":
        op = str(req.get("op") or "").strip()
        if not op:
            raise DesktopError('op 不能为空 —— 先 exec "os.operations" 看有哪些操作')
        params = req.get("params") or {}
        if not isinstance(params, dict):
            raise DesktopError("params 必须是对象")
        desktop_id = str(req.get("desktopId") or "")
        result = await _conn(desktop_id).call(op, params)
        return {"ok": True, "type": "exec", "desktopId": desktop_id, "op": op, "result": result}
    raise DesktopError(f'未知请求类型: {kind or "(空)"};只有 list 和 exec')


@router.websocket("/control")
async def desktop_control(websocket: WebSocket):
    """调用方的连接。第一条必须是 hello,之后 list / exec 随便发。"""
    await websocket.accept()
    hello = await websocket.receive_json()
    if not isinstance(hello, dict) or hello.get("type") != "hello":
        await websocket.send_json({"ok": False, "error": '第一条消息必须是 {"type":"hello"}'})
        await websocket.close(code=1008)
        return
    await websocket.send_json({"ok": True, "type": "hello", "desktops": _online()})
    try:
        while True:
            req = await websocket.receive_json()
            try:
                reply = await _handle(req if isinstance(req, dict) else {})
            except DesktopError as exc:
                reply = {"ok": False, "error": str(exc)}
            reply["reqId"] = (req or {}).get("reqId") if isinstance(req, dict) else None
            await websocket.send_json(reply)
    except WebSocketDisconnect:
        pass


@router.websocket("/screen")
async def desktop_screen(websocket: WebSocket, desktopId: str = ""):
    """浏览器里的桌面页报到,并在这条连接上执行下发的命令。"""
    desktop_id = str(desktopId or "").strip()
    if not desktop_id:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    global _loop
    _loop = asyncio.get_running_loop()
    previous = _desktops.get(desktop_id)
    if previous is not None:
        previous.fail_all(f"桌面 {desktop_id} 重新连接,旧连接上的命令已作废")
    conn = DesktopConn(desktop_id, websocket)
    _desktops[desktop_id] = conn
    await websocket.send_json({"type": "ready", "desktopId": desktop_id})
    try:
        while True:
            msg = await websocket.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "result":
                conn.resolve(msg)
    except WebSocketDisconnect:
        pass
    finally:
        if _desktops.get(desktop_id) is conn:
            del _desktops[desktop_id]
        conn.fail_all(f"桌面 {desktop_id} 的连接已断开,操作无法完成")
