"""对象存储上传 —— 只服务「上游强制要求公网 URL」的入参那一道。

有些模型的参考素材只能以 http(s) URL 提交,上游自己去拉:seedance 的
`image_url: {url}`、elevenlabs voice-clone 的 `urls` 列表、mureka 的参考音频。
本地文件要当参考素材,就必须先有一个上游取得到的地址,这一步绕不过去。

除此之外不要用这个模块。产物一律用 workspace.save_* 落进工作区,不要转存到对象
存储 —— 那是额外的外部依赖和成本,harness 不该替用户承担;工作区里的文件反而
用户自己就能看见、agent 也能直接读。

这里的 presign 接口和媒体模型是同一个上游、同一把 key,所以对能调这些模型的人
来说它不是额外依赖。但它也仅限于此:出口只有一个 upload_bytes(),给入参用。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx

from gateway.adapters.workspace import ext_for

_HOST = "https://stargate.dreamtechpte.com"
_API_KEY = "yTvpdfMannaAgent8sqw"
_PRESIGN_URL = f"{_HOST}/api/v1/oss/presign-upload"
_DEFAULT_EXPIRE_MIN = 60


def _upload_filename(filename: str, mime_type: str, user_id: str) -> str:
    name = Path(str(filename or "").strip()).name
    ext = ext_for(name, mime_type)
    if name:
        return name if name.endswith(ext) else f"{name}{ext}"
    uid = str(user_id or "").strip() or "anonymous"
    return f"nextagent_{uid}_{uuid.uuid4().hex}{ext}"


def presign_upload(filename: str, content_type: str, expire_min: int = _DEFAULT_EXPIRE_MIN) -> dict[str, str]:
    """→ {upload_url, content_type, public_url, object_key}。

    expire_min 只管 upload_url 这个 PUT 地址的有效期,public_url 不过期。
    """
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            _PRESIGN_URL,
            headers={"X-API-Key": _API_KEY, "X-Trace-Id": str(uuid.uuid4())},
            json={"file_name": filename, "content_type": content_type, "expire_min": int(expire_min)},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"oss presign HTTP {resp.status_code}: {resp.text[:2000]}")
    data = resp.json()
    if isinstance(data, dict) and "code" in data and "data" in data:
        if int(data.get("code") or 0) != 200:
            raise RuntimeError(str(data)[:2000])
        data = data.get("data") or {}
    upload_url = str(data.get("upload_url") or "").strip()
    signed_type = str(data.get("content_type") or "").strip()
    public_url = str(data.get("public_url") or "").strip()
    if not upload_url or not signed_type or not public_url:
        raise RuntimeError(resp.text[:2000])
    return {"upload_url": upload_url, "content_type": signed_type, "public_url": public_url}


def upload_bytes(
    data: bytes,
    mime_type: str = "",
    filename: str = "",
    *,
    user_id: str = "",
    expire_min: int = _DEFAULT_EXPIRE_MIN,
) -> str:
    """把字节传上对象存储,返回上游取得到的公网 URL。只给入参用。"""
    mime = str(mime_type or "").strip() or "application/octet-stream"
    presigned = presign_upload(_upload_filename(filename, mime, user_id), mime, expire_min)
    with httpx.Client(timeout=300.0) as client:
        resp = client.put(
            presigned["upload_url"],
            content=data,
            headers={"Content-Type": presigned["content_type"]},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"oss upload HTTP {resp.status_code}: {resp.text[:2000]}")
    return presigned["public_url"]
