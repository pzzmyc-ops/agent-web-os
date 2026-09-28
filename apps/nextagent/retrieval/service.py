from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from typing import Any, Awaitable, Callable

from appconfig import load_config
from fm.backend.pathutil import from_agent_path, resolve

from ..toolkit._rg import WORKSPACE_PRIVATE_DIRS
from .chunker import CHUNKER_VERSION, chunk_lines
from .files import (
    IndexableFile,
    canon_path,
    enumerate_files,
    is_under,
    path_key,
    read_text_or_none,
    sha256_of,
)
from .ollama import OllamaEmbedder, OllamaUnavailable
from .store import FileRecord, SearchHit, VectorStore

FLUSH_CHUNKS = 64
PROGRESS_INTERVAL = 1.0
NotifyFn = Callable[[dict[str, Any]], Awaitable[None]]


class IndexBusy(RuntimeError):
    pass


class IndexMissing(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class RetrievalService:
    def __init__(self) -> None:
        cfg = load_config()
        self.workspace = canon_path(cfg.fm_root_dir)
        self.index_dir = canon_path(cfg.index_dir)
        self.db_path = os.path.join(cfg.index_dir, "retrieval.sqlite")
        self.embedder = OllamaEmbedder(cfg.ollama_url, cfg.embedding_model)
        self.private_dirs = [canon_path(os.path.join(cfg.fm_root_dir, name)) for name in WORKSPACE_PRIVATE_DIRS]
        self._store: VectorStore | None = None
        self._task: asyncio.Task | None = None
        self._cancel = False
        self._state: dict[str, Any] = {
            "active": False,
            "phase": "idle",
            "source": "",
            "processed": 0,
            "total": 0,
            "changed": 0,
            "removed": 0,
            "error": "",
            "started_at": 0.0,
        }

    def store(self) -> VectorStore:
        if self._store is None:
            self._store = VectorStore(self.db_path)
        return self._store

    def status(self) -> dict[str, Any]:
        st = self.store()
        files, chunks = st.counts()
        per_source = st.source_counts()
        sources = []
        for src in st.sources():
            f, c = per_source.get(src.path, (0, 0))
            sources.append({
                "path": src.path,
                "display": self.display_path(src.path),
                "files": f,
                "chunks": c,
                "addedAt": src.added_at,
                "lastSync": src.last_sync,
            })
        return {
            **self._state,
            "files": files,
            "chunks": chunks,
            "indexExists": files > 0,
            "sources": sources,
            "model": self.embedder.model,
            "ollamaUrl": self.embedder.url,
            "indexedModel": st.get_meta("model") or "",
            "lastBuild": st.get_meta("last_build") or "",
            "workspace": self.workspace,
            "dbPath": self.db_path,
        }

    def is_active(self) -> bool:
        return self._task is not None and not self._task.done()

    def display_path(self, path: str) -> str:
        return path

    def _resolve_dir(self, raw: str) -> str:
        text = str(raw or "").strip().strip('"')
        if not text:
            raise ValueError("路径不能为空")
        full = canon_path(resolve(from_agent_path(text, is_directory=True)))
        if not os.path.isdir(full):
            raise ValueError(f"文件夹不存在: {full}")
        return full

    def _check_addable(self, full: str) -> None:
        for private in self.private_dirs:
            if is_under(full, private):
                raise ValueError(f"{self.display_path(private)} 是 agent 的私有目录,不能向量化")
        for src in self.store().sources():
            if path_key(src.path) == path_key(full):
                raise ValueError(f"已经添加过: {self.display_path(full)}")
            if is_under(full, src.path):
                raise ValueError(f"已被 {self.display_path(src.path)} 覆盖,不用重复添加")
            if is_under(src.path, full):
                raise ValueError(f"它包含已添加的 {self.display_path(src.path)},请先移除那一条")

    async def add_source(self, raw: str, notify: NotifyFn | None) -> dict[str, Any]:
        full = self._resolve_dir(raw)
        self._check_addable(full)
        if self.is_active():
            raise IndexBusy("索引正在建,等它跑完再添加")
        info = await self.embedder.check()
        self.store().add_source(full, _now())
        self._launch(notify, [full])
        return info

    def remove_source(self, raw: str) -> int:
        text = str(raw or "").strip()
        if not text:
            raise ValueError("路径不能为空")
        if self.is_active():
            raise IndexBusy("索引正在建,等它跑完再移除")
        st = self.store()
        match = [s.path for s in st.sources() if path_key(s.path) == path_key(text) or path_key(s.path) == path_key(canon_path(text))]
        if not match:
            raise ValueError(f"没有这个来源: {text}")
        removed = st.remove_source(match[0])
        st.bump_version()
        return removed

    async def start_rebuild(self, notify: NotifyFn | None) -> dict[str, Any]:
        if self.is_active():
            raise IndexBusy("索引正在建,等它跑完")
        sources = [s.path for s in self.store().sources()]
        if not sources:
            raise IndexMissing("还没有添加向量化文件夹,先在「代码索引」里添加一个")
        info = await self.embedder.check()
        self._launch(notify, sources)
        return info

    def _launch(self, notify: NotifyFn | None, sources: list[str]) -> None:
        self._cancel = False
        self._state.update({
            "active": True,
            "phase": "enumerating",
            "source": "",
            "processed": 0,
            "total": 0,
            "changed": 0,
            "removed": 0,
            "error": "",
            "started_at": time.time(),
        })
        self._task = asyncio.create_task(self._run(notify, sources))

    def cancel(self) -> bool:
        if not self.is_active():
            return False
        self._cancel = True
        return True

    async def _notify(self, notify: NotifyFn | None) -> None:
        if notify is None:
            return
        await notify({"type": "retrieval_indexing", "data": self.status()})

    async def _run(self, notify: NotifyFn | None, sources: list[str]) -> None:
        try:
            await self._rebuild(notify, sources)
            self._state["phase"] = "done"
        except asyncio.CancelledError:
            self._state["phase"] = "cancelled"
            raise
        except Exception as exc:
            self._state["phase"] = "error"
            self._state["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            self._state["active"] = False
            self._state["source"] = ""
            await self._notify(notify)

    async def _rebuild(self, notify: NotifyFn | None, sources: list[str]) -> None:
        st = self.store()
        if (st.get_meta("model") or "") not in ("", self.embedder.model):
            st.clear()
        if (st.get_meta("chunker") or "") not in ("", CHUNKER_VERSION):
            st.clear()
        st.set_meta("model", self.embedder.model)
        st.set_meta("chunker", CHUNKER_VERSION)
        await self._notify(notify)

        excludes = list(self.private_dirs) + [self.index_dir]
        plan: list[tuple[str, list[IndexableFile], dict[str, FileRecord]]] = []
        total = 0
        removed = 0
        for source in sources:
            self._state["source"] = source
            files = await asyncio.to_thread(enumerate_files, source, excludes)
            existing = st.files(source)
            current = {path_key(f.full_path) for f in files}
            for path in list(existing):
                if path_key(path) not in current:
                    st.remove_file(path)
                    removed += 1
            plan.append((source, files, existing))
            total += len(files)
        self._state.update({"phase": "embedding", "total": total, "removed": removed})
        await self._notify(notify)

        pending_texts: list[str] = []
        pending_items: list[tuple[FileRecord, list[tuple[int, int, str]], int]] = []
        last_notify = time.time()
        processed = 0

        async def flush() -> None:
            nonlocal pending_texts, pending_items
            if not pending_texts:
                return
            vectors = await self.embedder.embed(pending_texts)
            offset = 0
            for record, chunks, count in pending_items:
                st.replace_file(record, chunks, vectors[offset:offset + count])
                offset += count
            pending_texts = []
            pending_items = []

        for source, files, existing in plan:
            self._state["source"] = source
            for item in files:
                if self._cancel:
                    await flush()
                    st.bump_version()
                    raise asyncio.CancelledError()
                processed += 1
                self._state["processed"] = processed
                old = existing.get(item.full_path)
                if old is not None and old.size == item.size and old.mtime_ns == item.mtime_ns:
                    continue
                sha = await asyncio.to_thread(sha256_of, item.full_path)
                if old is not None and old.sha == sha:
                    st.update_file_stat(FileRecord(item.full_path, sha, item.size, item.mtime_ns, old.chunk_count))
                    continue
                text = await asyncio.to_thread(read_text_or_none, item.full_path)
                if text is None:
                    if old is not None:
                        st.remove_file(item.full_path)
                    continue
                chunks = chunk_lines(text)
                if not chunks:
                    if old is not None:
                        st.remove_file(item.full_path)
                    continue
                record = FileRecord(item.full_path, sha, item.size, item.mtime_ns, len(chunks))
                pending_items.append((record, [(c.start_line, c.end_line, c.text) for c in chunks], len(chunks)))
                pending_texts.extend(_embed_input(item, c.text) for c in chunks)
                self._state["changed"] += 1
                if len(pending_texts) >= FLUSH_CHUNKS:
                    await flush()
                now = time.time()
                if now - last_notify >= PROGRESS_INTERVAL:
                    last_notify = now
                    await self._notify(notify)
            await flush()
            st.set_source_sync(source, _now())

        st.set_meta("last_build", _now())
        st.bump_version()

    def _target_prefix(self, target_directory: str) -> str:
        raw = str(target_directory or "").strip()
        if not raw:
            return ""
        full = self._resolve_dir(raw)
        for src in self.store().sources():
            if is_under(full, src.path):
                return full
        raise RuntimeError(f"目录 {self.display_path(full)} 不在任何已添加的向量化文件夹内")

    async def search(self, query: str, *, target_directory: str = "", top_k: int = 10) -> list[SearchHit]:
        st = self.store()
        if not st.sources():
            raise IndexMissing("还没有添加向量化文件夹。请在顶栏「代码索引」里添加文件夹后再用 semantic_search")
        files, _chunks = st.counts()
        if files == 0:
            raise IndexMissing("已添加文件夹但索引还是空的。请在顶栏「代码索引」里点「建立 / 更新索引」")
        indexed_model = st.get_meta("model") or ""
        if indexed_model and indexed_model != self.embedder.model:
            raise RuntimeError(
                f"索引是用 {indexed_model} 建的,当前配置是 {self.embedder.model},请重建索引"
            )
        prefix = self._target_prefix(target_directory)
        vectors = await self.embedder.embed([query])
        return st.search(vectors[0], top_k=top_k, path_prefix=prefix)


def _embed_input(item: IndexableFile, text: str) -> str:
    return f"{item.rel_path}\n{text}"


retrieval_service = RetrievalService()

__all__ = ["IndexBusy", "IndexMissing", "OllamaUnavailable", "RetrievalService", "retrieval_service"]
