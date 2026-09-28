from __future__ import annotations

import json
import os
from dataclasses import dataclass

from fm.backend.pathutil import WORKSPACE, from_agent_path, resolve, to_fs

from ._context import current_turn
from ._registry import registry
from ._rg import run_rg, search_base_args

OUTPUT_MODES = ("files_with_matches", "content", "count")
DEFAULT_HEAD_LIMIT = 250
MAX_LINE_CHARS = 500
MAX_OUTPUT_CHARS = 30000
MATCH_ALL_GLOBS = frozenset({"*", "**", "**/*", "*.*", "**/*.*", "./**", "./*"})

DESCRIPTION = "ripgrep 精确搜索"

GREP_DOC = """用 ripgrep 在工作区里按正则搜文件内容。找确切的字符串、符号名、报错文案时用它;
按含义找「哪里处理了 X」用 semantic_search;按文件名找用 glob。不要用 terminal 跑 grep/rg/find。

参数:
  pattern            ripgrep 正则(Rust 语法)。字面括号要转义,如 interface\\{\\}
  path               文件或目录的真实绝对路径(正斜杠,如 D:/proj/src);相对路径按工作区目录解析;留空 = 工作区目录
  glob               只搜匹配的文件,如 "*.py"、"src/**/*.js";匹配一切的 glob(如 "*")会被拒绝
  type               ripgrep 文件类型,如 py / js / md / rust
  output_mode        files_with_matches(默认,只列文件) | content(命中行+行号) | count(每文件命中数)
  context_before / context_after / context   仅 content 模式:命中行前/后/前后各多少行
  case_insensitive   忽略大小写(默认区分)
  multiline          让 . 跨行、模式可跨多行匹配
  head_limit         最多返回多少条(content 按行、其余按条),默认 250
  offset             跳过前多少条,配合 head_limit 翻页;同参数翻页不重跑 ripgrep

输出尾部有一行统计:[total_matches=… total_files=… shown=… head_limit_applied=… offset_applied=… client_truncated=…],
被截断时据此决定翻页还是收窄模式,并会多一行 Note 明确提示结果不完整。默认遵守 .gitignore 与 .ignore,点文件可搜,.git/ 不搜。"""


@dataclass(frozen=True)
class _Key:
    pattern: str
    path: str
    glob: str
    type: str
    output_mode: str
    context_before: int
    context_after: int
    context: int
    case_insensitive: bool
    multiline: bool


def _resolve_search_path(path: str) -> str:
    return resolve(from_agent_path(str(path or "").strip(), is_directory=True))


def _rel(base: str, printed: str) -> str:
    return to_fs(os.path.join(base, printed))


def _invoke(args: list[str], root: str) -> tuple[int, str, str]:
    if os.path.isfile(root):
        base = os.path.dirname(root)
        return run_rg(args + [os.path.basename(root)], cwd=base)
    return run_rg(args, cwd=root)


def _base_dir(root: str) -> str:
    return os.path.dirname(root) if os.path.isfile(root) else root


def _clip(text: str) -> str:
    text = text.rstrip("\r\n")
    if len(text) > MAX_LINE_CHARS:
        return text[:MAX_LINE_CHARS] + " [line truncated]"
    return text


def _base_args(key: _Key) -> list[str]:
    args = search_base_args(resolve(WORKSPACE), key.path)
    if key.case_insensitive:
        args.append("-i")
    if key.multiline:
        args += ["-U", "--multiline-dotall"]
    if key.glob:
        args += ["--glob", key.glob]
    if key.type:
        args += ["--type", key.type]
    args += ["-e", key.pattern]
    return args


def _run_content(key: _Key, root: str) -> dict:
    args = ["--json"] + _base_args(key)
    if key.context:
        args += ["-C", str(key.context)]
    else:
        if key.context_before:
            args += ["-B", str(key.context_before)]
        if key.context_after:
            args += ["-A", str(key.context_after)]
    code, out, _err = _invoke(args, root)
    base = _base_dir(root)
    lines: list[tuple[str, int, str, bool]] = []
    files: set[str] = set()
    matched = 0
    if code == 1:
        return {"lines": lines, "total_files": 0, "total_matches": 0}
    for raw in out.splitlines():
        if not raw.strip():
            continue
        msg = json.loads(raw)
        kind = msg.get("type")
        if kind not in ("match", "context"):
            continue
        data = msg["data"]
        path_text = (data.get("path") or {}).get("text")
        if path_text is None:
            continue
        rel = _rel(base, path_text)
        text = (data.get("lines") or {}).get("text")
        if text is None:
            text = "<非 UTF-8 行>"
        line_no = int(data.get("line_number") or 0)
        is_ctx = kind == "context"
        if not is_ctx:
            matched += 1
            files.add(rel)
        lines.append((rel, line_no, _clip(text), is_ctx))
    return {"lines": lines, "total_files": len(files), "total_matches": matched}


def _run_files(key: _Key, root: str) -> dict:
    code, out, _err = _invoke(["--files-with-matches"] + _base_args(key), root)
    if code == 1:
        return {"files": [], "total_files": 0}
    base = _base_dir(root)
    files = [_rel(base, l) for l in out.splitlines() if l.strip()]
    return {"files": files, "total_files": len(files)}


