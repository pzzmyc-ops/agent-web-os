from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from agent_framework import Content

from fm.backend.pathutil import from_agent_path, resolve

from ._registry import registry

TOOL_NAME = "view_image"

_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}

DESCRIPTION = "查看工作区图片"

VIEW_IMAGE_DOC = """把工作区里已存在的图片送到你眼前,你会直接看到画面本身,不是文字描述。

  view_image(path="images/cat.png")
  view_image(paths=["视频站位/beat1/beat1_result_v1.png", "视频站位/beat1/beat1_result_v2.png"])

什么时候用:需要判断一张已经在工作区里的图画了什么、和提示词对不对得上、有没有多余元素,
调它之后凭你看到的画面下结论。用户直接贴在消息里的图片你本来就能看到,不需要再调。

path 用真实绝对路径(正斜杠,如 D:/a/b.png);相对路径按工作区目录解析。传单张用 path;传多张用 paths(数组),
两者给一个即可,同时给多个时以 paths 为准。支持 png / jpg / jpeg / webp / gif。
一次不要塞太多张:每张图都占上下文,逐组核对时按组调用。"""


def _resolve(rel: str) -> tuple[str, Path]:
    fs = from_agent_path(rel)
    return fs, Path(resolve(fs))


def _mime_of(rel: str) -> str:
    ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
    mime = _MIME_BY_EXT.get(ext)
    if not mime:
        raise ValueError(f"不支持的图片格式: {rel} —— 只认 " + " / ".join(sorted(_MIME_BY_EXT)))
    return mime


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


@registry.register(
    toolset="skills",
    name=TOOL_NAME,
    summary="把工作区里的图片送到模型眼前(模型直接看画面)",
    description=DESCRIPTION,
    doc=VIEW_IMAGE_DOC,
)
async def view_image(
    path: str = "",
    paths: Optional[list[str]] = None,
) -> list[Content] | str:
    rels = [str(p) for p in paths] if paths else [path]
    items: list[tuple[str, Path, str]] = []
    try:
        for rel in rels:
            rel, full = _resolve(rel)
            items.append((rel, full, _mime_of(rel)))
    except ValueError as e:
        return _err(str(e))
    if not items:
        return _err("没有要查看的图片")
    for rel, full, _mime in items:
        if not full.is_file():
            return _err(f"文件不存在: {rel}")

    out: list[Content] = []
    shown = [rel for rel, _full, _mime in items]
    out.append(Content.from_text(json.dumps(
        {
            "ok": True,
            "paths": shown,
            "note": "图片已随本条结果送到你眼前,顺序与 paths 一致,直接根据看到的画面作答",
        },
        ensure_ascii=False,
    )))
    for rel, full, mime in items:
        out.append(Content.from_data(full.read_bytes(), media_type=mime))
    return out
