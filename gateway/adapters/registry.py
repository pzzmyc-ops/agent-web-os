from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Type

from gateway.adapters._external import load_external_modules
from gateway.adapters.base import ModelAdapter
from gateway.adapters.media_base import MediaModelAdapter

_INFRA = frozenset({"base", "media_base", "catalog_types", "registry", "oss", "workspace", "_external"})


def _collect(mod, base) -> list:
    out = []
    for _n, obj in inspect.getmembers(mod, inspect.isclass):
        if obj is base or not issubclass(obj, base):
            continue
        if getattr(obj, "_skip_discovery", False):
            continue
        if obj.__module__ != mod.__name__:
            continue
        out.append(obj)
    return sorted(out, key=lambda c: c.__name__)


def _discover(base, *, reload_existing: bool) -> list:
    pkg_dir = Path(__file__).resolve().parent
    pkg_name = __package__ or "gateway.adapters"
    collected: list = []
    for _f, modname, _ispkg in pkgutil.iter_modules([str(pkg_dir)]):
        if modname.startswith("_") or modname in _INFRA:
            continue
        full = f"{pkg_name}.{modname}"
        mod = importlib.reload(sys.modules[full]) if (reload_existing and full in sys.modules) else importlib.import_module(full)
        collected.extend(_collect(mod, base))
    for mod in load_external_modules(reload=reload_existing):
        collected.extend(_collect(mod, base))
    return collected


def _instantiate_chat(classes: list[Type[ModelAdapter]]) -> dict[str, ModelAdapter]:
    """路由表的键一律小写。

    adapter 常把同一个模型的几种写法都列进 routing_keys(如 Qwen3.8-27B 与
    qwen3.8-27b),小写归一后它们落在同一个键上,所以同一个 adapter 的重复不算冲突;
    两个不同 adapter 抢同一个键才是错。
    """
    adapters: dict[str, ModelAdapter] = {}
    seen: dict[Type[ModelAdapter], ModelAdapter] = {}
    for cls in classes:
        keys = tuple(str(x).strip() for x in (cls.routing_keys or ()) if str(x).strip()) or (cls.route_id,)
        inst = seen.get(cls)
        if inst is None:
            inst = cls()
            seen[cls] = inst
        for k in keys:
            lower = k.lower()
            if lower in adapters and adapters[lower] is not inst:
                raise ValueError(f"duplicate routing key: {k}")
            adapters[lower] = inst
    return adapters


def _instantiate_media(classes: list[Type[MediaModelAdapter]]) -> dict[str, MediaModelAdapter]:
    adapters: dict[str, MediaModelAdapter] = {}
    for cls in classes:
        inst = cls()
        mid = str(inst.model_id or "").strip().lower()
        if not mid:
            raise ValueError(f"media adapter {cls.__name__} missing model_id")
        if mid in adapters:
            raise ValueError(f"duplicate media model_id: {mid}")
        adapters[mid] = inst
    return adapters


CHAT: dict[str, ModelAdapter] = {}
MEDIA: dict[str, MediaModelAdapter] = {}


def _load_all(*, reload_existing: bool) -> None:
    chat = _instantiate_chat(_discover(ModelAdapter, reload_existing=reload_existing))
    media = _instantiate_media(_discover(MediaModelAdapter, reload_existing=reload_existing))
    CHAT.clear()
    CHAT.update(chat)
    MEDIA.clear()
    MEDIA.update(media)


_load_all(reload_existing=False)


def reload_adapters() -> dict[str, list[str]]:
    _load_all(reload_existing=True)
    return {"chat": sorted(CHAT.keys()), "media": sorted(MEDIA.keys())}


def resolve_chat(model: str) -> ModelAdapter:
    key = (model or "").strip()
    if not key:
        raise ValueError("model name is required")
    adapter = CHAT.get(key.lower())
    if adapter is None:
        raise ValueError(f"unknown model: {key}")
    return adapter


def requires_official_deepseek_key(model: str) -> bool:
    key = (model or "").strip().lower()
    if not key:
        return False
    adapter = CHAT.get(key)
    if adapter is None:
        return False
    return str(adapter.__class__.__module__ or "") == "gateway.adapters.deepseek_official"


def has_non_official_chat() -> bool:
    for key, adapter in CHAT.items():
        rid = str(adapter.route_id or key).strip()
        if rid and not requires_official_deepseek_key(rid):
            return True
    return False


def resolve_media(model_id: str) -> MediaModelAdapter:
    key = (model_id or "").strip()
    adapter = MEDIA.get(key.lower())
    if adapter is None:
        raise ValueError(f"unknown media model: {key}")
    return adapter


def list_chat_catalog() -> list[dict]:
    seen: set[int] = set()
    out: list[dict] = []
    for route_id in sorted(CHAT.keys()):
        a = CHAT[route_id]
        if id(a) in seen:
            continue
        seen.add(id(a))
        pid = (a.route_id or route_id).strip()
        if not pid:
            continue
        out.append({
            "id": pid,
            "displayName": a.profile_display_name(),
            "provider": a.provider,
            "media_caps": a.profile_media_caps(),
            "context_window": a.context_window,
            "description": a.catalog_description,
            "ui": a.ui_caps(pid),
        })
    other = []
    official = []
    for item in out:
        if requires_official_deepseek_key(item["id"]):
            official.append(item)
        else:
            other.append(item)
    return other + official


def list_all_models() -> list[dict]:
    """列的是规范 id(route_id / model_id),不是路由表的小写键。

    一个 adapter 常占多个路由键(别名 + 大小写变体),按键列会把同一个模型报好几遍。
    """
    seen: set[int] = set()
    out: list[dict] = []
    for key in sorted(CHAT.keys()):
        adapter = CHAT[key]
        if id(adapter) in seen:
            continue
        seen.add(id(adapter))
        out.append({"id": (adapter.route_id or key).strip(), "provider": adapter.provider, "kind": "llm"})
    for key in sorted(MEDIA.keys()):
        media = MEDIA[key]
        out.append({"id": (media.model_id or key).strip(), "provider": media.provider, "kind": "media"})
    return out
