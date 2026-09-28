"""全项目统一配置。

唯一配置来源:项目根目录 config.json。fm / gateway / apps 三方都从这里读,不再经
环境变量注入。改配置只改 config.json 一个文件。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_CONFIG_PATH = _PROJECT_ROOT / "config.json"


@dataclass
class Config:
    base_url: str
    api_key: str
    model: str
    max_context_window_tokens: int
    max_output_tokens: int
    web_port: int
    data_dir: str = "data"
    fm_root: str = ""
    onlyoffice_url: str = ""
    onlyoffice_jwt: str = ""
    public_url: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "bge-m3"
    comfyui_port: int = 8188
    hermes_port: int = 18787
    hermes_embed_port: int = 18788
    deepseek_port: int = 13080
    deepseek_embed_port: int = 13081
    remote_port: int = 14822
    guacd_port: int = 4822
    deepseek_api_key: str = ""

    @property
    def gateway_port(self) -> int:
        from urllib.parse import urlparse
        p = urlparse(self.base_url).port
        return p if p else self.web_port

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

    def pick(key: str, default):
        v = raw.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        return v

    if raw.get("web_port") is None:
        raise RuntimeError(f"web_port 未配置:在 {_CONFIG_PATH} 里设 web_port")
    web_port = int(raw["web_port"])
    onlyoffice_url = str(pick("onlyoffice_url", ""))
    public_url = str(pick("public_url", ""))
    ollama_url = str(pick("ollama_url", "http://127.0.0.1:11434")).rstrip("/")
    if os.name == "nt":
        comfyui_port = 8188 if raw.get("comfyui_port") is None else int(raw["comfyui_port"])
        hermes_port = 18787 if raw.get("hermes_port") is None else int(raw["hermes_port"])
        hermes_embed_port = 18788 if raw.get("hermes_embed_port") is None else int(raw["hermes_embed_port"])
        deepseek_port = 13080 if raw.get("deepseek_port") is None else int(raw["deepseek_port"])
        deepseek_embed_port = 13081 if raw.get("deepseek_embed_port") is None else int(raw["deepseek_embed_port"])
        remote_port = 14822 if raw.get("remote_port") is None else int(raw["remote_port"])
        guacd_port = 4822 if raw.get("guacd_port") is None else int(raw["guacd_port"])
    else:
        for name in (
            "comfyui_port",
            "hermes_port",
            "hermes_embed_port",
            "deepseek_port",
            "deepseek_embed_port",
            "remote_port",
            "guacd_port",
        ):
            if raw.get(name) is None:
                raise RuntimeError(f"{name} 未配置:在 {_CONFIG_PATH} 里设 {name}")
        comfyui_port = int(raw["comfyui_port"])
        hermes_port = int(raw["hermes_port"])
        hermes_embed_port = int(raw["hermes_embed_port"])
        deepseek_port = int(raw["deepseek_port"])
        deepseek_embed_port = int(raw["deepseek_embed_port"])
        remote_port = int(raw["remote_port"])
        guacd_port = int(raw["guacd_port"])

    return Config(
        base_url=str(pick("base_url", f"http://127.0.0.1:{web_port}/api/llm-proxy/v1")),
        api_key=str(pick("api_key", "")),
        model=str(pick("model", "")),
        max_context_window_tokens=int(pick("max_context_window_tokens", 1048576)),
        max_output_tokens=int(pick("max_output_tokens", 16384)),
        web_port=web_port,
        data_dir=str(pick("data_dir", "data")),
        fm_root=str(pick("fm_root", "")),
        onlyoffice_url=onlyoffice_url,
        onlyoffice_jwt=str(pick("onlyoffice_jwt", "")),
        public_url=public_url,
        ollama_url=ollama_url,
        embedding_model=str(pick("embedding_model", "bge-m3")),
        comfyui_port=comfyui_port,
        hermes_port=hermes_port,
        hermes_embed_port=hermes_embed_port,
        deepseek_port=deepseek_port,
        deepseek_embed_port=deepseek_embed_port,
        remote_port=remote_port,
        guacd_port=guacd_port,
        deepseek_api_key=str(pick("deepseek_api_key", "")),
    )


def write_deepseek_api_key(key: str) -> None:
    text = str(key or "").strip()
    if not text:
        raise RuntimeError("DeepSeek API Key 为空")
    if not _CONFIG_PATH.is_file():
        raise RuntimeError("找不到 config.json")
    raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("config.json 不是对象")
    raw["deepseek_api_key"] = text
    _CONFIG_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
