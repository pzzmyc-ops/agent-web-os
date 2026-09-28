from __future__ import annotations

import asyncio
import os
import shutil
from typing import Annotated, Any

from agent_framework import FileAccessProvider, tool
from pydantic import BaseModel, Field

from fm.backend.pathutil import from_agent_path, is_root, parent_of, resolve, to_fs

from .checkpoints import missing_ancestors, track_before_transfer, track_current
from .fm_store import LS_MAX_ENTRIES
from .framework_compat import (
    FileStoreEntry,
    _DeleteFileInput,
    _ListInput,
    _ReadFileInput,
    _matches_glob,
)
from .toolkit._context import current_turn

MOVE_TOOL_NAME = "file_access_move"
COPY_TOOL_NAME = "file_access_copy"
MKDIR_TOOL_NAME = "file_access_mkdir"
RENAME_TOOL_NAME = "file_access_rename"


class _MoveInput(BaseModel):
    source: Annotated[str, Field(description="要移动的文件或目录路径。")]
    destination: Annotated[
        str,
        Field(
            description="目标位置。若是已存在的目录,则移入该目录并保留原名;否则视为新的完整路径(可同时改名),其父目录必须已存在。"
        ),
    ]


class _CopyInput(BaseModel):
    source: Annotated[str, Field(description="要复制的文件或目录路径。")]
    destination: Annotated[
        str,
        Field(
            description="目标位置。若是已存在的目录,则复制到该目录并保留原名;否则视为新的完整路径(可同时改名),其父目录必须已存在。"
        ),
    ]


class _MkdirInput(BaseModel):
    directory: Annotated[str, Field(description="要创建的目录路径,缺失的上级目录会一并创建。")]


class _RenameInput(BaseModel):
    file_name: Annotated[str, Field(description="要重命名的文件或目录路径。")]
    new_name: Annotated[str, Field(description="新名字。只能是名字本身,不能包含路径分隔符;位置不变。")]


def _full_of(path: str, *, is_directory: bool) -> str:
    return resolve(from_agent_path(path, is_directory=is_directory))


def _is_root(full: str) -> bool:
    return is_root(to_fs(full))


def _inside(parent: str, child: str) -> bool:
    parent_real = os.path.realpath(parent)
    child_real = os.path.realpath(child)
    if parent_real == child_real:
        return True
    root = parent_real if parent_real.endswith(os.sep) else parent_real + os.sep
    return child_real.startswith(root)


def _target_for(src_full: str, destination: str) -> str:
    dest_full = _full_of(destination, is_directory=True)
    if os.path.isdir(dest_full):
        return os.path.join(dest_full, os.path.basename(src_full))
    parent = os.path.dirname(dest_full)
    if not os.path.isdir(parent):
        raise FileNotFoundError(f"目标父目录不存在: {to_fs(parent)},先用 {MKDIR_TOOL_NAME} 创建")
    return dest_full


def _check_transfer(src_full: str, target: str, verb: str) -> None:
    if os.path.exists(target):
        if os.path.samefile(src_full, target):
            raise ValueError("源和目标是同一个路径")
        raise FileExistsError(f"目标已存在: {to_fs(target)}")
    if os.path.isdir(src_full) and _inside(src_full, os.path.dirname(target)):
        raise ValueError(f"不能把目录{verb}到它自己里面")


def _valid_name(name: str) -> str:
    new_name = name.strip()
    if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
        raise ValueError(f"无效的名字: {name!r}")
    return new_name


def _move_sync(source: str, destination: str) -> str:
    src_full = _full_of(source, is_directory=False)
    if not os.path.exists(src_full):
        raise FileNotFoundError(f"源不存在: {source}")
    if _is_root(src_full):
        raise ValueError("不能移动根目录")
    target = _target_for(src_full, destination)
    _check_transfer(src_full, target, "移动")
    track_before_transfer(src_full, target)
    shutil.move(src_full, target)
    return to_fs(target)


def _copy_sync(source: str, destination: str) -> str:
    src_full = _full_of(source, is_directory=False)
    if not os.path.exists(src_full):
        raise FileNotFoundError(f"源不存在: {source}")
    if _is_root(src_full):
        raise ValueError("不能复制根目录")
    target = _target_for(src_full, destination)
    _check_transfer(src_full, target, "复制")
    track_before_transfer(src_full, target)
    if os.path.isdir(src_full):
        shutil.copytree(src_full, target)
    else:
        shutil.copy2(src_full, target)
    return to_fs(target)


def _mkdir_sync(directory: str) -> tuple[str, bool]:
    full = _full_of(directory, is_directory=True)
    if os.path.isdir(full):
        return to_fs(full), False
    if os.path.exists(full):
        raise FileExistsError(f"同名文件已存在: {to_fs(full)}")
    track_current(missing_ancestors(full))
    os.makedirs(full)
    return to_fs(full), True


