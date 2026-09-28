import hashlib
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import settings as config
from .pathutil import resolve, to_fs

router = APIRouter(prefix="/api/onlyoffice")
_OO_WRITES = {}

EXT_MAP = {
    "doc": "word", "docx": "word", "odt": "word", "rtf": "word", "txt": "word",
    "xls": "cell", "xlsx": "cell", "ods": "cell", "csv": "cell",
    "ppt": "slide", "pptx": "slide", "odp": "slide",
}


def require_oo():
    if not config.ONLYOFFICE_URL:
        raise RuntimeError("未配置 config.json 的 onlyoffice_url")
    if not config.PUBLIC_URL:
        raise RuntimeError("未配置 config.json 的 public_url")
    if not config.ONLYOFFICE_JWT:
        raise RuntimeError("未配置 config.json 的 onlyoffice_jwt")


def encode_token(payload: dict, with_exp: bool = True) -> str:
    data = dict(payload)
    if with_exp:
        data["exp"] = datetime.now(timezone.utc) + timedelta(hours=12)
    return jwt.encode(data, config.ONLYOFFICE_JWT, algorithm="HS256")


def decode_jwt(token: str) -> dict:
    return jwt.decode(token, config.ONLYOFFICE_JWT, algorithms=["HS256"])


def file_token(path: str, purpose: str) -> str:
    return encode_token({"path": path, "purpose": purpose}, with_exp=True)


def doc_key(full: str, rel: str) -> str:
    st = os.stat(full)
    raw = f"{rel}:{st.st_mtime_ns}:{st.st_size}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def doc_type(ext: str) -> str:
    t = EXT_MAP.get(ext.lower())
    if not t:
        raise RuntimeError(f"OnlyOffice 不支持的格式: {ext}")
    return t


class ConfigQuery(BaseModel):
    path: str
    mode: str = "edit"


@router.get("/config")
def onlyoffice_config(path: str, mode: str = "edit"):
    require_oo()
    if mode not in ("edit", "view"):
        raise RuntimeError("mode 必须是 edit 或 view")
    full = resolve(path)
    if not os.path.isfile(full):
        raise FileNotFoundError(f"不是文件: {path}")
    rel = to_fs(full)
    name = os.path.basename(full)
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    dtype = doc_type(ext)
    ftok = file_token(rel, "file")
    ctok = file_token(rel, "callback")
    file_url = f"{config.PUBLIC_URL.rstrip('/')}/api/onlyoffice/file?token={quote(ftok)}"
    callback_url = f"{config.PUBLIC_URL.rstrip('/')}/api/onlyoffice/callback?token={quote(ctok)}"
    cfg = {
        "documentType": dtype,
        "document": {
            "title": name,
            "url": file_url,
            "fileType": ext,
            "key": doc_key(full, rel),
            "permissions": {
                "edit": mode == "edit",
                "download": True,
                "print": True,
            },
        },
        "editorConfig": {
            "mode": mode,
            "lang": "zh-CN",
            "callbackUrl": callback_url,
            "user": {
                "id": "fm-user",
                "name": "FM User",
            },
            "customization": {
                "forcesave": True,
                "autosave": True,
            },
        },
        "height": "100%",
        "width": "100%",
    }
    cfg["token"] = encode_token(cfg, with_exp=False)
    return {
        "code": True,
        "timeUse": "0",
        "timeNow": str(time.time()),
        "data": {
            "config": cfg,
            "documentServer": "/onlyoffice-ds",
            "apiJs": "/onlyoffice-ds/web-apps/apps/api/documents/api.js",
        },
        "info": "",
    }


@router.get("/file")
def onlyoffice_file(token: str):
    require_oo()
    data = decode_jwt(token)
    if data.get("purpose") != "file":
        raise RuntimeError("token purpose 无效")
    path = data.get("path")
    if not path:
        raise RuntimeError("token 缺少 path")
    full = resolve(path)
    if not os.path.isfile(full):
        raise FileNotFoundError(f"文件不存在: {path}")
    resp = FileResponse(
        full,
        filename=os.path.basename(full),
        media_type="application/octet-stream",
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@router.post("/callback")
async def onlyoffice_callback(request: Request, token: str):
    require_oo()
    data = decode_jwt(token)
    if data.get("purpose") != "callback":
        raise RuntimeError("token purpose 无效")
    path = data.get("path")
    if not path:
        raise RuntimeError("token 缺少 path")
    body = await request.json()
    status = body.get("status")
    if status in (2, 6):
        url = body.get("url")
        if not url:
            raise RuntimeError("callback 缺少 url")
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise RuntimeError(f"下载编辑结果失败: HTTP {resp.status_code}")
            content = resp.content
        full = resolve(path)
        with open(full, "wb") as f:
            f.write(content)
        st = os.stat(full)
        _OO_WRITES[to_fs(full)] = (st.st_mtime_ns, st.st_size)
    return JSONResponse({"error": 0})


@router.get("/stamp")
def onlyoffice_stamp(path: str):
    full = resolve(path)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
    st = os.stat(full)
    rel = to_fs(full)
    mark = _OO_WRITES.get(rel)
    from_office = bool(mark) and mark[0] == st.st_mtime_ns and mark[1] == st.st_size
    return {
        "code": True,
        "timeUse": "0",
        "timeNow": str(time.time()),
        "data": {
            "size": st.st_size,
            "mtimeNs": st.st_mtime_ns,
            "fromOffice": from_office,
        },
        "info": "",
    }
