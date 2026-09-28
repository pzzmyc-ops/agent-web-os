from __future__ import annotations

import json
import os

from fm.backend.pathutil import WORKSPACE, from_agent_path, resolve, to_fs

from ._registry import registry
from ._rg import run_rg, search_base_args

MAX_FILES = 1000
MAX_SCAN = 10000

DESCRIPTION = "按文件名模式找文件"

GLOB_DOC = """按文件名 glob 在工作区里找文件,结果按修改时间从新到旧排。找「叫什么名字的文件在哪」用它,
搜文件内容用 grep,按含义找用 semantic_search。不要用 terminal 跑 find/dir/ls -R。

参数:
  glob_pattern       如 "*.py"、"**/test_*.py"、"src/**/*.{js,ts}";不以 **/ 开头的模式会自动递归匹配子目录
  target_directory   目录的真实绝对路径(正斜杠,如 D:/proj);相对路径按工作区目录解析;留空 = 工作区目录

最多返回 1000 条,尾部一行 [total_files=… shown=… truncated=…]。truncated=true 表示结果不完整,
此时会多一行 Note 说明还有多少没显示 —— 应该收窄 glob 或指定 target_directory,不要照单全收。
默认遵守 .gitignore 与 .ignore,点文件可见,.git/ 不列。"""


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _normalize_pattern(pattern: str) -> str:
    pat = pattern.strip().replace("\\", "/")
    if pat.startswith("./"):
        pat = pat[2:]
    pat = pat.lstrip("/")
    if not pat.startswith("**/"):
        pat = "**/" + pat
    return pat


@registry.register(
    toolset="search",
    name="glob",
    summary="按文件名模式查找文件",
    description=DESCRIPTION,
    doc=GLOB_DOC,
)
def glob(glob_pattern: str, target_directory: str = "") -> str:
    """按文件名 glob 查找工作区文件,按修改时间倒序。

    Args:
        glob_pattern: 文件名模式,如 "*.py" 或 "src/**/*.js"。
        target_directory: 目录的真实绝对路径,相对路径按工作区目录解析,留空搜工作区目录。
    """
    raw = str(glob_pattern or "").strip()
    if not raw:
        return _error("glob_pattern 不能为空")
    try:
        root = resolve(from_agent_path(str(target_directory or "").strip(), is_directory=True))
    except ValueError as exc:
        return _error(f"路径无效: {exc}")
    if not os.path.isdir(root):
        return _error(f"目录不存在: {target_directory}")
    pat = _normalize_pattern(raw)
    try:
        code, out, _err = run_rg(
            ["--files"] + search_base_args(resolve(WORKSPACE), root) + ["--glob", pat],
            cwd=root,
        )
    except (RuntimeError, OSError) as exc:
        return _error(str(exc))
    if code == 1 or not out.strip():
        return "No files found\n[total_files=0 shown=0 truncated=false]"
    matched = [line.strip() for line in out.splitlines() if line.strip()]
    total = len(matched)
    scan_truncated = total > MAX_SCAN
    entries: list[tuple[float, str]] = []
    for name in matched[:MAX_SCAN]:
        full = os.path.join(root, name)
        try:
            mtime = os.stat(full).st_mtime
        except OSError:
            continue
        entries.append((mtime, full))
    entries.sort(key=lambda item: -item[0])
    shown = entries[:MAX_FILES]
    truncated = scan_truncated or total > len(shown)
    lines = [to_fs(full) for _, full in shown]
    lines.append(f"[total_files={total} shown={len(shown)} truncated={str(truncated).lower()}]")
    if truncated:
        remaining = total - len(shown)
        prefix = "至少还有" if scan_truncated else "还有"
        lines.append(
            f"Note: {prefix} {remaining} 个文件没有显示。"
            "请收窄 glob_pattern 或指定 target_directory 后重查,不要把这份结果当成全部。"
        )
        if scan_truncated:
            lines.append(
                f"Note: 匹配数超过 {MAX_SCAN},按修改时间排序只覆盖了前 {MAX_SCAN} 个匹配,排序结果不代表全局最新。"
            )
    return "\n".join(lines)
