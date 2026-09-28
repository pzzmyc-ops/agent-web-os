"""技能扫描(前端列表用)+ skill_manage(对话中创建/编辑/删除技能)。

技能发现、加载、system prompt 注入由 HermesSkillsProvider 接管(见 agent_core.build_web_agent)。本模块只做框架不提供的两件事:
  1. 扫描技能目录返回 name/description 列表(前端下拉用)
  2. skill_manage —— 在对话中沉淀工作流为技能

技能目录 = config.json 的 fm_root 下的 skills/,平铺一层,每个子目录一个 SKILL.md,全部可编辑。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import load_config
from ._registry import registry

_SKILLS_ROOT = Path(load_config().skills_dir).resolve()


def _outermost_skill_mds(root: Path) -> list[Path]:
    found: list[Path] = []

    def walk(directory: Path) -> None:
        md = directory / "SKILL.md"
        if md.is_file():
            found.append(md)
            return
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            if entry.is_dir() and not entry.name.startswith("."):
                walk(entry)

    walk(root)
    return found


def scan_skills() -> list[dict]:
    """扫描技能目录下的 SKILL.md,返回 name/description。"""
    if not _SKILLS_ROOT.is_dir():
        raise RuntimeError(f"技能目录不存在: {_SKILLS_ROOT}")
    items: list[dict] = []
    for md in _outermost_skill_mds(_SKILLS_ROOT):
        text = md.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        name = fm.get("name") or md.parent.name
        description = fm.get("description") or ""
        items.append({"name": name, "description": description})
    return items


def _parse_frontmatter(text: str) -> dict:
    """从 SKILL.md 提取 YAML frontmatter。"""
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    raw = text[3:end].strip()
    result: dict = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip("\"'")
        if key in ("tags", "platforms", "prerequisites", "environments"):
            result[key] = [v.strip().strip("\"'") for v in val.strip("[]").split(",") if v.strip()]
        else:
            result[key] = val
    return result


def _sanitize_name(raw: str) -> str:
    return raw.strip().lower().replace(" ", "-")


_SKILL_MD_TEMPLATE = """---
name: {name}
description: {description}
---
{content}
"""


SKILL_MANAGE_DOC = """管理技能文件。

action:
    create: 新建技能。需 name、description、content。
    edit:   覆盖已有技能的正文。需 name、content。
    delete: 删除技能。需 name。"""


@registry.register(
    toolset="skills",
    name="skill_manage",
    summary="创建/编辑/删除技能 —— 把反复使用的工作流沉淀为可复用技能",
    description="管理技能文件",
    doc=SKILL_MANAGE_DOC,
)
def skill_manage(action: str, name: str = "", description: str = "", content: str = "") -> str:
    """管理技能文件。

    action:
        create: 新建技能。需 name、description、content。
        edit:   覆盖已有技能的正文。需 name、content。
        delete: 删除技能。需 name。
    """
    action = str(action or "").strip().lower()
    name = str(name or "").strip()

    if not name:
        return json.dumps({"ok": False, "error": "name 不能为空"}, ensure_ascii=False)

    safe = _sanitize_name(name)
    if not safe:
        return json.dumps({"ok": False, "error": "name 无效"}, ensure_ascii=False)

    target_dir = _SKILLS_ROOT / safe
    target_file = target_dir / "SKILL.md"

    if action == "create":
        if target_dir.exists():
            return json.dumps({
                "ok": False,
                "error": f"技能 '{name}' 已存在。用 action=edit 改内容,或 action=delete 先删再建。",
            }, ensure_ascii=False)
        desc = str(description or "").strip()
        ct = str(content or "").strip()
        if not ct:
            return json.dumps({"ok": False, "error": "content 不能为空 —— 这是技能的正文"}, ensure_ascii=False)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file.write_text(
            _SKILL_MD_TEMPLATE.format(name=safe, description=desc, content=ct),
            encoding="utf-8",
        )
        return json.dumps({"ok": True, "action": "created", "name": safe}, ensure_ascii=False)

    if action == "edit":
        if not target_file.exists():
            return json.dumps({"ok": False, "error": f"技能 '{name}' 不存在,用 action=create 先建"}, ensure_ascii=False)
        ct = str(content or "").strip()
        if not ct:
            return json.dumps({"ok": False, "error": "content 不能为空"}, ensure_ascii=False)
        old = target_file.read_text(encoding="utf-8")
        fm = _parse_frontmatter(old)
        target_file.write_text(
            _SKILL_MD_TEMPLATE.format(name=safe, description=fm.get("description", ""), content=ct),
            encoding="utf-8",
        )
        return json.dumps({"ok": True, "action": "edited", "name": safe}, ensure_ascii=False)

    if action == "delete":
        if not target_dir.exists():
            return json.dumps({"ok": False, "error": f"技能 '{name}' 不存在"}, ensure_ascii=False)
        import shutil
        shutil.rmtree(target_dir)
        return json.dumps({"ok": True, "action": "deleted", "name": safe}, ensure_ascii=False)

    return json.dumps({
        "ok": False,
        "error": f"Unknown action: {action}. 可用: create, edit, delete.",
    }, ensure_ascii=False)
