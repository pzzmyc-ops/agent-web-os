"""工具输出截断上限(mafagent 版:常量 + 环境变量覆盖)。

hermes 原版从 config.yaml 的 tool_output 段读;mafagent 没有那份配置,
默认值与 hermes 一致,需要调时用环境变量:
  MAFAGENT_TOOL_MAX_BYTES / MAFAGENT_TOOL_MAX_LINES / MAFAGENT_TOOL_MAX_LINE_LENGTH
读取失败一律回落默认值 —— 工具不能因为配置写错就跑不起来。
"""
from __future__ import annotations

import os
from typing import Any, Dict

DEFAULT_MAX_BYTES = 50_000       # terminal_tool.MAX_OUTPUT_CHARS
DEFAULT_MAX_LINES = 2000         # file_operations.MAX_LINES
DEFAULT_MAX_LINE_LENGTH = 2000   # file_operations.MAX_LINE_LENGTH

_cached_limits: Dict[str, int] | None = None


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return default
    return iv if iv > 0 else default


def get_tool_output_limits() -> Dict[str, int]:
    """返回 {max_bytes, max_lines, max_line_length};进程内缓存,永不抛异常。"""
    global _cached_limits
    if _cached_limits is not None:
        return _cached_limits
    _cached_limits = {
        "max_bytes": _coerce_positive_int(os.environ.get("MAFAGENT_TOOL_MAX_BYTES"), DEFAULT_MAX_BYTES),
        "max_lines": _coerce_positive_int(os.environ.get("MAFAGENT_TOOL_MAX_LINES"), DEFAULT_MAX_LINES),
        "max_line_length": _coerce_positive_int(
            os.environ.get("MAFAGENT_TOOL_MAX_LINE_LENGTH"), DEFAULT_MAX_LINE_LENGTH
        ),
    }
    return _cached_limits


def _reset_tool_output_limits_cache() -> None:
    global _cached_limits
    _cached_limits = None


def get_max_bytes() -> int:
    return get_tool_output_limits()["max_bytes"]


def get_max_lines() -> int:
    return get_tool_output_limits()["max_lines"]


def get_max_line_length() -> int:
    return get_tool_output_limits()["max_line_length"]
