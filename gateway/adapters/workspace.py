"""媒体产物落盘 —— adapter 产出文件的唯一出口。

产物一律写进文件管理器的工作区,返回真实绝对路径(正斜杠)。不要把 base64 放进返回值:
返回值会原样进入调用方(agent)的上下文,一张图的 base64 就是几 MB,一次调用就能
把上下文吃光。也不要转存到对象存储:那是额外的外部依赖和成本,harness 不该替
用户承担。

拿到路径之后,agent 用 file_access_* 读写、用 render_media 在对话里展示,用户
也能在文件管理器里直接看到这个文件 —— 产物从此就是工作区里的一个普通文件。

路径解析走 fm.backend.pathutil,工作区目录取 pathutil.WORKSPACE(来自 config.json 的
fm_root)。返回的绝对路径就是 file_access_* 与 render_media 直接可用的形态。
"""
from __future__ import annotations

import base64
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any

import httpx

from fm.backend.pathutil import WORKSPACE, resolve, to_fs

#: 所有产物都落在工作区的这个目录下,按 adapter 给的 subdir 再分一层。
_MEDIA_DIR = "media"

_DATA_URL_RE = re.compile(r"^data:([^;,]+)?(?:;[^,]*)?;base64,(.+)$", re.DOTALL)

#: mimetypes 对这几种的猜测不稳(image/jpeg 会给 .jpe,audio/mpeg 会给 .mp2),
#: 常见类型直接定死。
_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
}


def is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    return s.startswith("http://") or s.startswith("https://")


def decode_b64(value: str, default_mime: str = "application/octet-stream") -> tuple[bytes, str]:
    """裸 base64 或 data url → (字节, mime)。data url 自带的 mime 优先。"""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("empty base64")
    m = _DATA_URL_RE.match(raw)
    if m:
        return base64.b64decode(m.group(2)), (m.group(1) or "").strip() or default_mime
    return base64.b64decode(raw), default_mime


def fetch_bytes(url: str, *, timeout: float = 600.0) -> tuple[bytes, str, str]:
    """→ (字节, mime, 文件名)。文件名从 URL 末段取,已去掉查询串。

    超时给得宽,因为视频产物动辄几十 MB。
    """
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
    if resp.status_code >= 400:
        raise RuntimeError(f"fetch product HTTP {resp.status_code}: {resp.text[:500]}")
    mime = (resp.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
    name = url.rsplit("/", 1)[-1].split("?", 1)[0].strip()
    return resp.content, mime, name


def ext_for(filename: str, mime_type: str) -> str:
    suffix = Path(str(filename or "")).suffix
    if suffix:
        return suffix
    mime = str(mime_type or "").strip()
    return _MIME_EXT.get(mime) or mimetypes.guess_extension(mime) or ".bin"


def _target(filename: str, mime_type: str, subdir: str) -> tuple[str, Path]:
    """→ (正斜杠绝对路径, 本机 Path)。父目录已建好。

    文件名一律用随机名,只从 filename 里借扩展名 —— 上游给的名字可能重复、带
    中文或带路径分隔符,拿来直接当文件名会互相覆盖。
    """
    parts = [WORKSPACE.rstrip("/"), _MEDIA_DIR]
    sub = str(subdir or "").replace("\\", "/").strip().strip("/")
    if sub:
        parts.append(sub)
    parts.append(f"{uuid.uuid4().hex[:8]}{ext_for(filename, mime_type)}")
    fs = "/".join(parts)
    full = Path(resolve(fs))
    full.parent.mkdir(parents=True, exist_ok=True)
    return to_fs(str(full)), full


def save_bytes(data: bytes, *, mime_type: str = "", filename: str = "", subdir: str = "") -> str:
    """把字节写进工作区,返回绝对路径。"""
    mime = str(mime_type or "").strip() or mimetypes.guess_type(filename or "")[0] or "application/octet-stream"
    rel, full = _target(filename, mime, subdir)
    full.write_bytes(data)
    return rel


def save_b64(value: str, *, default_mime: str = "application/octet-stream", filename: str = "", subdir: str = "") -> str:
    data, mime = decode_b64(value, default_mime)
    return save_bytes(data, mime_type=mime, filename=filename, subdir=subdir)


def save_from_url(url: str, *, filename: str = "", subdir: str = "") -> str:
    """下载上游产物再落盘。

    上游给的链接通常带 Expires / Signature,半小时就失效,不能直接交给调用方 —— 
    等用户回头想看那张图时链接已经死了。
    """
    data, mime, name = fetch_bytes(url)
    return save_bytes(data, mime_type=mime, filename=filename or name, subdir=subdir)


def save_any(
    value: Any,
    *,
    default_mime: str = "application/octet-stream",
    subdir: str = "",
    filename: str = "",
) -> str:
    """把产物的任意形态落成工作区里的文件,返回绝对路径。

    接受:http(s) 链接(下载后落盘)、data url、裸 base64、
    {"base64" 或 "b64_json": ..., "mime_type": ...}。
    """
    if isinstance(value, dict):
        raw = str(value.get("base64") or value.get("b64_json") or "").strip()
        if not raw:
            raise ValueError("dict value must carry base64 or b64_json")
        mime = str(value.get("mime_type") or value.get("mime") or default_mime).strip()
        return save_b64(raw, default_mime=mime, filename=filename, subdir=subdir)

    if not isinstance(value, str):
        raise TypeError("value must be a str or a base64 dict")

    s = value.strip()
    if not s:
        raise ValueError("empty value")
    if is_http_url(s):
        return save_from_url(s, filename=filename, subdir=subdir)
    return save_b64(s, default_mime=default_mime, filename=filename, subdir=subdir)
