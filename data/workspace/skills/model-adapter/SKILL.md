---
name: model-adapter
description: 为网关新增一个模型(对话模型或媒体模型)。当用户给出某模型的接入文档(base_url、鉴权、请求/响应格式)并希望在本工具里直接调用时使用。你写一个 adapter 文件,自带三件事:调用(把该模型千奇百怪的调用方式收束成统一格式)、计费(compute_cost)、说明(模型目录元数据)。对话模型收束成 OpenAI /chat/completions,媒体模型收束成统一媒体输出。文件放进工作区 adapters/ 目录,调热加载接口让运行中的网关立即认识它,最后自检。不管什么供应商、什么调用方法,都统一输出为一种格式,这是核心设计。
tags: [gateway, adapter, model, media, hotreload]
---

# 新增模型:写 adapter(调用 + 计费 + 说明)

## 契约总览

一句话分工:adapter 负责一个模型的全部,gateway 只统计与返回。

- adapter = 调用(把上游翻成统一格式) + 计费(compute_cost) + 说明(catalog 元数据)。
- gateway = 纯框架:分发请求 → 调 adapter → 记账(用量 + 成本) → 返回结果;并拿 adapter 的说明做展示。gateway 不替你调任何厂商上游,提供的可复用基建是产物落盘(`gateway.adapters.workspace`)和对象存储上传(`gateway.adapters.oss`)。

你只写一个 `.py` 文件,不改 gateway 任何代码。文件里是一个 Python 类,运行在网关进程内,**厂商上游的调用(httpx、鉴权、转发、解析)都自包含在这个文件里**;把产物变成文件这件事不要自己实现,调 `gateway.adapters.workspace`。供应商是谁不重要,只要 adapter 把上游调用翻译成统一格式,这个模型就能被列出、被调用、被计费。

统一目标格式分两类:

- 对话模型:统一成 OpenAI `/chat/completions` 的请求与响应(`AdapterResult`)。
- 媒体模型:统一成 `MediaInvokeResult`;产物一律落进工作区,返回值里给 `paths`(真实绝对路径、正斜杠),用 `workspace.save_any()` 拿。

## 落地位置与热加载闭环

1. 写文件:用 file_access 写到工作区的 `adapters/<模型名>.py`。网关从这里发现并热加载。
   - 一个文件一个模型(或一组同族模型),文件名不要以 `_` 开头。
   - 只用绝对导入 `from gateway.xxx import ...`,禁止相对导入 `from .xxx`。
2. 热加载:POST `${GATEWAY_BASE}/api/llm-proxy/v1/admin/reload`(与 /chat/completions 同一个 base;`${GATEWAY_BASE}` 加载时会替换成本机真实地址,端口来自配置,不要自己写端口)。
   - 成功返回 `{"ok": true, "chat": [...], "media": [...], "models": [...]}`。
   - 若类缺 `compute_cost`,或有语法/导入错误,接口直接 500 并带 traceback。这是加载期硬校验,报错就照提示改,别绕过。
3. 验证:GET `<base>/text-models`(对话)或 `<base>/models`(全部)能看到新模型 id,即可选择并对话/调用。

## 可用的契约(只许 import 这些)

- `from gateway.adapters.base import ModelAdapter`
- `from gateway.adapters.media_base import MediaModelAdapter`
- `from gateway.adapters.catalog_types import CatalogParam, MediaOperation`
- `from gateway.core.types import AdapterResult, Cost, Usage, RequestMeta, MediaInvokeResult, UpstreamHttpError, token_cost`
- `from gateway.adapters import workspace`
- `from gateway.adapters import oss`(只在上游强制要求公网 URL 时才用,见下)

除这几处之外没有别的可复用模块;厂商上游的调用与鉴权自己在 adapter 里用 httpx 实现。

### 产物落盘基建 `gateway.adapters.workspace`

媒体产物变成文件,只走这个模块,不要在 adapter 里自己写路径拼接、目录创建或 base64 解码。
落进去的文件就是文件管理器工作区里的普通文件:用户直接看得见,agent 能用 `file_access_*`
读写、用 `render_media` 在对话里展示。

