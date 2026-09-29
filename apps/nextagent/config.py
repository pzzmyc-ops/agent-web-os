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
    import json
    import urllib.error
    import urllib.request

    from gateway.adapters.registry import list_chat_catalog

    cfg = load_config()
    base = str(cfg.base_url or "").strip().rstrip("/")
    local = cfg.gateway_base.rstrip("/") + "/api/llm-proxy/v1"
    if base == local:
        return list_chat_catalog()
    url = base + "/models"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError("取不到模型列表: " + url + " " + str(exc)) from exc
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("模型列表格式不对: " + url)
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("kind") not in (None, "", "llm"):
            continue
        mid = str(item.get("id") or "").strip()
        if not mid:
            continue
        out.append({
            "id": mid,
            "displayName": str(item.get("displayName") or mid),
            "provider": item.get("provider") or item.get("owned_by") or "",
            "media_caps": item.get("media_caps") if isinstance(item.get("media_caps"), dict) else {},
            "context_window": item.get("context_window"),
            "description": str(item.get("description") or ""),
            "ui": item.get("ui") if isinstance(item.get("ui"), dict) else {},
        })
    return out


def resolve_default_chat_model(preferred: str = "") -> tuple[str, list[str]]:
    ids = [str(item.get("id") or "").strip() for item in load_chat_models()]
    ids = [item for item in ids if item]
    if not ids:
        raise RuntimeError("网关对话模型清单为空")
    want = (preferred or "").strip()
    if want in ids:
        return want, ids
    return ids[0], ids


def configured_gateway_base() -> str:
    return _get_cfg().gateway_base
