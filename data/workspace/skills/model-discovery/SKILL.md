---
name: model-discovery
description: 查询和调用媒体模型
tags: [gateway, models, discovery, media]
---

# 模型发现与查询

当用户需要生成图片、视频、音频时，用终端 curl 查询和调用网关 API。下面的 `${GATEWAY_BASE}`
在技能加载时会被替换成本机真实地址（端口来自配置，不要自己写端口）。

## 1. 查询模型列表

```bash
curl -s ${GATEWAY_BASE}/api/llm-proxy/v1/models
```
`kind: "media"` = 媒体模型，返回的 `id` 就是下一步 `model` 字段要填的值。

网关一共只有这五个端点，别猜别的：

```
GET  /api/llm-proxy/v1/models
GET  /api/llm-proxy/v1/text-models
POST /api/llm-proxy/v1/admin/reload
POST /api/llm-proxy/v1/chat/completions
POST /api/v1/media/invoke
```

媒体调用在 `/api/v1/` 下，**不在** `/api/llm-proxy/v1/` 下 —— 两个前缀不一样，这是最容易错的地方。

## 2. 查入参：读 adapter 源码（没有查模型文档的接口）

模型文档就写在 adapter 源码里。用 file_access 读工作区 `adapters/` 下对应的 `.py`，看三处：

- `catalog_extra_params()`：入参清单（名字、类型、是否必填、说明）。
- `catalog_example_json_body()`：一份可以直接照抄的示例入参。
- `invoke_sync()`：入参最终怎么拼给上游 —— 前两处说不清时以它为准。

文件名不一定等于 model_id（`wan.py` 里的 model_id 是 `wan2.70-image-pro`），先列一遍 `adapters/` 目录再读。

## 3. 调用：只有一个端点

```bash
curl -s -X POST ${GATEWAY_BASE}/api/v1/media/invoke \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer hhy-fdbbfe7eb27fab7b8d3409e2" \
  -d '{"model":"seedream","operation":"generate","input":{"prompt":"a cute cat"}}'
```

`model` 取第 1 步的 id，`operation` 取 `catalog_operations()` 里的 operation（通常是 `generate`），
`input` 按第 2 步的示例填。这个端点是同步的，会一直等到上游出结果（轮询在 adapter 内部做完），
成功返回 `{"ok":true, ..., "data":{"paths":[...]}}`。

出错就按响应原文判断，不要换路径重试：

- `400` —— 入参不对，原因在 `error.message`。
- `404` + `unknown model` —— `model` 写错了，回第 1 步看列表。
- `404` + `no such API route` —— 路径写错了，响应的 `available` 字段列出了真实路由。
- `502` —— 上游报错，原文在 `error.message`。

## 4. 拿到结果后：直接 render_media 展示

产物已经由网关落进工作区了，`data.paths` 里就是文件的真实绝对路径、正斜杠（形如
`D:/mafagent/data/workspace/media/seedream/a1b2c3d4.png`）。**不用下载**，直接把这个路径原样交给工具（不是 curl）：

```
render_media(path="D:/mafagent/data/workspace/media/seedream/a1b2c3d4.png")
```

图片/视频/音频会直接在对话里播放，其他格式给下载卡片。

路径要从响应 JSON 里读出来照抄，一个字符都不要改 —— 自己手打或者凭印象重写，
文件就找不到了。要看文件内容用 `file_access_read`，要挪位置用 `file_access_*`。

**不调 render_media，用户就只看到一段路径文字，看不到东西。**

## 5. 注意事项

- 中文提示词用英文写，避免终端编码问题
- 不要把 curl 的输出管道给 JSON 解析器：控制台是 cp936，会把响应里的 UTF-8 中文弄坏，
  解析报错看起来像"网关返回空数据"。直接看原文，或 `curl -s -o resp.json` 落盘再读文件
- 参考素材（图生图、声音克隆的样本）可以传 `{base64, mime_type}` 或公网 URL，
  adapter 内部会处理成上游要的形态
