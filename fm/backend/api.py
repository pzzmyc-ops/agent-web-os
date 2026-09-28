import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .desktop import notify_fs_changed
from .pathutil import IS_WINDOWS, WORKSPACE, is_computer_root, is_root, list_roots, parent_of, resolve, to_fs
from .settings import DESKTOP_PATH

router = APIRouter(prefix="/api")

TEXT_EXT = {
    ".txt", ".md", ".json", ".xml", ".html", ".htm", ".css", ".js", ".ts",
    ".py", ".java", ".c", ".cpp", ".h", ".hpp", ".go", ".rs", ".php", ".rb",
    ".sh", ".bat", ".ps1", ".ini", ".conf", ".log", ".csv", ".yml", ".yaml",
    ".toml", ".sql", ".vue", ".jsx", ".tsx",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv"}
_ARCHIVE_SUFFIXES = (
    ".tar.gz", ".tar.bz2", ".tar.xz",
    ".tgz", ".tbz2", ".txz",
    ".zip", ".7z", ".rar", ".tar",
    ".gz", ".bz2", ".xz",
)
_TAR_WRAP = {".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"}
_THUMBNAIL_DIR = os.path.join(tempfile.gettempdir(), "mafagent-fm-thumbnails")


def _archive_suffix(name: str) -> str:
    lower = name.lower()
    for suf in _ARCHIVE_SUFFIXES:
        if lower.endswith(suf):
            return suf
    return ""


def _archive_stem(name: str) -> str:
    suf = _archive_suffix(name)
    if not suf:
        raise ValueError("not an archive")
    return name[:-len(suf)]


def _reg_7z_folder() -> str:
    import winreg
    accesses = [winreg.KEY_READ]
    if hasattr(winreg, "KEY_WOW64_64KEY"):
        accesses = [winreg.KEY_READ | winreg.KEY_WOW64_64KEY, winreg.KEY_READ | winreg.KEY_WOW64_32KEY]
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for access in accesses:
            try:
                key = winreg.OpenKey(hive, r"SOFTWARE\7-Zip", 0, access)
            except OSError:
                continue
            try:
                for name in ("Path64", "Path"):
                    try:
                        val, _typ = winreg.QueryValueEx(key, name)
                    except OSError:
                        continue
                    folder = str(val).strip()
                    if folder:
                        return folder
            finally:
                winreg.CloseKey(key)
    return ""


def _7z_cmd() -> str:
    if shutil.which("7z"):
        return "7z"
    if not IS_WINDOWS:
        return ""
    folder = _reg_7z_folder()
    if not folder:
        return ""
    exe = os.path.join(folder, "7z.exe")
    if not os.path.isfile(exe):
        return ""
    return exe


def _has_7z() -> bool:
    return bool(_7z_cmd())


_PCT_RE = re.compile(r"^\s*(\d{1,3})\s*%")


def _7z_popen_kwargs() -> dict:
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _iter_7z_output(proc):
    buf = b""
    while True:
        chunk = proc.stdout.read(64)
        if not chunk:
            break
        buf += chunk
        while True:
            i = -1
            for sep in (b"\r", b"\n"):
                j = buf.find(sep)
                if j >= 0 and (i < 0 or j < i):
                    i = j
            if i < 0:
                break
            yield buf[:i].decode("utf-8", "replace")
            buf = buf[i + 1:]
    if buf:
        yield buf.decode("utf-8", "replace")


def _check_member(m: str) -> str:
    s = str(m).replace("\\", "/").strip()
    if not s or s.startswith("/") or ".." in s.split("/"):
        raise ValueError("unsafe archive member")
    if ":" in s.split("/")[0]:
        raise ValueError("unsafe archive member")
    return s


def _stream_7z_extract(archive: str, out_dir: str, members: Optional[List[str]] = None, mode: str = "x"):
    exe = _7z_cmd()
    if not exe:
        raise RuntimeError("7z is not installed")
    if mode not in ("x", "e"):
        raise ValueError("invalid 7z mode")
    cmd = [exe, mode, archive, "-o" + out_dir, "-y", "-bsp1", "-bse1", "-bb1", "-sccUTF-8"]
    if members:
        cmd.append("--")
        cmd.extend(members)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **_7z_popen_kwargs(),
    )
    percent = 0
    current = ""
    rc = -1
    try:
        for line in _iter_7z_output(proc):
            s = line.strip()
            if not s:
                continue
            m = _PCT_RE.match(s)
            if m:
                percent = int(m.group(1))
                yield {"type": "progress", "percent": percent, "file": current}
                continue
            if len(s) > 2 and s[0] in "+-U" and s[1] == " ":
                current = s[2:].strip()
                yield {"type": "progress", "percent": percent, "file": current}
        rc = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if rc != 0:
        raise RuntimeError(f"7z exited {rc}")


