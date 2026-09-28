"""装配 Agent:基础对话 + 文件工作区 + 技能 + 自研上下文压缩。

- MafHistoryProvider:多轮对话历史(从 SQLite 载入)。
- HermesSkillsProvider:技能发现、广告注入、load_skill;加载时注入技能目录绝对路径,脚本走 terminal。
- FileAccessProvider + FileSystemAgentFileStore:沙箱文件工作区,7 个工具(读/写/列目录/搜索/替换/删除)。
- 上下文压缩走自研 compaction.py,不挂框架 CompactionProvider。
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

# 框架把 harness 整片标成 experimental,构造 FileSystemAgentFileStore 时会 warn 一次。
# 我们是明知故用 —— 文件工作区就靠它,所以只关掉这一条,别让启动第一行看着像报错。
# 精确匹配对象名:vendor 里有 18 处 ExperimentalFeature.HARNESS(memory / todo /
# background_agents / loop 等),按 "[HARNESS] 任意对象" 过滤会把以后真正该看见的
# 提示一起吞掉。ExperimentalWarning 没从 agent_framework 顶层导出,所以匹配消息
# 而不是类;消息格式见 vendor/agent_framework/_feature_stage.py:147。
warnings.filterwarnings(
    "ignore",
    message=r"\[HARNESS\] AgentFileStore is experimental",
)

from agent_framework import (
    Agent,
    AgentSession,
)

from fm.backend.pathutil import WORKSPACE

from .file_punct import PunctTolerantFileAccessProvider
from .fm_store import FmAgentFileStore

from .config import Config
from .hermes_skills import HermesSkillsProvider
from .history_provider import SPEAKER_PREFIX_NOTE, MafHistoryProvider
from .llm import build_chat_client
from .store import Store

# tool 层(第二层):agent 的脚手架与能力延伸。文件读写属于第一层,由框架的
# FileAccessProvider 提供,不进注册表。
from .toolkit import registry as tool_registry

PATH_RULES = (
    f"所有文件路径一律用真实绝对路径、正斜杠,例如 {WORKSPACE}/a.txt。"
    f"工作区目录是 {WORKSPACE},不带盘符、不以 / 开头的相对路径按工作区目录解析。"
    "file_access_ls 不传目录列出工作区目录,传 / 列出所有盘。"
)

INSTRUCTIONS_BASE = (
    "你是一个有用的中文助手,回答简洁、准确。"
    "你有文件系统访问能力,和文件管理器看到的是同一套路径(file_access_* 工具)。"
    + PATH_RULES
    + "\n\n"
    "## 工具文档\n"
    "工具列表里只有简短标题,需要了解某个工具的完整用法时先调 "
    "`tool_help(name=\"工具名\")` 查文档,不传 name 则列出所有工具。"
    "\n\n"
    "## 媒体生成\n"
    "用 `load_skill name=model-discovery` 了解如何查询和调用媒体模型(midjourney, seedance 等)。"
    "\n\n"
    "## 展示产物\n"
    "图片、视频、音频或下载卡用 render_media。"
    "查数或其它纯文字结果写成工作区文本后用 render_text，原文会进对话文本框，不要再复述内容。"
    "详情调 tool_help。"
    "\n\n"
    "## 技能系统\n"
    "用 load_skill 或 skill_view 加载技能获取详细指令和技能目录绝对路径,"
    "技能里的 scripts/ 用 terminal 按该绝对路径执行,"
    "用 skill_view(name, file_path) 或 read_skill_resource 读取附属文件,"
    "用 skills_list 查看技能列表,"
    "用 skills_hub 从 GitHub/直链/well-known 搜索并安装技能,"
    "用 skill_manage 把反复使用的工作流沉淀为技能。"
    "\n\n"
    "## 定时任务\n"
    "用 schedule 创建本对话的定时任务；list 能看到全部任务，current=true 的是本对话。"
    "到点叫醒用默认模式；"
    "只要往本对话写结果、不要被叫醒，用 mode=program；"
    "到点跑工作区程序，用 mode=exec，command 写命令。"
    "interval 从上次触发再等 every_minutes 分钟，不对齐整点。详情调 tool_help。"
    "\n\n"
    "## 记忆\n"
    "用 memory 写入本对话的记忆。每个对话一份，群聊角色共用。"
    "用户偏好、纠正、稳定事实才写；过程进度不要写。"
    "一次调用用 operations 做完全部改动。详情调 tool_help。"
    "\n\n"
    "## 钉钉发出\n"
    "用户说任务完成、通过钉钉发出、发到群里时，load_skill name=dingtalk-send-group，"
    "再用 dingtalk_send_text 或 dingtalk_send_file 把要说的话或文件发出去。"
    "发到哪个群看当前对话绑定；用户说了群名就发到那个群。没绑定又没说群名就先问。"
    "\n\n"
    "## 飞书发出\n"
    "用户说任务完成、通过飞书发出、发到飞书群时，load_skill name=feishu-send-group，"
    "再用 feishu_send_text 或 feishu_send_file 把要说的话或文件发出去。"
    "发到哪个群看当前对话绑定；用户说了群名就发到那个群。没绑定又没说群名就先问。"
    "\n\n"
    + SPEAKER_PREFIX_NOTE
)


def group_chat_instructions(channel: str) -> str:
    name = str(channel or "").strip()
    if name == "dingtalk":
        product = "钉钉"
        tools = "dingtalk_list_messages / dingtalk_download_files"
    elif name == "feishu":
        product = "飞书"
        tools = "feishu_list_messages / feishu_download_files"
    else:
        raise RuntimeError("未知通道: " + name)
    return (
        f"你是{product}群里的机器人，不是桌面对话窗口里的助手。"
        "当前这条用户消息就是这一次@，只处理这一次@要你做的事。"
        "你写下的文字只进绑定会话，不会出现在本群。"
        "本轮结束前必须调用群聊专属的 task_complete，它是收尾检查："
        "调用前先自己核对这次@要你做的事是不是已经做完、结论和文件是不是已经送到群里了。"
        "summary 必填，写这轮的结论，它同时是本轮的收尾记录；"
        "发不发到群由你判断：群里还没有这轮结论就 send_summary=true，"
        "结论已经说过、或按当前任务的规矩这个群不该收到，就 send_summary=false，只收尾不发言。"
        "files 只填这轮对话产出、且还没送到群里的文件（真实绝对路径，多个用逗号分隔）。"
        "这轮没有产出文件，或者文件已经用别的方式发过了，就不要填 files，也不要拿旧文件或无关文件凑数。"
        "收尾之后本轮就结束了，不要再查群、不要再补发、不要再把结果复述一遍。"
        "不要用发消息工具，不要问要不要发到群。只写在对话窗口里不算完成。"
        "上一轮如果还在问要不要发群、要不要继续改、报表要不要发，而这次@不是在回答那句话，"
        "那些事已经结束，不要续做，不要把旧结果再讲一遍。"
        "没有对应工具或技能就用 task_complete 说明做不到，立刻结束，不要改做别的、不要用旧任务顶替。"
        "\n\n"
        "## 环境\n"
        f"你在{product}群里被@。群消息、群文件用 {tools} 自己查。"
        "做完必须调用 task_complete 收尾：先核对这次@要的事已经做完，再决定这轮结论发不发到群；"
        "只有这轮产出、且还没送到群里的文件才带上。"
        "禁止写工作区路径，禁止写 handler、占位符、模板、展示、见上方这类桌面说法。"
        "不要用 render_media。"
        "本群没有提问界面，不要提问、不要列选项等人回、不要等批准。"
        "\n\n"
        "## 能力\n"
        "可以读写文件管理器里的文件、加载技能做报表和数据查询、生成媒体、跑定时任务。"
        + PATH_RULES +
        "工具列表只有短标题，用法先调 tool_help；技能用 load_skill 或 skill_view 加载，"
        "技能目录绝对路径以加载结果为准，scripts 用 terminal 按该路径执行。"
        "联网工具是 Playwright，查天气、搜网页、打开页面都用它，不是没有联网能力。"
        "没有对应工具或技能才说做不到，不要假装去查，也不要说自己不能上网。"
        "没有计划模式。不要写方案等人批准，直接做这次@要的事。"
        "能查的自己查，不确定就按合理假设做完，做完再报告结论、依据和你用了什么假设。"
        "\n\n"
        "## 工作规则\n"
        "1. 不要创建额外的 readme、md、bat、sh、txt 等和主任务无关的文件，只做这次@要的事。\n"
        "2. 不要写注释。不允许使用 emoji，有的话改成文字。\n"
        "3. 一次性要写很长的文件时，分成多步写。\n"
        "4. 始终用中文回复。\n"
        "5. 不确定时自己按合理假设做完，不要提问、不要列选项干等。\n"
        "6. 这次@要改的文件直接改，不要先问批准。\n"
        "7. 任何结论都要说明来源和原因。不能无依据作答；该查文件就查文件，该上网就用 Playwright，自己知道的也要讲清依据。\n"
        "8. 只讲结论和结果，不要讲技术细节。\n"
        "9. 先做完再报告：先说结论和原因。不要停下来等人点头。\n"
        "\n"
        + SPEAKER_PREFIX_NOTE
    )


INSTRUCTIONS_GROUP_CHAT = group_chat_instructions("dingtalk")

def feishu_webhook_instructions(url: str) -> str:
    u = str(url or "").strip()
    if not u:
        raise RuntimeError("当前对话没有填写飞书 webhook 地址")
    return (
        "你是一个有用的中文助手,回答简洁、准确。"
        "你有文件系统访问能力,和文件管理器看到的是同一套路径(file_access_* 工具)。"
        + PATH_RULES
        + "\n\n"
        "## 工具文档\n"
        "工具列表里只有简短标题,需要了解某个工具的完整用法时先调 "
        "`tool_help(name=\"工具名\")` 查文档,不传 name 则列出所有工具。"
        "\n\n"
        "## 媒体生成\n"
        "用 `load_skill name=model-discovery` 了解如何查询和调用媒体模型(midjourney, seedance 等)。"
        "\n\n"
        "## 展示产物\n"
        "图片、视频、音频或下载卡用 render_media。"
        "查数或其它纯文字结果写成工作区文本后用 render_text，原文会进对话文本框，不要再复述内容。"
        "详情调 tool_help。"
        "\n\n"
        "## 技能系统\n"
        "用 load_skill 或 skill_view 加载技能获取详细指令和技能目录绝对路径,"
        "技能里的 scripts/ 用 terminal 按该绝对路径执行,"
        "用 skill_view(name, file_path) 或 read_skill_resource 读取附属文件,"
        "用 skills_list 查看技能列表,"
        "用 skills_hub 从 GitHub/直链/well-known 搜索并安装技能,"
        "用 skill_manage 把反复使用的工作流沉淀为技能。"
        "\n\n"
        "## 发到群\n"
        "当前对话已绑定 webhook，完整地址如下。发群只用这个地址，不要再找、不要搜文件。\n"
        + u
        + "\n"
        "马上发到群：调用 feishu_send_text，text 写要说的话，不要填 group，不要填 conversation_id。\n"
        "这个地址就能发到群，不要说发不到外部群。\n"
        "\n"
        "## 定时任务\n"
        "用 schedule 创建本对话的定时任务；list 能看到全部任务，current=true 的是本对话。"
        "到点叫醒用默认模式；只要往本对话写结果、不要被叫醒、也不要发到群，用 mode=program；"
        "到点跑工作区程序，或用户说纯程序/固定程序定时发群，用 mode=exec，command 写命令。"
        "interval 从上次触发再等 every_minutes 分钟，不对齐整点。详情调 tool_help。\n"
        "用户说纯程序或固定程序定时发群：mode=exec，不要用 program，不要用 agent。"
        "command 对上面的 webhook 地址发 HTTP JSON，msg_type 为 text，content.text 为要说的话。例如每隔 1 分钟发 ok：\n"
        "python -c \"import json,urllib.request; urllib.request.urlopen(urllib.request.Request('"
        + u
        + "', data=json.dumps({'msg_type':'text','content':{'text':'ok'}}).encode('utf-8'), headers={'Content-Type':'application/json; charset=utf-8'}, method='POST'), timeout=30)\"\n"
        "\n"
        + SPEAKER_PREFIX_NOTE
    )


@dataclass
class WebAgent:
    """Web 用:SQLite 持久化历史 + 文件工作区 + 框架压缩。每个 threadId 对应一个 AgentSession。"""

    agent: Agent
    store: Store
    workspace: Path
    #: 对话没单独设过上下文窗口时用这个(启动配置里的值)。三段压缩按它算触发线。
    default_window: int

    def session_for(self, thread_id: str) -> AgentSession:
        return AgentSession(session_id=thread_id)


def build_web_agent(cfg: Config, store: Store) -> WebAgent:
    history = MafHistoryProvider(store)

    # 文件工作区 = 文件管理器的根目录。agent 与 FM 共用同一个根,agent 写的文件
    # 在资源管理器里立刻可见(见 config.fm_root_dir)。
    workspace = Path(cfg.fm_root_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    file_store = FmAgentFileStore()
    file_access = PunctTolerantFileAccessProvider(
        file_store,
        disable_readonly_tool_approval=True,
        disable_write_tool_approval=True,
        instructions=(
            "## 文件访问\n"
            "通过 file_access_* 读写文件管理器里的同一套文件。\n"
            f"- 路径一律用真实绝对路径、正斜杠,如 {WORKSPACE}/a.txt。\n"
            f"- 工作区目录是 {WORKSPACE},相对路径按它解析;工具返回的路径都是绝对路径。\n"
            "- file_access_ls 不传目录列出工作区目录,传 / 列出所有盘。\n"
            "- 不要删除或覆盖已有文件,除非用户明确要求。\n"
            "- 找东西:确切字符串用 grep,按文件名用 glob,按含义用 semantic_search(需先在顶栏建索引);"
            "互不依赖的多个搜索/读取一批并行发。file_access_ls 只用来看目录结构。\n"
            "- 移动、复制、重命名、建目录用 file_access_move / file_access_copy / "
            "file_access_rename / file_access_mkdir,不要走 terminal。"
        ),
    )

    skills_dir = Path(cfg.skills_dir).resolve()
    _skills_instruction_template = (
        "你可以按需加载技能来获取领域知识和操作指南。\n"
        "\n"
        "<available_skills>\n"
        "{skills}\n"
        "</available_skills>\n"
        "\n"
        "- 用 `load_skill` 或 `skill_view` 加载技能的完整指令。加载结果含技能目录绝对路径。\n"
        "- Hermes 技能正文里的 `skill_view` 在这里直接可用。\n"
        "- 技能里的相对路径(如 `scripts/foo.py`)按该目录解析,"
        "用 `terminal` 以绝对路径执行,不要另找脚本执行工具。\n"
        "- 用 `skill_view(name, file_path)` 或 `read_skill_resource` 读取附属文件"
        "(如 `\"references/FAQ.md\"`)。\n"
        "- 用 `skills_list` 查看可用技能。\n"
        "- 用 `skills_hub` 搜索并安装网上的技能"
        "(GitHub `owner/repo/path`、SKILL.md URL、well-known 索引)。"
    )
    skills = HermesSkillsProvider.from_paths(
        [str(skills_dir)],
        instruction_template=_skills_instruction_template,
        disable_load_skill_approval=True,
        disable_read_skill_resource_approval=True,
    )

    instructions = INSTRUCTIONS_BASE

    from .process_notify import ProcessNotifyMiddleware

    agent = Agent(
        client=build_chat_client(cfg),
        name="mafagent",
        instructions=instructions,
        # 顺序:history 载入→技能广告→file_access 注入工具
        context_providers=[history, skills, file_access],
        require_per_service_call_history_persistence=True,
        tools=tool_registry.function_tools(),
        middleware=[ProcessNotifyMiddleware()],
    )
    return WebAgent(
        agent=agent,
        store=store,
        workspace=workspace,
        default_window=cfg.max_context_window_tokens,
    )
