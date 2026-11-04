from __future__ import annotations

from fastapi import APIRouter

from gateway.adapters import registry

router = APIRouter()


@router.get("/models")
async def models():
    return {"object": "list", "data": [
        {"id": m["id"], "object": "model", "owned_by": m["provider"], "kind": m["kind"]}
        for m in registry.list_all_models()
    ]}


@router.post("/admin/reload")
async def admin_reload():
    result = registry.reload_adapters()
    return {"ok": True, "media": result["media"], "models": [m["id"] for m in registry.list_all_models()]}
