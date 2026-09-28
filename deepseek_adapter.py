from __future__ import annotations

import http.client
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from appconfig import load_config

PROJECT = Path(__file__).resolve().parent
HARNESS = PROJECT / "apps" / "deepseek"
HOME = PROJECT / "data" / "dsh-home"
BIN = HARNESS / "apps" / "cli" / "lib" / "bin.js"
DIST = HARNESS / "apps" / "web" / "dist" / "index.html"
_CFG = load_config()
PORT = _CFG.deepseek_port
EMBED_PORT = _CFG.deepseek_embed_port
PROVIDER = "mafagent"
CREDENTIAL = "MAFAGENT_API_KEY"
HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                 "te", "trailers", "transfer-encoding", "upgrade", "host", "origin", "referer",
                 "cookie", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "sec-fetch-user"})
UUID_PATCH = (
    "<script>if(!crypto.randomUUID){crypto.randomUUID=function(){"
    "var a=new Uint8Array(16);crypto.getRandomValues(a);"
    "a[6]=a[6]&15|64;a[8]=a[8]&63|128;"
    "var h=[].map.call(a,function(x){return(x+256).toString(16).slice(1)}).join('');"
    "return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20)}}"
    "</script>"
)
PREFIX_PATCH = (
    "<script>(function(){"
    "var prefix='/deepseek';"
    "function rewrite(u){"
    "var url=new URL(u,location.origin);"
    "var path=url.pathname;"
    "if(path==='/api'||path.indexOf('/api/')===0||path==='/plugins'||path.indexOf('/plugins/')===0||path==='/assets'||path.indexOf('/assets/')===0){"
    "url.pathname=prefix+path;"
    "}"
    "return url;"
    "}"
    "var fetchOrig=globalThis.fetch.bind(globalThis);"
    "globalThis.fetch=function(input,init){"
    "if(typeof Request!=='undefined'&&input instanceof Request){"
    "return fetchOrig(new Request(rewrite(input.url),input),init);"
    "}"
    "return fetchOrig(rewrite(input),init);"
    "};"
    "var WS=globalThis.WebSocket;"
    "function Wrapped(url,protocols){"
    "var href=rewrite(url).href;"
    "return protocols===undefined?new WS(href):new WS(href,protocols);"
    "}"
    "Wrapped.prototype=WS.prototype;"
    "Wrapped.CONNECTING=WS.CONNECTING;"
    "Wrapped.OPEN=WS.OPEN;"
    "Wrapped.CLOSING=WS.CLOSING;"
    "Wrapped.CLOSED=WS.CLOSED;"
    "globalThis.WebSocket=Wrapped;"
    "})();</script>"
)
_SKILLS_ROOT = Path(_CFG.skills_dir)
SKILLS = (
    _SKILLS_ROOT / "desktop-control",
    _SKILLS_ROOT / "model-adapter",
    _SKILLS_ROOT / "model-discovery",
)

_proc: subprocess.Popen | None = None
_proxy: ThreadingHTTPServer | None = None
_dsh_cookie = ""


def available() -> bool:
    if not BIN.is_file() or not DIST.is_file():
        return False
    if str(load_config().deepseek_api_key or "").strip():
        return True
    from gateway.adapters.registry import has_non_official_chat
    return has_non_official_chat()


def embed_url() -> str:
    return "/deepseek/"


def _rewrite_web(raw: bytes, ctype: str) -> bytes:
    low = ctype.lower()
    if "text/" not in low and "javascript" not in low and "json" not in low:
        return raw
    text = raw.decode("utf-8")
    text = text.replace('<base href="/">', '<base href="/deepseek/">')
    text = text.replace('const API_PATH = "/api"', 'const API_PATH = "/deepseek/api"')
    text = text.replace('"/plugins/', '"/deepseek/plugins/')
    text = text.replace("'/plugins/", "'/deepseek/plugins/")
    text = text.replace('"/assets/', '"/deepseek/assets/')
    text = text.replace("'/assets/", "'/deepseek/assets/")
    text = text.replace("url(/assets/", "url(/deepseek/assets/")
    text = text.replace('"/api/', '"/deepseek/api/')
    text = text.replace("'/api/", "'/deepseek/api/")
    text = text.replace("`/api/", "`/deepseek/api/")
    if "html" in low:
        marker = "<head>"
        if marker not in text:
            if 'id="root"' in text:
                raise RuntimeError("DeepSeek 首页没有 head,不能打路径前缀")
        else:
            text = text.replace(marker, marker + PREFIX_PATCH, 1)
    return text.encode("utf-8")


