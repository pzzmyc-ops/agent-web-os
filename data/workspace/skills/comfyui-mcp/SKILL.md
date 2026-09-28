---
name: comfyui-mcp
description: 接入 ComfyUI：生成图片/视频/音频/3D，检索模型/节点/模板，运行工作流。本环境没有 MCP 工具，改为用 ComfyUI 的 HTTP API 直接驱动本机实例（等价于 comfy-mcp 的底层行为）。
---
---
name: comfyui-mcp
description: 接入 ComfyUI：生成图片/视频/音频/3D，检索模型/节点/模板，运行工作流。本环境没有 MCP 工具，改为用 ComfyUI 的 HTTP API 直接驱动本机实例（等价于 comfy-mcp 的底层行为）。
tags: [comfyui, mcp, api, media, workflow]
---

# ComfyUI MCP / API

让 AI agent 连上 ComfyUI 跑真实工作流。官方 Comfy MCP 有两条连接：**云端 Comfy Cloud** 和 **本机 ComfyUI**。
本文档是给 agent 用的操作手册。来源：https://docs.comfy.org/agent-tools/mcp.md

## 0. 先判断用哪条连接

| 情况 | 建议 |
| --- | --- |
| 有 Comfy Cloud 账号、想省事、没有本机显卡 | 云端 `https://cloud.comfy.org/mcp`（新用户 5 次免费） |
| 本机已有 ComfyUI / 想用自己的模型、LoRA、自定义节点 / 用自己 GPU | 本机连接 |
| Mac 苹果 GPU | 用云端（开源权重模型在苹果 GPU 上跑不动） |

本环境机器是 RTX 4090（48G），跑本机 ComfyUI 完全够，**默认走本机**。

## 1. 关键点：本环境没有 MCP 工具

本 agent 不具备 MCP 客户端能力，所以不能以「MCP server + 工具注册」的方式用 ComfyUI。
等价方案：直接用 **ComfyUI 的 REST API**。`comfy-mcp` 服务器在底层就是把 `comfy-cli` 打包成 MCP，
对 agent 的效用等于这些 HTTP 调用。本技能自带 `scripts/comfy.py`（纯标准库，无第三方依赖）封装这些调用。

ComfyUI 默认监听 `http://127.0.0.1:8188`（脚本里可通过 `--base http://127.0.0.1:8188` 覆盖）。

### 核心 REST 端点（本地 ComfyUI）

| 用途 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 确认在跑 | GET | `/system_stats` | 有返回即运行中，看 device/gpu |
| 节点定义 | GET | `/object_info` | 全量节点 class 与输入规格（含自定义节点） |
| 模型列表 | GET | `/object_info/CheckpointLoaderSimple` 等 | 可枚举模型目录 |
| 提交工作流 | POST | `/prompt` | 传 **API 格式** workflow，返回 `prompt_id` |
| 轮询状态 | GET | `/history/{prompt_id}` | 完成后 `outputs` 里是产物 |
| 队列 | GET | `/queue` | running/pending 数 |
| 下载产物 | GET | `/view?filename=..&subfolder=..&type=output` | 返回文件字节 |
| 上传输入图 | POST | `/upload/image` | form-data 字段 `image` |
| 中断 | POST | `/interrupt` | 停当前任务 |
| 校验工作流 | POST | `/prompt`（不带 client 提交前） | `/object_info` 也可预检 |

### scripts/comfy.py 子命令

| 子命令 | 对应 MCP 工具 | 作用 |
| --- | --- | --- |
| `server-info` | `server_info()` | 确认本机 ComfyUI 是否在跑 |
| `object-info [名称]` | `search_nodes` / `get_node` | 看节点/输入规格 |
| `submit <workflow.json> [--wait]` | `submit_workflow` | 提交 API 格式工作流，返回 prompt_id |
| `status <prompt_id>` | `job_status` | 轮询任务状态 |
| `wait <prompt_id> [--timeout 900]` | `wait_for_job` | 阻塞到任务完成 |
| `fetch <prompt_id> --out <dir>` | `fetch_outputs` | 拷出该任务产物到目录 |
| `upload <image>` | `upload_file` | 上传输入图，返回 `{name,subfolder,type}` |
| `queue` | `get_queue` | 看排队情况 |
| `validate <workflow.json>` | `validate_workflow` | 本地预检，慢跑前先跑 |

`scripts/comfy.py` 只依赖标准库（urllib），Python 3.8+ 可跑，命令形如：
`python scripts/comfy.py server-info`

## 2. 标准工作流（生成一张图）

1. 确认在跑：`python scripts/comfy.py server-info`
   - 报「连接失败 / Connection refused」→ 先 `comfy launch` 起服务（见第 4 节），或问用户是否要起。
   - 确认 GPU 型号/显存符合预期。
2. 准备 API 格式 workflow JSON（不是 UI 保存的「图格式」，是 API 格式；可用 `validate` 预检）。
   - 技能里自带一个已在本机 Krea 2 管线实测可跑的模板 `references/txt2img.example.json`
     （UNETLoader + krea2_turbo lora + KSampler + qwen_image_vae + VAEDecodeTiled）。
     本机 ComfyUI 装的是 Krea 2（krea2_raw/turbo, qwen3vl 文本编码器）+ Wan/Anima 视频模型，
     没有 SDXL checkpoint，别用 SDXL 模板。改 `CLIPTextEncode.text` 即可换提示词。
   - 用脚本预检：`python scripts/comfy.py validate 你的flow.json`
