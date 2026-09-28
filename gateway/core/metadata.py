from __future__ import annotations

import uuid

from fastapi import Request

from .types import RequestMeta


def extract_meta(request: Request) -> RequestMeta:
    api_key = str(getattr(request.state, "gateway_api_key", "") or "").strip()
    username = str(getattr(request.state, "gateway_username", "") or "").strip()
    return RequestMeta(
        api_key=api_key,
        username=username,
        request_id=f"gw_{uuid.uuid4().hex[:24]}",
    )