- `workspace.save_any(value, *, default_mime, subdir, filename="")` → 真实绝对路径(正斜杠,如 `D:/mafagent/data/workspace/media/<subdir>/xxx.png`)。这是最常用的一个:`value` 可以是 http 链接(下载后落盘)、data url、裸 base64,或 `{"base64": ..., "mime_type": ...}`。
- `workspace.save_bytes(data, *, mime_type, filename="", subdir="")` → 手里已经是字节时用它(比如上游直接返回二进制流)。
- `workspace.save_from_url(url, *, filename="", subdir="")` → 只有上游链接时用它。
- `workspace.decode_b64(value, default_mime)` → `(bytes, mime)`,吃裸 base64 和 data url。
- `workspace.fetch_bytes(url)` → `(bytes, mime, filename)`,要拿字节自己处理时用(比如 multipart 上传参考图)。
- `workspace.is_http_url(v)` / `workspace.ext_for(filename, mime)` → 判断与扩展名推断。

`subdir` 填自己的 model_id,产物会落在工作区目录的 `media/<subdir>/` 下,便于用户分辨来源。

三条硬要求:

1. **返回值里不许出现 base64。** `MediaInvokeResult.json` 会原样进调用方(agent)的上下文,一张图的 base64 就是几 MB,一次调用能把上下文吃光。上游响应里的 `b64_json` 这类字段要摘掉,换成落盘后的路径。
2. **不要直接把上游给的产物链接当结果返回。** 上游 URL 通常带 `Expires` / `Signature`,半小时就失效 —— 用户回头想看那张图时链接已经死了。用 `save_any()` / `save_from_url()` 下载落盘,并把响应里那个临时链接也摘掉。
3. **不要把产物转存到对象存储。** 那是额外的外部依赖和成本,harness 不该替用户承担;工作区里的文件反而用户自己就能看见。

`raw` 里的 `usage` 等计费字段要留着 —— `compute_cost` 从那里取数,只摘 base64 和临时链接。

### 对象存储上传 `gateway.adapters.oss`

**只有一种情况用它**:上游只接受 http(s) URL、自己去拉参考素材(seedance 的 `image_url`、
elevenlabs voice-clone 的 `urls` 列表、mureka 的参考音频)。这时本地素材必须先有一个上游取得到
的地址,这一步绕不过去,属于模型自身的要求。

- `oss.upload_bytes(data, mime_type, filename="", *, user_id="")` → 上游取得到的公网 URL。

产物侧一律不用这个模块。入参侧如果上游本来就能接受 base64 或 multipart 文件上传,也不要用。

## 一、对话模型(继承 ModelAdapter)

契约:把任意上游翻成 OpenAI `/chat/completions`。

说明(类属性,直接喂给模型目录):

- `routing_keys`:路由键元组,用户用哪个 id 选它;不设则回落到 `route_id`。
- `route_id` / `provider` / `display_name` / `catalog_description` / `catalog_category`。
- `context_window` / `media_caps`(image/video/file/audio 布尔) / `supports_thinking`。
- `upstream_model`(上游真实模型名) / `adapter_model`(内部标识)。
- `ui_caps(route_id)`:前端控件说明。需要思考开关时返回 `{"params":[{"submit_key":"thinking","kind":"boolean","label":"Thinking","default":True}, ...]}`;网关目录原样透出 `ui`,前端按 `submit_key` 自动显示/隐藏按钮。没有思考能力就不要写,默认 `{"params":[]}`。

调用(唯一入口):

`async def chat_completions(self, body: dict, meta: RequestMeta) -> AdapterResult`

- `body` 是 OpenAI 请求(messages/stream 等);`meta` 有 api_key/username/request_id。
- 流式(`body["stream"]` 为真):把 SSE 字节迭代器放到 `AdapterResult.stream`,并设 `media_type="text/event-stream"`。
- 非流式:把 OpenAI 补全字典放到 `AdapterResult.response_json`。
- 必填 `provider`/`requested_model`/`adapter_model`/`upstream_model`,以及 `usage`(Usage),供网关记账。流式下 token 往往要到最后一个 SSE 分片才知道,可在生成器结束前回填 `result.usage`(网关在流耗尽后才记账)。
- 上游报错抛 `UpstreamHttpError(status_code=..., body=...)`,不要吞。

计费:

`def compute_cost(self, *, usage: Usage, response_json: dict | None) -> Cost`

完整示例(自包含,转发一个 OpenAI 兼容上游):

