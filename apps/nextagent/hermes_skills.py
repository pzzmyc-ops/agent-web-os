from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from agent_framework import (
    DeduplicatingSkillsSource,
    FileSkill,
    FileSkillsSource,
    FunctionTool,
    SkillFrontmatter,
    SkillsProvider,
)
from .framework_compat import SKILL_FILE_NAME, Skill, _FileSkillResource

from .config import configured_gateway_base, load_config
from .maf_tools._env_passthrough import register_env_passthrough
from .toolkit._context import current_turn

_SKILL_TEMPLATE_RE = re.compile(
    r"\$\{(HERMES_SKILL_DIR|HERMES_SESSION_ID|GATEWAY_BASE|AGENT_PYTHON)\}"
)
_AVAILABLE_SCRIPTS_RE = re.compile(
    r"\n*<available_scripts(?:\s*/>|>.*?</available_scripts>)\s*",
    re.DOTALL,
)
_FRONTMATTER_RE = re.compile(
    r"\A\uFEFF?---\s*$(.+?)^---\s*$",
    re.MULTILINE | re.DOTALL,
)
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SUPPORTING_SUBDIRS = ("references", "templates", "scripts", "assets", "examples")
_RESOURCE_EXTENSIONS = (
    ".md",
    ".markdown",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".xml",
    ".txt",
    ".html",
    ".htm",
    ".css",
    ".svg",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".py",
    ".sh",
    ".bash",
    ".js",
    ".ts",
    ".rb",
    ".ps1",
    ".bat",
    ".cmd",
    ".sql",
    ".r",
    ".go",
    ".rs",
)
_SKILLS_ROOT = Path(load_config().skills_dir).resolve()
_DISCOVER_DEPTH = 6
_RESOURCE_DEPTH = 8


def _session_id() -> str | None:
    ctx = current_turn()
    if ctx is None:
        return None
    return ctx.thread_id


def _yaml_frontmatter(content: str) -> dict[str, Any]:
    match = _FRONTMATTER_RE.search(content or "")
    if not match:
        return {}
    data = yaml.safe_load(match.group(1))
    if isinstance(data, dict):
        return data
    return {}


def _sanitize_skill_name(raw: str, fallback: str) -> str:
    text = (raw or fallback or "").strip().lower().replace(" ", "-").replace("_", "-")
    text = re.sub(r"[^a-z0-9-]", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:64] or re.sub(r"[^a-z0-9-]", "", fallback.lower())[:64]


def _current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _platform_ok(fm: dict[str, Any]) -> bool:
    plats = fm.get("platforms")
    if not plats:
        return True
    if isinstance(plats, str):
        items = [p.strip().strip("\"'") for p in plats.strip("[]").split(",") if p.strip()]
    elif isinstance(plats, (list, tuple)):
        items = [str(p).strip().lower() for p in plats if str(p).strip()]
    else:
        return True
    items = [p.lower() for p in items if p]
    if not items:
        return True
    return _current_platform() in items