def _run_count(key: _Key, root: str) -> dict:
    code, out, _err = _invoke(["--count-matches", "--with-filename"] + _base_args(key), root)
    if code == 1:
        return {"counts": [], "total_files": 0, "total_matches": 0}
    base = _base_dir(root)
    counts: list[tuple[str, int]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        path_part, _, num = line.rpartition(":")
        counts.append((_rel(base, path_part), int(num)))
    counts.sort(key=lambda item: -item[1])
    return {"counts": counts, "total_files": len(counts), "total_matches": sum(c for _, c in counts)}


def _cached(key: _Key, root: str) -> dict:
    ctx = current_turn()
    if ctx is None:
        return _execute(key, root)
    if ctx.grep_cache is None:
        ctx.grep_cache = {}
    hit = ctx.grep_cache.get(key)
    if hit is None:
        hit = _execute(key, root)
        ctx.grep_cache[key] = hit
    return hit


def _execute(key: _Key, root: str) -> dict:
    if key.output_mode == "content":
        return _run_content(key, root)
    if key.output_mode == "files_with_matches":
        return _run_files(key, root)
    return _run_count(key, root)


def _stats_line(total_matches: int, total_files: int, shown: int, head_applied: bool, offset_applied: bool, client_truncated: bool) -> str:
    return (
        f"[total_matches={total_matches} total_files={total_files} shown={shown} "
        f"head_limit_applied={str(head_applied).lower()} offset_applied={str(offset_applied).lower()} "
        f"client_truncated={str(client_truncated).lower()}]"
    )


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


TRUNCATED_NOTE = (
    "Note: 结果不完整。收窄 pattern/glob/type 或指定 path 重查,"
    "要看后续结果用 offset 翻页,不要把这份结果当成全部。"
)


def _finish(body_lines: list[str], stats: str) -> str:
    body = "\n".join(body_lines)
    truncated = False
    if len(body) > MAX_OUTPUT_CHARS:
        body = body[:MAX_OUTPUT_CHARS] + "\n... [output truncated]"
        truncated = True
    if truncated:
        stats = stats.replace("client_truncated=false", "client_truncated=true")
    if truncated or "head_limit_applied=true" in stats:
        stats += "\n" + TRUNCATED_NOTE
    return body + ("\n" if body else "") + stats


@registry.register(
    toolset="search",
    name="grep",
    summary="ripgrep 精确搜索文件内容",
    description=DESCRIPTION,
    doc=GREP_DOC,
)
def grep(
    pattern: str,
    path: str = "",
    glob: str = "",
    type: str = "",
    output_mode: str = "files_with_matches",
    context_before: int = 0,
    context_after: int = 0,
    context: int = 0,
    case_insensitive: bool = False,
    multiline: bool = False,
    head_limit: int = DEFAULT_HEAD_LIMIT,
    offset: int = 0,
) -> str:
    """用 ripgrep 按正则搜索工作区文件内容。

    Args:
        pattern: ripgrep 正则。
        path: 文件或目录的真实绝对路径,相对路径按工作区目录解析,留空搜工作区目录。
        glob: 文件过滤 glob,如 "*.py";匹配一切的 glob 会被拒绝。
        type: ripgrep 文件类型名,如 py / js / md。
        output_mode: files_with_matches | content | count。
        context_before: content 模式下命中行之前的行数。
        context_after: content 模式下命中行之后的行数。
        context: content 模式下命中行前后各多少行,设了就忽略前两项。
        case_insensitive: 忽略大小写。
        multiline: 允许跨行匹配。
        head_limit: 最多返回多少条,默认 250。
        offset: 跳过前多少条,用于翻页。
    """
    pat = str(pattern or "")
    if not pat:
        return _error("pattern 不能为空")
    mode = str(output_mode or "files_with_matches").strip()
    if mode not in OUTPUT_MODES:
        return _error(f"output_mode 只能是 {', '.join(OUTPUT_MODES)}")
    g = str(glob or "").strip()
    if g and g in MATCH_ALL_GLOBS:
        return _error(f'Glob pattern "{g}" matches every file and is not allowed. Use a more specific glob or no glob.')
    limit = int(head_limit) if head_limit is not None else DEFAULT_HEAD_LIMIT
    if limit <= 0:
        return _error("head_limit 必须大于 0")
    skip = max(0, int(offset or 0))
    try:
        root = _resolve_search_path(path)
    except ValueError as exc:
        return _error(f"路径无效: {exc}")
    if not os.path.exists(root):
        return _error(f"路径不存在: {path}")
    key = _Key(
        pattern=pat,
        path=root,
        glob=g,
        type=str(type or "").strip(),
        output_mode=mode,
        context_before=max(0, int(context_before or 0)),
        context_after=max(0, int(context_after or 0)),
        context=max(0, int(context or 0)),
        case_insensitive=bool(case_insensitive),
        multiline=bool(multiline),
    )
    try:
        result = _cached(key, root)
    except (RuntimeError, OSError) as exc:
        return _error(str(exc))

    if mode == "content":
        items = result["lines"]
        page = items[skip:skip + limit]
        body: list[str] = []
        last_file = None
        for rel, line_no, text, is_ctx in page:
            if rel != last_file:
                if last_file is not None:
                    body.append("")
                body.append(rel)
                last_file = rel
            sep = "-" if is_ctx else ":"
            body.append(f"{line_no}{sep} {text}")
        if not page:
            body.append("No matches found")
        stats = _stats_line(
            result["total_matches"], result["total_files"], len(page),
            len(items) - skip > limit, skip > 0, False,
        )
        return _finish(body, stats)

    if mode == "files_with_matches":
        items = result["files"]
        page = items[skip:skip + limit]
        body = list(page) if page else ["No files found"]
        stats = _stats_line(
            result["total_files"], result["total_files"], len(page),
            len(items) - skip > limit, skip > 0, False,
        )
        return _finish(body, stats)

    items = result["counts"]
    page = items[skip:skip + limit]
    body = [f"{rel}:{count}" for rel, count in page] if page else ["No matches found"]
    stats = _stats_line(
        result["total_matches"], result["total_files"], len(page),
        len(items) - skip > limit, skip > 0, False,
    )
    return _finish(body, stats)