```python
from __future__ import annotations

from typing import Any, AsyncIterator

import httpx

from gateway.adapters.base import ModelAdapter
from gateway.core.types import AdapterResult, Cost, RequestMeta, UpstreamHttpError, Usage, token_cost

_BASE = "https://api.example.com/v1"
_KEY = "sk-example"


class ExampleChatAdapter(ModelAdapter):
    routing_keys = ("example-chat",)
    route_id = "example-chat"
    provider = "example"
    display_name = "Example Chat"
    catalog_description = "示例:OpenAI 兼容上游"
    upstream_model = "example-model-1"
    adapter_model = "example/example-model-1"
    context_window = 128_000

    async def chat_completions(self, body: dict[str, Any], meta: RequestMeta) -> AdapterResult:
        payload = dict(body)
        payload["model"] = self.upstream_model
        headers = {"Authorization": f"Bearer {_KEY}"}
        result = AdapterResult(
            provider=self.provider,
            requested_model=str(body.get("model") or self.route_id),
            adapter_model=self.adapter_model,
            upstream_model=self.upstream_model,
        )
        if body.get("stream"):
            async def gen() -> AsyncIterator[bytes]:
                async with httpx.AsyncClient(timeout=600) as client:
                    async with client.stream("POST", f"{_BASE}/chat/completions", json=payload, headers=headers) as resp:
                        if resp.status_code >= 400:
                            raise UpstreamHttpError(status_code=resp.status_code, body=await resp.aread())
                        async for chunk in resp.aiter_bytes():
                            yield chunk
            result.stream = gen()
            result.media_type = "text/event-stream"
            return result
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(f"{_BASE}/chat/completions", json=payload, headers=headers)
        if resp.status_code >= 400:
            raise UpstreamHttpError(status_code=resp.status_code, body=resp.json())
        data = resp.json()
        u = data.get("usage") or {}
        result.usage = Usage(
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
            total_tokens=int(u.get("total_tokens") or 0),
        )
        result.response_json = data
        return result

    def compute_cost(self, *, usage: Usage, response_json: dict[str, Any] | None) -> Cost:
        return token_cost(
            currency="USD",
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            text_input=0.5,
            output=1.5,
        )
```

### 工具调用:对内只认 OpenAI 三件套

网关内部只流通一种工具格式,就是 OpenAI `/chat/completions` 的三件套。上游长什么样是 adapter 的私事,但**进出 adapter 的边界必须是这三样**:

1. 请求里的工具清单 `body["tools"]`
2. 模型决定调工具时,回给上层的 `message.tool_calls`
3. 工具结果回传时的 `role: "tool"` 消息

上游不是 OpenAI 协议(Anthropic、Gemini、自研协议)时,**必须在 adapter 里做双向翻译**。把 `body["tools"]` 原样丢给非 OpenAI 上游是最常见也最难查的错误:上游不认识这个结构,通常不报错,只是当它不存在,表现为"模型永远不调工具,只会用自然语言说我需要更多信息"。整条链路一切正常,唯独 agent 干不了活。

三件套的确切形状:

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "read_file",
        "description": "读取一个文件",
        "parameters": {
          "type": "object",
          "properties": {"path": {"type": "string"}},
          "required": ["path"]
        }
      }
    }
  ],
  "messages": [
    {"role": "user", "content": "看看 a.txt 写了什么"},
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call_1",
          "type": "function",
          "function": {"name": "read_file", "arguments": "{\"path\": \"a.txt\"}"}
        }
      ]
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "文件正文..."}
  ]
}
```

三条容易踩的细节:

- `function.arguments` 是**字符串**(JSON 文本),不是对象;`role: "tool"` 的 `content` 同样是字符串。
- `tool_call_id` 必须和发起时的 `id` 逐一配对。少一个 `tool` 消息,上游就 400。
- `role: "tool"` 只有 OpenAI 协议有。Anthropic 要把它翻成 `user` 消息里的 `tool_result` 块,别原样透传。

#### 转换模板(以 Anthropic 为例,换别的上游照改)

两个方向、三个函数,一个都不能省。上游协议不同就只改函数体,签名和职责保持一致:

```python
def _tools_to_upstream(tools: list[dict]) -> list[dict]:
    """请求方向:OpenAI 工具清单 → 上游工具声明。"""
    out = []
    for t in tools or []:
        fn = t.get("function") or {}
        out.append({
            "name": fn.get("name"),
            "description": fn.get("description") or "",
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def _messages_to_upstream(messages: list[dict]) -> list[dict]:
    """请求方向:assistant.tool_calls 与 role=tool 消息 → 上游的调用块 / 结果块。"""
    out = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id"),
                "content": str(m.get("content") or ""),
            }]})
            continue
        if role == "assistant" and m.get("tool_calls"):
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": str(m["content"])})
            for c in m["tool_calls"]:
                fn = c.get("function") or {}
                blocks.append({
                    "type": "tool_use",
                    "id": c.get("id"),
                    "name": fn.get("name"),
                    "input": json.loads(fn.get("arguments") or "{}"),
                })
            out.append({"role": "assistant", "content": blocks})
            continue
        out.append({"role": role, "content": [{"type": "text", "text": str(m.get("content") or "")}]})
    return out