def install(app) -> None:
    from embed_proxy import make_router
    app.include_router(make_router("/deepseek", f"http://127.0.0.1:{EMBED_PORT}", rewrite=_rewrite_web))


def _port_taken(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _lan_hosts() -> list[str]:
    hosts: list[str] = []
    name = socket.gethostname()
    if name and name.lower() != "localhost":
        hosts.append(name)
    for info in socket.getaddrinfo(name, None, socket.AF_INET):
        ip = info[4][0]
        if ip.startswith("127.") or ip in hosts:
            continue
        hosts.append(ip)
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.connect(("1.1.1.1", 80))
    ip = probe.getsockname()[0]
    probe.close()
    if not ip.startswith("127.") and ip not in hosts:
        hosts.append(ip)
    if not hosts:
        raise RuntimeError("找不到局域网地址,DeepSeek 嵌入代理不能启动")
    return hosts


def _patch_html(body: bytes) -> bytes:
    if body[:2] == b"\x1f\x8b":
        raise RuntimeError("DeepSeek 首页是 gzip,嵌入通道不能解")
    text = body.decode("utf-8")
    marker = "<head>"
    if marker not in text:
        raise RuntimeError("DeepSeek 首页没有 head,不能打 randomUUID 补丁")
    return text.replace(marker, marker + UUID_PATCH, 1).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self):
        if (self.headers.get("Upgrade") or "").lower() == "websocket":
            self._forward_ws()
            return
        self._safe_forward()

    def do_POST(self):
        self._safe_forward()

    def do_PUT(self):
        self._safe_forward()

    def do_PATCH(self):
        self._safe_forward()

    def do_DELETE(self):
        self._safe_forward()

    def do_HEAD(self):
        self._safe_forward()

    def do_OPTIONS(self):
        self._safe_forward()

    def _safe_forward(self) -> None:
        try:
            self._forward()
        except Exception as exc:
            payload = ("%s: %s" % (type(exc).__name__, exc)).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
                self.wfile.flush()

    def _backend_headers(self) -> dict[str, str]:
        if not _dsh_cookie:
            raise RuntimeError("dsh 认证 cookie 未就绪")
        headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in HOP and k.lower() != "accept-encoding"
        }
        headers["Host"] = "127.0.0.1:%s" % PORT
        headers["Cookie"] = _dsh_cookie
        headers["Accept-Encoding"] = "identity"
        return headers

    def _forward_ws(self) -> None:
        backend = socket.create_connection(("127.0.0.1", PORT))
        headers = self._backend_headers()
        for key in ("Upgrade", "Connection", "Sec-WebSocket-Key", "Sec-WebSocket-Version",
                    "Sec-WebSocket-Extensions", "Sec-WebSocket-Protocol"):
            value = self.headers.get(key)
            if value:
                headers[key] = value
        lines = [f"{self.command} {self.path} HTTP/1.1"]
        for key, value in headers.items():
            lines.append(f"{key}: {value}")
        backend.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1"))
        client = self.connection

        def pump(src: socket.socket, dst: socket.socket) -> None:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)

        up = threading.Thread(target=pump, args=(client, backend), daemon=True)
        up.start()
        pump(backend, client)
        up.join()
        backend.close()

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = self._backend_headers()
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=None)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            encoding = (resp.getheader("Content-Encoding") or "").lower()
            if encoding and encoding != "identity":
                raise RuntimeError("dsh 返回了压缩正文: %s" % encoding)
            ctype = (resp.getheader("Content-Type") or "").lower()
            if self.path in ("/", "/index.html") and "text/html" in ctype:
                raw = _patch_html(raw)
            self.send_response(resp.status, resp.reason)
            skip = {"content-length", "content-encoding", "x-frame-options"} | HOP
            for key, value in resp.getheaders():
                if key.lower() in skip:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if self.command == "HEAD":
                return
            self.wfile.write(raw)
            self.wfile.flush()
        finally:
            conn.close()


def _cmd_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def _gateway_chat() -> tuple[str, list[str], str, str, str]:
    from apps.nextagent.config import configured_gateway_base, load_chat_models, load_config

    cfg = load_config()
    ids = [str(item.get("id") or "").strip() for item in load_chat_models()]
    ids = [item for item in ids if item]
    want = (cfg.model or "").strip()
    default = want if want in ids else (ids[0] if ids else "")
    key = str(cfg.api_key or "").strip()
    if not key:
        raise RuntimeError("config.json 没有 api_key,DeepSeek Harness 不能启动")
    base = str(cfg.base_url or "").strip()
    if not base:
        raise RuntimeError("config.json 没有 base_url,DeepSeek Harness 不能启动")
    return default, ids, base, key, configured_gateway_base()


