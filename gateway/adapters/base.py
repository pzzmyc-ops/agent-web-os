from __future__ import annotations

from typing import Any

import httpx

from gateway.core.types import AdapterResult, Cost, RequestMeta, Usage

UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0)


class ModelAdapter:
    """对话模型 adapter 契约。

    负责一个对话模型的全部:调用(收束成 OpenAI /chat/completions)、计费、说明文档。
    框架只负责分发、记账、展示,不关心上游怎么调用。
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "_skip_discovery", False):
            return
        if cls.compute_cost is ModelAdapter.compute_cost:
            raise TypeError(f"{cls.__name__} 必须实现 compute_cost")

    _skip_discovery: bool = False
    routing_keys: tuple[str, ...] = ()

    route_id: str = ""
    provider: str = ""
    adapter_model: str = ""
    upstream_model: str = ""
    display_name: str = ""
    catalog_description: str = ""
    catalog_category: str = "文本对话"
    context_window: int = 128_000
    media_caps: dict[str, bool] = {"image": True, "video": False, "file": True, "audio": True}
    video_wire: str = "none"
    supports_thinking: bool = False

    def profile_display_name(self) -> str:
        return self.display_name or self.route_id

    def profile_media_caps(self) -> dict[str, bool]:
        return dict(self.media_caps)

    def profile_video_wire(self) -> str:
        return str(self.video_wire or "none").strip() or "none"

    def is_available(self, user_api_keys: dict | None = None) -> bool:
        return True

    def ui_caps(self, route_id: str) -> dict[str, Any]:
        del route_id
        return {"params": []}

    async def chat_completions(self, body: dict[str, Any], meta: RequestMeta) -> AdapterResult:
        raise NotImplementedError

    def compute_cost(self, *, usage: Usage, response_json: dict[str, Any] | None) -> Cost:
        raise NotImplementedError(f"{type(self).__name__} 必须实现 compute_cost")
