"""mafagent agent 侧配置。

Config / load_config 统一来自项目根 appconfig(唯一配置文件:根目录 config.json)。
本模块只保留 agent 专属派生:对话模型清单直接来自内嵌网关的 adapter 注册表
(load_chat_models() → 网关 list_chat_catalog()),agent 写完新 adapter 调
/api/llm-proxy/v1/admin/reload 后,模型下拉与 media_caps 立即同步。
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
    """对话模型目录,直接取自内嵌网关的 adapter 注册表,每次实时读取不缓存。"""
    from gateway.adapters.registry import list_chat_catalog

    models = list_chat_catalog()
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict) and str(m.get("id") or "").strip()]


def resolve_default_chat_model(preferred: str = "") -> tuple[str, list[str]]:
    from gateway.adapters.registry import requires_official_deepseek_key

    ids = [str(item.get("id") or "").strip() for item in load_chat_models()]
    ids = [item for item in ids if item]
    if not ids:
        raise RuntimeError("网关对话模型清单为空")
    want = (preferred or "").strip()
    if want in ids:
        return want, ids
    if not str(load_config().deepseek_api_key or "").strip():
        usable = [item for item in ids if not requires_official_deepseek_key(item)]
        if usable:
            return usable[0], ids
    return ids[0], ids


def configured_gateway_base() -> str:
    return _get_cfg().gateway_base
