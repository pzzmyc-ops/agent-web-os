from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from appconfig import load_config

from .api import item_info, ok
from .pathutil import is_computer_root, normalize, resolve, to_fs

TTLS = ("7d", "1m", "3m", "12m", "forever")
_MONTHS = {"1m": 1, "3m": 3, "12m": 12}

TEXT_EXT = {
    ".txt", ".md", ".json", ".jsonl", ".xml", ".html", ".htm", ".css", ".js", ".ts",
    ".py", ".java", ".c", ".cpp", ".h", ".hpp", ".go", ".rs", ".php", ".rb",
    ".sh", ".bat", ".ps1", ".ini", ".conf", ".log", ".csv", ".yml", ".yaml",
    ".toml", ".sql", ".vue", ".jsx", ".tsx",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}

STORE_PATH = Path(load_config().desktop_state_path).with_name("shares.json")
_PAGE_PATH = Path(__file__).resolve().parent.parent / "frontend" / "share.html"
_LOCK = threading.Lock()

_FILE_HEADERS = {
    "Content-Security-Policy": "sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}
_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self'; media-src 'self'; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "frame-src 'self'; base-uri 'none'; form-action 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

router = APIRouter()


class CreateBody(BaseModel):
    path: str
    ttl: str


class RevokeBody(BaseModel):
    id: str


class GuestPaths(BaseModel):
    paths: list[str]


class GuestSearch(BaseModel):
    path: str = ""
    keyword: str


def add_months(when: datetime, months: int) -> datetime:
    index = when.month - 1 + months
    year = when.year + index // 12
    month = index % 12 + 1
    day = min(when.day, calendar.monthrange(year, month)[1])
    return when.replace(year=year, month=month, day=day)


def expiry_at(ttl: str, now: datetime | None = None):
    if ttl not in TTLS:
        raise ValueError("过期时间不正确")
    when = now or datetime.now()
    if ttl == "forever":
        return None
    if ttl == "7d":
        return int((when + timedelta(days=7)).timestamp())
    return int(add_months(when, _MONTHS[ttl]).timestamp())


def _plain(path: str) -> str:
    full = os.path.abspath(path)
    if full.startswith("\\\\?\\"):
        full = full[4:]
    return os.path.normcase(full)


def inside_root(root_real: str, target_real: str) -> bool:
    root = _plain(root_real)
    target = _plain(target_real)
    if target == root:
        return True
    prefix = root if root.endswith(os.sep) else root + os.sep
    return target.startswith(prefix)


def _is_store(real: str) -> bool:
    return _plain(real) == _plain(os.path.realpath(STORE_PATH))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load() -> dict:
    if not STORE_PATH.is_file():
        return {"shares": []}
    data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("shares"), list):
        raise RuntimeError("分享记录格式不对: " + str(STORE_PATH))
    for rec in data["shares"]:
        if not isinstance(rec, dict):
            raise RuntimeError("分享记录格式不对: " + str(STORE_PATH))
    return data


