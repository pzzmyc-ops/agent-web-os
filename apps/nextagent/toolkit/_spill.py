from __future__ import annotations

import json
import uuid
from pathlib import Path

from fm.backend.pathutil import to_fs
from fm.backend.settings import ROOT

OUTPUT_LIMIT_CHARS = 10_000
OUTPUT_HEAD_CHARS = 5_000
OUTPUT_TAIL_CHARS = 5_000
OUTPUT_DIR_NAME = "agent-tools"
READ_MAX_CHARS = 100_000


def spill_text(text: str) -> dict | None:
    if len(text) <= OUTPUT_LIMIT_CHARS:
        return None
    folder = Path(ROOT) / OUTPUT_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{uuid.uuid4().hex}.txt"
    target.write_text(text, encoding="utf-8")
    path = to_fs(str(target))
    elided = len(text) - OUTPUT_HEAD_CHARS - OUTPUT_TAIL_CHARS
    return {
        "text": (
            text[:OUTPUT_HEAD_CHARS]
            + f"\n\n... [省略 {elided} 字符，完整内容见 {path}] ...\n\n"
            + text[-OUTPUT_TAIL_CHARS:]
        ),
        "elided_chars": elided,
        "output_file": path,
        "output_lines": text.count("\n") + 1,
    }


def fit_messages(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    spilled = spill_text(text)
    if spilled is None:
        return text
    return json.dumps(
        {
            "conversation_id": payload.get("conversation_id"),
            "complete": payload.get("complete"),
            "hasMore": payload.get("hasMore"),
            "message_count": len(payload.get("messages") or []),
            "output_file": spilled["output_file"],
            "elided_chars": spilled["elided_chars"],
            "preview": spilled["text"],
            "warning": "消息太多，完整 JSON 存在 output_file。用 grep 搜关键词，或用 file_access_read 的 offset 和 limit 分段读。",
        },
        ensure_ascii=False,
    )


def read_slice(content: str, file_name: str, offset: int | None, limit: int | None) -> str:
    if offset is None and limit is None:
        if len(content) > READ_MAX_CHARS:
            return (
                f"Could not read file '{file_name}': 文件有 {len(content)} 字符，超过一次读取上限 {READ_MAX_CHARS}。"
                "用 offset 和 limit 分段读，或用 grep 搜具体内容。"
            )
        return content
    start = 1 if offset is None else int(offset)
    if start < 1:
        return f"Could not read file '{file_name}': offset 从 1 开始。"
    if limit is not None and int(limit) < 1:
        return f"Could not read file '{file_name}': limit 必须大于 0。"
    lines = content.splitlines(keepends=True)
    total = len(lines)
    if start > total:
        return f"Could not read file '{file_name}': 文件只有 {total} 行，offset={start} 超出范围。"
    end = total if limit is None else min(total, start - 1 + int(limit))
    chunk = "".join(lines[start - 1:end])
    if len(chunk) > READ_MAX_CHARS:
        return (
            f"Could not read file '{file_name}': 第 {start} 到 {end} 行有 {len(chunk)} 字符，超过一次读取上限 {READ_MAX_CHARS}。"
            "减小 limit；单行就很长时用 grep 搜。"
        )
    return f"{chunk}\n[lines {start}-{end} of {total}]"
