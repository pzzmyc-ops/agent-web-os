from __future__ import annotations

import json

from ._registry import registry

MAX_RESULTS = 20
DEFAULT_RESULTS = 10

DESCRIPTION = "按含义搜索代码和文档"

SEMANTIC_DOC = """按含义在用户添加的向量化文件夹里找相关代码/文档块。适合 how / where / what 类问题:
「X 是怎么实现的」「Y 在哪里处理」「Z 出错时会发生什么」。把 query 写成一句完整的问题,像问同事一样,
不要只丢关键词。找确切字符串用 grep,找文件名用 glob;两者可以和它并行发。

参数:
  query              一句完整的自然语言问题
  target_directory   限定在某个目录下(单个目录,不支持 glob;真实绝对路径,相对路径按工作区目录解析),留空 = 全部已索引文件夹
  num_results        返回多少块,默认 10,最多 20

结果每块给出 路径 L起-L止 和带 L<n>: 行号前缀的正文;路径是真实绝对路径。
已经拿到完整内容的块不要再用 file_access_read 重复读。
索引范围由用户在顶栏「代码索引」里手动添加/移除文件夹决定,只在用户手动点更新时刷新,不会自动跟随文件改动;
结果里会标出索引时间。没有添加文件夹、索引为空或 Ollama 未启动时会直接报错。"""


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _render_chunk(path: str, start: int, end: int, text: str, score: float) -> str:
    lines = text.split("\n")
    body = "\n".join(f"L{start + i}: {line}" for i, line in enumerate(lines))
    return f"### {path} L{start}-L{end} (score {score:.3f})\n{body}"


@registry.register(
    toolset="search",
    name="semantic_search",
    summary="按含义搜索工作区(需要先建索引)",
    description=DESCRIPTION,
    doc=SEMANTIC_DOC,
)
async def semantic_search(query: str, target_directory: str = "", num_results: int = DEFAULT_RESULTS) -> str:
    """按含义在语义索引里搜索相关代码或文档块。

    Args:
        query: 一句完整的自然语言问题。
        target_directory: 限定的工作区相对目录,留空搜整个索引。
        num_results: 返回块数,默认 10,最多 20。
    """
    from ..retrieval import retrieval_service
    from ..retrieval.service import IndexMissing, OllamaUnavailable

    q = str(query or "").strip()
    if not q:
        return _error("query 不能为空")
    k = int(num_results or DEFAULT_RESULTS)
    if k <= 0:
        return _error("num_results 必须大于 0")
    k = min(k, MAX_RESULTS)
    try:
        hits = await retrieval_service.search(q, target_directory=target_directory, top_k=k)
    except (IndexMissing, OllamaUnavailable, RuntimeError, ValueError) as exc:
        return _error(str(exc))
    status = retrieval_service.status()
    header = (
        f"[index files={status['files']} chunks={status['chunks']} built_at={status['lastBuild'] or 'unknown'} "
        f"model={status['indexedModel'] or status['model']}]"
    )
    if not hits:
        return header + "\nNo results"
    blocks = [
        _render_chunk(retrieval_service.display_path(h.path), h.start_line, h.end_line, h.text, h.score)
        for h in hits
    ]
    return header + "\n\n" + "\n\n".join(blocks)
