"""tool 层 —— agent 的脚手架与能力延伸。

分层:

    第三层  skill    能力包 = instructions + toolset 子集(暂未实现,挂点已留)
    第二层  tool     本包
    第一层  底层     文件管理器桌面本身;文件读写由框架 FileAccessProvider 提供

用法:

    from toolkit import registry, describe_all
    agent = Agent(..., tools=registry.function_tools())

新增工具:在本包里建模块,用 @registry.register(toolset=...) 声明,然后在下面的
「工具模块导入」区加一行 import。显式导入而不是扫目录 —— 出问题时栈是干净的。

工具的开始/完成事件不用管:tool_tracker.ToolTracker 统一处理所有工具的
function_call / function_result,新工具自动就有生命周期事件。
"""
from __future__ import annotations

from ._context import (
    ClarifyChannel,
    TurnContext,
    bind_turn,
    current_turn,
    require_turn,
    reset_turn,
    set_turn,
)
from ._registry import ToolRegistry, ToolSpec, registry

# ── 工具模块导入(注册发生在导入时)──
from . import terminal as _terminal  # noqa: F401  terminal
from . import process as _process  # noqa: F401  process (list/poll/log/wait/kill)
from . import clarify as _clarify  # noqa: F401  clarify (问用户并等回答)
from . import schedule as _schedule  # noqa: F401  schedule (定时任务)
from . import media as _media  # noqa: F401  render_media / render_text
from . import view_image as _view_image  # noqa: F401  view_image
from . import skills_manage as _skills  # noqa: F401  skill_manage + scan_skills
from . import skills_hub as _skills_hub  # noqa: F401  skills_hub
from . import roles_manage as _roles  # noqa: F401  role_manage (角色卡 + 拉进对话)
from . import tool_help as _tool_help  # noqa: F401  tool_help (查阅工具文档)
from . import dingtalk as _dingtalk  # noqa: F401  dingtalk_list_messages / download / send_text / send_file
from . import feishu as _feishu  # noqa: F401  feishu_list_messages / download / send_text / send_file
from . import memory as _memory  # noqa: F401  memory
from . import grep as _grep  # noqa: F401  grep (ripgrep 精确搜索)
from . import glob as _glob  # noqa: F401  glob (按文件名找)
from . import semantic_search as _semantic_search  # noqa: F401  semantic_search (语义索引)

__all__ = [
    "ClarifyChannel",
    "ToolRegistry",
    "ToolSpec",
    "TurnContext",
    "bind_turn",
    "current_turn",
    "describe_all",
    "registry",
    "require_turn",
    "reset_turn",
    "set_turn",
]

#: 描述工具清单时只暴露 skills 工具集给前端 UI。底层能力(file_access_* 等)
#: 仍然由框架注入 Agent,只是不在前端下拉里显示,避免干扰用户。

def describe_all(*, toolsets: list[str] | None = None) -> list[dict]:
    """对外的工具清单。只暴露 skills 工具集,底层工具(terminal/process/file)不显示。

    形状迁就前端已有的工具下拉(static/js/input.js:fetchSkills 用 id/name)。
    """
    # 只暴露 skills 工具集给前端 UI
    items = registry.describe(toolsets=["skills"] if toolsets is None else toolsets)
    return items