def _required_env_names(fm: dict[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        value = str(raw or "").strip()
        if not value or value in seen or not _ENV_VAR_NAME_RE.match(value):
            return
        seen.add(value)
        names.append(value)

    required_raw = fm.get("required_environment_variables")
    if isinstance(required_raw, dict):
        required_raw = [required_raw]
    if isinstance(required_raw, list):
        for item in required_raw:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, dict):
                add(item.get("name") or item.get("env_var"))
    prereq = fm.get("prerequisites")
    if isinstance(prereq, dict):
        env_vars = prereq.get("env_vars")
        if isinstance(env_vars, str):
            env_vars = [v.strip() for v in env_vars.strip("[]").split(",") if v.strip()]
        if isinstance(env_vars, list):
            for item in env_vars:
                add(item)
    return names


def _apply_env_passthrough(fm: dict[str, Any]) -> list[str]:
    names = _required_env_names(fm)
    present = [n for n in names if os.environ.get(n)]
    if present:
        register_env_passthrough(present)
    return [n for n in names if n not in present]


def _substitute_template_vars(content: str, skill_dir: Path, session_id: str | None) -> str:
    skill_dir_str = skill_dir.as_posix()

    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == "HERMES_SKILL_DIR":
            return skill_dir_str
        if token == "HERMES_SESSION_ID" and session_id:
            return session_id
        if token == "GATEWAY_BASE":
            return configured_gateway_base()
        # 技能自带的脚本要用「跑着本服务的那个解释器」,而不是终端 PATH 上的 python ——
        # 后者常常是另一个 Python(conda、系统自带),装的包不一样,脚本会 import 失败。
        if token == "AGENT_PYTHON":
            return Path(sys.executable).as_posix()
        return match.group(0)

    return _SKILL_TEMPLATE_RE.sub(_replace, content)


def _list_supporting(skill_dir: Path) -> list[str]:
    found: list[str] = []
    for subdir in _SUPPORTING_SUBDIRS:
        root = skill_dir / subdir
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*")):
            if f.is_file() and not f.is_symlink():
                found.append(str(f.relative_to(skill_dir)).replace("\\", "/"))
    return found


def _allowed_tools_note(fm: dict[str, Any]) -> str:
    raw = fm.get("allowed-tools") or fm.get("allowed_tools")
    if not raw:
        return ""
    if isinstance(raw, list):
        text = " ".join(str(x) for x in raw)
    else:
        text = str(raw).strip()
    if not text:
        return ""
    return (
        f"[allowed-tools: {text}]\n"
        "Prefer these tools for this skill. Do not use unrelated tools unless the user asks."
    )


def format_skill_activation(loaded: str, skill_dir: Path, skill_name: str, raw_md: str | None = None) -> str:
    skill_dir = skill_dir.resolve()
    raw = raw_md if raw_md is not None else loaded
    fm = _yaml_frontmatter(raw)
    loaded = _substitute_template_vars(loaded, skill_dir, _session_id())
    loaded = _AVAILABLE_SCRIPTS_RE.sub("\n", loaded).rstrip()
    missing = _apply_env_passthrough(fm)
    parts = [
        loaded,
        "",
        f"[Skill directory: {skill_dir}]",
        "Resolve any relative paths in this skill (e.g. `scripts/foo.js`, "
        "`templates/config.yaml`) against that directory, then run them "
        "with the terminal tool using the absolute path.",
        "Hermes `skill_view` maps to `skill_view` / `load_skill` / `read_skill_resource` here.",
    ]
    note = _allowed_tools_note(fm)
    if note:
        parts.extend(["", note])
    if missing:
        parts.extend(
            [
                "",
                "[Skill setup note: missing environment variables: "
                + ", ".join(missing)
                + ". Continue and explain reduced functionality if it matters.]",
            ]
        )
    supporting = _list_supporting(skill_dir)
    if supporting:
        parts.append("")
        parts.append("[This skill has supporting files:]")
        for rel in supporting:
            parts.append(f"- {rel} -> {skill_dir / rel}")
        parts.append(
            f'\nLoad any of these with skill_view(name="{skill_name}", '
            f'file_path="<path>") or read_skill_resource(skill_name="{skill_name}", '
            f'resource_name="<path>"), or run scripts directly by absolute path '
            f"(e.g. `python {skill_dir}/scripts/foo.py`)."
        )
    return "\n".join(parts)


def _outermost_skill_dirs(root: Path) -> list[Path]:
    found: list[Path] = []

    def walk(directory: Path) -> None:
        if (directory / SKILL_FILE_NAME).is_file():
            found.append(directory)
            return
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            if entry.is_dir() and not entry.name.startswith("."):
                walk(entry)

    walk(root)
    return found


def _iter_skill_dirs() -> list[Path]:
    if not _SKILLS_ROOT.is_dir():
        raise RuntimeError(f"技能目录不存在: {_SKILLS_ROOT}")
    return _outermost_skill_dirs(_SKILLS_ROOT)


def find_skill_directory(name: str) -> Path | None:
    needle = (name or "").strip().replace("\\", "/").lower().rstrip("/")
    if not needle:
        return None
    matches: list[Path] = []
    for d in _iter_skill_dirs():
        rel = str(d.relative_to(_SKILLS_ROOT)).replace("\\", "/").lower()
        fm_name = str(_yaml_frontmatter(d.joinpath("SKILL.md").read_text(encoding="utf-8")).get("name") or "")
        names = {d.name.lower(), rel, fm_name.lower(), _sanitize_skill_name(fm_name, d.name)}
        if needle in names or rel.endswith("/" + needle):
            matches.append(d)
    if not matches:
        return None
    return matches[0]


def _live_skill_items() -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for skill_dir in _iter_skill_dirs():
        raw = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        fm = _yaml_frontmatter(raw)
        if not _platform_ok(fm):
            continue
        name = str(fm.get("name") or skill_dir.name)
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "name": name,
            "description": str(fm.get("description") or ""),
            "path": str(skill_dir),
        })
    return items


