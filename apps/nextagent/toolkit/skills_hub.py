from __future__ import annotations

import json

from .. import skills_hub as hub
from ._registry import registry

SKILLS_HUB_DOC = """从 GitHub、直链 SKILL.md、well-known 索引安装技能。

action:
    search:    搜索。query 为关键词、owner/repo/path、或 https URL。
    inspect:   预览。identifier 同上。
    install:   下载并安装到技能目录。identifier 同上。
    list:      列出已安装的 Hub 技能。
    uninstall: 卸载 Hub 技能。需 name。

identifier 示例:
    huggingface/skills/skills/hf-cli
    anthropics/skills/skills/docx
    openai/skills/skills/.curated/cli-creator
    https://github.com/owner/repo/tree/main/skills/foo
    https://example.com/skills/foo/SKILL.md
    well-known:https://example.com/.well-known/skills/foo
"""


@registry.register(
    toolset="skills",
    name="skills_hub",
    summary="搜索/安装/卸载网上的技能包",
    description="Skills Hub",
    doc=SKILLS_HUB_DOC,
)
def skills_hub(action: str, query: str = "", identifier: str = "", name: str = "", limit: int = 10) -> str:
    action = str(action or "").strip().lower()
    if action == "search":
        return json.dumps(hub.search_skills(query, limit=int(limit or 10)), ensure_ascii=False)
    if action == "inspect":
        return json.dumps(hub.inspect_skill(identifier or query), ensure_ascii=False)
    if action == "install":
        return json.dumps(hub.install_skill(identifier or query), ensure_ascii=False)
    if action == "list":
        return json.dumps(hub.list_installed(), ensure_ascii=False)
    if action == "uninstall":
        return json.dumps(hub.uninstall_skill(name or query or identifier), ensure_ascii=False)
    raise ValueError(f"Unknown action: {action}. 可用: search, inspect, install, list, uninstall")
