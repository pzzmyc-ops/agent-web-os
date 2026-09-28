from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yaml

from appconfig import load_config

PROJECT = Path(__file__).resolve().parent
AGENT_DIR = PROJECT / "apps" / "hermes-agent"
WEBUI_DIR = PROJECT / "apps" / "hermes-webui"
_CFG = load_config()
WEBUI_PORT = _CFG.hermes_port
EMBED_PORT = _CFG.hermes_embed_port
PROVIDER_NAME = "mafagent"
HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                 "te", "trailers", "transfer-encoding", "upgrade"})

_webui: subprocess.Popen | None = None
_proxy: ThreadingHTTPServer | None = None


def available() -> bool:
    if not AGENT_DIR.joinpath("run_agent.py").is_file() or not WEBUI_DIR.joinpath("server.py").is_file():
        return False
    if str(load_config().deepseek_api_key or "").strip():
        return True
    from gateway.adapters.registry import has_non_official_chat
    return has_non_official_chat()


def embed_url() -> str:
    return "/hermes/"


def install(app) -> None:
    from embed_proxy import make_router
    app.include_router(make_router("/hermes", f"http://127.0.0.1:{EMBED_PORT}"))


def _hermes_home() -> Path:
    existing = os.environ.get("HERMES_HOME", "").strip()
    if existing:
        return Path(existing)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        raise RuntimeError("LOCALAPPDATA 未设置,无法定位 Hermes 家目录")
    return Path(local) / "hermes"


def _python() -> str:
    override = os.environ.get("HERMES_WEBUI_PYTHON", "").strip()
    if override:
        return override
    return sys.executable


def _origin_of(raw: str) -> str:
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname:
        return ""
    port = parsed.port
    if port is None or (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443):
        return f"{parsed.scheme}://{parsed.hostname}"
    return f"{parsed.scheme}://{parsed.hostname}:{port}"