def _run_7z_extract(archive: str, out_dir: str):
    for _ev in _stream_7z_extract(archive, out_dir):
        pass


def _parse_7z_slt(text: str) -> list:
    parts = text.split("----------", 1)
    if len(parts) < 2:
        raise RuntimeError("7z list output missing entries")
    entries = []
    current = {}

    def flush():
        path = str(current.get("Path") or "").replace("\\", "/").strip()
        if not path:
            return
        folder = current.get("Folder", "-") == "+" or path.endswith("/")
        path = path.rstrip("/")
        size_raw = current.get("Size") or "0"
        packed_raw = current.get("Packed Size") or "0"
        entries.append({
            "path": path,
            "name": os.path.basename(path) or path,
            "dir": folder,
            "size": int(size_raw) if str(size_raw).isdigit() else 0,
            "packedSize": int(packed_raw) if str(packed_raw).isdigit() else 0,
            "modified": current.get("Modified") or "",
        })

    for line in parts[1].splitlines():
        raw = line.strip()
        if not raw:
            flush()
            current = {}
            continue
        if " = " not in raw:
            continue
        key, val = raw.split(" = ", 1)
        current[key] = val
    flush()
    return entries


def _list_archive(src: str) -> list:
    exe = _7z_cmd()
    if not exe:
        raise RuntimeError("7z is not installed")
    proc = subprocess.run(
        [exe, "l", "-slt", "-sccUTF-8", src],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_7z_popen_kwargs(),
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(msg or f"7z exited {proc.returncode}")
    return _parse_7z_slt(proc.stdout)


def _iter_extract_archive(src: str, out_dir: str, members: Optional[List[str]] = None, mode: str = "x"):
    suf = _archive_suffix(os.path.basename(src))
    if suf in _TAR_WRAP:
        yield from _stream_7z_extract(src, out_dir)
        tars = [
            n for n in os.listdir(out_dir)
            if n.lower().endswith(".tar") and os.path.isfile(os.path.join(out_dir, n))
        ]
        if len(tars) != 1:
            return
        tar_path = os.path.join(out_dir, tars[0])
        yield from _stream_7z_extract(tar_path, out_dir, members, mode)
        os.remove(tar_path)
        return
    yield from _stream_7z_extract(src, out_dir, members, mode)


def _extract_archive(src: str, out_dir: str):
    for _ev in _iter_extract_archive(src, out_dir):
        pass


def ok(data=None, info=""):
    return {
        "code": True,
        "timeUse": "0",
        "timeNow": str(time.time()),
        "data": data if data is not None else "",
        "info": info,
    }


def fail(msg: str):
    return {
        "code": False,
        "timeUse": "0",
        "timeNow": str(time.time()),
        "data": msg,
        "info": "",
    }


_NAT = re.compile(r"(\d+)")


def natural_key(name: str):
    return [int(p) if p.isdigit() else p for p in _NAT.split(name.lower())]


def item_info(full: str) -> dict:
    st = os.stat(full)
    rel = to_fs(full)
    name = os.path.basename(full) or rel.rstrip("/")
    is_dir = os.path.isdir(full)
    ext = "" if is_dir else os.path.splitext(name)[1].lower()
    return {
        "name": name,
        "path": rel,
        "type": "folder" if is_dir else "file",
        "size": 0 if is_dir else st.st_size,
        "ext": ext.lstrip("."),
        "modifyTime": int(st.st_mtime),
        "createTime": int(st.st_ctime),
        "isParent": is_dir,
    }


class PathBody(BaseModel):
    path: str


class MkBody(BaseModel):
    path: str
    name: str


class MkFileBody(BaseModel):
    path: str
    name: str
    content: str = ""


class RenameBody(BaseModel):
    path: str
    newName: str


class PathsBody(BaseModel):
    paths: List[str]
    dest: Optional[str] = None


class SearchBody(BaseModel):
    path: str
    keyword: str


class SaveBody(BaseModel):
    path: str
    content: str


class ZipBody(BaseModel):
    paths: List[str]
    dest: str
    name: str


class UnzipBody(BaseModel):
    path: str
    dest: Optional[str] = None
    members: Optional[List[str]] = None


class ArchiveOpenBody(BaseModel):
    path: str
    member: str


class StatBody(BaseModel):
    paths: List[str]


@router.get("/embedded-apps")
def embedded_apps(request: Request):
    fn = getattr(request.app.state, "list_embedded", None)
    if fn is None:
        raise RuntimeError("嵌入程序列表未就绪")
    apps = fn()
    if not isinstance(apps, list):
        raise RuntimeError("嵌入程序列表格式不对")
    return ok({"apps": apps})


def _page(folder_list: list, file_list: list) -> dict:
    return {
        "totalNum": len(folder_list) + len(file_list),
        "page": 1,
        "pageNum": 99999,
        "pageTotal": 1,
    }


def _root_item(root: str) -> dict:
    st = os.stat(resolve(root))
    return {
        "name": root.rstrip("/"),
        "path": root,
        "type": "folder",
        "size": 0,
        "ext": "",
        "modifyTime": int(st.st_mtime),
        "createTime": int(st.st_ctime),
        "isParent": True,
    }


@router.get("/list")
def list_path(path: str = "/"):
    if is_computer_root(path):
        drives = [_root_item(r) for r in list_roots()]
        return ok({
            "current": {"name": "此电脑", "path": "/", "type": "folder"},
            "folderList": drives,
            "fileList": [],
            "pageInfo": _page(drives, []),
        })
    full = resolve(path)
    if not os.path.isdir(full):
        raise FileNotFoundError(f"not a directory: {path}")
    folder_list = []
    file_list = []
    with os.scandir(full) as it:
        for entry in it:
            if entry.name.startswith("."):
                continue
            info = item_info(entry.path)
            if entry.is_dir(follow_symlinks=False):
                folder_list.append(info)
            else:
                file_list.append(info)
    folder_list.sort(key=lambda x: natural_key(x["name"]))
    file_list.sort(key=lambda x: natural_key(x["name"]))
    rel = to_fs(full)
    current = {
        "name": os.path.basename(full) or rel.rstrip("/"),
        "path": rel,
        "type": "folder",
    }
    return ok({
        "current": current,
        "folderList": folder_list,
        "fileList": file_list,
        "pageInfo": _page(folder_list, file_list),
    })


@router.get("/info")
def path_info(path: str):
    full = resolve(path)
    if not os.path.exists(full):
        raise FileNotFoundError(path)
    return ok(item_info(full))


@router.post("/info/stat")
def path_stat(body: StatBody):
    size = 0
    files = 0
    folders = 0
    stack = []
    for path in body.paths:
        full = resolve(path)
        if not os.path.exists(full):
            raise FileNotFoundError(path)
        if os.path.isdir(full):
            stack.append(full)
        else:
            files += 1
            size += os.stat(full).st_size
    while stack:
        with os.scandir(stack.pop()) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    folders += 1
                    stack.append(entry.path)
                else:
                    files += 1
                    size += entry.stat(follow_symlinks=False).st_size
    return ok({"size": size, "files": files, "folders": folders})


@router.post("/mkdir")
def mkdir(body: MkBody):
    parent = resolve(body.path)
    if not os.path.isdir(parent):
        raise NotADirectoryError(body.path)
    name = body.name.strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError("invalid name")
    target = os.path.join(parent, name)
    if os.path.exists(target):
        raise FileExistsError(name)
    os.mkdir(target)
    notify_fs_changed([to_fs(parent)])
    return ok(item_info(target), to_fs(target))


@router.post("/mkfile")
def mkfile(body: MkFileBody):
    parent = resolve(body.path)
    if not os.path.isdir(parent):
        raise NotADirectoryError(body.path)
    name = body.name.strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError("invalid name")
    target = os.path.join(parent, name)
    if os.path.exists(target):
        raise FileExistsError(name)
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(body.content)
    notify_fs_changed([to_fs(parent)])
    return ok(item_info(target), to_fs(target))


@router.post("/rename")
def rename(body: RenameBody):
    full = resolve(body.path)
    if not os.path.exists(full):
        raise FileNotFoundError(body.path)
    if is_root(body.path):
        raise ValueError("cannot rename root")
    new_name = body.newName.strip()
    if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
        raise ValueError("invalid name")
    target = os.path.join(os.path.dirname(full), new_name)
    if os.path.exists(target):
        raise FileExistsError(new_name)
    os.rename(full, target)
    notify_fs_changed([parent_of(to_fs(target))])
    return ok(item_info(target), to_fs(target))


@router.post("/delete")
def delete(body: PathsBody):
    removed = []
    for p in body.paths:
        if is_root(p):
            raise ValueError("cannot delete root")
        full = resolve(p)
        if not os.path.exists(full):
            raise FileNotFoundError(p)
        if os.path.isdir(full):
            shutil.rmtree(full)
        else:
            os.remove(full)
        removed.append(to_fs(full))
    notify_fs_changed(sorted({parent_of(p) for p in removed}))
    return ok(removed)


@router.post("/copy")
def copy_paths(body: PathsBody):
    if body.dest is None:
        raise ValueError("dest is required")
    dest_dir = resolve(body.dest)
    if not os.path.isdir(dest_dir):
        raise NotADirectoryError(body.dest)
    results = []
    for p in body.paths:
        src = resolve(p)
        if not os.path.exists(src):
            raise FileNotFoundError(p)
        name = os.path.basename(src)
        target = os.path.join(dest_dir, name)
        if os.path.exists(target):
            raise FileExistsError(name)
        if os.path.isdir(src):
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)
        results.append(item_info(target))
    notify_fs_changed([to_fs(dest_dir)])
    return ok(results)


