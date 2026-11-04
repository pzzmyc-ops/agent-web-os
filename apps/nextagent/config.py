"""mafagent agent 侧配置。

Config / load_config 统一来自项目根 appconfig(唯一配置文件:根目录 config.json)。
对话模型清单来自 config.json 的 upstreams(用户在「上游接口」窗口手动登记的
接口地址 + 模型名),协议由 upstreams 包按地址和模型名自动判断并翻译。
"""
from __future__ import annotations

from appconfig import Config, load_config

__all__ = [
    "Config",
    "load_config",
    "load_chat_models",
    "resolve_default_chat_model",
    "configured_gateway_base",
]

_cfg_cache = None


def _get_cfg():
    global _cfg_cache
    if _cfg_cache is None:
        _cfg_cache = load_config()
    return _cfg_cache


def load_chat_models() -> list[dict]:
    from .upstreams.catalog import model_catalog

    return model_catalog()


def resolve_default_chat_model(preferred: str = "") -> tuple[str, list[str]]:
    ids = [str(item.get("id") or "").strip() for item in load_chat_models()]
    ids = [item for item in ids if item]
    want = (preferred or "").strip()
    if want and want in ids:
        return want, ids
    if ids:
        return ids[0], ids
    return "", []


def configured_gateway_base() -> str:
    return _get_cfg().gateway_base
