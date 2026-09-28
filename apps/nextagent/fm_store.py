from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from .framework_compat import (
    AgentFileStore,
    FileSearchResult,
    FileStoreEntry,
    _compile_search_regex,
    _matches_glob,
    _run_search_with_timeout,
    _search_file_content,
)

from fm.backend.pathutil import from_agent_path, is_computer_root, list_roots, resolve, to_fs

from .checkpoints import missing_ancestors, track_before_write, track_current

LS_MAX_ENTRIES = 1000


class FmAgentFileStore(AgentFileStore):
    def _full(self, path: str, *, is_directory: bool = False) -> Path:
        return Path(resolve(from_agent_path(path, is_directory=is_directory)))

    async def write(self, path: str, content: str, *, overwrite: bool = True) -> None:
        full = self._full(path)
        def run() -> None:
            track_before_write(str(full))
            full.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT
            flags |= os.O_TRUNC if overwrite else os.O_EXCL
            fd = os.open(full, flags, 0o644)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content.encode("utf-8"))
        await asyncio.to_thread(run)

    async def read(self, path: str) -> str | None:
        full = self._full(path)
        def run() -> str | None:
            if not full.is_file():
                return None
            raw = full.read_bytes()
            return raw.decode("utf-8")
        return await asyncio.to_thread(run)

    async def delete(self, path: str) -> bool:
        full = self._full(path)
        def run() -> bool:
            if not full.is_file():
                return False
            track_current([to_fs(str(full))])
            full.unlink()
            return True
        return await asyncio.to_thread(run)

    async def list_children(self, directory: str = "") -> list[FileStoreEntry]:
        entries, _ = await self.list_children_capped(directory, LS_MAX_ENTRIES)
        return entries

    async def list_children_capped(
        self, directory: str = "", limit: int = LS_MAX_ENTRIES
    ) -> tuple[list[FileStoreEntry], bool]:
        virt = from_agent_path(directory, is_directory=True)
        if is_computer_root(virt):
            roots = [FileStoreEntry(r, FileStoreEntry.DIRECTORY) for r in list_roots()]
            return roots, False
        full = Path(resolve(virt))
        def run() -> tuple[list[FileStoreEntry], bool]:
            if not full.is_dir():
                return [], False
            directories: list[FileStoreEntry] = []
            files: list[FileStoreEntry] = []
            truncated = False
            for entry in full.iterdir():
                if len(directories) + len(files) >= limit:
                    truncated = True
                    break
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    directories.append(FileStoreEntry(entry.name, FileStoreEntry.DIRECTORY))
                elif entry.is_file():
                    files.append(FileStoreEntry(entry.name, FileStoreEntry.FILE))
            return directories + files, truncated
        return await asyncio.to_thread(run)

    async def file_exists(self, path: str) -> bool:
        full = self._full(path)
        return await asyncio.to_thread(full.is_file)

    async def search(
        self,
        directory: str,
        regex_pattern: str,
        glob_pattern: str | None = None,
        *,
        recursive: bool = False,
    ) -> list[FileSearchResult]:
        full_dir = self._full(directory, is_directory=True)
        regex = _compile_search_regex(regex_pattern)
        return await _run_search_with_timeout(
            lambda: self._search_sync(full_dir, regex, glob_pattern, recursive)
        )

    @staticmethod
    def _search_sync(
        full_dir: Path,
        regex: re.Pattern[str],
        glob_pattern: str | None,
        recursive: bool,
    ) -> list[FileSearchResult]:
        if not full_dir.is_dir():
            return []
        results: list[FileSearchResult] = []
        stack = [full_dir]
        while stack:
            current = stack.pop()
            for entry in current.iterdir():
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    if recursive:
                        stack.append(entry)
                    continue
                if not entry.is_file():
                    continue
                relative_name = entry.relative_to(full_dir).as_posix()
                if not _matches_glob(relative_name, glob_pattern):
                    continue
                try:
                    file_content = entry.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                result = _search_file_content(relative_name, file_content, regex)
                if result is not None:
                    results.append(result)
        return results

    async def create_directory(self, path: str) -> None:
        full = self._full(path, is_directory=True)
        def run() -> None:
            track_current(missing_ancestors(str(full)))
            full.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(run)


def fm_parent_of(rel_path: str) -> str:
    from fm.backend.pathutil import parent_of
    return parent_of(from_agent_path(rel_path, is_directory=False))


def to_fm_path(full: str) -> str:
    return to_fs(full)
