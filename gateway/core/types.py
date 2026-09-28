from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class RequestMeta:
    api_key: str = ""
    username: str = ""
    request_id: str = ""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class Cost:
    value: float = 0.0
    currency: str = "USD"

    def to_dict(self) -> dict[str, Any]:
        return {"cost": self.value, "cost_currency": self.currency}


def token_cost(
    *,
    currency: str = "USD",
    prompt_tokens: int = 0,
    cached_tokens: int = 0,
    completion_tokens: int = 0,
    text_input: float = 0.0,
    cached_input: float = 0.0,
    output: float = 0.0,
) -> Cost:
    uncached = max(prompt_tokens - cached_tokens, 0)
    value = (
        uncached * text_input
        + cached_tokens * cached_input
        + completion_tokens * output
    ) / 1_000_000.0
    return Cost(value=value, currency=currency)


@dataclass
class AdapterResult:
    provider: str = ""
    requested_model: str = ""
    adapter_model: str = ""
    upstream_model: str = ""
    success: int = 1
    latency_ms: int = 0
    error_text: str = ""
    usage: Usage = field(default_factory=Usage)
    response_json: dict[str, Any] | None = None
    stream: AsyncIterator[bytes] | None = None
    media_type: str = "application/json"
    upstream_status_code: int | None = None


@dataclass
class MediaInvokeResult:
    json: dict[str, Any] = field(default_factory=dict)
    bytes: bytes = b""
    status_code: int = 200


class UpstreamHttpError(Exception):
    def __init__(self, *, status_code: int, body: Any):
        self.status_code = int(status_code)
        self.body = body
        text = json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else str(body)
        super().__init__(text[:2000])


class GatewayBadRequest(Exception):
    pass
