import ctypes
import os
import re
import shutil
import stat
import threading
import time
import uuid
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from .desktop import notify_fs_changed
from .pathutil import IS_WINDOWS, WORKSPACE, is_computer_root, is_root, normalize, parent_of, resolve, to_fs
from .settings import DESKTOP_PATH

router = APIRouter(prefix="/api/fileop")

CHUNK = 1024 * 1024
_TERMINAL = ("success", "error", "cancelled")
_DESKTOP_FS = to_fs(DESKTOP_PATH)
_DRIVE_ROOT = re.compile(r"^([A-Z]):/$")


class FileOpCancelled(Exception):
    pass


def ok(data=None, info=""):
    return {
        "code": True,
        "timeUse": "0",
        "timeNow": str(time.time()),
        "data": data if data is not None else "",
        "info": info,
    }


def same_volume(src: str, dest: str) -> bool:
    if IS_WINDOWS:
        left = os.path.splitdrive(os.path.abspath(src))[0].upper()
        right = os.path.splitdrive(os.path.abspath(dest))[0].upper()
        if not left or not right:
            raise ValueError(f"无法判断所在磁盘: {src} -> {dest}")
        return left == right
    return os.stat(src).st_dev == os.stat(dest).st_dev


def volume_label(letter: str) -> str:
    if not IS_WINDOWS:
        raise RuntimeError("volume label is only available on Windows")
    root = letter.upper() + ":\\"
    name = ctypes.create_unicode_buffer(261)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetVolumeInformationW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    ]
    kernel32.GetVolumeInformationW.restype = ctypes.c_int
    ok_call = kernel32.GetVolumeInformationW(root, name, 261, None, None, None, None, 0)
    if not ok_call:
        raise OSError(ctypes.get_last_error(), f"GetVolumeInformationW {root}")
    label = name.value.strip() or "本地磁盘"
    return f"{label} ({letter.upper()}:)"


def place_label(path: str) -> str:
    norm = normalize(path)
    if norm == WORKSPACE:
        return "我的文件"
    if norm == _DESKTOP_FS:
        return "桌面"
    if is_computer_root(norm):
        return "此电脑"
    match = _DRIVE_ROOT.match(norm)
    if match:
        return volume_label(match.group(1))
    parts = norm.rstrip("/").split("/")
    return parts[-1] if parts else norm


def _check_rel(rel: str) -> None:
    if not rel or os.path.isabs(rel):
        raise ValueError("unsafe path")
    parts = rel.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("unsafe path")


def _reject_into_self(src: str, dest_dir: str) -> None:
    dest_real = os.path.normcase(os.path.realpath(dest_dir))
    src_real = os.path.normcase(os.path.realpath(src))
    if dest_real == src_real:
        raise ValueError("不能把文件夹放进它自己里面")
    prefix = src_real if src_real.endswith(os.sep) else src_real + os.sep
    if dest_real.startswith(prefix):
        raise ValueError("不能把文件夹放进它自己里面")


def _reject_overlap(abs_paths: List[str]) -> None:
    norms = [os.path.normcase(os.path.realpath(p)) for p in abs_paths]
    if len(set(norms)) != len(norms):
        raise ValueError("选择了重复的项目")
    ordered = sorted(norms, key=len)
    for index, parent in enumerate(ordered):
        prefix = parent if parent.endswith(os.sep) else parent + os.sep
        for child in ordered[index + 1:]:
            if child.startswith(prefix):
                raise ValueError("不能同时选择文件夹和它里面的内容")


def _join_target(dest_full: str, rel: str) -> str:
    _check_rel(rel)
    target = os.path.normpath(os.path.join(dest_full, rel))
    parent = os.path.normcase(os.path.normpath(dest_full))
    child = os.path.normcase(target)
    prefix = parent if parent.endswith(os.sep) else parent + os.sep
    if not child.startswith(prefix):
        raise ValueError("unsafe path")
    return target


def _remove_file(path: str) -> None:
    if IS_WINDOWS and not os.path.islink(path):
        os.chmod(path, stat.S_IWRITE)
    os.remove(path)


def _remove_dir(path: str) -> None:
    if os.path.islink(path):
        os.rmdir(path)
        return
    if IS_WINDOWS:
        os.chmod(path, stat.S_IWRITE)
    os.rmdir(path)


