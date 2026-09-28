"""render_media 工具 —— 把工作区里已经存在的文件展示给用户。

它**不生成任何东西**,只负责「展示」这一件事:agent 用别的手段(媒体模型、脚本、
下载)把文件写进工作区之后,调一次 render_media,那个文件就在对话里显示出来。

图片 / 视频 / 音频直接内嵌播放,其余任何格式一律给一张下载卡片。

怎么送到前端:返回值里带一个 `_media` 对象,tool_tracker.ToolTracker._lift_media
按 call_id 把它贴到**这次调用自己的工具块**的 block_end 上(并顺手从模型看到的结果里
删掉,那段内容是给前端渲染用的)。前端 applyToolMedia 按 mediaType 决定渲成
img / video / audio / 下载卡。

为什么不走 TurnContext 通道:那条路只能拿到「最后一个」工具块(见
ToolTracker.current_block),而模型很自然会在一条消息里连着调好几次 render_media,
那样几张图会全挤到同一个块上。跟着返回值走就不会错位。

同一份载荷也进 event_bus → 随 assistant 消息的 blocks 落盘 → 刷新、切会话、重连都还在。

前端能吃的 mediaContent 有两种(chat-ui.js extractMediaOnlyHtml):
  - markdown:`![image](url)` / `[video](url)` / `[audio](url)`
  - 文件下载:JSON `{"kind":"file","path","name","url"}`(resolveFileMediaTarget 解析)
这里就产出这两种,不自己拼 HTML。

多图支持:传入多个 path(用 paths 数组),前端 resolveMediaContentHtml 会在
urls.length > 1 时走 buildMediaHtmlFromUrls,把多张图渲染成网格(media-grid /
media-quad-grid),即一个卡片里同时展示多张。
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import quote

from fm.backend.pathutil import from_agent_path, resolve

from ._registry import registry

_IMAGE_EXTS = {
    "png", "jpg", "jpeg", "webp", "gif", "bmp", "svg", "ico",
    "tif", "tiff", "avif", "heic", "heif",
}
_VIDEO_EXTS = {"mp4", "webm", "mov", "avi", "mkv", "m4v", "flv", "wmv", "mpeg", "mpg"}
_AUDIO_EXTS = {"mp3", "wav", "ogg", "flac", "m4a", "aac", "opus", "wma", "mid", "midi", "amr"}

#: 图/视频/音频的 markdown 模板。前端 extractMediaOnlyHtml 认这三种。
_MEDIA_MARKDOWN = {
    "image": "![image]({url})",
    "video": "[video]({url})",
    "audio": "[audio]({url})",
}

DESCRIPTION = "展示工作区文件"

RENDER_MEDIA_DOC = """把工作区里已存在的文件展示给用户 —— 图片、视频、音频直接在对话里播放,其他格式给下载卡片。

**这个工具不生成内容,只负责展示。** 文件必须已经在工作区里。

什么时候用:你(或你调的模型/脚本/命令)往工作区写了文件之后,调它一次,用户就能直接看到。
生成了图不调它,用户只能看到一段文字路径,看不到图。

  render_media(path="D:/work/images/cat.png")            → 图片显示在对话里
  render_media(path="D:/work/videos/clip.mp4")           → 视频播放器
  render_media(path="D:/work/audio/bgm.mp3")             → 音频播放器
  render_media(path="D:/work/docs/report.docx")          → 下载卡片
  render_media(path="D:/work/images/cat.png", caption="按你要求生成的橘猫")  → 带一句说明

  # 多图:一次展示多个文件,前端渲染成网格(图片一张卡片里并排)
  render_media(paths=["D:/work/images/a.png", "D:/work/images/b.png"])

