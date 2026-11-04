"""全项目统一配置。

唯一配置来源:项目根目录 config.json。fm / gateway / apps 三方都从这里读,不再经
环境变量注入。改配置只改 config.json 一个文件。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_CONFIG_PATH = _PROJECT_ROOT / "config.json"


@dataclass
class Config:
    api_key: str
    max_context_window_tokens: int
    max_output_tokens: int
    web_port: int
    web_password: str
    data_dir: str = "data"
    fm_root: str = ""
    onlyoffice_url: str = ""
    onlyoffice_jwt: str = ""
    public_url: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    embedding_model: str = ""
    embedding_url: str = ""
    embedding_api_key: str = ""
    comfyui_port: int = 8188
    comfyui_url: str = ""
    hermes_port: int = 18787
    hermes_embed_port: int = 18788
    deepseek_port: int = 13080
    deepseek_embed_port: int = 13081
    remote_port: int = 14822
    guacd_port: int = 4822
    upstreams: list = field(default_factory=list)

    @property
    def gateway_base(self) -> str:
        return f"http://127.0.0.1:{self.web_port}"

    @property
    def db_path(self) -> str:
        d = Path(self.data_dir)
        if not d.is_absolute():
            d = _PROJECT_ROOT / d
        return str(d / "app.db")

    @property
    def workspace_dir(self) -> str:
        d = Path(self.data_dir)
        if not d.is_absolute():
            d = _PROJECT_ROOT / d
        return str(d / "workspace")

    @property
    def desktop_state_path(self) -> str:
        d = Path(self.data_dir)
        if not d.is_absolute():
            d = _PROJECT_ROOT / d
        return str(d / "desktop_state.json")

    @property
    def fm_root_dir(self) -> str:
        raw = (self.fm_root or "").strip()
        if not raw:
            return self.workspace_dir
        d = Path(raw)
        if not d.is_absolute():
            d = _PROJECT_ROOT / d
        return str(d)

    @property
    def adapters_dir(self) -> str:
        return str(Path(self.fm_root_dir) / "adapters")

    @property
    def index_dir(self) -> str:
        return str(Path(self.fm_root_dir) / ".index")

    @property
    def skills_dir(self) -> str:
        return str(Path(self.fm_root_dir) / "skills")

    @property
    def usage_db(self) -> str:
        return str(_PROJECT_ROOT / "gateway" / "data" / "usage.db")

    @property
    def public_url_effective(self) -> str:
        return (self.public_url or f"http://127.0.0.1:{self.web_port}").rstrip("/")


def load_config() -> Config:
    raw: dict = {}
    if _CONFIG_PATH.is_file():
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    windows = os.name == "nt"

    def pick(key: str, default):
        v = raw.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        return v

    def need(key: str):
        if raw.get(key) in (None, ""):
            raise RuntimeError(f"{key} 未配置:在 {_CONFIG_PATH} 里设 {key}")
        return raw[key]

    def platform(key: str):
        name = key if windows else "linux_" + key
        return need(name)

    def http_origin(key: str, value: str) -> str:
        from urllib.parse import urlparse
        text = str(value).strip().rstrip("/")
        parsed = urlparse(text)
        if parsed.scheme != "http" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise RuntimeError(key + " 必须是不带路径的 http 地址: " + text)
        return text

    web_port = int(platform("web_port"))
    onlyoffice_url = http_origin("onlyoffice_url" if windows else "linux_onlyoffice_url", platform("onlyoffice_url"))
    ollama_url = http_origin("ollama_url" if windows else "linux_ollama_url", platform("ollama_url"))
    comfyui_key = "comfyui_url" if windows else "linux_comfyui_url"
    comfyui_url = str(raw.get(comfyui_key) or "").strip().rstrip("/")
    if comfyui_url:
        comfyui_url = http_origin(comfyui_key, comfyui_url)
    comfyui_port = int(platform("comfyui_port"))
    hermes_port = int(platform("hermes_port"))
    hermes_embed_port = int(platform("hermes_embed_port"))
    deepseek_port = int(platform("deepseek_port"))
    deepseek_embed_port = int(platform("deepseek_embed_port"))
    remote_port = int(platform("remote_port"))
    guacd_port = int(platform("guacd_port"))
    public_url = str(pick("public_url", ""))
    embedding_url = str(need("embedding_url")).strip().rstrip("/")
    if not embedding_url.startswith(("http://", "https://")):
        raise RuntimeError("embedding_url 必须是 http 或 https 地址: " + embedding_url)

    return Config(
        api_key=str(pick("api_key", "")),
        max_context_window_tokens=int(pick("max_context_window_tokens", 1048576)),
        max_output_tokens=int(pick("max_output_tokens", 16384)),
        web_port=web_port,
        web_password=str(need("web_password")),
        data_dir=str(pick("data_dir", "data")),
        fm_root=str(pick("fm_root", "")),
        onlyoffice_url=onlyoffice_url,
        onlyoffice_jwt=str(pick("onlyoffice_jwt", "")),
        public_url=public_url,
        ollama_url=ollama_url,
        embedding_model=str(need("embedding_model")),
        embedding_url=embedding_url,
        embedding_api_key=str(need("embedding_api_key")),
        comfyui_port=comfyui_port,
        comfyui_url=comfyui_url,
        hermes_port=hermes_port,
        hermes_embed_port=hermes_embed_port,
        deepseek_port=deepseek_port,
        deepseek_embed_port=deepseek_embed_port,
        remote_port=remote_port,
        guacd_port=guacd_port,
        upstreams=normalize_upstreams(pick("upstreams", [])),
    )


def normalize_upstreams(raw) -> list[dict]:
    from urllib.parse import urlparse

    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise RuntimeError("upstreams 必须是数组")
    out: list[dict] = []
    names: set[str] = set()
    models_seen: dict[str, str] = {}
    for i, item in enumerate(raw):
        where = f"upstreams[{i}]"
        if not isinstance(item, dict):
            raise RuntimeError(where + " 必须是对象")
        name = str(item.get("name") or "").strip()
        if not name:
            raise RuntimeError(where + " 缺少 name")
        if name.lower() in names:
            raise RuntimeError(where + " 名称重复: " + name)
        names.add(name.lower())
        base_url = str(item.get("base_url") or "").strip().rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
            raise RuntimeError(where + " base_url 必须是 http(s) 地址且不带查询串: " + base_url)
        api_key = str(item.get("api_key") or "").strip()
        models_raw = item.get("models")
        if isinstance(models_raw, str):
            models_raw = [m for m in models_raw.replace(",", "\n").split("\n")]
        if not isinstance(models_raw, list):
            raise RuntimeError(where + " models 必须是数组")
        models: list[str] = []
        for m in models_raw:
            mid = str(m or "").strip()
            if not mid:
                continue
            if mid in models:
                continue
            owner = models_seen.get(mid)
            if owner is not None and owner != name:
                raise RuntimeError(where + f" 模型 {mid} 已在上游 {owner} 里登记,模型名不能跨上游重复")
            models_seen[mid] = name
            models.append(mid)
        if not models:
            raise RuntimeError(where + " 至少要填一个模型")
        headers_raw = item.get("headers") or {}
        if not isinstance(headers_raw, dict):
            raise RuntimeError(where + " headers 必须是对象")
        headers = {str(k).strip(): str(v) for k, v in headers_raw.items() if str(k).strip()}
        out.append({
            "name": name,
            "base_url": base_url,
            "api_key": api_key,
            "models": models,
            "headers": headers,
        })
    return out


def write_upstreams(upstreams) -> list[dict]:
    clean = normalize_upstreams(upstreams)
    if not _CONFIG_PATH.is_file():
        raise RuntimeError("找不到 config.json")
    raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("config.json 不是对象")
    raw["upstreams"] = clean
    raw.pop("model", None)
    _CONFIG_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return clean


def normalize_embedding(url: str, api_key: str, model: str) -> dict:
    text = str(url or "").strip().rstrip("/")
    if not text.startswith(("http://", "https://")):
        raise RuntimeError("embedding_url 必须是 http 或 https 地址: " + text)
    name = str(model or "").strip()
    if not name:
        raise RuntimeError("嵌入模型名不能为空")
    key = str(api_key or "").strip()
    if not key:
        raise RuntimeError("嵌入服务密钥不能为空")
    return {"embedding_url": text, "embedding_api_key": key, "embedding_model": name}


def write_embedding(url: str, api_key: str, model: str) -> dict:
    clean = normalize_embedding(url, api_key, model)
    if not _CONFIG_PATH.is_file():
        raise RuntimeError("找不到 config.json")
    raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("config.json 不是对象")
    raw.update(clean)
    raw.pop("model", None)
    _CONFIG_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return clean
