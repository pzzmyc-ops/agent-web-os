from __future__ import annotations

import json
import time
import traceback

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from gateway.adapters import registry
from gateway.core.accounting import record_event, sanitize_body
from gateway.core.metadata import extract_meta
from gateway.core.types import GatewayBadRequest, UpstreamHttpError

router = APIRouter()


def _upstream_error_response(exc: UpstreamHttpError) -> Response:
    if isinstance(exc.body, (dict, list)):
        return JSONResponse(exc.body, status_code=exc.status_code)
    return Response(content=str(exc.body), status_code=exc.status_code, media_type="text/plain; charset=utf-8")


def _stream_error_text(exc: Exception) -> str:
    if not isinstance(exc, UpstreamHttpError):
        return f"{type(exc).__name__}: {exc}"
    body = exc.body
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", "replace")
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    return f"上游 {exc.status_code}: {body}"


def _sse_error(message: str) -> bytes:
    """把错误塞回 SSE 流。

    流一旦开始,响应头就发出去了,状态码改不了。让异常直接穿出去的话连接会被掐断,
    而客户端 SDK 把「流突然结束」当成正常结束 —— 于是一整轮对话既没有内容也没有
    报错,界面上什么都不显示。SDK 见到 data 里带 error 字段会抛 APIError
    (openai/_streaming.py),这是唯一能把错误原文送到用户面前的通道。
    """
    payload = {"error": {"message": message, "type": "upstream_error"}}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


@router.get("/models")
async def models():
    return {"object": "list", "data": [
        {"id": m["id"], "object": "model", "owned_by": m["provider"], "kind": m["kind"],
         "displayName": m.get("displayName") or m["id"], "media_caps": m.get("media_caps") or {},
         "context_window": m.get("context_window"), "description": m.get("description") or "",
         "ui": m.get("ui") or {}}
        for m in registry.list_all_models()
    ]}


@router.get("/text-models")
async def text_models():
    return {"models": registry.list_chat_catalog()}


@router.post("/admin/reload")
async def admin_reload():
    result = registry.reload_adapters()
    return {"ok": True, "chat": result["chat"], "media": result["media"], "models": [m["id"] for m in registry.list_all_models()]}


@router.post("/chat/completions")
async def chat_completions(request: Request):
    meta = extract_meta(request)
    body = await request.json()
    t0 = time.monotonic()
    try:
        adapter = registry.resolve_chat(str(body.get("model") or ""))
        result = await adapter.chat_completions(body, meta)
    except (GatewayBadRequest, ValueError) as e:
        return JSONResponse({"error": {"message": str(e), "type": "invalid_request"}}, status_code=400)
    except UpstreamHttpError as e:
        return _upstream_error_response(e)

    def _record(response_body_json: str) -> None:
        cost = adapter.compute_cost(usage=result.usage, response_json=result.response_json)
        record_event(
            kind="chat",
            username=meta.username,
            api_key=meta.api_key,
            provider=result.provider,
            model=adapter.route_id,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
            cost=cost.value,
            cost_currency=cost.currency,
            latency_ms=result.latency_ms,
            success=result.success,
            error_text=result.error_text,
            request_body_json=sanitize_body(body),
            response_body_json=response_body_json,
        )

    if body.get("stream"):
        async def iterator():
            chunks: list[bytes] = []
            try:
                if result.stream is None:
                    raise RuntimeError("stream missing")
                async for chunk in result.stream:
                    chunks.append(chunk)
                    yield chunk
            except Exception as exc:
                # 堆栈照旧打到服务端日志,同时把错误原文送进流里给客户端
                traceback.print_exc()
                result.success = False
                result.error_text = _stream_error_text(exc)
                yield _sse_error(result.error_text)
            finally:
                result.latency_ms = int((time.monotonic() - t0) * 1000)
                _record("")
        return StreamingResponse(iterator(), media_type=result.media_type or "text/event-stream")

    result.latency_ms = int((time.monotonic() - t0) * 1000)
    _record(sanitize_body(result.response_json or {}))
    if not result.success:
        status = result.upstream_status_code or 502
        return JSONResponse(result.response_json or {"error": result.error_text}, status_code=status)
    return JSONResponse(result.response_json or {})
