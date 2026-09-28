from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from appconfig import load_config
from embed_proxy import make_router

PROJECT = Path(__file__).resolve().parent
GATEWAY = PROJECT / "apps" / "remote" / "gateway.js"
LITE = PROJECT / "vendor" / "guacamole" / "guacamole-lite"
WEB = PROJECT / "apps" / "remote" / "web" / "index.html"
GUAC_JS = PROJECT / "vendor" / "guacamole" / "guacamole-common-js" / "all.min.js"
_CFG = load_config()
EMBED_PORT = _CFG.remote_port
GUACD_PORT = _CFG.guacd_port

_proc: subprocess.Popen | None = None


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def available() -> bool:
    if not GATEWAY.is_file() or not WEB.is_file() or not GUAC_JS.is_file():
        return False
    return _port_open(GUACD_PORT)


def embed_url() -> str:
    return "/remote/"


def install(app) -> None:
    app.include_router(make_router("/remote", f"http://127.0.0.1:{EMBED_PORT}"))


def _wait_gateway() -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        if _proc is not None and _proc.poll() is not None:
            raise RuntimeError("远程桌面桥进程退出,退出码 " + str(_proc.returncode))
        if _port_open(EMBED_PORT):
            return
        time.sleep(0.1)
    raise RuntimeError("远程桌面桥没有在 127.0.0.1:" + str(EMBED_PORT) + " 起来")


def start(workspace: str) -> None:
    global _proc
    if not GATEWAY.is_file():
        raise RuntimeError("找不到远程桌面桥: " + str(GATEWAY))
    if not (LITE / "node_modules" / "ws").is_dir():
        raise RuntimeError("找不到 guacamole-lite 依赖,先在 " + str(LITE) + " 执行 npm install")
    if not _port_open(GUACD_PORT):
        raise RuntimeError("本机 " + str(GUACD_PORT) + " 没有 guacd,远程桌面不能启动")
    if _port_open(EMBED_PORT):
        raise RuntimeError("127.0.0.1:" + str(EMBED_PORT) + " 已被占用,远程桌面桥不能启动")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("找不到 node,远程桌面桥不能启动")
    env = os.environ.copy()
    env["REMOTE_GATEWAY_PORT"] = str(EMBED_PORT)
    env["REMOTE_GUACD_PORT"] = str(GUACD_PORT)
    _proc = subprocess.Popen([node, str(GATEWAY)], cwd=str(GATEWAY.parent), env=env)
    _wait_gateway()


def stop() -> None:
    global _proc
    if _proc is None:
        return
    _proc.terminate()
    try:
        _proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _proc.kill()
        _proc.wait()
    _proc = None
