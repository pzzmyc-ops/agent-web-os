"""tool_help —— agent 按需查阅工具的完整用法文档。

渐进式披露的第二层:工具列表里只有简短标题(≤10 字),模型需要了解某个工具的
详细用法、参数说明、注意事项时调这个。
"""
from __future__ import annotations

import json

from ._registry import registry


@registry.register(
    toolset="skills",
    name="tool_help",
    summary="查阅工具的完整用法文档(详细参数、示例、注意事项)",
    description="查阅工具用法",
    doc="查阅某个或多个工具的完整用法文档。不传 name 则返回所有可用工具的名称列表。",
)
def tool_help(name: str = "") -> str:
    """查阅工具的完整用法文档。

    不传 name:返回所有可用工具的名称与简短描述。
    传 name:返回该工具的完整文档(参数说明、用法示例、注意事项)。

    Args:
        name: 工具名,如 terminal、schedule、clarify。留空列出所有工具。
    """
    name = str(name or "").strip()
    if not name:
        items = []
        for s in registry.specs():
            items.append({"name": s.name, "brief": s.summary})
        return json.dumps({"tools": items}, ensure_ascii=False)

    body = registry.get_body(name)
    if not body:
        available = [s.name for s in registry.specs()]
        return json.dumps({
            "error": f"没有叫 '{name}' 的工具",
            "available_tools": available,
        }, ensure_ascii=False)

    return json.dumps({"name": name, "documentation": body}, ensure_ascii=False)