def _rewrite_csp(value: str, parent: str) -> str:
    allowed = [
        "http://127.0.0.1:80",
        "http://127.0.0.1",
        "http://localhost:80",
        "http://localhost",
    ]
    if parent and parent not in allowed:
        allowed.append(parent)
    return value.replace("frame-ancestors 'none'", "frame-ancestors " + " ".join(allowed))


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self):
        self._forward()

    def do_POST(self):
        self._forward()

    def do_PUT(self):
        self._forward()

    def do_PATCH(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    def do_HEAD(self):
        self._forward()

    def do_OPTIONS(self):
        self._forward()

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        parent = _origin_of(self.headers.get("Referer") or self.headers.get("Origin") or "")
        conn = http.client.HTTPConnection("127.0.0.1", WEBUI_PORT, timeout=None)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                low = key.lower()
                if low == "x-frame-options" or low in HOP:
                    continue
                if low == "content-security-policy":
                    value = _rewrite_csp(value, parent)
                self.send_header(key, value)
            self.end_headers()
            if self.command == "HEAD":
                return
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    return
        finally:
            conn.close()


def _wait_webui(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        if _webui is not None and _webui.poll() is not None:
            raise RuntimeError(f"hermes-webui 已退出,code={_webui.returncode} {last}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{WEBUI_PORT}/", timeout=2) as resp:
                if resp.status < 500:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return
            last = str(exc)
        except Exception as exc:
            last = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"hermes-webui 没有在 {timeout:g} 秒内起来: {last}")


def _port_taken(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _gateway_chat() -> tuple[str, list[str], str, str]:
    from apps.nextagent.config import load_chat_models, load_config

    cfg = load_config()
    ids = [str(item.get("id") or "").strip() for item in load_chat_models()]
    ids = [item for item in ids if item]
    want = (cfg.model or "").strip()
    default = want if want in ids else (ids[0] if ids else "")
    key = str(cfg.api_key or "").strip()
    if not key:
        raise RuntimeError("config.json 没有 api_key,Hermes 不能启动")
    base = str(cfg.base_url or "").strip()
    if not base:
        raise RuntimeError("config.json 没有 base_url,Hermes 不能启动")
    return default, ids, base, key


def _apply_workspace(home: Path, workspace: str) -> None:
    root = Path(workspace)
    if not root.is_dir():
        raise RuntimeError(f"工作目录不存在: {root}")
    names = [item.name for item in root.iterdir()]
    if not names:
        raise RuntimeError(f"工作目录是空的: {root}")
    state = home / "webui"
    state.mkdir(parents=True, exist_ok=True)
    (state / "last_workspace.txt").write_text(str(root), encoding="utf-8")
    (state / "workspaces.json").write_text(
        json.dumps([{"path": str(root), "name": "Home"}], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    settings_path = state / "settings.json"
    if settings_path.is_file():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise RuntimeError(f"settings.json 不是对象: {settings_path}")
        settings["default_workspace"] = str(root)
        settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_config(home: Path, workspace: str, default: str, ids: list[str], base: str, key: str) -> None:
    path = home / "config.yaml"
    if not path.is_file():
        raise RuntimeError(f"找不到 Hermes 配置: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Hermes 配置不是对象: {path}")
    data["workspace"] = workspace
    data["default_workspace"] = workspace
    term = data.get("terminal")
    if not isinstance(term, dict):
        raise RuntimeError(f"Hermes 配置缺少 terminal: {path}")
    term["cwd"] = workspace
    data["model"] = {
        "default": default,
        "provider": f"custom:{PROVIDER_NAME}",
    }
    data["custom_providers"] = [
        {
            "name": PROVIDER_NAME,
            "base_url": base,
            "api_key": key,
            "model": default,
            "models": ids,
            "api_mode": "chat_completions",
            "discover_models": False,
        }
    ]
    providers = data.get("providers")
    if providers is None:
        data["providers"] = {}
    elif not isinstance(providers, dict):
        raise RuntimeError(f"Hermes 配置 providers 不是对象: {path}")
    else:
        providers.pop(PROVIDER_NAME, None)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    cache = home / "webui" / "models_cache.json"
    if cache.is_file():
        cache.unlink()


def start(workspace: str) -> None:
    global _webui, _proxy
    if not AGENT_DIR.joinpath("run_agent.py").is_file():
        raise RuntimeError(f"找不到 hermes-agent: {AGENT_DIR}")
    if not WEBUI_DIR.joinpath("server.py").is_file():
        raise RuntimeError(f"找不到 hermes-webui: {WEBUI_DIR}")
    if _port_taken(WEBUI_PORT):
        raise RuntimeError(f"127.0.0.1:{WEBUI_PORT} 已被占用,Hermes WebUI 不能启动")
    if _port_taken(EMBED_PORT):
        raise RuntimeError(f"127.0.0.1:{EMBED_PORT} 已被占用,Hermes 嵌入代理不能启动")
    home = _hermes_home()
    if not home.is_dir():
        raise RuntimeError(f"找不到 Hermes 家目录: {home}")
    root = str(Path(workspace).resolve())
    default, ids, base, key = _gateway_chat()
    _apply_workspace(home, root)
    _apply_config(home, root, default, ids, base, key)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["HERMES_WEBUI_AGENT_DIR"] = str(AGENT_DIR)
    env["HERMES_WEBUI_DEFAULT_WORKSPACE"] = root
    env["HERMES_WEBUI_DEFAULT_MODEL"] = default
    env["HERMES_HOME"] = str(home)
    env["HERMES_WEBUI_STATE_DIR"] = str(home / "webui")
    env["HERMES_WEBUI_HOST"] = "127.0.0.1"
    env["HERMES_WEBUI_PORT"] = str(WEBUI_PORT)
    env["HERMES_WEBUI_PYTHON"] = _python()
    env["TERMINAL_CWD"] = root
    env["HERMES_MODEL"] = default
    _webui = subprocess.Popen(
        [_python(), str(WEBUI_DIR / "server.py")],
        cwd=str(WEBUI_DIR),
        env=env,
    )
    _wait_webui()
    _proxy = ThreadingHTTPServer(("0.0.0.0", EMBED_PORT), _Handler)
    thread = threading.Thread(target=_proxy.serve_forever, daemon=True)
    thread.start()


def stop() -> None:
    global _webui, _proxy
    if _proxy is not None:
        _proxy.shutdown()
        _proxy.server_close()
        _proxy = None
    if _webui is not None:
        _webui.terminate()
        try:
            _webui.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _webui.kill()
        _webui = None