3. 提交：`python scripts/comfy.py submit 我的flow.json --wait`
   返回 `prompt_id`。
4. 取回产物：`python scripts/comfy.py fetch <prompt_id> --out <相对目录>`
   `fetch` 会把图片/视频/音频下载到指定目录，打印本地相对路径。
5. 展示：用工具 `render_media` 输出路径（不要 curl），图片/视频直接播放。

图生图：先 `python scripts/comfy.py upload 参考图.png`，把返回的 `{name,subfolder,type}`
填进 workflow 里 `LoadImage` 节点的对应字段，再走第 3 步。

## 3. 首次安装（本机）

```bash
# 引擎（comfy-cli）
pip install "comfy-cli>=1.14.0"
# 建一个 ComfyUI 工作区（已有可跳过）
comfy install
# 常驻启动（保持这个终端开着，或后台跑）
comfy launch
```

给支持 MCP 的客户端（Claude Code / Cursor / Claude Desktop）用的本地 MCP 服务器：

```bash
pip install comfy-mcp   # 产生 comfy-mcp 命令
# Claude Code 一行注册：
claude mcp add comfy-mcp -e COMFY_BIN=/path/to/venv/bin/comfy -- comfy-mcp
# Claude Desktop：在 claude_desktop_config.json 加 mcpServers.comfy-mcp
```

> 注意：MCP 客户端启动服务器用的环境通常不含你 shell 的 PATH。若 `comfy` 在 venv 或非标准位置，
> 用环境变量 `COMFY_BIN` 指向其绝对路径。`comfy-mcp` 只在支持 MCP 的客户端里有用，本环境用不到。

## 4. 云端连接（给外站客户端，如 Cursor/Codex/Claude）

云端 MCP 无 URL 之外的安装，服务器地址固定：
`https://cloud.comfy.org/mcp`。方法要点：

- **Claude Code**：`claude mcp add --transport http comfy-cloud https://cloud.comfy.org/mcp`，再 `/mcp` → Authenticate（浏览器 OAuth）。
- **Cursor**（无 OAuth）：mcp.json 里 `"url": "https://cloud.comfy.org/mcp"` + `"headers": {"X-API-Key": "${env:COMFY_API_KEY}"}`，key 在 platform.comfy.org/profile/api-keys 创建（`comfyui-` 开头）。
- **Codex**：`codex mcp add comfy-cloud --url https://cloud.comfy.org/mcp` + `codex mcp login comfy-cloud`。
- **其他 / 无浏览器（CI）**：加 `X-API-Key` 头，或 OpenClaw 用 `Authorization: Bearer`。

注意：云端「搜索（search_templates/search_models/search_nodes）」免费，**运行生成需要有效 Comfy Cloud 订阅**（有剩余点数不算）。

### 云端 MCP 工具清单（agent 一旦连上可调）
- 发现：`search_templates` / `get_template` / `get_template_schema` / `search_models` / `search_nodes` / `get_node` / `cql` / `get_prompting_guide`
- 生成：`run_template` / `submit_workflow` / `partner_generate` / `upload_file` / `apply_slots`
- 任务：`get_job_status` / `wait_for_job` / `get_output` / `use_previous_output` / `cancel_job` / `get_queue` / `submit_batch` 等
- 已存工作流：`list_saved_workflows` / `get_saved_workflow` / `save_workflow` / `run_saved_workflow` / `share_workflow`
- 账号：`get_billing_status` / `get_server_info` / `report_session_summary`（需用户明确同意）

## 5. 注意事项

- 本环境走本机 HTTP API，注意与「网关媒体模型 / `/api/v1/media/invoke`（seedream/seedance 等）」区分：
  ComfyUI 是用你自己的 GPU 跑本机 Comfy 工作流，二者不同，不要混用路径。
- workflow 必须是 **API 格式**（节点是「id → class_type」，不是「节点连线图格式」）。UI 里「导出(API)」得到的才是。
- `submit` 前先 `validate` 预检，避免长任务跑一半才报错。
- 产物下载到工作区相对目录，用 `render_media` 展示，不要手打路径（从 fetch 输出里照抄）。
- 中文提示词在终端可能因 cp936 编码出错；必要时写英文。
- 搜索/发现本地用 `/object_info`；「有 GPU 才跑得动」—— 老卡/小显存跑视频会慢或放不下。
- 本机有些节点是**云端 API 节点**（如 `Krea2ImageNode`/`WanImageToVideoApi`），调用会报
  「Unauthorized: Please login first」—— 需要先在该 ComfyUI 网页端登录对应平台账号；
  纯本地生成走 `UNETLoader` + `LoraLoaderModelOnly` + `KSampler` + qwen vae 这条链路。

## 6. 排查

| 现象 | 处理 |
| --- | --- |
| `Connection refused` | ComfyUI 没起；`comfy launch` 或问用户 |
| `submit` 返回 400 | workflow 不是 API 格式 / 节点缺参数；看响应 `error` 字段 |
| `submit` 404 | base 地址不对，确认端口 8188 |
| fetch 无产物 | 任务没跑完（先 wait）或 workflow 没输出节点 |
| MCP 客户端连不上 | `comfy` 不在客户端环境 PATH，设 `COMFY_BIN` |

---
本技能数据来源：https://docs.comfy.org/agent-tools/mcp.md（公开 beta，API 可能有变）。