class Job:
    def __init__(self, op, sources, dest_full, src_label, dest_label, changed):
        self.id = uuid.uuid4().hex
        self.op = op
        self.sources = sources
        self.dest_full = dest_full
        self.src_label = src_label
        self.dest_label = dest_label
        self.changed = changed
        self.status = "scanning"
        self.phase = "scan"
        self.total_files = 0
        self.done_files = 0
        self.total_bytes = 0
        self.done_bytes = 0
        self.current_name = ""
        self.speed = 0.0
        self.error = ""
        self.files = []
        self.dest_rels = []
        self.src_dirs = []
        self.cancel = False
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.lock = threading.Lock()
        self._speed_mark = 0.0
        self._speed_bytes = 0
        self.thread = threading.Thread(target=self.run, name="fm-fileop-" + self.id[:8], daemon=True)

    def start(self) -> None:
        self.thread.start()

    def public(self) -> dict:
        with self.lock:
            status = self.status
            phase = self.phase
            total_bytes = self.total_bytes
            done_bytes = self.done_bytes
            total_files = self.total_files
            done_files = self.done_files
            speed = self.speed if status == "running" else 0.0
            if status == "success":
                percent = 1.0
            elif phase == "scan":
                percent = 0.0
            elif total_bytes > 0:
                percent = min(1.0, done_bytes / total_bytes)
            elif total_files > 0:
                percent = min(1.0, done_files / total_files)
            else:
                percent = 0.0
            return {
                "id": self.id,
                "op": self.op,
                "status": status,
                "phase": phase,
                "srcLabel": self.src_label,
                "destLabel": self.dest_label,
                "totalFiles": total_files,
                "doneFiles": done_files,
                "totalBytes": total_bytes,
                "doneBytes": done_bytes,
                "currentName": self.current_name,
                "speed": speed,
                "error": self.error,
                "percent": percent,
            }

    def request_pause(self) -> None:
        with self.lock:
            if self.status in _TERMINAL:
                raise ValueError("任务已结束")
            if self.status == "paused":
                return
            self.status = "paused"
            self.speed = 0.0
        self.pause_event.clear()

    def request_resume(self) -> None:
        with self.lock:
            if self.status in _TERMINAL:
                raise ValueError("任务已结束")
            if self.pause_event.is_set() and self.status != "paused":
                raise ValueError("任务没有暂停")
            self._speed_mark = 0.0
            self._speed_bytes = self.done_bytes
            self.speed = 0.0
            self.status = "scanning" if self.phase == "scan" else "running"
        self.pause_event.set()

    def request_cancel(self) -> None:
        with self.lock:
            if self.status in _TERMINAL:
                raise ValueError("任务已结束")
            self.cancel = True
        self.pause_event.set()

    def _cancelled(self) -> bool:
        with self.lock:
            return self.cancel

    def checkpoint(self) -> None:
        if not self.pause_event.is_set():
            with self.lock:
                if self.status not in _TERMINAL:
                    self.status = "paused"
                    self.speed = 0.0
            while not self.pause_event.is_set():
                if self._cancelled():
                    raise FileOpCancelled()
                self.pause_event.wait(0.05)
            with self.lock:
                if self.status == "paused" and not self.cancel:
                    self.status = "scanning" if self.phase == "scan" else "running"
                self._speed_mark = 0.0
                self._speed_bytes = self.done_bytes
        if self._cancelled():
            raise FileOpCancelled()

    def _set_name(self, name: str) -> None:
        with self.lock:
            self.current_name = name

    def _scan_tick(self, name: str, size: int) -> None:
        with self.lock:
            self.total_files += 1
            self.total_bytes += size
            self.current_name = name

    def _add_bytes(self, count: int, name: str) -> None:
        with self.lock:
            self.done_bytes += count
            self.current_name = name
            now = time.monotonic()
            if self._speed_mark <= 0:
                self._speed_mark = now
                self._speed_bytes = self.done_bytes
            elif now - self._speed_mark >= 0.4:
                delta = now - self._speed_mark
                self.speed = (self.done_bytes - self._speed_bytes) / delta
                self._speed_mark = now
                self._speed_bytes = self.done_bytes

    def _finish_file(self) -> None:
        with self.lock:
            self.done_files += 1

    def _mark(self, status: str, error: str = "") -> None:
        with self.lock:
            self.status = status
            self.error = error
            self.speed = 0.0
            if status == "success":
                self.done_files = self.total_files
                self.done_bytes = self.total_bytes
                self.current_name = ""
                self.phase = "run"

    def _add_dir(self, abs_path: str, rel: str) -> None:
        _check_rel(rel)
        self._set_name(os.path.basename(abs_path.rstrip("\\/")))
        if self.op != "copy":
            self.src_dirs.append(abs_path)
        if self.op != "delete":
            self.dest_rels.append(rel)

    def _scan_one(self, src: str) -> None:
        self.checkpoint()
        if os.path.isfile(src) and not os.path.isdir(src):
            size = os.path.getsize(src)
            rel = os.path.basename(src)
            _check_rel(rel)
            self.files.append((src, rel, size))
            self._scan_tick(rel, size)
            return
        if not os.path.isdir(src):
            raise FileNotFoundError(src)
        parent = os.path.dirname(src.rstrip("\\/"))
        top = os.path.basename(src.rstrip("\\/"))
        self._add_dir(src, top)
        for root, dirnames, filenames in os.walk(src):
            self.checkpoint()
            rel_root = os.path.relpath(root, parent)
            for dirname in dirnames:
                abs_dir = os.path.join(root, dirname)
                rel_dir = os.path.join(rel_root, dirname)
                self._add_dir(abs_dir, rel_dir)
            for filename in filenames:
                self.checkpoint()
                abs_file = os.path.join(root, filename)
                if os.path.isdir(abs_file):
                    raise OSError(f"无法处理目录项: {abs_file}")
                rel_file = os.path.join(rel_root, filename)
                _check_rel(rel_file)
                size = os.path.getsize(abs_file)
                self.files.append((abs_file, rel_file, size))
                self._scan_tick(filename, size)

    def _copy_bytes(self, src: str, target: str, size: int) -> None:
        name = os.path.basename(src)
        self._set_name(name)
        if os.path.lexists(target):
            raise FileExistsError(name)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        copied = 0
        with open(src, "rb") as src_file:
            with open(target, "wb") as dest_file:
                while True:
                    self.checkpoint()
                    buf = src_file.read(CHUNK)
                    if not buf:
                        break
                    dest_file.write(buf)
                    copied += len(buf)
                    self._add_bytes(len(buf), name)
        if copied != size:
            with self.lock:
                self.total_bytes += copied - size
        shutil.copystat(src, target)

    def _ensure_dirs(self) -> None:
        rels = sorted(self.dest_rels, key=lambda rel: rel.count(os.sep))
        for rel in rels:
            self.checkpoint()
            target = _join_target(self.dest_full, rel)
            self._set_name(os.path.basename(rel))
            if os.path.isfile(target) or os.path.islink(target):
                raise FileExistsError(os.path.basename(rel))
            os.makedirs(target, exist_ok=True)

    def _copy_all(self) -> None:
        self._ensure_dirs()
        for src, rel, size in self.files:
            self.checkpoint()
            target = _join_target(self.dest_full, rel)
            self._copy_bytes(src, target, size)
            self._finish_file()

    def _move_all(self) -> None:
        self._ensure_dirs()
        for src, rel, size in self.files:
            self.checkpoint()
            target = _join_target(self.dest_full, rel)
            name = os.path.basename(src)
            if os.path.lexists(target):
                raise FileExistsError(name)
            if same_volume(src, self.dest_full):
                self._set_name(name)
                os.rename(src, target)
                self._add_bytes(size, name)
            else:
                self._copy_bytes(src, target, size)
                _remove_file(src)
            self._finish_file()
        self._remove_source_dirs()

    def _delete_all(self) -> None:
        for src, _rel, size in self.files:
            self.checkpoint()
            name = os.path.basename(src)
            self._set_name(name)
            _remove_file(src)
            self._add_bytes(size, name)
            self._finish_file()
        self._remove_source_dirs()

    def _remove_source_dirs(self) -> None:
        dirs = sorted(self.src_dirs, key=lambda path: path.count(os.sep), reverse=True)
        for path in dirs:
            self.checkpoint()
            self._set_name(os.path.basename(path.rstrip("\\/")))
            if os.path.lexists(path):
                _remove_dir(path)

    def _execute(self) -> None:
        with self.lock:
            self.phase = "scan"
            if self.pause_event.is_set():
                self.status = "scanning"
        self.checkpoint()
        for src in self.sources:
            self._scan_one(src)
        self.checkpoint()
        with self.lock:
            self.phase = "run"
            self._speed_mark = 0.0
            self._speed_bytes = self.done_bytes
            self.speed = 0.0
            if self.pause_event.is_set() and not self.cancel:
                self.status = "running"
        if self.op == "copy":
            self._copy_all()
        elif self.op == "move":
            self._move_all()
        else:
            self._delete_all()

    def run(self) -> None:
        try:
            try:
                self._execute()
                self._mark("success")
            except FileOpCancelled:
                self._mark("cancelled")
            except Exception as exc:
                self._mark("error", f"{type(exc).__name__}: {exc}")
        finally:
            notify_fs_changed(self.changed)


