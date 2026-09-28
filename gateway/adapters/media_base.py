from __future__ import annotations

from typing import Any

from gateway.core.types import Cost, MediaInvokeResult, RequestMeta


class MediaModelAdapter:
    """媒体模型 adapter 契约。

    负责一个媒体模型的全部:调用(收束成统一媒体输出)、计费、说明文档。

    产物一律落进工作区、以真实绝对路径返回,用 gateway.adapters.workspace 的
    save_any() / save_bytes() / save_from_url() 落盘,返回值里放 paths。框架把
    result.json 原样回传给调用方,不会替 adapter 清理 base64、也不会替它处理上游
    那种带签名会过期的临时链接 —— 这两样都要在 adapter 里收口,否则 base64 会随
    返回值吃光调用方的上下文,临时链接则会在用户回头查看时失效。

    入参是另一回事:有些上游只接受 http(s) URL、自己去拉参考素材(seedance 的
    image_url、elevenlabs voice-clone 的 urls、mureka 的参考音频),这种情况下本地
    素材必须先用 gateway.adapters.oss 的 upload_bytes() 换一个公网地址。除此之外
    不要用 oss 模块。
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "_skip_discovery", False):
            return
        if cls.compute_cost is MediaModelAdapter.compute_cost:
            raise TypeError(f"{cls.__name__} 必须实现 compute_cost")

    _skip_discovery: bool = False

    model_id: str = ""
    category: str = "媒体"
    description: str = ""
    skill_id: str = ""
    provider: str = ""

    def catalog_kind(self) -> str:
        return "media"

    def catalog_operations(self) -> list[Any]:
        return []

    def catalog_extra_params(self) -> list[Any]:
        return []

    def catalog_example_json_body(self, operation: str) -> dict[str, Any]:
        del operation
        return {}

    def normalize_input(self, meta: RequestMeta, operation: str, input_data: dict[str, Any]) -> dict[str, Any]:
        del meta, operation
        return input_data

    def invoke_sync(self, meta: RequestMeta, operation: str, input_data: dict[str, Any]) -> MediaInvokeResult:
        del meta, operation, input_data
        raise NotImplementedError(f"{self.model_id} does not support sync invoke")

    def compute_cost(self, *, result: MediaInvokeResult, operation: str) -> Cost:
        raise NotImplementedError(f"{type(self).__name__} 必须实现 compute_cost")

    def is_available(self, user_api_keys: dict | None = None) -> bool:
        del user_api_keys
        return True