def _adapt_skill_md(name: str, text: str, dest: Path, gateway_base: str, python: str, key: str) -> str:
    text = text.replace("${AGENT_PYTHON}", python)
    text = text.replace("${HERMES_SKILL_DIR}", _cmd_path(dest))
    text = text.replace("${GATEWAY_BASE}", gateway_base)
    text = text.replace("file_access_read", "read")
    text = text.replace("file_access_*", "read / write / edit")
    text = text.replace("file_access", "write")
    text = text.replace("write_file", "write")
    text = text.replace("read_file", "read")
    text = text.replace(
        "Authorization: Bearer hhy-fdbbfe7eb27fab7b8d3409e2",
        f"Authorization: Bearer {key}",
    )
    if name == "model-adapter":
        text = text.replace(
            "agent 能用 `read / write / edit` 读写、用 `render_media` 在对话里展示。",
            "agent 能用 read / write / edit 读写,文件管理器里能直接看到。",
        )
    if name == "model-discovery":
        old = (
            "## 4. 拿到结果后：直接 render_media 展示\n"
            "\n"
            "产物已经由网关落进工作区了，`data.paths` 里就是文件的真实绝对路径、正斜杠（形如\n"
            "`D:/mafagent/data/workspace/media/seedream/a1b2c3d4.png`）。**不用下载**，直接把这个路径原样交给工具（不是 curl）：\n"
            "\n"
            "```\n"
            "render_media(path=\"D:/mafagent/data/workspace/media/seedream/a1b2c3d4.png\")\n"
            "```\n"
            "\n"
            "图片/视频/音频会直接在对话里播放，其他格式给下载卡片。\n"
            "\n"
            "路径要从响应 JSON 里读出来照抄，一个字符都不要改 —— 自己手打或者凭印象重写，\n"
            "文件就找不到了。要看文件内容用 `read`，要挪位置用 `read / write / edit`。\n"
            "\n"
            "**不调 render_media，用户就只看到一段路径文字，看不到东西。**\n"
        )
        new = (
            "## 4. 拿到结果后：把工作区路径告诉用户\n"
            "\n"
            "产物已经由网关落进工作区了，`data.paths` 里就是文件的真实绝对路径、正斜杠（形如\n"
            "`D:/mafagent/data/workspace/media/seedream/a1b2c3d4.png`）。不用下载。路径要从响应 JSON 里读出来照抄，\n"
            "一个字符都不要改。要看文件用 read，要改文件用 write / edit。\n"
            "文件管理器里能直接看到这些文件。\n"
        )
        if old not in text:
            raise RuntimeError("model-discovery 的第 4 节对不上,不能改写")
        text = text.replace(old, new)
    return text


def _sync_skills(gateway_base: str, key: str) -> None:
    python = _cmd_path(Path(sys.executable))
    root = HOME / "skills"
    root.mkdir(parents=True, exist_ok=True)
    for src in SKILLS:
        if not src.joinpath("SKILL.md").is_file():
            raise RuntimeError(f"找不到 skill: {src}")
        dest = root / src.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        md = dest / "SKILL.md"
        text = _adapt_skill_md(src.name, md.read_text(encoding="utf-8"), dest, gateway_base, python, key)
        md.write_text(text, encoding="utf-8")