def _tool_calls_from_upstream(data: dict) -> list[dict]:
    """响应方向:上游的工具调用 → OpenAI tool_calls。"""
    calls = []
    for block in data.get("content") or []:
        if block.get("type") != "tool_use":
            continue
        calls.append({
            "id": block.get("id"),
            "type": "function",
            "function": {
                "name": block.get("name"),
                "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
            },
        })
    return calls
```

非流式响应里,有 `tool_calls` 时 `message.content` 填 `None`,`finish_reason` 填 `"tool_calls"`。

#### 流式的两条硬规矩

第一条:`data: [DONE]` 整条流**只发一次**,在最末尾。客户端 SDK 读到第一个 `[DONE]` 就停止读取整条流,所以每个分片后面都跟一个的话,后面的正文和工具调用会全部丢失,而且不报任何错 —— 界面上就是"模型没说话就结束了"。转换函数负责单个分片时,不要在里面发 `[DONE]`,交给最外层的生成器发。

第二条:工具参数是分片到达的,要按 `index` 累积成一个调用:

```json
{"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": ""}}]}}]}
{"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"path\":"}}]}}]}
{"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": " \"a.txt\"}"}}]}}]}
```

第一片带 `id`、`type`、`name`,后续只带 `arguments` 增量,同一个调用的 `index` 始终一致;并行调多个工具就递增 `index`。收尾分片的 `finish_reason` 填 `"tool_calls"`。

思考内容同理:上游的思考增量要转成 `delta.reasoning_content`,否则前端不会出思考卡片。

#### 工具自检(reload 之后必做)

实调一次带 `tools` 的请求,确认返回里**真的有 `tool_calls`**。如果模型改用自然语言说"请告诉我文件路径",说明工具清单在转换中丢了,不是模型不聪明。再补一次带完整工具历史(assistant.tool_calls + role=tool)的请求,确认不报 400。

## 二、媒体模型(继承 MediaModelAdapter)

契约:把任意媒体上游翻成统一的 `MediaInvokeResult`。产物无论是二进制、base64 还是上游的临时链接,一律用 `workspace.save_any()` 落进工作区、把它返回的绝对路径放进 `json` 的 `paths`;框架把 `result.json` 原样回传,不会替你清理,所以 base64 和临时链接必须在 adapter 里就摘干净。

说明(类属性 + catalog 方法)。这几处就是这个模型的全部文档:网关没有"查模型文档"的接口,调用方(人或另一个 agent)是直接读这个 `.py` 文件的,所以要写全、写准。

- `model_id`:网关路由 id,调用时 `{"model": ...}` 填的就是它,全局唯一。**它和上游真实模型名是两回事**:上游模型名单独写成模块级常量(如 `_MODEL = "wan2.7-image-pro"`),放进 `MediaOperation(..., model=_MODEL)`、示例入参和 `invoke_sync` 的请求体。两者允许不同(路由 id `wan2.70-image-pro`、上游 `wan2.7-image-pro` 就是这种情况);用户指定了路由 id 就照用户的写,上游名一律照接入文档原文抄,不要拿一个去改另一个。
- `category` / `description` / `skill_id`。
- `catalog_operations()`:返回 `MediaOperation(operation, product_code, method, path, model, note="")` 列表;新模型的 `product_code` 取唯一值(通常等于 model_id),`model` 填上游真实模型名。
- `catalog_extra_params()`:返回 `CatalogParam(name, type, required, desc)` 列表,把每个入参都列上 —— 这是调用方唯一能看到的入参清单,漏一个别人就调不出来。
- `catalog_example_json_body(operation)`:示例入参,填一份照抄就能跑通的。

调用(唯一入口):

`def invoke_sync(self, meta: RequestMeta, operation: str, input_data: dict) -> MediaInvokeResult`

- 在方法内完成上游调用(httpx 直连)。上游是异步任务的,轮询也在这里做完 —— 对外只有同步一种调用方式。
- 规整成 `MediaInvokeResult(json={... "paths": [...]})`,`paths` 里必须是 `workspace.save_*()` 返回的绝对路径,不要自己改写。
- 自检实调走 `POST ${GATEWAY_BASE}/api/v1/media/invoke`,body 是 `{"model": model_id, "operation": ..., "input": {...}}`。注意**媒体调用不在 `/api/llm-proxy/v1` 下**,那个前缀只有对话、模型列表和 reload。

计费:

`def compute_cost(self, *, result: MediaInvokeResult, operation: str) -> Cost`

完整示例(自包含图像生成):

```python
from __future__ import annotations

