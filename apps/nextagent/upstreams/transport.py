from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from . import anthropic_codec, chat_codec, protocol, responses_codec
from .anthropic_codec import HIDDEN_THINKING
from .catalog import Upstream
from .chunks import make_chunk, new_chunk_id
from .reasoning import pop_reasoning

_TIMEOUT = httpx.Timeout(connect=30.0, read=900.0, write=120.0, pool=30.0)
_client: httpx.AsyncClient | None = None


class UpstreamError(RuntimeError):
    def __init__(self, upstream: str, proto: str, status: int, body: str):
        self.upstream = upstream
        self.protocol = proto
        self.status = status
        self.body = body
        super().__init__(f"上游 {upstream}({proto})返回 {status}: {body[:2000]}")


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _client


def _headers(up: Upstream, proto: str) -> dict[str, str]:
    out: dict[str, str] = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if proto == protocol.ANTHROPIC:
        out.update(anthropic_codec.headers(up.api_key))
    elif up.api_key:
        out["Authorization"] = "Bearer " + up.api_key
    out.update(up.headers)
    return out


def _payload(up: Upstream, proto: str, body: dict[str, Any], default_max_tokens: int) -> dict[str, Any]:
    if proto == protocol.CHAT:
        return chat_codec.encode(body, up.base_url)
    if proto == protocol.RESPONSES:
        return responses_codec.encode(body)
    if proto == protocol.ANTHROPIC:
        return anthropic_codec.encode(body, default_max_tokens)
    raise RuntimeError("未知协议: " + proto)


def _decoder(proto: str):
    if proto == protocol.CHAT:
        return chat_codec.decode
    if proto == protocol.RESPONSES:
        return responses_codec.decode
    if proto == protocol.ANTHROPIC:
        return anthropic_codec.decode
    raise RuntimeError("未知协议: " + proto)


async def _read_error(resp: httpx.Response) -> str:
    raw = await resp.aread()
    return raw.decode("utf-8", "replace")


def _thinking_requested(body: dict[str, Any]) -> bool:
    enabled, _effort = pop_reasoning(dict(body))
    return enabled is True


def _chunk_has_reasoning(chunk: dict[str, Any]) -> bool:
    for choice in chunk.get("choices") or []:
        delta = choice.get("delta") or {}
        if delta.get("reasoning_content") or delta.get("reasoning_details"):
            return True
    return False


def _chunk_finished(chunk: dict[str, Any]) -> bool:
    for choice in chunk.get("choices") or []:
        if choice.get("finish_reason"):
            return True
    return False


async def _with_hidden_thinking(chunks: AsyncIterator[dict], model: str, expect: bool) -> AsyncIterator[dict]:
    saw = False
    async for chunk in chunks:
        if _chunk_has_reasoning(chunk):
            saw = True
        if expect and not saw and _chunk_finished(chunk):
            yield make_chunk(str(chunk.get("id") or new_chunk_id()), str(chunk.get("model") or model), {"reasoning_content": HIDDEN_THINKING})
            saw = True
        yield chunk
    if expect and not saw:
        yield make_chunk(new_chunk_id(), model, {"reasoning_content": HIDDEN_THINKING})


async def stream_chat(up: Upstream, body: dict[str, Any], default_max_tokens: int) -> AsyncIterator[dict]:
    model = str(body.get("model") or "")
    expect_thinking = _thinking_requested(body)
    attempts: list[str] = []
    for proto in protocol.candidates(up.base_url, model):
        url = protocol.endpoint(up.base_url, proto)
        payload = _payload(up, proto, body, default_max_tokens)
        req = _http().build_request("POST", url, json=payload, headers=_headers(up, proto))
        resp = await _http().send(req, stream=True)
        if resp.status_code >= 400:
            text = await _read_error(resp)
            await resp.aclose()
            if protocol.is_mismatch(resp.status_code, text):
                attempts.append(f"{proto} -> {resp.status_code} {text[:300]}")
                continue
            raise UpstreamError(up.name, proto, resp.status_code, text)
        protocol.remember(up.base_url, model, proto)
        try:
            decoded = _decoder(proto)(resp.aiter_lines(), model)
            async for chunk in _with_hidden_thinking(decoded, model, expect_thinking):
                yield chunk
        finally:
            await resp.aclose()
        return
    raise UpstreamError(
        up.name, "none", 0,
        "三种协议都被 " + up.base_url + " 拒绝,逐个尝试的结果: " + json.dumps(attempts, ensure_ascii=False),
    )


async def probe(up: Upstream) -> dict[str, Any]:
    base = up.base_url.rstrip("/")
    url = base + "/models" if base.endswith("/v1") else base + "/v1/models"
    headers: dict[str, str] = {"Accept": "application/json"}
    if up.api_key:
        headers["Authorization"] = "Bearer " + up.api_key
        headers.update(anthropic_codec.headers(up.api_key))
    headers.update(up.headers)
    resp = await _http().get(url, headers=headers, timeout=httpx.Timeout(20.0))
    text = resp.text
    ids: list[str] = []
    if resp.status_code < 400:
        try:
            data = resp.json()
        except ValueError:
            data = None
        rows = data.get("data") if isinstance(data, dict) else None
        if isinstance(rows, list):
            ids = [str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")]
    return {"url": url, "status": resp.status_code, "ok": resp.status_code < 400, "models": ids, "body": text[:800]}
