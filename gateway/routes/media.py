from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from gateway.adapters import registry
from gateway.core.accounting import record_event, sanitize_body
from gateway.core.metadata import extract_meta

router = APIRouter()


@router.post("/invoke")
async def media_invoke(request: Request):
    meta = extract_meta(request)
    body = await request.json()
    model_id = str(body.get("model") or "").strip()
    operation = str(body.get("operation") or "").strip()
    input_data = body.get("input")
    if not model_id:
        return JSONResponse({"error": {"message": "model is required", "type": "invalid_request"}}, status_code=400)
    if not operation:
        return JSONResponse({"error": {"message": "operation is required", "type": "invalid_request"}}, status_code=400)
    if not isinstance(input_data, dict):
        return JSONResponse({"error": {"message": "input must be object", "type": "invalid_request"}}, status_code=400)
    try:
        adapter = registry.resolve_media(model_id)
    except ValueError:
        return JSONResponse({"error": {"message": f"unknown model: {model_id}", "type": "invalid_request"}}, status_code=404)

    t0 = time.monotonic()
    try:
        result = await asyncio.to_thread(
            adapter.invoke_sync,
            meta,
            operation,
            adapter.normalize_input(meta, operation, input_data),
        )
    except ValueError as exc:
        return JSONResponse({"error": {"message": str(exc), "type": "invalid_request"}}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": {"message": str(exc), "type": "upstream_error"}}, status_code=502)
    latency_ms = int((time.monotonic() - t0) * 1000)

    cost = adapter.compute_cost(result=result, operation=operation)
    record_event(
        kind="media",
        username=meta.username,
        api_key=meta.api_key,
        provider=adapter.provider,
        model=adapter.model_id,
        operation=operation,
        cost=cost.value,
        cost_currency=cost.currency,
        latency_ms=latency_ms,
        success=1,
        request_body_json=sanitize_body(body),
        response_body_json=sanitize_body(result.json if isinstance(result.json, dict) else {}),
    )
    payload = {
        "ok": True,
        "model": model_id,
        "operation": operation,
        "latency_ms": latency_ms,
        "cost": cost.value,
        "cost_currency": cost.currency,
        "data": result.json if isinstance(result.json, dict) else {},
    }
    return JSONResponse(payload)
