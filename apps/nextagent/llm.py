"""构建指向 gateway 的 OpenAI 兼容 ChatClient。

DeepSeek(及某些严格实现)要求 tool 消息必须紧跟在对应的 assistant tool_calls 之后。
框架 Handoff / compaction 流程都可能把这一对拆开,导致 API 拒绝整个请求。
此模块提供 wrapper 在发送前归一化消息顺序(成对保留,成对丢弃)。
"""
from __future__ import annotations

import inspect
from itertools import chain
from typing import Any, AsyncIterable, AsyncIterator, Awaitable, Sequence

from agent_framework import ChatResponseUpdate, Content, Message
from agent_framework.openai import OpenAIChatCompletionClient

from .config import Config
from .dsml_recover import DsmlStreamRecoverer

DSML_RECOVERED_KEY = "dsml_recovered"


class MafChatClient(OpenAIChatCompletionClient):
    """在发送前归一化 tool 消息,确保每个 tool 紧跟在匹配的 assistant tool_calls 后。

    收流时再过一遍 DsmlStreamRecoverer:DeepSeek V4 Flash 会把工具调用以畸形 DSML 明文
    吐在正文里,上游不解析,这里把它还原成 function_call 交给框架,否则本轮会被当成
    「模型说完了」直接结束。
    """

    def _build_response_stream(
        self,
        stream: AsyncIterable[ChatResponseUpdate] | Awaitable[AsyncIterable[ChatResponseUpdate]],
        *,
        response_format: Any | None = None,
    ):
        return super()._build_response_stream(_recover_dsml(stream), response_format=response_format)

    def _prepare_messages_for_openai(
        self,
        chat_messages: Sequence[Message],
        role_key: str = "role",
        content_key: str = "content",
    ) -> list[dict[str, Any]]:
        raw: list[dict[str, Any]] = list(
            chain.from_iterable(self._prepare_message_for_openai(m) for m in chat_messages)
        )
        return _fix_tool_ordering(raw)

    def _prepare_message_for_openai(self, message: Message) -> list[dict[str, Any]]:
        reasons: list[str] = []
        rest = []
        for c in message.contents:
            if getattr(c, "type", None) == "text_reasoning" and getattr(c, "protected_data", None) is None:
                if c.text:
                    reasons.append(c.text)
                continue
            rest.append(c)
        work = message
        if reasons:
            work = Message(
                message.role,
                rest,
                author_name=message.author_name,
                message_id=message.message_id,
                additional_properties=message.additional_properties,
                raw_representation=message.raw_representation,
            )
        if work.role == "tool":
            return self._prepare_tool_message(work)
        out = super()._prepare_message_for_openai(work)
        if work.role == "assistant":
            out = _merge_assistant_parts(out)
        if reasons:
            text = "".join(reasons)
            if out:
                out[0]["reasoning_content"] = text
            else:
                item: dict[str, Any] = {"role": "assistant", "content": "", "reasoning_content": text}
                if message.author_name:
                    item["name"] = message.author_name
                out = [item]
        return out

    def _prepare_tool_message(self, message: Message) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for c in message.contents:
            if getattr(c, "type", None) != "function_result":
                raise ValueError(f"tool 消息里出现了非 function_result 内容: {getattr(c, 'type', None)}")
            items = list(getattr(c, "items", None) or [])
            texts = [str(i.text or "") for i in items if getattr(i, "type", None) == "text"]
            rich = [i for i in items if getattr(i, "type", None) in ("data", "uri")]
            if items:
                text = "\n".join(texts)
            else:
                text = c.result if c.result is not None else ""
            out.append({"role": "tool", "tool_call_id": c.call_id, "content": text})
            if rich:
                parts: list[dict[str, Any]] = [
                    {"type": "text", "text": f"[工具调用 {c.call_id} 返回的图片,共 {len(rich)} 张,顺序与结果中的 paths 一致]"}
                ]
                parts.extend(self._prepare_content_for_openai(i) for i in rich)
                out.append({"role": "user", "content": parts})
        return out

    def _prepare_content_for_openai(self, content: Any) -> dict[str, Any]:
        media = str(getattr(content, "media_type", None) or "")
        if getattr(content, "type", None) in ("data", "uri") and media.startswith("video/"):
            return {"type": "video_url", "video_url": {"url": content.uri}}
        if getattr(content, "type", None) in ("data", "uri") and media.startswith("audio/"):
            if "wav" in media:
                fmt = "wav"
            elif "mpeg" in media or "mp3" in media:
                fmt = "mp3"
            else:
                raise ValueError(f"unsupported audio media_type: {media}")
            data = str(content.uri or "")
            if data.startswith("data:"):
                data = data.split(",", 1)[-1]
            return {"type": "input_audio", "input_audio": {"data": data, "format": fmt}}
        out = super()._prepare_content_for_openai(content)
        if getattr(content, "type", None) == "function_call":
            extra = (getattr(content, "additional_properties", None) or {}).get("extra_content")
            if extra:
                out["extra_content"] = extra
        return out

    def _parse_tool_calls_from_openai(self, choice: Any) -> list[Any]:
        from agent_framework import Content

        resp: list[Any] = []
        content = getattr(choice, "message", None)
        if content is None:
            content = getattr(choice, "delta", None)
        if content and getattr(content, "tool_calls", None):
            for tool in content.tool_calls:
                fn = getattr(tool, "function", None)
                if fn is None:
                    continue
                extra = _tool_extra_content(tool)
                props = {"extra_content": extra} if extra else None
                resp.append(
                    Content.from_function_call(
                        call_id=tool.id if tool.id else "",
                        name=fn.name if fn.name else "",
                        arguments=fn.arguments if fn.arguments else "",
                        additional_properties=props,
                        raw_representation=fn,
                    )
                )
        return resp