path 用文件的真实绝对路径(正斜杠);相对路径按工作区目录解析。
传单个文件用 path;传多个用 paths(数组)。两者给一个即可,同时给多个时以 paths 为准。
多图时按同类媒体(都是图片/都是视频)渲染成网格;混用不同类型以前面为主,建议同类型一起。
type 一般不用填,按扩展名自动判断;扩展名不认识时会当成文件给下载卡片,
需要强制时可以填 image / video / audio / file。"""


def _resolve(rel: str) -> tuple[str, Path]:
    """→ (规范形态的真实路径, 系统路径)。"""
    fs = from_agent_path(rel, is_directory=False)
    return fs, Path(resolve(fs))


def _resolve_multi(rels: list[str]) -> list[tuple[str, Path]]:
    """校验并解析一组路径。任一无效都会抛错。"""
    out = []
    for rel in rels:
        rel, full = _resolve(rel)
        out.append((rel, full))
    return out


def _infer_kind(rel: str, explicit: str) -> str:
    forced = str(explicit or "").strip().lower()
    if forced in ("image", "video", "audio", "file"):
        return forced
    if forced == "download":
        return "file"
    ext = PurePosixPath(rel).suffix.lstrip(".").lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    return "file"


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def _read_url(rel: str) -> str:
    return "/workspace/read?path=" + quote(rel, safe="")


@registry.register(
    toolset="skills",
    name="render_media",
    summary="把工作区里的文件展示给用户(图/视频/音频内嵌播放,其他给下载卡)",
    description=DESCRIPTION,
    doc=RENDER_MEDIA_DOC,
)
async def render_media(
    path: str = "",
    type: str = "",
    caption: str = "",
    paths: Optional[list[str]] = None,
) -> str:
    """把工作区里已存在的文件展示给用户。只负责展示,不生成内容。

    Args:
        path: 单个文件的真实绝对路径,如 D:/work/images/cat.png（与 paths 二选一）
        type: 可选,强制类型 image / video / audio / file。默认按扩展名判断
        caption: 可选,显示在媒体卡片上的一句说明
        paths: 可选,多个文件的真实绝对路径数组。传给这个当多图渲染成网格
    """
    # 收集要展示的文件
    items: list[tuple[str, Path]] = []
    if paths:
        try:
            items = _resolve_multi([str(p) for p in paths])
        except ValueError as e:
            return _err(str(e))
    else:
        try:
            items = [_resolve(path)]
        except ValueError as e:
            return _err(str(e))

    if not items:
        return _err("没有要展示的文件")

    # 全部校验存在
    for rel, full in items:
        if not full.is_file():
            return _err(f"文件不存在: {rel}")

    # 类型用第一个文件推断(多图按同一媒体类型渲染)
    kind = _infer_kind(items[0][0], type)
    urls = [_read_url(rel) for rel, _full in items]

    if len(urls) == 1:
        # 单文件:保持原有行为
        rel = items[0][0]
        if kind == "file":
            content = json.dumps(
                {"kind": "file", "path": rel, "name": PurePosixPath(rel).name or "download", "url": urls[0]},
                ensure_ascii=False,
            )
        else:
            content = _MEDIA_MARKDOWN[kind].format(url=urls[0])
        payload = {
            "status": "succeeded",
            "skill": "render_media",
            "toolName": "render_media",
            "mediaType": kind,
            "content": content,
            "mediaContent": content,
            "urls": urls,
        }
        cap = str(caption or "").strip()
        if cap:
            payload["textContent"] = cap
        return json.dumps(
            {"ok": True, "path": rel, "kind": kind, "url": urls[0], "shown": True,
             "note": "已展示给用户,不用再把 URL 或路径复述一遍",
             "_media": payload},
            ensure_ascii=False,
        )

    # 多文件:走网格。content 保留 markdown 形式(多行),urls 填满供 buildMediaHtmlFromUrls 用
    if kind == "file":
        # 多文件下载卡:给一组 {kind:file} JSON 也是前端可解析的,但更稳妥直接给 markdown 下载卡片。
        lines = "".join(json.dumps(
            {"kind": "file", "path": rel, "name": PurePosixPath(rel).name or "download", "url": url},
            ensure_ascii=False,
        ) + "\n" for rel, url in zip([r for r, _f in items], urls)).strip()
        content = lines
    else:
        content = "\n".join(_MEDIA_MARKDOWN[kind].format(url=u) for u in urls)

    payload = {
        "status": "succeeded",
        "skill": "render_media",
        "toolName": "render_media",
        "mediaType": kind,
        "content": content,
        "mediaContent": content,
        "urls": urls,
    }
    cap = str(caption or "").strip()
    if cap:
        payload["textContent"] = cap
    return json.dumps(
        {"ok": True, "path": [r for r, _f in items], "kind": kind, "url": urls, "shown": True,
         "note": "已展示给用户,不用再把 URL 或路径复述一遍",
         "_media": payload},
        ensure_ascii=False,
    )


RENDER_TEXT_DOC = """把工作区里已存在的文本文件原文展示到对话文本框。

这个工具不生成内容,只负责展示。文件必须已经在工作区里。

什么时候用:脚本已经把结果写成工作区文本文件之后,调它一次,原文会出现在对话文本框里。

  render_text(path="campaign-answers/V-10.md")

path 用真实绝对路径(正斜杠);相对路径按工作区目录解析。不要把文件内容再打一遍,也不要把数字和名字复述给用户。"""


@registry.register(
    toolset="skills",
    name="render_text",
    summary="把工作区文本文件的原文展示到对话文本框",
    description="展示工作区文本",
    doc=RENDER_TEXT_DOC,
)
async def render_text(path: str) -> str:
    try:
        rel, full = _resolve(path)
    except ValueError as e:
        return _err(str(e))
    if not full.is_file():
        return _err(f"文件不存在: {rel}")
    text = full.read_text(encoding="utf-8")
    if not str(text).strip():
        return _err(f"文件是空的: {rel}")
    payload = {
        "status": "succeeded",
        "skill": "render_text",
        "toolName": "render_text",
        "mediaType": "text",
        "content": text,
        "mediaContent": text,
        "textContent": text,
    }
    return json.dumps(
        {
            "ok": True,
            "path": rel,
            "kind": "text",
            "shown": True,
            "note": "原文已展示到文本框,不要再复述文件内容、数字或名字",
            "_media": payload,
        },
        ensure_ascii=False,
    )