def build_selected_skill_prefix(skill_name: str) -> str:
    skill_name = (skill_name or "").strip()
    if not skill_name:
        return ""
    skill_dir = find_skill_directory(skill_name)
    if skill_dir is None:
        return f"[Selected skill '{skill_name}' was not found.]"
    raw = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    fm = _yaml_frontmatter(raw)
    resolved = str(fm.get("name") or skill_dir.name)
    body = format_skill_activation(raw, skill_dir, resolved, raw_md=raw)
    return f"[User selected skill: {resolved}. Follow it for this turn.]\n\n{body}"


def _read_within_skill(skill_dir: Path, rel: str) -> str:
    rel_n = (rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel_n:
        return "Error: Resource name cannot be empty."
    parts = Path(rel_n).parts
    if ".." in parts:
        return "Error: invalid resource path."
    root = skill_dir.resolve()
    full = (root / rel_n).resolve()
    try:
        full.relative_to(root)
    except ValueError:
        return "Error: resource path escapes skill directory."
    if not full.is_file() or full.is_symlink():
        return f"Error: file not found: {rel_n}"
    data = full.read_bytes()
    if b"\x00" in data[:2048]:
        return f"Binary file. Absolute path: {full}. Use the terminal tool."
    return data.decode("utf-8", errors="replace")


class HermesFileSkillsSource(FileSkillsSource):
    async def get_skills(self, context) -> list:
        skills: dict[str, FileSkill] = {}
        discovered = self._discover_skill_directories(self._skill_paths)
        for skill_path in discovered:
            skill_file = Path(skill_path) / SKILL_FILE_NAME
            try:
                content = skill_file.read_text(encoding="utf-8")
            except OSError:
                continue
            fm_dict = _yaml_frontmatter(content)
            if not _platform_ok(fm_dict):
                continue
            frontmatter = FileSkillsSource._extract_frontmatter(content, str(skill_file))
            if frontmatter is None:
                name = _sanitize_skill_name(str(fm_dict.get("name") or ""), Path(skill_path).name)
                desc = str(fm_dict.get("description") or name)[:1024]
                if not desc.strip():
                    continue
                try:
                    frontmatter = SkillFrontmatter(name=name, description=desc)
                except ValueError:
                    continue
            if frontmatter.name in skills:
                continue
            resources = []
            for rn in self._discover_resource_files(skill_path, frontmatter.name):
                resource_full_path = FileSkillsSource._get_validated_resource_path(skill_path, rn)
                resources.append(_FileSkillResource(name=rn, full_path=resource_full_path))
            file_skill = FileSkill(
                frontmatter=frontmatter,
                content=content,
                path=skill_path,
                resources=resources,
                scripts=[],
            )
            skills[frontmatter.name] = file_skill
        return list(skills.values())

    @staticmethod
    def _discover_skill_directories(skill_paths: Sequence[str]) -> list[str]:
        discovered: list[str] = []

        def _search(directory: str, current_depth: int) -> None:
            dir_path = Path(directory)
            if (dir_path / SKILL_FILE_NAME).is_file():
                discovered.append(str(dir_path.absolute()))
                return
            if current_depth >= _DISCOVER_DEPTH:
                return
            try:
                entries = list(dir_path.iterdir())
            except OSError:
                return
            for entry in entries:
                if entry.is_dir():
                    _search(str(entry), current_depth + 1)

        for root_dir in skill_paths:
            if not root_dir or not str(root_dir).strip() or not Path(root_dir).is_dir():
                continue
            _search(str(root_dir), current_depth=0)
        return discovered


class HermesSkillsProvider(SkillsProvider):
    @classmethod
    def from_paths(cls, skill_paths, **kwargs: Any) -> HermesSkillsProvider:
        file_source = HermesFileSkillsSource(
            skill_paths,
            script_extensions=(),
            resource_extensions=_RESOURCE_EXTENSIONS,
            search_depth=_RESOURCE_DEPTH,
        )
        source = DeduplicatingSkillsSource(file_source)
        forwarded: dict[str, Any] = {
            "instruction_template": kwargs.get("instruction_template"),
            "disable_caching": True,
            "source_id": kwargs.get("source_id"),
        }
        if kwargs.get("disable_load_skill_approval"):
            forwarded["disable_load_skill_approval"] = True
        if kwargs.get("disable_read_skill_resource_approval"):
            forwarded["disable_read_skill_resource_approval"] = True
        return cls(source, **{k: v for k, v in forwarded.items() if v is not None or k == "instruction_template"})

    def _resolve_skill(self, skills: Sequence[Skill], name: str) -> Skill | None:
        found = self._find_skill(skills, name)
        if found is not None:
            return found
        needle = (name or "").strip().replace("\\", "/").lower().rstrip("/")
        if not needle:
            return None
        for skill in skills:
            if not isinstance(skill, FileSkill):
                continue
            path = Path(skill.path)
            rel = str(path).replace("\\", "/").lower()
            aliases = {path.name.lower(), skill.frontmatter.name.lower()}
            if needle in aliases or rel.endswith("/" + needle):
                return skill
        return None

    def _create_tools(self, skills: Sequence[Skill]) -> list[FunctionTool]:
        tools = [t for t in super()._create_tools(skills) if t.name != self.RUN_SKILL_SCRIPT_TOOL_NAME]
        never = self._approval_mode(True)

        async def _skill_view(name: str, file_path: str = "") -> str:
            if file_path:
                return await self._read_skill_resource(skills, name, file_path)
            return await self._load_skill(skills, name)

        async def _skills_list(category: str = "") -> str:
            items = []
            for item in _live_skill_items():
                if category:
                    cat = str(category).strip().lower()
                    blob = f"{item['name']} {item.get('path', '')}".lower()
                    if cat not in blob:
                        continue
                items.append(item)
            return json.dumps({"skills": items}, ensure_ascii=False)

        tools.append(
            FunctionTool(
                name="skill_view",
                description=(
                    "Load a skill's SKILL.md, or a file inside the skill directory. "
                    "Omit file_path for the main instructions; pass file_path for "
                    "references/templates/scripts (e.g. references/api.md)."
                ),
                func=_skill_view,
                approval_mode=never,
                input_model={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Skill name or directory path."},
                        "file_path": {
                            "type": "string",
                            "description": "Optional path within the skill directory.",
                        },
                    },
                    "required": ["name"],
                },
            )
        )
        tools.append(
            FunctionTool(
                name="skills_list",
                description="List available skills (name and description).",
                func=_skills_list,
                approval_mode=never,
                input_model={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Optional category/path filter."},
                    },
                },
            )
        )
        return tools

    async def _load_skill(self, skills: Sequence[Skill], skill_name: str) -> str:
        if not skill_name or not skill_name.strip():
            return "Error: Skill name cannot be empty."
        skill_dir = find_skill_directory(skill_name)
        if skill_dir is None:
            return f"Error: Skill '{skill_name}' not found."
        raw = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        fm = _yaml_frontmatter(raw)
        resolved = str(fm.get("name") or skill_dir.name)
        return format_skill_activation(raw, skill_dir, resolved, raw_md=raw)

    async def _read_skill_resource(
        self, skills: Sequence[Skill], skill_name: str, resource_name: str, **kwargs: Any
    ) -> Any:
        if not skill_name or not skill_name.strip():
            return "Error: Skill name cannot be empty."
        if not resource_name or not resource_name.strip():
            return "Error: Resource name cannot be empty."
        skill_dir = find_skill_directory(skill_name)
        if skill_dir is None:
            return f"Error: Skill '{skill_name}' not found."
        return _read_within_skill(skill_dir, resource_name)