async def _recover_dsml(
    stream: AsyncIterable[ChatResponseUpdate] | Awaitable[AsyncIterable[ChatResponseUpdate]],
) -> AsyncIterator[ChatResponseUpdate]:
    """流经这里的 text 内容先过 DsmlStreamRecoverer,还原出的 function_call 原位替换。

    还原发生过的 update 带 additional_properties[DSML_RECOVERED_KEY] = 本次还原的调用数,
    回合层据此落一条系统提示。流收尾时若还扣着未闭合的块,尽力解析后补发一条 update,
    finish_reason 改成 tool_calls,框架才会继续执行工具而不是收工。
    """
    if inspect.isawaitable(stream):
        stream = await stream
    rec = DsmlStreamRecoverer()
    last: ChatResponseUpdate | None = None
    async for update in stream:
        last = update
        if any(getattr(c, "type", None) == "text" for c in update.contents):
            contents: list[Content] = []
            recovered = 0
            for c in update.contents:
                if getattr(c, "type", None) != "text":
                    contents.append(c)
                    continue
                text, calls = rec.feed(c.text or "")
                if text:
                    contents.append(Content.from_text(text, raw_representation=c.raw_representation))
                contents.extend(calls)
                recovered += len(calls)
            update.contents = contents
            if recovered:
                if update.additional_properties is None:
                    update.additional_properties = {}
                update.additional_properties[DSML_RECOVERED_KEY] = recovered
        if rec.recovered and update.finish_reason == "stop":
            update.finish_reason = "tool_calls"
        yield update
    text, calls = rec.finish()
    if not text and not calls:
        return
    tail = ChatResponseUpdate(
        contents=([Content.from_text(text)] if text else []) + calls,
        role="assistant",
        response_id=last.response_id if last else None,
        message_id=last.message_id if last else None,
        model=last.model if last else None,
        finish_reason="tool_calls" if calls else None,
        additional_properties={DSML_RECOVERED_KEY: len(calls)} if calls else None,
    )
    yield tail


