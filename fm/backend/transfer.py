import asyncio
import os
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .desktop import notify_fs_changed
from .pathutil import resolve, to_fs

router = APIRouter(prefix="/api/transfer")

MAX_TASKS = 2000
MAX_DONE_TASKS = 80
BROADCAST_INTERVAL = 0.25
CHUNK_SIZE = 2 * 1024 * 1024
TEMP_ROOT = os.path.join(tempfile.gettempdir(), "fm_transfer")
PACK_IO_CHUNK = 1024 * 1024
os.makedirs(TEMP_ROOT, exist_ok=True)


class TaskCancelled(Exception):
    pass


class UploadItem(BaseModel):
    destPath: str
    relativePath: str
    name: str
    size: int = 0
    fileKey: str = ""


class UploadCreateBody(BaseModel):
    items: List[UploadItem]


class DownloadCreateBody(BaseModel):
    paths: List[str]


class IdBody(BaseModel):
    id: str


class TransferStore:
    def __init__(self):
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.order: List[str] = []
        self.lock = asyncio.Lock()
        self.clients: List[WebSocket] = []
        self._last_broadcast = 0.0
        self._pending = False
        self._flush_task: Optional[asyncio.Task] = None
        self._dirty: Set[str] = set()
        self._removed: Set[str] = set()

    def snapshot(self) -> List[Dict[str, Any]]:
        return [self.public(self.tasks[i]) for i in self.order if i in self.tasks]

    def public(self, task: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in task.items() if not k.startswith("_")}

    def _drop_task_locked(self, task_id: str):
        if task_id in self.order:
            self.order.remove(task_id)
        task = self.tasks.pop(task_id, None)
        if task:
            stop_download_worker(task)
            cleanup_task_files(task)
        self._dirty.discard(task_id)
        self._removed.add(task_id)

    def _trim_done_locked(self):
        done = [
            tid for tid in self.order
            if tid in self.tasks and self.tasks[tid]["status"] in ("success", "error", "cancelled")
        ]
        extra = len(done) - MAX_DONE_TASKS
        if extra <= 0:
            return
        for tid in done[:extra]:
            self._drop_task_locked(tid)

    def _evict_overflow_locked(self):
        while len(self.order) > MAX_TASKS:
            victim = None
            for tid in self.order:
                task = self.tasks.get(tid)
                if task and task["status"] in ("success", "error", "cancelled"):
                    victim = tid
                    break
            if victim is None:
                victim = self.order[0]
            self._drop_task_locked(victim)

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)
        await ws.send_json({"type": "snapshot", "tasks": self.snapshot()})

    def disconnect(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def _flush_later(self, delay: float):
        await asyncio.sleep(delay)
        self._flush_task = None
        if self._pending:
            await self.broadcast(force=True)

    async def broadcast(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_broadcast < BROADCAST_INTERVAL:
            self._pending = True
            if self._flush_task is None:
                delay = BROADCAST_INTERVAL - (now - self._last_broadcast)
                self._flush_task = asyncio.get_running_loop().create_task(self._flush_later(delay))
            return
        self._last_broadcast = now
        self._pending = False
        dirty_ids = list(self._dirty)
        removed_ids = list(self._removed)
        self._dirty.clear()
        self._removed.clear()
        if not dirty_ids and not removed_ids:
            return
        tasks = []
        for tid in dirty_ids:
            task = self.tasks.get(tid)
            if task:
                tasks.append(self.public(task))
        payload = {"type": "patch", "tasks": tasks, "removed": removed_ids}
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
        if self._pending:
            self._pending = False
            await self.broadcast(force=True)

    async def create(self, **fields) -> Dict[str, Any]:
        async with self.lock:
            task_id = uuid.uuid4().hex
            now = time.time()
            task = {
                "id": task_id,
                "kind": fields["kind"],
                "name": fields["name"],
                "path": fields.get("path", ""),
                "destPath": fields.get("destPath", ""),
                "relativePath": fields.get("relativePath", ""),
                "status": fields.get("status", "waiting"),
                "progress": 0.0,
                "size": int(fields.get("size") or 0),
                "transferred": 0,
                "speed": 0.0,
                "error": "",
                "client": fields.get("client", ""),
                "chunkSize": int(fields.get("chunkSize") or 0),
                "chunks": int(fields.get("chunks") or 0),
                "fileKey": fields.get("fileKey", ""),
                "createdAt": now,
                "updatedAt": now,
                "_cancel": False,
                "_speed_ts": 0.0,
                "_speed_bytes": 0,
                "_tmp": fields.get("_tmp", ""),
                "_paths": fields.get("_paths") or [],
                "_send_path": fields.get("_send_path", ""),
                "_cleanup": bool(fields.get("_cleanup", False)),
                "_chunk_dir": fields.get("_chunk_dir", ""),
                "_done_chunks": set(fields.get("_done_chunks") or []),
                "_worker": None,
                "_pack_total": 0,
                "_pack_done": 0,
            }
            self.tasks[task_id] = task
            self.order.append(task_id)
            self._dirty.add(task_id)
            self._trim_done_locked()
            self._evict_overflow_locked()
            return self.public(task)

    async def find_resume_upload(self, file_key: str) -> Optional[Dict[str, Any]]:
        if not file_key:
            return None
        async with self.lock:
            for tid in reversed(self.order):
                task = self.tasks.get(tid)
                if not task:
                    continue
                if task["kind"] != "upload":
                    continue
                if task.get("fileKey") != file_key:
                    continue
                if task["status"] in ("waiting", "running", "error"):
                    return task
        return None

    async def get(self, task_id: str) -> Dict[str, Any]:
        async with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            return task

    async def update(self, task_id: str, **fields):
        status = fields.get("status")
        async with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            if task.get("_cancel"):
                return
            if task["status"] in ("success", "error", "cancelled") and status != "waiting":
                return
            now = time.time()
            if "transferred" in fields:
                transferred = int(fields.get("transferred") or 0)
                prev_ts = float(task.get("_speed_ts") or 0)
                prev_bytes = int(task.get("_speed_bytes") or 0)
                if prev_ts > 0:
                    dt = now - prev_ts
                    if dt >= 0.35:
                        task["speed"] = max(0.0, (transferred - prev_bytes) / dt)
                        task["_speed_ts"] = now
                        task["_speed_bytes"] = transferred
                else:
                    task["_speed_ts"] = now
                    task["_speed_bytes"] = transferred
                    task["speed"] = 0.0
            for k, v in fields.items():
                task[k] = v
            if status in ("success", "error", "cancelled", "waiting", "ready"):
                task["speed"] = 0.0
            task["updatedAt"] = now
            self._dirty.add(task_id)
            if status in ("success", "error", "cancelled"):
                self._trim_done_locked()
        await self.broadcast(force=status in ("success", "error", "cancelled"))

    async def cancel(self, task_id: str):
        worker = None
        async with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task["status"] in ("success", "error", "cancelled"):
                return self.public(task)
            task["_cancel"] = True
            task["status"] = "error"
            task["error"] = "下载失败" if task.get("kind") == "download" else "上传失败"
            task["speed"] = 0.0
            task["updatedAt"] = time.time()
            worker = task.get("_worker")
            self._dirty.add(task_id)
            pub = self.public(task)
            cleanup_task_files(task)
        if worker:
            worker.stop()
        await self.broadcast(force=True)
        return pub

    async def clear_done(self):
        async with self.lock:
            keep = []
            for tid in self.order:
                task = self.tasks.get(tid)
                if not task:
                    continue
                if task["status"] in ("success", "error", "cancelled"):
                    stop_download_worker(task)
                    cleanup_task_files(task)
                    self.tasks.pop(tid, None)
                    self._dirty.discard(tid)
                    self._removed.add(tid)
                else:
                    keep.append(tid)
            self.order = keep
        await self.broadcast(force=True)


store = TransferStore()
merge_locks: Dict[str, asyncio.Lock] = {}


def ok(data=None, info=""):
    return {
        "code": True,
        "timeUse": "0",
        "timeNow": str(time.time()),
        "data": data if data is not None else "",
        "info": info,
    }


def client_host(request: Request) -> str:
    if request.client:
        return request.client.host or ""
    return ""


def cleanup_task_files(task: Dict[str, Any]):
    chunk_dir = task.get("_chunk_dir") or ""
    if chunk_dir and os.path.isdir(chunk_dir):
        shutil.rmtree(chunk_dir, ignore_errors=True)
    tmp = task.get("_tmp") or ""
    if tmp and task.get("_cleanup") and os.path.isfile(tmp):
        try:
            os.unlink(tmp)
        except OSError:
            pass
        task["_tmp"] = ""


def resolve_upload_target(dest_path: str, relative_path: str, filename: str) -> str:
    parent = resolve(dest_path)
    if not os.path.isdir(parent):
        raise NotADirectoryError(dest_path)
    rel = (relative_path or "").replace("\\", "/").strip("/")
    parts = [p for p in rel.split("/") if p]
    for p in parts:
        if p in (".", ".."):
            raise ValueError("invalid relativePath")
    if parts:
        name = parts[-1]
        dir_parts = parts[:-1]
    else:
        name = os.path.basename(filename or "")
        dir_parts = []
    if not name:
        raise ValueError("empty filename")
    target_dir = os.path.join(parent, *dir_parts) if dir_parts else parent
    os.makedirs(target_dir, exist_ok=True)
    return os.path.join(target_dir, name)


def calc_chunks(size: int, chunk_size: int = CHUNK_SIZE) -> tuple:
    size = max(0, int(size or 0))
    chunk_size = max(1, int(chunk_size or CHUNK_SIZE))
    if size <= chunk_size:
        return 1, chunk_size
    chunks = (size + chunk_size - 1) // chunk_size
    return chunks, chunk_size


def list_done_chunks(chunk_dir: str) -> Set[int]:
    done = set()
    if not os.path.isdir(chunk_dir):
        return done
    for name in os.listdir(chunk_dir):
        if not name.startswith("chunk_"):
            continue
        try:
            done.add(int(name.split("_", 1)[1]))
        except ValueError:
            continue
    return done


def transferred_from_chunks(chunk_dir: str, size: int, chunk_size: int, chunks: int) -> int:
    done = list_done_chunks(chunk_dir)
    total = 0
    for i in done:
        if i < chunks - 1:
            total += chunk_size
        else:
            total += max(0, size - chunk_size * (chunks - 1))
    return min(size, total) if size else total


@router.get("/tasks")
async def list_tasks():
    return ok(store.snapshot())


@router.websocket("/ws")
async def transfer_ws(websocket: WebSocket):
    await store.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        store.disconnect(websocket)
    except Exception:
        store.disconnect(websocket)


@router.post("/uploads")
async def create_uploads(body: UploadCreateBody, request: Request):
    if not body.items:
        raise ValueError("items is required")
    host = client_host(request)
    created = []
    for item in body.items:
        size = max(0, int(item.size or 0))
        chunks, chunk_size = calc_chunks(size)
        file_key = item.fileKey or f"{item.destPath}|{item.relativePath}|{size}|{item.name}"
        exist = await store.find_resume_upload(file_key)
        if exist:
            chunk_dir = exist.get("_chunk_dir") or ""
            done = list_done_chunks(chunk_dir)
            exist["_done_chunks"] = done
            transferred = transferred_from_chunks(chunk_dir, size, exist.get("chunkSize") or chunk_size, exist.get("chunks") or chunks)
            progress = (transferred / size) if size else 0.0
            await store.update(
                exist["id"],
                status="waiting",
                error="",
                transferred=transferred,
                progress=progress,
                client=host,
            )
            pub = store.public(await store.get(exist["id"]))
            pub["resume"] = True
            pub["doneChunks"] = sorted(list(done))
            created.append(pub)
            continue
        pub = await store.create(
            kind="upload",
            name=item.name,
            destPath=item.destPath,
            relativePath=item.relativePath,
            path=item.relativePath,
            size=size,
            client=host,
            chunkSize=chunk_size,
            chunks=chunks,
            fileKey=file_key,
            _chunk_dir="",
        )
        pub["resume"] = False
        pub["doneChunks"] = []
        created.append(pub)
    await store.broadcast(force=True)
    return ok(created)


@router.get("/upload/{task_id}/check")
async def upload_check(task_id: str):
    task = await store.get(task_id)
    if task["kind"] != "upload":
        raise ValueError("not an upload task")
    done = sorted(list(list_done_chunks(task.get("_chunk_dir") or "")))
    task["_done_chunks"] = set(done)
    return ok({
        "id": task_id,
        "chunks": task.get("chunks") or 0,
        "chunkSize": task.get("chunkSize") or CHUNK_SIZE,
        "doneChunks": done,
        "size": task.get("size") or 0,
        "status": task.get("status"),
    })


@router.post("/upload/{task_id}/chunk")
async def upload_chunk(
    task_id: str,
    chunk: int = Form(...),
    chunks: int = Form(...),
    chunkSize: int = Form(...),
    file: UploadFile = File(...),
):
    task = await store.get(task_id)
    if task["kind"] != "upload":
        raise ValueError("not an upload task")
    if task["status"] == "cancelled" or task.get("_cancel"):
        raise RuntimeError("task cancelled")
    if task["status"] == "success":
        return ok(store.public(task))
    size = int(task["size"] or 0)
    expect_chunks = int(task.get("chunks") or chunks)
    expect_size = int(task.get("chunkSize") or chunkSize)
    if chunks != expect_chunks or chunkSize != expect_size:
        raise ValueError("chunk meta mismatch")
    if chunk < 0 or chunk >= expect_chunks:
        raise ValueError("invalid chunk index")
    if expect_chunks == 1:
        target = resolve_upload_target(task["destPath"], task["relativePath"], task["name"])
        await store.update(task_id, status="running", error="")
        try:
            with open(target, "wb") as out:
                while True:
                    cur = await store.get(task_id)
                    if cur.get("_cancel") or cur["status"] == "cancelled":
                        raise RuntimeError("task cancelled")
                    data = await file.read(1024 * 1024)
                    if not data:
                        break
                    out.write(data)
                    await asyncio.sleep(0)
            written = size or os.path.getsize(target)
            await store.update(
                task_id,
                status="success",
                progress=1.0,
                transferred=written,
                size=written,
                path=to_fs(target),
                error="",
            )
            notify_fs_changed([to_fs(target)])
            return ok(store.public(await store.get(task_id)))
        except Exception as exc:
            cur = await store.get(task_id)
            if cur.get("_cancel") or cur["status"] == "cancelled":
                await store.broadcast(force=True)
                raise
            await store.update(task_id, status="error", error=f"{type(exc).__name__}: {exc}")
            raise
    chunk_dir = task.get("_chunk_dir") or ""
    if not chunk_dir:
        chunk_dir = os.path.join(TEMP_ROOT, f"up_{uuid.uuid4().hex}")
        os.makedirs(chunk_dir, exist_ok=True)
        task["_chunk_dir"] = chunk_dir
    part_path = os.path.join(chunk_dir, f"chunk_{chunk}")
    await store.update(task_id, status="running", error="")
    try:
        with open(part_path, "wb") as out:
            while True:
                cur = await store.get(task_id)
                if cur.get("_cancel") or cur["status"] == "cancelled":
                    raise RuntimeError("task cancelled")
                data = await file.read(1024 * 1024)
                if not data:
                    break
                out.write(data)
                await asyncio.sleep(0)
        if task_id not in merge_locks:
            merge_locks[task_id] = asyncio.Lock()
        async with merge_locks[task_id]:
            done = list_done_chunks(chunk_dir)
            transferred = transferred_from_chunks(chunk_dir, size, expect_size, expect_chunks)
            progress = (transferred / size) if size else (len(done) / expect_chunks)
            await store.update(task_id, transferred=transferred, progress=min(0.99, progress))
            cur = await store.get(task_id)
            if cur["status"] == "success":
                return ok(store.public(cur))
            if len(done) < expect_chunks:
                return ok({"id": task_id, "chunk": chunk, "done": False, "doneChunks": sorted(list(done))})
            target = resolve_upload_target(task["destPath"], task["relativePath"], task["name"])
            await asyncio.to_thread(merge_chunks, chunk_dir, expect_chunks, target)
            shutil.rmtree(chunk_dir, ignore_errors=True)
            cur = await store.get(task_id)
            cur["_chunk_dir"] = ""
            await store.update(
                task_id,
                status="success",
                progress=1.0,
                transferred=size or os.path.getsize(target),
                size=size or os.path.getsize(target),
                path=to_fs(target),
                error="",
            )
            merge_locks.pop(task_id, None)
            notify_fs_changed([to_fs(target)])
            return ok(store.public(await store.get(task_id)))
    except Exception as exc:
        if os.path.isfile(part_path):
            try:
                os.unlink(part_path)
            except OSError:
                pass
        cur = await store.get(task_id)
        if cur.get("_cancel") or cur["status"] == "cancelled":
            await store.broadcast(force=True)
            raise
        await store.update(task_id, status="error", error=f"{type(exc).__name__}: {exc}")
        raise


def merge_chunks(chunk_dir: str, chunks: int, target: str):
    with open(target, "wb") as out:
        for i in range(chunks):
            part = os.path.join(chunk_dir, f"chunk_{i}")
            if not os.path.isfile(part):
                raise FileNotFoundError(f"missing chunk {i}")
            with open(part, "rb") as inp:
                shutil.copyfileobj(inp, out, 1024 * 1024)


def collect_zip_entries(paths: List[str]) -> List[tuple]:
    entries = []
    for p in paths:
        src = resolve(p)
        if not os.path.exists(src):
            raise FileNotFoundError(p)
        if os.path.isfile(src):
            entries.append((src, os.path.basename(src)))
            continue
        base = os.path.dirname(src)
        for root, _, files in os.walk(src):
            for f in files:
                fp = os.path.join(root, f)
                entries.append((fp, os.path.relpath(fp, base)))
    return entries


def build_zip_entries(entries: List[tuple], tmp_path: str, progress_cb=None, cancel_cb=None):
    total = len(entries) or 1
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for i, (fp, arc) in enumerate(entries):
            if cancel_cb and cancel_cb():
                raise TaskCancelled()
            info = zipfile.ZipInfo(str(arc).replace("\\", "/"))
            info.compress_type = zipfile.ZIP_STORED
            with open(fp, "rb") as src, zf.open(info, "w") as dst:
                while True:
                    if cancel_cb and cancel_cb():
                        raise TaskCancelled()
                    buf = src.read(PACK_IO_CHUNK)
                    if not buf:
                        break
                    dst.write(buf)
            if progress_cb:
                progress_cb(i + 1, total)


def stop_download_worker(task: Optional[Dict[str, Any]]):
    if not task:
        return
    worker = task.get("_worker")
    if worker:
        worker.stop()
        task["_worker"] = None


class DownloadWorker:
    def __init__(self, task_id: str, loop: asyncio.AbstractEventLoop):
        self.task_id = task_id
        self.loop = loop
        self.cancel_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name=f"fm-dl-{task_id[:8]}",
            daemon=True,
        )

    def start(self):
        self.thread.start()

    def stop(self):
        self.cancel_event.set()

    def stopped(self) -> bool:
        return self.cancel_event.is_set()

    def _cancelled(self) -> bool:
        if self.cancel_event.is_set():
            return True
        task = store.tasks.get(self.task_id)
        return bool(task and task.get("_cancel"))

    def _schedule(self, coro):
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _mark_ready(self, **fields):
        async def apply():
            task = store.tasks.get(self.task_id)
            if not task or task.get("_cancel"):
                return
            await store.update(self.task_id, **fields)
            asyncio.create_task(expire_ready_task(self.task_id, 30))

        self._schedule(apply())

    def _mark_error(self):
        self._schedule(store.update(self.task_id, status="error", error="下载失败", speed=0.0))

    def _update_packing(self, done: int, total: int):
        self._schedule(
            store.update(
                self.task_id,
                status="packing",
                progress=min(0.99, done / total) if total else 0.0,
                transferred=done,
                size=total,
                error="",
            )
        )

    def _run(self):
        tmp_path = ""
        try:
            if self._cancelled():
                return
            task = store.tasks.get(self.task_id)
            if not task:
                return
            paths = list(task.get("_paths") or [])
            if not paths:
                self._mark_error()
                return
            if len(paths) == 1:
                full = resolve(paths[0])
                if os.path.isfile(full):
                    if self._cancelled():
                        return
                    task["_send_path"] = full
                    task["_cleanup"] = False
                    self._mark_ready(
                        status="ready",
                        progress=0.0,
                        transferred=0,
                        size=os.path.getsize(full),
                        path=to_fs(full),
                        error="",
                    )
                    return

            filename = task.get("name") or "download.zip"
            if len(paths) > 1:
                filename = "download.zip"
            if self._cancelled():
                return
            fd, tmp_path = tempfile.mkstemp(suffix=".zip", dir=TEMP_ROOT)
            os.close(fd)
            task["_tmp"] = tmp_path
            task["_cleanup"] = True
            self._schedule(
                store.update(
                    self.task_id,
                    status="packing",
                    name=filename,
                    progress=0.0,
                    transferred=0,
                    size=0,
                    error="",
                )
            )
            entries = collect_zip_entries(paths)
            total = len(entries) or 1
            self._update_packing(0, total)
            last = {"t": 0.0}

            def cancel_cb():
                return self._cancelled()

            def progress_cb(done, total_count):
                now = time.time()
                if done < total_count and now - last["t"] < 0.2:
                    return
                last["t"] = now
                self._update_packing(done, total_count)

            build_zip_entries(entries, tmp_path, progress_cb, cancel_cb)
            if self._cancelled():
                raise TaskCancelled()
            task["_send_path"] = tmp_path
            self._mark_ready(
                status="ready",
                name=filename,
                progress=0.0,
                transferred=0,
                size=os.path.getsize(tmp_path),
                path="",
                error="",
            )
        except TaskCancelled:
            if tmp_path and os.path.isfile(tmp_path):
                os.unlink(tmp_path)
            task = store.tasks.get(self.task_id)
            if task:
                task["_tmp"] = ""
        except Exception:
            if tmp_path and os.path.isfile(tmp_path):
                os.unlink(tmp_path)
            task = store.tasks.get(self.task_id)
            if task:
                task["_tmp"] = ""
            if not self._cancelled():
                self._mark_error()
        finally:
            task = store.tasks.get(self.task_id)
            if task and task.get("_worker") is self:
                task["_worker"] = None


def start_download_worker(task_id: str):
    task = store.tasks.get(task_id)
    if not task:
        raise KeyError(task_id)
    stop_download_worker(task)
    task["_cancel"] = False
    loop = asyncio.get_running_loop()
    worker = DownloadWorker(task_id, loop)
    task["_worker"] = worker
    worker.start()


@router.post("/downloads")
async def create_download(body: DownloadCreateBody, request: Request):
    if not body.paths:
        raise ValueError("paths is required")
    paths = body.paths
    host = client_host(request)
    if len(paths) == 1:
        full = resolve(paths[0])
        if not os.path.exists(full):
            raise FileNotFoundError(paths[0])
        if os.path.isfile(full):
            name = os.path.basename(full)
            size = os.path.getsize(full)
            kind_path = to_fs(full)
        else:
            name = f"{os.path.basename(full) or 'download'}.zip"
            size = 0
            kind_path = paths[0]
    else:
        name = "download.zip"
        size = 0
        kind_path = ""
    pub = await store.create(
        kind="download",
        name=name,
        path=kind_path,
        size=size,
        client=host,
        status="waiting",
        _paths=paths,
    )
    await store.broadcast(force=True)
    start_download_worker(pub["id"])
    return ok({
        "id": pub["id"],
        "name": name,
        "url": f"/api/transfer/download/{pub['id']}",
    })


async def expire_ready_task(task_id: str, timeout: int = 30):
    await asyncio.sleep(timeout)
    task = store.tasks.get(task_id)
    if not task:
        return
    if task.get("status") == "ready":
        cleanup_task_files(task)
        await store.update(
            task_id,
            status="error",
            error="下载失败",
            progress=0.0,
            transferred=0,
        )


@router.post("/retry")
async def retry_task(body: IdBody, request: Request):
    task = await store.get(body.id)
    if task["status"] not in ("error", "cancelled"):
        raise RuntimeError(f"invalid status: {task['status']}")
    if task["kind"] == "download":
        paths = task.get("_paths") or []
        if not paths:
            raise ValueError("download paths missing")
        stop_download_worker(task)
        cleanup_task_files(task)
        task["_cancel"] = False
        task["_tmp"] = ""
        task["_send_path"] = ""
        task["_cleanup"] = False
        await store.update(
            body.id,
            status="waiting",
            error="",
            progress=0.0,
            transferred=0,
            client=client_host(request),
        )
        start_download_worker(body.id)
        return ok(store.public(await store.get(body.id)))
    if task["kind"] == "upload":
        task["_cancel"] = False
        chunk_dir = task.get("_chunk_dir") or ""
        if not chunk_dir or not os.path.isdir(chunk_dir):
            chunk_dir = os.path.join(TEMP_ROOT, f"up_{uuid.uuid4().hex}")
            os.makedirs(chunk_dir, exist_ok=True)
            task["_chunk_dir"] = chunk_dir
        done = list_done_chunks(chunk_dir)
        size = int(task.get("size") or 0)
        chunk_size = int(task.get("chunkSize") or CHUNK_SIZE)
        chunks = int(task.get("chunks") or 1)
        transferred = transferred_from_chunks(chunk_dir, size, chunk_size, chunks)
        progress = (transferred / size) if size else 0.0
        await store.update(
            body.id,
            status="waiting",
            error="",
            transferred=transferred,
            progress=progress,
            client=client_host(request),
        )
        pub = store.public(await store.get(body.id))
        pub["doneChunks"] = sorted(list(done))
        return ok(pub)
    raise ValueError(f"unsupported kind: {task['kind']}")


def parse_range(range_header: str, size: int):
    if not range_header or not range_header.startswith("bytes="):
        return None
    spec = range_header.replace("bytes=", "").strip()
    if "," in spec:
        raise ValueError("multi-range not supported")
    start_s, _, end_s = spec.partition("-")
    if start_s == "":
        length = int(end_s)
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    if start < 0 or end < start or start >= size:
        raise ValueError("invalid range")
    end = min(end, size - 1)
    return start, end


@router.get("/download/{task_id}")
async def download_task(task_id: str, request: Request):
    task = await store.get(task_id)
    if task["kind"] != "download":
        raise ValueError("not a download task")
    if task.get("_cancel") or task["status"] in ("cancelled", "error"):
        raise RuntimeError(task.get("error") or "下载失败")
    if task["status"] in ("waiting", "packing"):
        raise RuntimeError("file is packing, not ready")
    if task["status"] not in ("ready", "running"):
        raise RuntimeError(f"invalid status: {task['status']}")

    send_path = task.get("_send_path") or task.get("_tmp") or ""
    if not send_path or not os.path.isfile(send_path):
        raise FileNotFoundError("download file missing")
    filename = task["name"]
    size = os.path.getsize(send_path)
    range_header = request.headers.get("range") or request.headers.get("Range")
    start = 0
    end = size - 1
    status_code = 200
    if range_header:
        start, end = parse_range(range_header, size)
        status_code = 206

    length = end - start + 1
    await store.update(
        task_id,
        status="running",
        size=size,
        error="",
    )
    transferred_holder = {"n": start}
    expect_end = start + length

    async def mark_failed():
        cur = store.tasks.get(task_id)
        if not cur:
            return
        if cur.get("_cancel") or cur.get("status") in ("cancelled", "success"):
            return
        await store.update(
            task_id,
            status="error",
            error="下载失败",
            speed=0.0,
        )

    async def is_cancelled() -> bool:
        cur = store.tasks.get(task_id)
        if not cur:
            return True
        if cur.get("_cancel"):
            return True
        return cur.get("status") in ("cancelled", "error", "success")

    async def stream():
        sent_all = False
        try:
            with open(send_path, "rb") as f:
                f.seek(start)
                remain = length
                while remain > 0:
                    if await is_cancelled():
                        return
                    if await request.is_disconnected():
                        await mark_failed()
                        return
                    if await is_cancelled():
                        return
                    chunk = await asyncio.to_thread(f.read, min(1024 * 1024, remain))
                    if not chunk:
                        break
                    if await is_cancelled():
                        return
                    remain -= len(chunk)
                    transferred_holder["n"] += len(chunk)
                    if await is_cancelled():
                        return
                    yield chunk
                    await asyncio.sleep(0)
                sent_all = remain == 0 and transferred_holder["n"] >= expect_end
            if await is_cancelled():
                return
            if await request.is_disconnected():
                await mark_failed()
                return
            if not sent_all:
                await mark_failed()
                return
            if start == 0 and end == size - 1:
                await store.update(
                    task_id,
                    status="success",
                    size=size,
                    error="",
                )
                cur = await store.get(task_id)
                if cur.get("_cleanup"):
                    await asyncio.to_thread(cleanup_task_files, cur)
                    cur["_send_path"] = ""
            else:
                await store.update(
                    task_id,
                    status="ready",
                )
        except asyncio.CancelledError:
            if not await is_cancelled():
                await mark_failed()
            raise
        except Exception:
            if not await is_cancelled():
                await mark_failed()
            raise
        finally:
            if not sent_all:
                cur = store.tasks.get(task_id)
                if cur and cur.get("status") == "running":
                    try:
                        asyncio.get_running_loop().create_task(mark_failed())
                    except RuntimeError:
                        pass

    ascii_name = "".join(ch if ch.isascii() and ch not in '"\\' else "_" for ch in filename)
    headers = {
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(stream(), media_type="application/octet-stream", headers=headers, status_code=status_code)


@router.post("/cancel")
async def cancel_task(body: IdBody):
    pub = await store.cancel(body.id)
    return ok(pub)


@router.post("/clear")
async def clear_done():
    await store.clear_done()
    return ok(True)
