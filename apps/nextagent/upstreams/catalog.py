from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from appconfig import load_config, normalize_upstreams, write_upstreams


@dataclass(frozen=True)
class Upstream:
    name: str
    base_url: str
    api_key: str
    models: tuple[str, ...]
    headers: dict[str, str]


def _to_upstream(item: dict) -> Upstream:
    return Upstream(
        name=str(item["name"]),
        base_url=str(item["base_url"]),
        api_key=str(item.get("api_key") or ""),
        models=tuple(str(m) for m in item["models"]),
        headers=dict(item.get("headers") or {}),
    )


def load_upstreams() -> list[Upstream]:
    return [_to_upstream(item) for item in load_config().upstreams]


def upstream_from_dict(raw: dict) -> Upstream:
    return _to_upstream(normalize_upstreams([raw])[0])


def save_upstreams(raw_list: list[dict]) -> list[dict]:
    return write_upstreams(raw_list)


def public_upstreams() -> list[dict]:
    out = []
    for u in load_upstreams():
        out.append({
            "name": u.name,
            "base_url": u.base_url,
            "api_key": u.api_key,
            "models": list(u.models),
            "headers": dict(u.headers),
        })
    return out


def _model_ui() -> dict[str, Any]:
    return {
        "params": [
            {"submit_key": "thinking", "kind": "boolean", "label": "Thinking", "default": False},
            {
                "submit_key": "reasoning_effort",
                "kind": "enum",
                "label": "Effort level",
                "default": "medium",
                "shows_when": {"submit_key": "thinking", "equals": True},
                "options": [
                    {"value": "low", "label": "low"},
                    {"value": "medium", "label": "medium"},
                    {"value": "high", "label": "high"},
                ],
            },
        ]
    }


def model_id(upstream_name: str, model: str) -> str:
    return upstream_name + "/" + model


def _display_name(u: Upstream, model: str) -> str:
    if len(u.models) == 1:
        return u.name
    return u.name + " · " + model


def model_catalog() -> list[dict]:
    out: list[dict] = []
    for u in load_upstreams():
        for mid in u.models:
            out.append({
                "id": model_id(u.name, mid),
                "displayName": _display_name(u, mid),
                "model": mid,
                "provider": u.name,
                "media_caps": {},
                "context_window": None,
                "description": u.base_url,
                "ui": _model_ui(),
            })
    return out


def resolve_model(model: str) -> tuple[Upstream, str]:
    mid = str(model or "").strip()
    if not mid:
        raise RuntimeError("模型名为空")
    for u in load_upstreams():
        for m in u.models:
            if model_id(u.name, m) == mid:
                return u, m
    raise RuntimeError("模型 " + mid + " 不在任何上游接口的模型列表里,请到「上游接口」里添加")