class _Store:
    def __init__(self):
        self.jobs = {}
        self.order = []
        self.lock = threading.Lock()

    def add(self, job: Job) -> None:
        with self.lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
            done = [job_id for job_id in self.order if self.jobs[job_id].status in _TERMINAL]
            extra = len(done) - 40
            if extra > 0:
                for job_id in done[:extra]:
                    self.order.remove(job_id)
                    self.jobs.pop(job_id, None)

    def get(self, job_id: str) -> Job:
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            raise ValueError("任务不存在")
        return job


STORE = _Store()


def create_job(op: str, paths: List[str], dest: str = "") -> Job:
    if op not in ("copy", "move", "delete"):
        raise ValueError("invalid op")
    if not paths:
        raise ValueError("paths is required")
    dest_full = ""
    dest_label = ""
    if op in ("copy", "move"):
        if not dest:
            raise ValueError("dest is required")
        dest_full = resolve(dest)
        if not os.path.isdir(dest_full):
            raise NotADirectoryError(dest)
        dest_label = place_label(dest)
    sources = []
    for path in paths:
        if is_root(path):
            if op == "delete":
                raise ValueError("cannot delete root")
            if op == "move":
                raise ValueError("cannot move root")
            raise ValueError("cannot copy root")
        src = resolve(path)
        if not os.path.exists(src):
            raise FileNotFoundError(path)
        sources.append(src)
    _reject_overlap(sources)
    if op in ("copy", "move"):
        kept = []
        names = []
        for src in sources:
            if os.path.isdir(src):
                _reject_into_self(src, dest_full)
            name = os.path.basename(src.rstrip("\\/"))
            if name in names:
                raise FileExistsError(name)
            names.append(name)
            target = os.path.join(dest_full, name)
            if os.path.lexists(target):
                if op == "move" and os.path.realpath(src) == os.path.realpath(target):
                    continue
                raise FileExistsError(name)
            kept.append(src)
        sources = kept
    parents = []
    changed = []
    for path in paths:
        parent = parent_of(path)
        if parent not in parents:
            parents.append(parent)
            changed.append(parent)
    if dest:
        dest_norm = normalize(dest)
        if dest_norm not in changed:
            changed.append(dest_norm)
    if len(parents) == 1:
        src_label = place_label(parents[0])
    else:
        src_label = "多个位置"
    return Job(op, sources, dest_full, src_label, dest_label, changed)


def start_job(op: str, paths: List[str], dest: str = "") -> dict:
    job = create_job(op, paths, dest)
    STORE.add(job)
    job.start()
    return job.public()


class StartBody(BaseModel):
    op: str
    paths: List[str]
    dest: str = ""


class IdBody(BaseModel):
    id: str


@router.post("/start")
def start_op(body: StartBody):
    return ok(start_job(body.op, body.paths, body.dest))


@router.get("/status")
def status_op(id: str):
    return ok(STORE.get(id).public())


@router.post("/pause")
def pause_op(body: IdBody):
    job = STORE.get(body.id)
    job.request_pause()
    return ok(job.public())


@router.post("/resume")
def resume_op(body: IdBody):
    job = STORE.get(body.id)
    job.request_resume()
    return ok(job.public())


@router.post("/cancel")
def cancel_op(body: IdBody):
    job = STORE.get(body.id)
    job.request_cancel()
    return ok(job.public())