@router.post("/move")
def move_paths(body: PathsBody):
    if body.dest is None:
        raise ValueError("dest is required")
    dest_dir = resolve(body.dest)
    if not os.path.isdir(dest_dir):
        raise NotADirectoryError(body.dest)
    results = []
    for p in body.paths:
        if is_root(p):
            raise ValueError("cannot move root")
        src = resolve(p)
        if not os.path.exists(src):
            raise FileNotFoundError(p)
        name = os.path.basename(src)
        target = os.path.join(dest_dir, name)
        if os.path.realpath(src) == os.path.realpath(target):
            continue
        if os.path.exists(target):
            raise FileExistsError(name)
        if os.path.isdir(src):
            dest_real = os.path.realpath(dest_dir)
            src_real = os.path.realpath(src)
            root = src_real if src_real.endswith(os.sep) else src_real + os.sep
            if dest_real == src_real or dest_real.startswith(root):
                raise ValueError("cannot move folder into itself")
        shutil.move(src, target)
        results.append(item_info(target))
    notify_fs_changed(sorted({parent_of(p) for p in body.paths} | {to_fs(dest_dir)}))
    return ok(results)


@router.post("/upload")
async def upload(
    path: str = Form(...),
    file: UploadFile = File(...),
    relativePath: str = Form(""),
    fileRepeat: str = Form("replace"),
):
    parent = resolve(path)
    if not os.path.isdir(parent):
        raise NotADirectoryError(path)
    rel = (relativePath or "").replace("\\", "/").strip("/")
    parts = [p for p in rel.split("/") if p]
    for p in parts:
        if p in (".", ".."):
            raise ValueError("invalid relativePath")
    if parts:
        name = parts[-1]
        dir_parts = parts[:-1]
    else:
        name = os.path.basename(file.filename or "")
        dir_parts = []
    if not name:
        raise ValueError("empty filename")
    target_dir = os.path.join(parent, *dir_parts) if dir_parts else parent
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, name)
    if os.path.exists(target):
        if fileRepeat == "skip":
            return ok(item_info(target), to_fs(target))
        if fileRepeat != "replace":
            raise FileExistsError(name)
    with open(target, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    notify_fs_changed([to_fs(target_dir)])
    return ok(item_info(target), to_fs(target))


@router.get("/download")
def download(path: str):
    full = resolve(path)
    if not os.path.isfile(full):
        raise FileNotFoundError(path)
    return FileResponse(full, filename=os.path.basename(full), media_type="application/octet-stream")


@router.post("/downloadZip")
def download_zip(body: PathsBody, background_tasks: BackgroundTasks):
    if not body.paths:
        raise ValueError("paths is required")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = tmp.name
    tmp.close()
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in body.paths:
            src = resolve(p)
            if not os.path.exists(src):
                raise FileNotFoundError(p)
            if os.path.isfile(src):
                zf.write(src, arcname=os.path.basename(src))
            else:
                base = os.path.dirname(src)
                for root, _, files in os.walk(src):
                    for f in files:
                        fp = os.path.join(root, f)
                        zf.write(fp, arcname=os.path.relpath(fp, base))
    background_tasks.add_task(os.unlink, tmp_path)
    return FileResponse(tmp_path, filename="download.zip", media_type="application/zip")


@router.get("/browse/{file_path:path}")
def browse(file_path: str):
    raw = file_path.replace("\\", "/")
    if not IS_WINDOWS and not raw.startswith("/"):
        raw = "/" + raw
    full = resolve(raw)
    if not os.path.isfile(full):
        raise FileNotFoundError(raw)
    name = os.path.basename(full)
    ext = os.path.splitext(name)[1].lower()
    media_type, _ = mimetypes.guess_type(name)
    if ext in (".html", ".htm"):
        media_type = "text/html"
    return FileResponse(
        full,
        media_type=media_type,
        filename=name,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/media")
def media(path: str, v: str):
    full = resolve(path)
    if not os.path.isfile(full):
        raise FileNotFoundError(path)
    st = os.stat(full)
    current = f"{int(st.st_mtime)}-{st.st_size}"
    if v != current:
        raise ValueError(f"media version mismatch: requested {v}, current {current}")
    return FileResponse(
        full,
        filename=os.path.basename(full),
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


def _render_thumbnail(source: str, target: str):
    subprocess.run([
        "ffmpeg",
        "-loglevel", "error",
        "-y",
        "-i", source,
        "-map", "0:v:0",
        "-frames:v", "1",
        "-vf", "scale=256:256:force_original_aspect_ratio=decrease",
        target,
    ], check=True)


@router.get("/thumbnail")
def thumbnail(path: str):
    full = resolve(path)
    if not os.path.isfile(full):
        raise FileNotFoundError(path)
    ext = os.path.splitext(full)[1].lower()
    if ext not in IMAGE_EXT and ext not in VIDEO_EXT:
        raise ValueError(f"unsupported thumbnail type: {ext}")
    if ext == ".svg":
        return FileResponse(full, media_type="image/svg+xml")
    stat = os.stat(full)
    identity = f"{os.path.realpath(full)}|{stat.st_mtime_ns}|{stat.st_size}"
    cache_name = hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".png"
    os.makedirs(_THUMBNAIL_DIR, exist_ok=True)
    target = os.path.join(_THUMBNAIL_DIR, cache_name)
    if not os.path.isfile(target):
        descriptor, pending = tempfile.mkstemp(dir=_THUMBNAIL_DIR, suffix=".png")
        os.close(descriptor)
        try:
            _render_thumbnail(full, pending)
            os.replace(pending, target)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
    return FileResponse(target, media_type="image/png", headers={"Cache-Control": "no-cache"})


@router.get("/preview")
def preview(path: str):
    full = resolve(path)
    if not os.path.isfile(full):
        raise FileNotFoundError(path)
    ext = os.path.splitext(full)[1].lower()
    if ext in IMAGE_EXT:
        return FileResponse(full)
    if ext in TEXT_EXT:
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
        return ok({"type": "text", "content": content, "path": to_fs(full), "name": os.path.basename(full)})
    raise ValueError(f"unsupported preview type: {ext}")


@router.post("/save")
def save_file(body: SaveBody):
    full = resolve(body.path)
    if not os.path.isfile(full):
        raise FileNotFoundError(body.path)
    with open(full, "w", encoding="utf-8", newline="") as f:
        f.write(body.content)
    notify_fs_changed([to_fs(full)])
    return ok(item_info(full))


@router.post("/search")
def search(body: SearchBody):
    base = resolve(body.path)
    if not os.path.isdir(base):
        raise NotADirectoryError(body.path)
    keyword = body.keyword.strip().lower()
    if not keyword:
        raise ValueError("keyword is required")
    folder_list = []
    file_list = []
    for root, dirs, files in os.walk(base):
        for d in dirs:
            if keyword in d.lower():
                folder_list.append(item_info(os.path.join(root, d)))
        for f in files:
            if keyword in f.lower():
                file_list.append(item_info(os.path.join(root, f)))
    return ok({
        "current": {"name": "搜索", "path": to_fs(base), "type": "folder"},
        "folderList": folder_list,
        "fileList": file_list,
        "pageInfo": _page(folder_list, file_list),
    })


@router.post("/zip")
def zip_paths(body: ZipBody):
    dest_dir = resolve(body.dest)
    if not os.path.isdir(dest_dir):
        raise NotADirectoryError(body.dest)
    name = body.name.strip()
    if not name.endswith(".zip"):
        name += ".zip"
    if "/" in name or "\\" in name:
        raise ValueError("invalid name")
    target = os.path.join(dest_dir, name)
    if os.path.exists(target):
        raise FileExistsError(name)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in body.paths:
            src = resolve(p)
            if not os.path.exists(src):
                raise FileNotFoundError(p)
            if os.path.isfile(src):
                zf.write(src, arcname=os.path.basename(src))
            else:
                base = os.path.dirname(src)
                for root, _, files in os.walk(src):
                    for f in files:
                        fp = os.path.join(root, f)
                        arc = os.path.relpath(fp, base)
                        zf.write(fp, arcname=arc)
    notify_fs_changed([to_fs(dest_dir)])
    return ok(item_info(target), to_fs(target))


@router.post("/unzip")
def unzip_path(body: UnzipBody):
    src = resolve(body.path)
    if not os.path.isfile(src):
        raise FileNotFoundError(body.path)
    name = os.path.basename(src)
    if not _archive_suffix(name):
        raise ValueError("not an archive")
    if not _has_7z():
        raise RuntimeError("7z is not installed")
    dest_rel = body.dest if body.dest is not None else parent_of(body.path)
    dest_dir = resolve(dest_rel)
    if not os.path.isdir(dest_dir):
        raise NotADirectoryError(dest_rel)
    folder_name = _archive_stem(name)
    out_dir = os.path.join(dest_dir, folder_name)
    if os.path.exists(out_dir):
        raise FileExistsError(folder_name)
    os.mkdir(out_dir)
    try:
        _extract_archive(src, out_dir)
    except Exception:
        shutil.rmtree(out_dir)
        raise
    notify_fs_changed([to_fs(dest_dir)])
    return ok(item_info(out_dir), to_fs(out_dir))


@router.post("/archive/list")
def archive_list(body: PathBody):
    src = resolve(body.path)
    if not os.path.isfile(src):
        raise FileNotFoundError(body.path)
    name = os.path.basename(src)
    if not _archive_suffix(name):
        raise ValueError("not an archive")
    if not _has_7z():
        raise RuntimeError("7z is not installed")
    entries = _list_archive(src)
    return ok({"entries": entries, "total": len(entries)})


@router.post("/archive/extract")
def archive_extract(body: UnzipBody):
    src = resolve(body.path)
    if not os.path.isfile(src):
        raise FileNotFoundError(body.path)
    name = os.path.basename(src)
    if not _archive_suffix(name):
        raise ValueError("not an archive")
    if not _has_7z():
        raise RuntimeError("7z is not installed")
    members = [_check_member(m) for m in (body.members or [])]
    dest_rel = body.dest if body.dest is not None else parent_of(body.path)
    dest_dir = resolve(dest_rel)
    if not os.path.isdir(dest_dir):
        raise NotADirectoryError(dest_rel)
    folder_name = _archive_stem(name)
    out_dir = os.path.join(dest_dir, folder_name)
    created = False
    if os.path.exists(out_dir):
        if not members or not os.path.isdir(out_dir):
            raise FileExistsError(folder_name)
    else:
        os.mkdir(out_dir)
        created = True
    out_fs = to_fs(out_dir)

    def gen():
        finished = False
        try:
            yield json.dumps({"type": "start", "out": out_fs}, ensure_ascii=False) + "\n"
            for ev in _iter_extract_archive(src, out_dir, members or None):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
            yield json.dumps({"type": "progress", "percent": 100, "file": ""}, ensure_ascii=False) + "\n"
            finished = True
            notify_fs_changed([to_fs(dest_dir)])
            yield json.dumps({"type": "done", "out": out_fs}, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps(
                {"type": "error", "message": f"{type(e).__name__}: {e}"},
                ensure_ascii=False,
            ) + "\n"
        finally:
            if not finished and created and os.path.isdir(out_dir):
                shutil.rmtree(out_dir)

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.post("/archive/open")
def archive_open(body: ArchiveOpenBody):
    src = resolve(body.path)
    if not os.path.isfile(src):
        raise FileNotFoundError(body.path)
    name = os.path.basename(src)
    if not _archive_suffix(name):
        raise ValueError("not an archive")
    if not _has_7z():
        raise RuntimeError("7z is not installed")
    member = _check_member(body.member)
    tmp = tempfile.mkdtemp(prefix="fm-arc-open-")

    def gen():
        finished = False
        try:
            yield json.dumps({"type": "start", "out": to_fs(tmp)}, ensure_ascii=False) + "\n"
            for ev in _iter_extract_archive(src, tmp, [member], "e"):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
            files = [f for f in os.listdir(tmp) if os.path.isfile(os.path.join(tmp, f))]
            if len(files) != 1:
                raise RuntimeError("open extract did not produce one file")
            info = item_info(os.path.join(tmp, files[0]))
            yield json.dumps({"type": "progress", "percent": 100, "file": ""}, ensure_ascii=False) + "\n"
            finished = True
            yield json.dumps({"type": "done", "out": info["path"], "item": info}, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps(
                {"type": "error", "message": f"{type(e).__name__}: {e}"},
                ensure_ascii=False,
            ) + "\n"
        finally:
            if not finished and os.path.isdir(tmp):
                shutil.rmtree(tmp)

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.get("/root")
def root_info():
    return ok({
        "path": "/",
        "name": "此电脑",
        "type": "folder",
        "workspace": WORKSPACE,
        "desktop": to_fs(DESKTOP_PATH),
        "roots": list_roots(),
        "has7z": _has_7z(),
    })
