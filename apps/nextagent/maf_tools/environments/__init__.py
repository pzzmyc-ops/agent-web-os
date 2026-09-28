"""执行环境后端。

mafagent 只有 local 一种:命令直接跑在宿主机上,和文件管理器共用同一个工作区根。

上游 hermes 这里有 6 个后端(local/docker/singularity/modal/daytona/ssh),由
terminal_tool._create_environment 按 TERMINAL_ENV 选择。那 5 个沙箱后端在移植时
就没接过 —— 对应文件长期是 `class Sentinel: pass` 占位,配了 TERMINAL_ENV=docker
只会拿到 Sentinel 然后在别处炸掉。所以已整体删除,_create_environment 收敛成
local-only 并对其余取值明确报错。

要重新支持沙箱执行,应该基于框架能力做(agent_framework 的 hyperlight CodeAct
连接器),而不是复活这些占位文件。

BaseEnvironment 保留:它是 LocalEnvironment 的抽象基类,也是将来新后端的接口约定。
"""

from .base import BaseEnvironment

__all__ = ["BaseEnvironment"]