def _tool_extra_content(tool: Any) -> dict[str, Any] | None:
    extra = getattr(tool, "extra_content", None)
    if extra is None:
        model_extra = getattr(tool, "model_extra", None)
        if isinstance(model_extra, dict):
            extra = model_extra.get("extra_content")
    if isinstance(extra, dict) and extra:
        return extra
    return None


def _merge_assistant_parts(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(msgs) <= 1:
        return msgs
    head: dict[str, Any] | None = None
    rest: list[dict[str, Any]] = []
    for m in msgs:
        if m.get("role") != "assistant":
            rest.append(m)
            continue
        if head is None:
            head = dict(m)
            continue
        extra = m.get("content")
        if extra:
            prev = head.get("content")
            if not prev:
                head["content"] = extra
            elif isinstance(prev, str) and isinstance(extra, str):
                head["content"] = prev + extra
            elif isinstance(prev, list) and isinstance(extra, list):
                head["content"] = prev + extra
            else:
                head["content"] = extra
        if m.get("tool_calls"):
            head.setdefault("tool_calls", []).extend(m["tool_calls"])
        if m.get("reasoning_content") and not head.get("reasoning_content"):
            head["reasoning_content"] = m["reasoning_content"]
        if m.get("reasoning_details") and not head.get("reasoning_details"):
            head["reasoning_details"] = m["reasoning_details"]
    if head is None:
        return rest
    return [head, *rest]


def _fix_tool_ordering(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 tool 消息重排到它对应的 assistant tool_calls 后面。配不上就直接抛。

    DeepSeek(以及多数严格实现)要求:每个 assistant.tool_calls[i] 必须紧跟一条
    tool_call_id 相同的 tool 消息;反之孤立的 tool 消息也会被拒。重排是格式要求,
    留着。

    丢弃不留了。以前这里成对保留、成对丢弃,于是「历史里那次工具调用没有结果」这件事
    在发请求前被悄悄抹平,模型下一轮把活重做一遍,而日志里看不出任何异常。现在历史来自
    事件日志,调用和结果各是一条事件,中断的调用也有一条说明中断的结果 —— 到这里还配不上
    只有两种可能,都得当场看见:

    - 框架自己造的孤立 tool 消息(handoff 工具被中间件短路,只留下合成的 function_result);
    - 上下文压缩把一对消息拆开了。

    两种都是要处理的真问题,不是可以圆过去的噪声。
    """
    results: dict[str, dict[str, Any]] = {}
    for m in messages:
        if m.get("role") == "tool":
            tcid = str(m.get("tool_call_id") or "")
            if tcid:
                results[tcid] = m

    declared: set[str] = set()
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            continue
        if role == "assistant" and m.get("tool_calls"):
            calls = list(m.get("tool_calls") or [])
            missing = [str(c.get("id") or "") for c in calls if str(c.get("id") or "") not in results]
            if missing:
                raise ValueError(
                    f"assistant 声明的工具调用没有结果消息: {missing} —— "
                    "上游会判 400。历史侧应该由 session_log 保证成对,"
                    "本轮内则要查 middleware 有没有把结果吃掉"
                )
            out.append(dict(m))
            for c in calls:
                cid = str(c["id"])
                declared.add(cid)
                out.append(results[cid])
            continue
        out.append(m)

    orphans = sorted(set(results) - declared)
    if orphans:
        raise ValueError(
            f"tool 消息没有声明它的 assistant tool_calls: {orphans} —— "
            "多半是 handoff 工具被中间件短路后只留下了合成的 function_result"
        )
    return out


MAX_TOOL_ITERATIONS_PER_TURN = 1_000_000


def build_chat_client(cfg: Config) -> MafChatClient:
    client = MafChatClient(
        model="deepseek-official-flash",
        api_key=cfg.api_key,
        base_url=cfg.base_url,
    )
    client.function_invocation_configuration["max_iterations"] = MAX_TOOL_ITERATIONS_PER_TURN
    return client