def _save(data: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_name(STORE_PATH.name + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STORE_PATH)


def _folder_key(path: str) -> str:
    norm = normalize(path)
    if is_computer_root(norm):
        raise ValueError("不能分享此电脑")
    full = resolve(norm)
    if not os.path.isdir(full):
        raise ValueError("只能分享文件夹")
    return to_fs(os.path.realpath(full))


def _display_name(fs_path: str) -> str:
    full = resolve(fs_path)
    base = os.path.basename(full.rstrip("\\/"))
    if base:
        return base
    return fs_path.rstrip("/")


def _expired(rec: dict, now: float | None = None) -> bool:
    if "expires_at" not in rec:
        raise RuntimeError("分享记录格式不对: " + str(STORE_PATH))
    exp = rec["expires_at"]
    if exp is None:
        return False
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise RuntimeError("分享记录格式不对: " + str(STORE_PATH))
    return float(exp) <= (time.time() if now is None else now)


def _status(rec: dict, now: float) -> str:
    if rec["expires_at"] is None:
        return "forever"
    if _expired(rec, now):
        return "expired"
    return "active"


def _public(rec: dict, now: float) -> dict:
    for key in ("id", "path", "ttl", "created_at", "expires_at"):
        if key not in rec:
            raise RuntimeError("分享记录格式不对: " + str(STORE_PATH))
    return {
        "id": rec["id"],
        "path": rec["path"],
        "name": _display_name(str(rec["path"])),
        "ttl": rec["ttl"],
        "createdAt": rec["created_at"],
        "expiresAt": rec["expires_at"],
        "status": _status(rec, now),
    }


def create_share(path: str, ttl: str) -> dict:
    expires = expiry_at(ttl)
    fs = _folder_key(path)
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    record = {
        "id": uuid.uuid4().hex,
        "token_hash": _hash(token),
        "path": fs,
        "ttl": ttl,
        "created_at": now,
        "expires_at": expires,
    }
    with _LOCK:
        data = _load()
        data["shares"].append(record)
        _save(data)
    return {
        "id": record["id"],
        "token": token,
        "urlPath": "/s/" + token,
        "expiresAt": expires,
        "ttl": ttl,
        "path": fs,
        "name": _display_name(fs),
        "createdAt": now,
    }


def list_shares(path: str) -> list:
    fs = _folder_key(path)
    with _LOCK:
        data = _load()
    now = time.time()
    matched = [rec for rec in data["shares"] if rec.get("path") == fs]
    matched.reverse()
    return [_public(rec, now) for rec in matched]


def revoke_share(share_id: str) -> None:
    with _LOCK:
        data = _load()
        kept = [rec for rec in data["shares"] if rec.get("id") != share_id]
        if len(kept) == len(data["shares"]):
            raise ValueError("分享不存在")
        data["shares"] = kept
        _save(data)


def require_share(token: str) -> dict:
    if not isinstance(token, str) or not token or "/" in token or "\\" in token:
        raise LookupError("链接无效或已过期")
    with _LOCK:
        data = _load()
    digest = _hash(token)
    found = None
    for rec in data["shares"]:
        stored = rec.get("token_hash")
        if not isinstance(stored, str) or len(stored) != len(digest):
            raise RuntimeError("分享记录格式不对: " + str(STORE_PATH))
        if hmac.compare_digest(stored, digest):
            found = rec
            break
    if found is None or _expired(found):
        raise LookupError("链接无效或已过期")
    if "path" not in found or "ttl" not in found:
        raise RuntimeError("分享记录格式不对: " + str(STORE_PATH))
    full = resolve(str(found["path"]))
    if not os.path.isdir(full):
        raise FileNotFoundError("分享的文件夹不存在")
    return found


def _root_real(record: dict) -> str:
    return os.path.realpath(resolve(str(record["path"])))


def resolve_guest(root_real: str, rel: str) -> str:
    raw = "" if rel is None else str(rel).strip()
    raw = raw.replace("\\", "/")
    if "\x00" in raw:
        raise ValueError("路径超出分享范围")
    if len(raw) >= 2 and raw[1] == ":":
        full = resolve(normalize(raw))
        if not os.path.exists(full):
            raise FileNotFoundError("文件不存在")
        real = os.path.realpath(full)
        if not inside_root(root_real, real):
            raise ValueError("路径超出分享范围")
        if _is_store(real):
            raise FileNotFoundError("文件不存在")
        return real
    if raw.startswith("/"):
        raise ValueError("路径超出分享范围")
    parts = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("路径超出分享范围")
        if part.startswith("."):
            raise ValueError("不能访问隐藏文件")
        parts.append(part)
    target = root_real if not parts else os.path.join(root_real, *parts)
    if not os.path.exists(target):
        raise FileNotFoundError("文件不存在")
    real = os.path.realpath(target)
    if not inside_root(root_real, real):
        raise ValueError("路径超出分享范围")
    if _is_store(real):
        raise FileNotFoundError("文件不存在")
    return real


def _rel_posix(root_real: str, target_real: str) -> str:
    rel = os.path.relpath(target_real, root_real)
    if rel == ".":
        return ""
    return rel.replace("\\", "/")


def preview_kind(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXT:
        return "text"
    return ""


def _visible_real(root_real: str, path: str):
    real = os.path.realpath(path)
    if _is_store(real):
        return None
    if not inside_root(root_real, real):
        raise ValueError("路径超出分享范围")
    return real


def guest_list(record: dict, rel: str) -> dict:
    root_real = _root_real(record)
    target = resolve_guest(root_real, rel)
    if not os.path.isdir(target):
        raise ValueError("这不是文件夹")
    folders = []
    files = []
    try:
        entries = list(os.scandir(target))
    except FileNotFoundError as exc:
        raise FileNotFoundError("文件不存在") from exc
    for entry in entries:
        if entry.name.startswith("."):
            continue
        real = _visible_real(root_real, entry.path)
        if real is None:
            continue
        is_dir = os.path.isdir(real)
        try:
            os.stat(real)
        except FileNotFoundError as exc:
            raise FileNotFoundError("文件不存在") from exc
        item = item_info(real)
        if is_dir:
            folders.append(item)
        else:
            files.append(item)
    folders.sort(key=lambda item: item["name"].lower())
    files.sort(key=lambda item: item["name"].lower())
    if os.path.normcase(_plain(target)) == os.path.normcase(_plain(root_real)):
        name = os.path.basename(target.rstrip("\\/")) or str(record["path"]).rstrip("/")
    else:
        name = os.path.basename(target)
    return ok({
        "current": {"name": name, "path": to_fs(target), "type": "folder"},
        "folderList": folders,
        "fileList": files,
        "pageInfo": {
            "totalNum": len(folders) + len(files),
            "page": 1,
            "pageNum": 99999,
            "pageTotal": 1,
        },
    })


def _read_text(target: str) -> str:
    try:
        with open(target, "r", encoding="utf-8") as handle:
            return handle.read()
    except UnicodeDecodeError as exc:
        raise ValueError("文件不是文本") from exc


def _media_type(name: str) -> str:
    guessed, _encoding = mimetypes.guess_type(name)
    if guessed:
        return guessed
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXT:
        return "image/" + ext.lstrip(".")
    if ext in VIDEO_EXT:
        return "video/" + ext.lstrip(".")
    if ext in AUDIO_EXT:
        return "audio/" + ext.lstrip(".")
    if ext == ".pdf":
        return "application/pdf"
    return "application/octet-stream"


def _file_response(target: str, inline: bool, download_name: str = "", media: str = "") -> FileResponse:
    name = download_name or os.path.basename(target)
    return FileResponse(
        target,
        media_type=media or (_media_type(name) if inline else "application/octet-stream"),
        filename=name,
        content_disposition_type="inline" if inline else "attachment",
        headers=dict(_FILE_HEADERS),
    )


def _add_tree(archive: zipfile.ZipFile, root_real: str, target: str, parent: str) -> None:
    for dirpath, dirnames, filenames in os.walk(target):
        kept = []
        for dirname in dirnames:
            if dirname.startswith("."):
                continue
            real = _visible_real(root_real, os.path.join(dirpath, dirname))
            if real is None:
                continue
            kept.append(dirname)
        dirnames[:] = kept
        for filename in filenames:
            if filename.startswith("."):
                continue
            full = os.path.join(dirpath, filename)
            real = _visible_real(root_real, full)
            if real is None:
                continue
            if not os.path.isfile(real):
                raise ValueError("路径超出分享范围")
            arc = os.path.relpath(full, parent).replace("\\", "/")
            archive.write(real, arcname=arc)


def _write_zip(root_real: str, target: str, dest: str) -> None:
    parent = os.path.dirname(target.rstrip("\\/"))
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _add_tree(archive, root_real, target, parent)


def _zip_many(root_real: str, targets: list[str], dest: str) -> None:
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        names = set()
        for target in targets:
            if os.path.isdir(target):
                parent = os.path.dirname(target.rstrip("\\/"))
                before = set(archive.namelist())
                _add_tree(archive, root_real, target, parent)
                for name in archive.namelist():
                    if name not in before and name in names:
                        raise ValueError("下载项重名")
                    names.add(name)
                continue
            name = os.path.basename(target)
            if name in names:
                raise ValueError("下载项重名")
            names.add(name)
            archive.write(target, arcname=name)


def _zip_response(root_real: str, target: str, background_tasks: BackgroundTasks) -> FileResponse:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = tmp.name
    tmp.close()
    try:
        _write_zip(root_real, target, tmp_path)
    except Exception:
        os.unlink(tmp_path)
        raise
    background_tasks.add_task(os.unlink, tmp_path)
    name = os.path.basename(target.rstrip("\\/")) or "download"
    return _file_response(tmp_path, False, name + ".zip", "application/zip")


def guest_error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"code": False, "data": message, "info": ""}, status_code=status)


