from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from appconfig import load_config
from gateway.adapters.base import ModelAdapter, UPSTREAM_TIMEOUT
from gateway.core.types import (
    AdapterResult,
    Cost,
    RequestMeta,
    UpstreamHttpError,
    Usage,
    token_cost,
)

_URL = "https://api.deepseek.com/v1/chat/completions"


def _pad_assistant_reasoning(payload: dict[str, Any]) -> None:
    if payload["thinking"]["type"] != "enabled":
        return
    for m in payload.get("messages") or []:
        if isinstance(m, dict) and m.get("role") == "assistant" and "reasoning_content" not in m:
            m["reasoning_content"] = ""


def _map_effort(eff: str) -> str:
    e = str(eff or "").strip().lower()
    if e in {"minimal", "low"}:
        return "low"
    if e in {"high", "xhigh"}:
        return "max"
    if e == "medium":
        return "high"
    if e == "max":
        return "max"
    return "high"


class _OfficialDeepSeek(ModelAdapter):
    _skip_discovery = True
    provider = "deepseek"
    context_window = 1_000_000
    supports_thinking = True

    def ui_caps(self, route_id: str) -> dict[str, Any]:
        del route_id
        return {
            "params": [
                {
                    "submit_key": "thinking",
                    "kind": "boolean",
                    "label": "Thinking",
                    "default": True,
                },
                {
                    "submit_key": "reasoning_effort",
                    "kind": "enum",
                    "label": "Effort level",
                    "default": "high",
                    "shows_when": {"submit_key": "thinking", "equals": True},
                    "options": [
                        {"value": "low", "label": "low"},
                        {"value": "high", "label": "high"},
                        {"value": "max", "label": "max"},
                    ],
                },
            ]
        }

    def _headers(self) -> dict[str, str]:
        key = str(load_config().deepseek_api_key or "").strip()
        if not key:
            raise RuntimeError("未配置 DeepSeek API Key")
        return {
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        }

    def _inject_thinking(self, payload: dict[str, Any]) -> None:
        reasoning = payload.pop("reasoning", None)
        re_top = payload.pop("reasoning_effort", None)
        thinking = payload.pop("thinking", None)
        enabled = True
        api_eff = "high"
        if isinstance(thinking, dict) and str(thinking.get("type") or "") in {"enabled", "disabled"}:
            enabled = str(thinking.get("type")) == "enabled"
            if thinking.get("reasoning_effort"):
                api_eff = _map_effort(str(thinking.get("reasoning_effort")))
        elif thinking is False or thinking == "disabled":
            enabled = False
        elif isinstance(reasoning, dict) and reasoning.get("enabled") is False:
            enabled = False
        elif isinstance(reasoning, dict):
            api_eff = _map_effort(str(reasoning.get("effort") or "high"))
        elif isinstance(re_top, str) and re_top.strip():
            e = re_top.strip().lower()
            if e == "none":
                enabled = False
            else:
                api_eff = _map_effort(e)
        if enabled and isinstance(re_top, str) and re_top.strip() and re_top.strip().lower() != "none":
            api_eff = _map_effort(re_top)
        if enabled:
            payload["thinking"] = {"type": "enabled", "reasoning_effort": api_eff}
        else:
            payload["thinking"] = {"type": "disabled"}

    async def chat_completions(self, body: dict[str, Any], meta: RequestMeta) -> AdapterResult:
        del meta
        payload = dict(body)
        payload["model"] = self.upstream_model
        self._inject_thinking(payload)
        _pad_assistant_reasoning(payload)
        result = AdapterResult(
            provider=self.provider,
            requested_model=str(body.get("model") or self.route_id),
            adapter_model=self.adapter_model,
            upstream_model=self.upstream_model,
        )
        headers = self._headers()
        if body.get("stream"):
            async def gen() -> AsyncIterator[bytes]:
                async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
                    async with client.stream("POST", _URL, json=payload, headers=headers) as resp:
                        if resp.status_code >= 400:
                            raise UpstreamHttpError(status_code=resp.status_code, body=await resp.aread())
                        async for raw in resp.aiter_lines():
                            yield (raw + "\n").encode("utf-8")
                            if not raw.startswith("data: "):
                                continue
                            text = raw[6:].strip()
                            if not text or text == "[DONE]":
                                continue
                            u = json.loads(text).get("usage")
                            if isinstance(u, dict) and u:
                                result.usage = Usage(
                                    prompt_tokens=int(u.get("prompt_tokens") or 0),
                                    completion_tokens=int(u.get("completion_tokens") or 0),
                                    total_tokens=int(u.get("total_tokens") or 0),
                                    cached_tokens=int(u.get("prompt_cache_hit_tokens") or u.get("cached_tokens") or 0),
                                )
            result.stream = gen()
            result.media_type = "text/event-stream"
            return result
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
            resp = await client.post(_URL, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise UpstreamHttpError(status_code=resp.status_code, body=resp.json())
        data = resp.json()
        u = data.get("usage") or {}
        result.usage = Usage(
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
            total_tokens=int(u.get("total_tokens") or 0),
            cached_tokens=int(u.get("prompt_cache_hit_tokens") or u.get("cached_tokens") or 0),
        )
        result.response_json = data
        return result

    def compute_cost(self, *, usage: Usage, response_json: dict[str, Any] | None) -> Cost:
        del response_json
        return token_cost(
            currency="CNY",
            prompt_tokens=usage.prompt_tokens,
            cached_tokens=usage.cached_tokens,
            completion_tokens=usage.completion_tokens,
            text_input=1.0,
            cached_input=0.02,
            output=2.0,
        )


class DeepSeekOfficialFlashAdapter(_OfficialDeepSeek):
    _skip_discovery = False
    routing_keys = ("deepseek-official-flash",)
    route_id = "deepseek-official-flash"
    display_name = "DeepSeek Official Flash"
    catalog_description = "DeepSeek 官方 API，初始化用 Flash，支持图像输入"
    upstream_model = "deepseek-v4-flash-vision-exp"
    adapter_model = "deepseek/deepseek-official-flash"
    media_caps = {"image": True, "video": False, "file": False, "audio": False}


class DeepSeekOfficialProAdapter(_OfficialDeepSeek):
    _skip_discovery = False
    routing_keys = ("deepseek-official-pro",)
    route_id = "deepseek-official-pro"
    display_name = "DeepSeek Official Pro"
    catalog_description = "DeepSeek 官方 API，初始化用 Pro"
    upstream_model = "deepseek-v4-pro"
    adapter_model = "deepseek/deepseek-official-pro"
    media_caps = {"image": False, "video": False, "file": False, "audio": False}
