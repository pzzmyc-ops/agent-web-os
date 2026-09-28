"""agent_framework 私有接口的统一收口。

所有对 agent_framework._xxx 私有实现的依赖都集中在这一个文件；框架升级导致私有
接口改名或移动时，只需要改这里。其它模块一律从本模块导入，不直接 import 框架私有路径。
"""
from __future__ import annotations

from agent_framework._tools import FunctionTool
from agent_framework._skills import SKILL_FILE_NAME, Skill, _FileSkillResource
from agent_framework._harness import _file_access
from agent_framework._harness._file_access import (
    AgentFileStore,
    FileSearchResult,
    FileStoreEntry,
    _DeleteFileInput,
    _ListInput,
    _ReadFileInput,
    _compile_search_regex,
    _matches_glob,
    _run_search_with_timeout,
    _search_file_content,
)

from fm.backend.pathutil import from_agent_path

_file_access._normalize_relative_path = from_agent_path

__all__ = [
    "FunctionTool",
    "SKILL_FILE_NAME",
    "Skill",
    "_FileSkillResource",
    "AgentFileStore",
    "FileSearchResult",
    "FileStoreEntry",
    "_DeleteFileInput",
    "_ListInput",
    "_ReadFileInput",
    "_compile_search_regex",
    "_matches_glob",
    "_run_search_with_timeout",
    "_search_file_content",
]
