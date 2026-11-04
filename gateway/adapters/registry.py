from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Type

from gateway.adapters._external import load_external_modules
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


MEDIA: dict[str, MediaModelAdapter] = {}


def _load_all(*, reload_existing: bool) -> None:
    media = _instantiate_media(_discover(MediaModelAdapter, reload_existing=reload_existing))
    MEDIA.clear()
    MEDIA.update(media)


_load_all(reload_existing=False)


def reload_adapters() -> dict[str, list[str]]:
    _load_all(reload_existing=True)
    return {"media": sorted(MEDIA.keys())}


def resolve_media(model_id: str) -> MediaModelAdapter:
    key = (model_id or "").strip()
    adapter = MEDIA.get(key.lower())
    if adapter is None:
        raise ValueError(f"unknown media model: {key}")
    return adapter


def list_all_models() -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for key in sorted(MEDIA.keys()):
        media = MEDIA[key]
        mid = (media.model_id or key).strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append({"id": mid, "provider": media.provider, "kind": "media"})
    return out