def _json(fn):
    try:
        result = fn()
    except LookupError as exc:
        return guest_error(str(exc), 404)
    except FileNotFoundError as exc:
        return guest_error(str(exc), 404)
    except ValueError as exc:
        if type(exc) is not ValueError:
            raise
        return guest_error(str(exc), 400)
    if isinstance(result, JSONResponse) or isinstance(result, FileResponse):
        return result
    return JSONResponse(result)


def _error_html(message: str) -> HTMLResponse:
    safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"/>"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>"
        "<title>分享</title></head><body><p>"
        + safe
        + "</p></body></html>"
    )
    return HTMLResponse(
        body,
        status_code=404,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def _owner(fn):
    try:
        return ok(fn())
    except ValueError as exc:
        if type(exc) is not ValueError:
            raise
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/share/create")
def share_create(body: CreateBody):
    return _owner(lambda: create_share(body.path, body.ttl))


@router.get("/api/share/list")
def share_list_owner(path: str):
    return _owner(lambda: list_shares(path))


@router.post("/api/share/revoke")
def share_revoke(body: RevokeBody):
    def run():
        revoke_share(body.id)
        return True
    return _owner(run)


@router.get("/s/{token}")
def share_page(token: str):
    try:
        require_share(token)
    except LookupError as exc:
        return _error_html(str(exc))
    except FileNotFoundError as exc:
        return _error_html(str(exc))
    return HTMLResponse(_PAGE_PATH.read_text(encoding="utf-8"), headers=dict(_PAGE_HEADERS))


@router.get("/s/{token}/api/meta")
def share_meta(token: str):
    def run():
        record = require_share(token)
        root = _root_real(record)
        name = os.path.basename(root.rstrip("\\/")) or str(record["path"]).rstrip("/")
        return ok({"name": name, "path": to_fs(root), "expiresAt": record["expires_at"], "ttl": record["ttl"]})
    return _json(run)


@router.get("/s/{token}/api/list")
def share_list_guest(token: str, path: str = ""):
    def run():
        record = require_share(token)
        return guest_list(record, path)
    return _json(run)


@router.get("/s/{token}/api/info")
def share_info(token: str, path: str = ""):
    def run():
        record = require_share(token)
        target = resolve_guest(_root_real(record), path)
        return ok(item_info(target))
    return _json(run)


@router.post("/s/{token}/api/info/stat")
def share_stat(token: str, body: GuestPaths):
    def run():
        record = require_share(token)
        root_real = _root_real(record)
        size = 0
        files = 0
        folders = 0
        stack = []
        for path in body.paths:
            target = resolve_guest(root_real, path)
            if os.path.isdir(target):
                stack.append(target)
            else:
                files += 1
                size += os.stat(target).st_size
        while stack:
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except FileNotFoundError as exc:
                raise FileNotFoundError("文件不存在") from exc
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                real = _visible_real(root_real, entry.path)
                if real is None:
                    continue
                if os.path.isdir(real):
                    folders += 1
                    stack.append(real)
                else:
                    files += 1
                    size += os.stat(real).st_size
        return ok({"size": size, "files": files, "folders": folders})
    return _json(run)


@router.post("/s/{token}/api/search")
def share_search(token: str, body: GuestSearch):
    def run():
        record = require_share(token)
        root_real = _root_real(record)
        base = resolve_guest(root_real, body.path)
        if not os.path.isdir(base):
            raise ValueError("这不是文件夹")
        keyword = body.keyword.strip().lower()
        if not keyword:
            raise ValueError("keyword is required")
        folders = []
        files = []
        for dirpath, dirnames, filenames in os.walk(base):
            kept = []
            for dirname in dirnames:
                if dirname.startswith("."):
                    continue
                real = _visible_real(root_real, os.path.join(dirpath, dirname))
                if real is None:
                    continue
                kept.append(dirname)
                if keyword in dirname.lower():
                    folders.append(item_info(real))
            dirnames[:] = kept
            for filename in filenames:
                if filename.startswith("."):
                    continue
                full = os.path.join(dirpath, filename)
                real = _visible_real(root_real, full)
                if real is None:
                    continue
                if not os.path.isfile(real):
                    raise ValueError("路径超出分享范围")
                if keyword in filename.lower():
                    files.append(item_info(real))
        return ok({
            "current": {"name": "搜索", "path": to_fs(base), "type": "folder"},
            "folderList": folders,
            "fileList": files,
            "pageInfo": {
                "totalNum": len(folders) + len(files),
                "page": 1,
                "pageNum": 99999,
                "pageTotal": 1,
            },
        })
    return _json(run)


@router.get("/s/{token}/api/download")
def share_download(token: str, background_tasks: BackgroundTasks, path: str = ""):
    def run():
        record = require_share(token)
        root_real = _root_real(record)
        target = resolve_guest(root_real, path)
        if os.path.isdir(target):
            return _zip_response(root_real, target, background_tasks)
        return _file_response(target, False)
    return _json(run)


@router.post("/s/{token}/api/downloadZip")
def share_download_zip(token: str, body: GuestPaths, background_tasks: BackgroundTasks):
    def run():
        record = require_share(token)
        if not body.paths:
            raise ValueError("paths is required")
        root_real = _root_real(record)
        targets = [resolve_guest(root_real, path) for path in body.paths]
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp_path = tmp.name
        tmp.close()
        try:
            _zip_many(root_real, targets, tmp_path)
        except Exception:
            os.unlink(tmp_path)
            raise
        background_tasks.add_task(os.unlink, tmp_path)
        return _file_response(tmp_path, False, "download.zip", "application/zip")
    return _json(run)


@router.get("/s/{token}/api/preview")
def share_preview(token: str, path: str = ""):
    def run():
        record = require_share(token)
        root_real = _root_real(record)
        target = resolve_guest(root_real, path)
        if os.path.isdir(target):
            raise ValueError("这不是文件")
        kind = preview_kind(os.path.basename(target))
        if kind == "text":
            return ok({
                "type": "text",
                "content": _read_text(target),
                "path": to_fs(target),
                "name": os.path.basename(target),
            })
        if not kind:
            raise ValueError("这个文件不能预览")
        return _file_response(target, True)
    return _json(run)


@router.get("/s/{token}/api/media")
def share_media(token: str, path: str, v: str):
    def run():
        record = require_share(token)
        target = resolve_guest(_root_real(record), path)
        if os.path.isdir(target):
            raise ValueError("这不是文件")
        st = os.stat(target)
        current = f"{int(st.st_mtime)}-{st.st_size}"
        if v != current:
            raise ValueError(f"media version mismatch: requested {v}, current {current}")
        return _file_response(target, True)
    return _json(run)


@router.get("/s/{token}/api/thumbnail")
def share_thumbnail(token: str, path: str):
    def run():
        from .api import thumbnail

        record = require_share(token)
        target = resolve_guest(_root_real(record), path)
        if os.path.isdir(target):
            raise ValueError("这不是文件")
        return thumbnail(to_fs(target))
    return _json(run)


@router.get("/s/{token}/api/browse/{file_path:path}")
def share_browse(token: str, file_path: str):
    def run():
        record = require_share(token)
        raw = file_path.replace("\\", "/")
        target = resolve_guest(_root_real(record), raw)
        if os.path.isdir(target):
            raise ValueError("这不是文件")
        return _file_response(target, True)
    return _json(run)