from typing import Any

import httpx

from gateway.adapters import workspace
from gateway.adapters.catalog_types import CatalogParam, MediaOperation
from gateway.adapters.media_base import MediaModelAdapter
from gateway.core.types import Cost, MediaInvokeResult, RequestMeta

_BASE = "https://api.example.com/v1"
_KEY = "sk-example"
_SUBDIR = "example-image"


class ExampleImageAdapter(MediaModelAdapter):
    model_id = "example-image"
    category = "图像"
    description = "示例图像生成"
    skill_id = "example-image"

    def catalog_operations(self) -> list[MediaOperation]:
        return [MediaOperation("generate", "example-image", "POST", "images/generate", "example-image-1")]

    def catalog_extra_params(self) -> list[CatalogParam]:
        return [CatalogParam("input.prompt", "string", True, "图像描述")]

    def catalog_example_json_body(self, operation: str) -> dict[str, Any]:
        return {"prompt": "一只在窗台上的猫"}

    def invoke_sync(self, meta: RequestMeta, operation: str, input_data: dict[str, Any]) -> MediaInvokeResult:
        del meta
        if operation != "generate":
            raise ValueError(f"unknown operation: {operation}")
        prompt = str(input_data.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("input.prompt is required")
        resp = httpx.post(
            f"{_BASE}/images/generate",
            json={"prompt": prompt},
            headers={"Authorization": f"Bearer {_KEY}"},
            timeout=600,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"upstream {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        paths = []
        for item in data.get("images", []):
            b64 = str(item.pop("b64_json", "") or "").strip()
            source = str(item.pop("url", "") or "").strip() or b64
            if not source:
                continue
            rel = workspace.save_any(source, default_mime="image/png", subdir=_SUBDIR)
            item["path"] = rel
            paths.append(rel)
        return MediaInvokeResult(json={"paths": paths, "raw": data})

    def compute_cost(self, *, result: MediaInvokeResult, operation: str) -> Cost:
        return Cost(1.0, "USD")
```

## 三、计费契约(compute_cost 必填)

- 返回 `Cost(value, currency)`;`currency` 用 `"USD"` 或 `"CNY"`。
- 免费或暂不计费:填 `Cost(0.0, "USD")`。
- 按次计费:写 `Cost(1.0, "USD")`(一次调用记 1)。
- 按 token 计费用 `token_cost(...)`:传 `prompt_tokens`/`cached_tokens`/`completion_tokens`,以及每百万 token 单价 `text_input`/`cached_input`/`output`。
- 网关先转发、后计费:`compute_cost` 在请求结束后才被调用,此时 usage(对话)或 result(媒体)已就绪。上游一开始不返回 cost/token 是正常的。
- 不写 `compute_cost`:reload 直接抛 `TypeError`,模型加载不进来。

## 四、硬性规则与自检

- 一个文件放 `adapters/`,不以 `_` 开头。
- 只用绝对导入 `gateway.*`(且仅限上面"可用的契约"列出的模块),禁止 `from .xxx`。
- 不写任何兜底:上游错就抛 `UpstreamHttpError` 或异常,别吞、别造假数据。
- 媒体产物一律 `workspace.save_*()` 落工作区、返回 `paths`:返回值里不许有 base64,也不许直接用上游那种带 `Expires` / `Signature` 的临时链接,更不许把产物转存到对象存储。自己再写一遍落盘或 base64 解码同样算违规。
- 对话模型的工具三件套必须双向翻译,不许把 `tools` 原样透传给非 OpenAI 上游。
- 流式的 `data: [DONE]` 整条流只发一次,放在最末尾。
- 流式的 usage 要原地累进 `result.usage`(上游把 input/output token 分在不同事件里给),不要每个分片新建一个 Usage,否则记账永远是 0。
- 写完必做闭环:POST `/admin/reload` → 无 500 → GET `/text-models` 或 `/models` 看到 id → 实调一次,确认返回与计费都正常 → 带 `tools` 再调一次,确认真的返回 `tool_calls`。