def _apply_home(default: str, ids: list[str], base: str, key: str) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    settings = {
        "llm-pi-ai": {
            "providers": {
                PROVIDER: {
                    "displayName": PROVIDER,
                    "apiKeyEnv": CREDENTIAL,
                    "api": "openai-completions",
                    "baseURL": base,
                    "models": [{"id": item, "name": item} for item in ids],
                }
            }
        },
        "agent-default-model": {
            "provider": PROVIDER,
            "model": default,
        },
    }
    (HOME / "settings.yaml").write_text(
        yaml.safe_dump(settings, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    cred = HOME / ".credentials.yaml"
    cred.write_text(
        yaml.safe_dump({CREDENTIAL: key}, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    os.chmod(cred, 0o600)


def _parse_token_url(line: str) -> str:
    prefix = "dsh web: "
    if not line.startswith(prefix):
        return ""
    url = line[len(prefix):].strip().split(" ", 1)[0]
    if "/?token=" not in url:
        return ""
    return url


def _relay_stdout(stream) -> None:
    for line in stream:
        sys.stdout.write(line)
        sys.stdout.flush()


def _wait_ready(timeout: float = 90.0) -> str:
    if _proc is None or _proc.stdout is None:
        raise RuntimeError("dsh 没有标准输出")
    deadline = time.time() + timeout
    leftover = []
    while time.time() < deadline:
        if _proc.poll() is not None:
            rest = _proc.stdout.read()
            raise RuntimeError(f"dsh 已退出,code={_proc.returncode} {''.join(leftover)}{rest}")
        line = _proc.stdout.readline()
        if line == "":
            raise RuntimeError("dsh 输出结束,没有给出启动地址: " + "".join(leftover))
        sys.stdout.write(line)
        sys.stdout.flush()
        leftover.append(line)
        url = _parse_token_url(line.rstrip("\r\n"))
        if url:
            threading.Thread(target=_relay_stdout, args=(_proc.stdout,), daemon=True).start()
            return url
    raise RuntimeError("dsh 没有在 %.0f 秒内给出启动地址: %s" % (timeout, "".join(leftover)))


def _exchange_cookie(token_url: str) -> str:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.open(token_url, timeout=5)
    probe = urllib.request.Request("http://127.0.0.1:%s/" % PORT)
    jar.add_cookie_header(probe)
    cookie = probe.get_header("Cookie")
    if not cookie:
        raise RuntimeError("dsh 没有下发认证 cookie")
    return cookie


def _wait_workspace(workspace: str, cookie: str, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    last = ""
    payload = json.dumps({
        "type": "client-request",
        "rpcId": "mafagent-workspace",
        "method": "workspace/create",
        "payload": {"args": {"request": {"path": workspace}}},
    }).encode("utf-8")
    url = f"http://127.0.0.1:{PORT}/api/workspace/create"
    while time.time() < deadline:
        if _proc is not None and _proc.poll() is not None:
            raise RuntimeError(f"dsh 已退出,code={_proc.returncode} {last}")
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                method="POST",
                headers={"Content-Type": "application/json", "Cookie": cookie},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            result = data.get("result")
            if isinstance(result, dict) and result.get("ok") is True:
                return
            last = raw
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in (401, 403, 404):
                raise RuntimeError(f"dsh 登记工作区失败: {exc.code} {body}")
            last = f"{exc.code} {body}"
        except Exception as exc:
            last = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"dsh 没有在 {timeout:g} 秒内登记工作区: {last}")


def start(workspace: str) -> None:
    global _proc, _proxy, _dsh_cookie
    if not BIN.is_file():
        raise RuntimeError(f"找不到 dsh CLI: {BIN}")
    if not DIST.is_file():
        raise RuntimeError(f"找不到 dsh 前端产物: {DIST}")
    if _port_taken(PORT):
        raise RuntimeError(f"127.0.0.1:{PORT} 已被占用,DeepSeek Harness 不能启动")
    if _port_taken(EMBED_PORT):
        raise RuntimeError(f"127.0.0.1:{EMBED_PORT} 已被占用,DeepSeek 嵌入代理不能启动")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("找不到 node,DeepSeek Harness 不能启动")
    root = str(Path(workspace).resolve())
    if not Path(root).is_dir():
        raise RuntimeError(f"工作目录不存在: {root}")
    if not any(Path(root).iterdir()):
        raise RuntimeError(f"工作目录是空的: {root}")
    default, ids, base, key, gateway_base = _gateway_chat()
    _apply_home(default, ids, base, key)
    _sync_skills(gateway_base, key)
    env = os.environ.copy()
    env["DSH_HOME"] = str(HOME)
    env.pop("OPENAI_BASE_URL", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("DEEPSEEK_BASE_URL", None)
    hosts = _lan_hosts()
    cmd = [node, "--expose-internals", str(BIN), "--profile", "web", "--port", str(PORT), "--no-open", "--trusted-host", *hosts]
    _proc = subprocess.Popen(
        cmd,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    try:
        token_url = _wait_ready()
        _dsh_cookie = _exchange_cookie(token_url)
        _wait_workspace(root, _dsh_cookie)
    except Exception:
        stop()
        raise
    _proxy = ThreadingHTTPServer(("0.0.0.0", EMBED_PORT), _Handler)
    thread = threading.Thread(target=_proxy.serve_forever, daemon=True)
    thread.start()


def stop() -> None:
    global _proc, _proxy, _dsh_cookie
    _dsh_cookie = ""
    if _proxy is not None:
        _proxy.shutdown()
        _proxy.server_close()
        _proxy = None
    if _proc is None:
        return
    _proc.terminate()
    try:
        _proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _proc.kill()
    _proc = None
