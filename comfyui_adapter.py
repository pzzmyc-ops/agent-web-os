from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from appconfig import load_config
from embed_proxy import make_leaf_router, make_router

PORT = load_config().comfyui_port
UPSTREAM = load_config().comfyui_url or ("http://127.0.0.1:%s" % PORT)
ROOT = Path(__file__).resolve().parent / "apps" / "comfyui"

_PREFIX_PATCH = (
    "<script>(function(){"
    "var P='/comfyui';"
    "function fix(u){"
    "if(typeof u!=='string'||!u||u.charAt(0)!=='/')return u;"
    "if(u===P||u.indexOf(P+'/')===0)return u;"
    "if(/^\\/(api|view|manager|upload|extensions|internal|userdata|ws|object_info|prompt|queue|history|interrupt|free|system_stats|embeddings|models|hhywebui)(\\/|\\?|$)/.test(u))return P+u;"
    "return u;"
    "}"
    "var fetch0=window.fetch;"
    "window.fetch=function(input,init){"
    "if(typeof input==='string')input=fix(input);"
    "return fetch0.call(this,input,init);"
    "};"
    "var WS0=window.WebSocket;"
    "window.WebSocket=function(url,prot){"
    "if(typeof url==='string'){"
    "var u=new URL(url,location.origin);"
    "if(u.origin===location.origin)url=u.origin+fix(u.pathname+(u.search||''));"
    "}"
    "return prot===undefined?new WS0(url):new WS0(url,prot);"
    "};"
    "window.WebSocket.prototype=WS0.prototype;"
    "window.WebSocket.CONNECTING=WS0.CONNECTING;"
    "window.WebSocket.OPEN=WS0.OPEN;"
    "window.WebSocket.CLOSING=WS0.CLOSING;"
    "window.WebSocket.CLOSED=WS0.CLOSED;"
    "function hook(proto,key){"
    "var d=Object.getOwnPropertyDescriptor(proto,key);"
    "if(!d||!d.set)return;"
    "Object.defineProperty(proto,key,{"
    "configurable:true,enumerable:d.enumerable,"
    "get:function(){return d.get.call(this)},"
    "set:function(v){d.set.call(this,fix(v))}"
    "});"
    "}"
    "hook(HTMLImageElement.prototype,'src');"
    "hook(HTMLMediaElement.prototype,'src');"
    "hook(HTMLScriptElement.prototype,'src');"
    "hook(HTMLLinkElement.prototype,'href');"
    "})();</script>"
)

_proc: subprocess.Popen | None = None


def _running() -> bool:
    parsed = urlparse(UPSTREAM)
    host = parsed.hostname
    port = parsed.port if parsed.port is not None else 80
    conn = http.client.HTTPConnection(host, port, timeout=0.5)
    try:
        conn.request("GET", "/system_stats")
        resp = conn.getresponse()
        raw = resp.read()
    except (ConnectionRefusedError, TimeoutError, socket.timeout, OSError):
        return False
    finally:
        conn.close()
    if resp.status != 200:
        raise RuntimeError("%s 有服务但不是 ComfyUI: HTTP " % UPSTREAM + str(resp.status))
    body = json.loads(raw)
    version = body["system"]["comfyui_version"]
    if not version:
        raise RuntimeError("%s 返回了空的 ComfyUI 版本" % UPSTREAM)
    return True


def _root() -> Path:
    if not (ROOT / "main.py").is_file():
        raise RuntimeError("找不到 ComfyUI: " + str(ROOT / "main.py"))
    return ROOT


def available() -> bool:
    if load_config().comfyui_url:
        return _running()
    if _running():
        return True
    return (ROOT / "main.py").is_file()


def embed_url() -> str:
    return "/comfyui/"


def _wait(proc: subprocess.Popen, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    last = ""
    url = f"http://127.0.0.1:{PORT}/system_stats"
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("ComfyUI 已退出,code=" + str(proc.returncode) + " " + last)
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    body = json.loads(resp.read().decode("utf-8"))
                    if not body["system"]["comfyui_version"]:
                        raise RuntimeError("本机 %s 返回了空的 ComfyUI 版本" % PORT)
                    return
                last = "HTTP " + str(resp.status)
        except urllib.error.HTTPError as exc:
            last = str(exc)
        except Exception as exc:
            last = str(exc)
        time.sleep(0.5)
    raise RuntimeError("ComfyUI 没有在 " + str(timeout) + " 秒内起来: " + last)


def start(workspace: str) -> None:
    global _proc
    if load_config().comfyui_url:
        return
    if _running():
        return
    root = _root()
    _proc = subprocess.Popen(
        [sys.executable, str(root / "main.py"), "--listen", "127.0.0.1", "--port", str(PORT)],
        cwd=str(root),
    )
    _wait(_proc)


def stop() -> None:
    global _proc
    if _proc is None:
        return
    _proc.terminate()
    try:
        _proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _proc.kill()
    _proc = None


def _rewrite_index(raw: bytes, ctype: str) -> bytes:
    if "html" not in ctype.lower():
        return raw
    text = raw.decode("utf-8")
    if "<head>" not in text:
        raise RuntimeError("ComfyUI 首页没有 head,不能注入路径补丁")
    return text.replace("<head>", "<head>" + _PREFIX_PATCH, 1).encode("utf-8")


def install(app) -> None:
    app.include_router(make_router("/comfyui", UPSTREAM, rewrite=_rewrite_index))
    app.include_router(make_leaf_router(UPSTREAM, (
        "/view",
        "/api/view",
        "/upload/image",
        "/upload/mask",
        "/api/upload/image",
        "/api/upload/mask",
    )))