def _rename_sync(file_name: str, new_name: str) -> str:
    src_full = _full_of(file_name, is_directory=False)
    if not os.path.exists(src_full):
        raise FileNotFoundError(f"源不存在: {file_name}")
    if _is_root(src_full):
        raise ValueError("不能重命名根目录")
    name = _valid_name(new_name)
    if os.path.basename(src_full) == name:
        raise ValueError("新名字和原名字相同")
    target = os.path.join(os.path.dirname(src_full), name)
    if os.path.exists(target) and not os.path.samefile(src_full, target):
        raise FileExistsError(f"目标已存在: {to_fs(target)}")
    track_before_transfer(src_full, target)
    os.rename(src_full, target)
    return to_fs(target)

_LS_TRUNCATED_NOTE = (
    f"目录条目过多,只返回了前 {LS_MAX_ENTRIES} 项。"
    "请改列更具体的子目录,或用 glob 按文件名查找、用 grep 按内容查找。"
)


def _render_ls(entries, truncated: bool) -> str:
    lines = [
        f"{entry.name}/" if entry.type == FileStoreEntry.DIRECTORY else entry.name
        for entry in entries
    ]
    lines.append(f"[entries={len(entries)} truncated={str(truncated).lower()}]")
    if truncated:
        lines.append(f"Note: {_LS_TRUNCATED_NOTE}")
    return "\n".join(lines)


_DELETE_WARN_AFTER = 10
_DELETE_WARN_LIMIT = 3
_DELETE_WARN_TEXT = (
    "提醒: 本轮已连续删除超过 10 个文件，请确认这不是误删或危险操作。"
    "删除已经执行，没有拦截。"
)


def _delete_warning() -> str:
    ctx = current_turn()
    if ctx is None:
        return ""
    ctx.file_delete_count += 1
    if ctx.file_delete_count <= _DELETE_WARN_AFTER:
        return ""
    if ctx.file_delete_warns >= _DELETE_WARN_LIMIT:
        return ""
    ctx.file_delete_warns += 1
    return f"{_DELETE_WARN_TEXT} 本轮已删除 {ctx.file_delete_count} 个。"


def fold_punct(name: str) -> str:
    out: list[str] = []
    for ch in name:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
            continue
        if 0xFF01 <= code <= 0xFF5E and not (
            0xFF10 <= code <= 0xFF19 or 0xFF21 <= code <= 0xFF3A or 0xFF41 <= code <= 0xFF5A
        ):
            out.append(chr(code - 0xFEE0))
            continue
        out.append(ch)
    return "".join(out)


def _split_rel(path: str) -> tuple[str, str]:
    virt = from_agent_path(path, is_directory=False)
    name = virt.rstrip("/").rsplit("/", 1)[-1]
    return parent_of(virt), name


async def resolve_punct_alias(store: Any, path: str) -> str | None:
    parent, name = _split_rel(path)
    if not name:
        return None
    folded = fold_punct(name)
    hits: list[str] = []
    for entry in await store.list_children(parent):
        if entry.type != "file":
            continue
        if entry.name == name:
            continue
        if fold_punct(entry.name) == folded:
            hits.append(entry.name)
    if not hits:
        return None
    if len(hits) > 1:
        raise ValueError(f"同目录下有多个标点等价文件名: {hits}")
    return f"{parent.rstrip('/')}/{hits[0]}"


