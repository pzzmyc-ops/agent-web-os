"""tool 层的注册表。

分层约定(为什么这一层不做文件操作):

    第三层  skill    —— 挂在 toolset 上的能力包(暂未实现)
    第二层  tool     —— 本包:agent 的脚手架与能力延伸
    第一层  底层     —— 文件管理器 / webos 本身

底层能力不进这一层:文件读写由框架的 FileAccessProvider 提供(每轮注入 7 个
file_access_* 工具),文件管理器的 /api/* 是它自己的底层接口。本包只放「agent 需要
的、底层不提供的」东西 —— 终端、以后的 webos 操作(窗口/app/桌面)等。

与框架的关系:register() 内部就调框架的 tool(),所以产出的直接是 FunctionTool,
交给 Agent(tools=...) 即可,不需要任何 schema 转换层。工具事件(开始/完成)由
tool_tracker.ToolTracker 统一处理,新增工具不用做任何事就有生命周期事件。

toolset 是给 skill 预留的挂点:一个 skill 将来 = 一段 instructions + 选定的
toolset 子集,所以 specs()/function_tools() 都支持按 toolset 过滤。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent_framework import tool

from ..framework_compat import FunctionTool


@dataclass(frozen=True)
class ToolSpec:
    """一个已注册工具的全部元信息。"""

    name: str
    toolset: str
    summary: str
    tool: FunctionTool
    available: Callable[[], bool] | None = None
    body: str = ""

    def is_available(self) -> bool:
        if self.available is None:
            return True
        try:
            return bool(self.available())
        except Exception:
            return False  # 探测本身失败就当不可用,不要让它把整轮搞崩


class ToolRegistry:
    """进程级注册表。工具在模块导入时注册(见 toolkit/__init__.py 的显式导入)。

    刻意不做的事(hermes 的 registry 做了,但那是在解它自己的问题):
    - 不 AST 扫描目录发现工具 —— 显式 import 更好排查
    - 不做 check_fn 的 TTL 缓存与抖动抑制 —— 那是为探 Docker/Modal 存活设计的
    - 不做插件覆盖授权 —— 没有插件生态
    - 不产出 OpenAI schema —— 框架要的是 FunctionTool
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    # ---- 注册 ----

    def register(
        self,
        *,
        toolset: str,
        name: str | None = None,
        summary: str = "",
        available: Callable[[], bool] | None = None,
        description: str | None = None,
        doc: str = "",
        max_invocations: int | None = None,
    ):
        """把一个普通函数注册成工具。装饰器,返回框架的 FunctionTool。

        description 给模型看(工具列表里的简短标题,≤10 字);
        doc 是完整的用法文档,模型通过 tool_help 按需查阅;
        summary 给用户看(UI 列表)。
        """

        def deco(func: Callable[..., Any]) -> FunctionTool:
            tool_name = name or func.__name__
            desc = description or (func.__doc__ or "").strip().split("\n\n")[0].strip()
            ft = tool(
                func,
                name=tool_name,
                description=desc or tool_name,
                max_invocations=max_invocations,
            )
            if tool_name in self._specs:
                raise ValueError(f"工具重名: {tool_name}(已属于 toolset {self._specs[tool_name].toolset})")
            body = doc or (func.__doc__ or "").strip()
            self._specs[tool_name] = ToolSpec(
                name=tool_name,
                toolset=toolset,
                summary=summary or desc or tool_name,
                tool=ft,
                available=available,
                body=body,
            )
            return ft

        return deco

    # ---- 查询 ----

    def toolsets(self) -> list[str]:
        return sorted({s.toolset for s in self._specs.values()})

    def specs(self, *, toolsets: list[str] | None = None, include_unavailable: bool = False) -> list[ToolSpec]:
        """按 toolset 过滤(None = 全部)。默认剔除当前不可用的工具。"""
        out = []
        for spec in self._specs.values():
            if toolsets is not None and spec.toolset not in toolsets:
                continue
            if not include_unavailable and not spec.is_available():
                continue
            out.append(spec)
        return sorted(out, key=lambda s: (s.toolset, s.name))

    def function_tools(self, *, toolsets: list[str] | None = None) -> list[FunctionTool]:
        """交给 Agent(tools=...) 的列表。skill 将来靠 toolsets 参数裁剪。"""
        return [s.tool for s in self.specs(toolsets=toolsets)]

    def get_body(self, tool_name: str) -> str:
        """取工具的完整用法文档。找不到返回空字符串。"""
        spec = self._specs.get(tool_name)
        return spec.body if spec else ""

    def describe(self, *, toolsets: list[str] | None = None) -> list[dict]:
        """对外列表(前端工具下拉用)。id/name 是前端要的最小字段。"""
        return [
            {
                "id": s.name,
                "name": s.name,
                "summary": s.summary,
                "toolset": s.toolset,
                "source": "toolkit",
            }
            for s in self.specs(toolsets=toolsets)
        ]


registry = ToolRegistry()
