from __future__ import annotations

import json
from typing import Any, AsyncIterator

from appconfig import load_config
from gateway.adapters.base import ModelAdapter, upstream_client
from gateway.core.types import (
    AdapterResult,
    Cost,
    RequestMeta,
    UpstreamHttpError,
    Usage,
    token_cost,
)

_URL = "https://api.deepseek.com/v1/chat/completions"

_AUTH_FAIL_HINT = (
    "DeepSeek 官方 API Key 认证失败。"
    "如果这个 Key 是刚刚创建、刚刚写入的，通常要等 1~2 分钟才会生效，请稍后重试；"
    "若一直失败，请到 https://platform.deepseek.com 确认该 Key 仍然存在（没有被删除或重新生成），"
    "并完整复制后再发一次，注意不要带空格或换行。"
)


def _friendly_error_body(status_code: int, body: Any) -> Any:
    """把上游的英文错误翻成能照着做的中文提示。

    DeepSeek 对刚创建、还没生效的 Key 也返回 401 "Your api key: ****xxxx is invalid",
    原文看起来像 Key 填错了,用户会反复重建 Key。401 时直接换成一句中文提示
    (纯文本,网关按原状态码回给前端,界面上不会再多套一层 JSON);
    其余状态码和上游原文都原样保留(原文里带 request_id,排查时要用)。
    """
    if int(status_code) != 401:
        return body
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", "replace")
    upstream_message = ""
    request_id = ""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            upstream_message = body
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            upstream_message = str(err.get("message") or upstream_message)
            request_id = str(err.get("request_id") or "")
        else:
            upstream_message = upstream_message or json.dumps(body, ensure_ascii=False)
    message = _AUTH_FAIL_HINT
    if upstream_message:
        message += "上游原文:" + upstream_message
    if request_id:
        message += "(request_id: " + request_id + ")"
    return message



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
                async with upstream_client() as client:
                    async with client.stream("POST", _URL, json=payload, headers=headers) as resp:
                        if resp.status_code >= 400:
                            raise UpstreamHttpError(
                                status_code=resp.status_code,
                                body=_friendly_error_body(resp.status_code, await resp.aread()),
                            )
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
        async with upstream_client() as client:
            resp = await client.post(_URL, json=payload, headers=headers)
        if resp.status_code >= 400:
            body = resp.text
            try:
                body = resp.json()
            except Exception:
                pass
            raise UpstreamHttpError(status_code=resp.status_code, body=_friendly_error_body(resp.status_code, body))
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