class PunctTolerantFileAccessProvider(FileAccessProvider):
    async def before_run(
        self,
        *,
        agent: Any,
        session: Any,
        context: Any,
        state: dict[str, Any],
    ) -> None:
        await super().before_run(agent=agent, session=session, context=context, state=state)
        store = self.store
        approval = "never_require" if self.disable_readonly_tool_approval else "always_require"
        write_approval = "never_require" if self.disable_write_tool_approval else "always_require"

        @tool(name=FileAccessProvider.READ_TOOL_NAME, schema=_ReadFileInput, approval_mode=approval)
        async def file_access_read(file_name: str) -> str:
            try:
                normalized = from_agent_path(file_name)
                content = await store.read(normalized)
                if content is None:
                    alt = await resolve_punct_alias(store, normalized)
                    if alt is not None:
                        content = await store.read(alt)
                if content is None:
                    return f"File '{file_name}' not found."
                return content
            except ValueError as exc:
                return f"Could not read file '{file_name}': {exc}"
            except OSError as exc:
                return f"Could not read file '{file_name}': {exc.strerror or exc}"

        @tool(name=FileAccessProvider.DELETE_TOOL_NAME, schema=_DeleteFileInput, approval_mode=write_approval)
        async def file_access_delete(file_name: str) -> str:
            try:
                normalized = from_agent_path(file_name)
                async with self._write_lock:
                    deleted = await store.delete(normalized)
            except ValueError as exc:
                return f"Could not delete file '{file_name}': {exc}"
            except OSError as exc:
                return f"Could not delete file '{file_name}': {exc.strerror or exc}"
            if not deleted:
                return f"File '{file_name}' not found."
            warn = _delete_warning()
            if warn:
                return f"File '{file_name}' deleted.\n{warn}"
            return f"File '{file_name}' deleted."

        @tool(name=FileAccessProvider.LS_TOOL_NAME, schema=_ListInput, approval_mode=approval)
        async def file_access_ls(
            directory: str | None = None,
            glob_pattern: str | None = None,
        ) -> str:
            """列出一个目录的直接子项(不递归)。directory 用真实绝对路径(正斜杠,如 D:/a/b);省略或传空字符串列出工作区目录;传 / 列出所有盘。可用 glob_pattern 过滤条目名,如 "*.md"。目录名带尾部 /,目录排在文件前。条目超过上限时只返回前面的部分并在 Note 里说明。"""
            target = directory if directory and directory.strip() else ""
            try:
                entries, truncated = await store.list_children_capped(target, LS_MAX_ENTRIES)
            except ValueError as exc:
                return f"Could not list directory '{directory or ''}': {exc}"
            except OSError as exc:
                return f"Could not list directory '{directory or ''}': {exc.strerror or exc}"
            return _render_ls(
                [entry for entry in entries if _matches_glob(entry.name, glob_pattern)],
                truncated,
            )

        @tool(
            name=MOVE_TOOL_NAME,
            schema=_MoveInput,
            approval_mode=write_approval,
            description="移动文件或目录。destination 是已存在的目录则移入其中,否则作为新的完整路径(可同时改名)。不要用 shell 的 mv。",
        )
        async def file_access_move(source: str, destination: str) -> str:
            try:
                async with self._write_lock:
                    moved_to = await asyncio.to_thread(_move_sync, source, destination)
            except ValueError as exc:
                return f"Could not move '{source}': {exc}"
            except OSError as exc:
                return f"Could not move '{source}': {exc.strerror or exc}"
            return f"Moved '{source}' to '{moved_to}'."

        @tool(
            name=COPY_TOOL_NAME,
            schema=_CopyInput,
            approval_mode=write_approval,
            description="复制文件或目录(目录递归复制)。destination 是已存在的目录则复制进去,否则作为新的完整路径。不要用 shell 的 cp。",
        )
        async def file_access_copy(source: str, destination: str) -> str:
            try:
                async with self._write_lock:
                    copied_to = await asyncio.to_thread(_copy_sync, source, destination)
            except ValueError as exc:
                return f"Could not copy '{source}': {exc}"
            except OSError as exc:
                return f"Could not copy '{source}': {exc.strerror or exc}"
            return f"Copied '{source}' to '{copied_to}'."

        @tool(
            name=MKDIR_TOOL_NAME,
            schema=_MkdirInput,
            approval_mode=write_approval,
            description="创建目录,缺失的上级目录一并创建;已存在则原样返回。不要用 shell 的 mkdir。",
        )
        async def file_access_mkdir(directory: str) -> str:
            try:
                async with self._write_lock:
                    made, created = await asyncio.to_thread(_mkdir_sync, directory)
            except ValueError as exc:
                return f"Could not create directory '{directory}': {exc}"
            except OSError as exc:
                return f"Could not create directory '{directory}': {exc.strerror or exc}"
            if not created:
                return f"Directory '{made}' already exists."
            return f"Directory '{made}' created."

        @tool(
            name=RENAME_TOOL_NAME,
            schema=_RenameInput,
            approval_mode=write_approval,
            description="就地重命名文件或目录,只改名字不改位置。要换目录用 file_access_move。",
        )
        async def file_access_rename(file_name: str, new_name: str) -> str:
            try:
                async with self._write_lock:
                    renamed_to = await asyncio.to_thread(_rename_sync, file_name, new_name)
            except ValueError as exc:
                return f"Could not rename '{file_name}': {exc}"
            except OSError as exc:
                return f"Could not rename '{file_name}': {exc.strerror or exc}"
            return f"Renamed '{file_name}' to '{renamed_to}'."

        source_id = self.source_id
        replacements = {
            FileAccessProvider.READ_TOOL_NAME: file_access_read,
            FileAccessProvider.DELETE_TOOL_NAME: file_access_delete,
            FileAccessProvider.LS_TOOL_NAME: file_access_ls,
        }
        for fn in replacements.values():
            extra = getattr(fn, "additional_properties", None)
            if isinstance(extra, dict):
                extra["context_source"] = source_id
        for i, item in enumerate(context.tools):
            repl = replacements.get(getattr(item, "name", None))
            if repl is not None:
                context.tools[i] = repl
        context.tools[:] = [
            item for item in context.tools if getattr(item, "name", None) != FileAccessProvider.GREP_TOOL_NAME
        ]
        if not self.disable_write_tools:
            context.extend_tools(
                source_id,
                [file_access_move, file_access_copy, file_access_mkdir, file_access_rename],
            )
