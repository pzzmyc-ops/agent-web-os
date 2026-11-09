from __future__ import annotations

from collections.abc import AsyncIterable, Awaitable, Mapping, Sequence
from typing import Any

from agent_framework import ChatResponse, ChatResponseUpdate, Message, ResponseStream
from agent_framework.exceptions import ChatClientException
from openai import AsyncOpenAI
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk

from ..llm import MAX_TOOL_ITERATIONS_PER_TURN, MafChatClient
from .catalog import resolve_model
from .chunks import collect_chunks
from .transport import stream_chat


class UpstreamChatClient(MafChatClient):
    def __init__(self, default_max_tokens: int):
        super().__init__(
            model="upstream",
            async_client=AsyncOpenAI(api_key="unused", base_url="http://127.0.0.1:9/v1"),
        )
        self.default_max_tokens = int(default_max_tokens)

    def _request_body(self, messages: Sequence[Message], options: Mapping[str, Any]) -> dict[str, Any]:
        body = self._prepare_options(messages, options)
        extra = body.pop("extra_body", None)
        if isinstance(extra, Mapping):
            body.update(dict(extra))
        body.pop("stream", None)
        body.pop("stream_options", None)
        model = str(body.get("model") or "").strip()
        if not model or model == "upstream":
            raise ChatClientException("本次请求没有指定模型")
        return body

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        options: Mapping[str, Any],
        stream: bool = False,
        **kwargs: Any,
    ) -> Awaitable[ChatResponse] | ResponseStream[ChatResponseUpdate, ChatResponse]:
        body = self._request_body(messages, options)
        try:
            upstream, body["model"] = resolve_model(body["model"])
        except RuntimeError as ex:
            raise ChatClientException(str(ex), inner_exception=ex) from ex

        async def _chunks():
            try:
                async for chunk in stream_chat(upstream, body, self.default_max_tokens):
                    yield chunk
            except ChatClientException:
                raise
            except Exception as ex:
                raise ChatClientException(f"模型 {body['model']} 调用失败: {ex}", inner_exception=ex) from ex

        if stream:
            async def _stream() -> AsyncIterable[ChatResponseUpdate]:
                async for chunk in _chunks():
                    parsed = ChatCompletionChunk.model_validate(chunk)
                    if len(parsed.choices) == 0 and parsed.usage is None:
                        continue
                    yield self._parse_response_update_from_openai(parsed)

            return self._build_response_stream(_stream(), response_format=options.get("response_format"))

        async def _get_response() -> ChatResponse:
            full = await collect_chunks(_chunks())
            return self._parse_response_from_openai(ChatCompletion.model_validate(full), options)

        return _get_response()


def build_upstream_client(default_max_tokens: int) -> UpstreamChatClient:
    client = UpstreamChatClient(default_max_tokens)
    client.function_invocation_configuration["max_iterations"] = MAX_TOOL_ITERATIONS_PER_TURN
    client.function_invocation_configuration["include_detailed_errors"] = True
    return client
