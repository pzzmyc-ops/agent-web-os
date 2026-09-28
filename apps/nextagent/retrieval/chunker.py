from __future__ import annotations

from dataclasses import dataclass

CHUNKER_VERSION = "2"
MIN_LINES = 30
MAX_LINES = 100
OVERLAP_LINES = 10
MAX_CHUNK_CHARS = 2000
MAX_LINE_CHARS = 500


@dataclass(frozen=True)
class Chunk:
    start_line: int
    end_line: int
    text: str


def _clip_line(line: str) -> str:
    if len(line) > MAX_LINE_CHARS:
        return line[:MAX_LINE_CHARS]
    return line


def chunk_lines(text: str) -> list[Chunk]:
    lines = [_clip_line(l) for l in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    if total == 0:
        return []
    chunks: list[Chunk] = []
    start = 0
    while start < total:
        end = start
        chars = 0
        while end < total:
            add = len(lines[end]) + 1
            if end - start >= MAX_LINES:
                break
            if chars + add > MAX_CHUNK_CHARS and end > start:
                break
            chars += add
            end += 1
            if end - start >= MIN_LINES and end < total and lines[end].strip() == "":
                break
        if end == start:
            end = start + 1
        chunks.append(Chunk(start_line=start + 1, end_line=end, text="\n".join(lines[start:end])))
        if end >= total:
            break
        next_start = end - OVERLAP_LINES
        if next_start <= start:
            next_start = end
        start = next_start
    return chunks
