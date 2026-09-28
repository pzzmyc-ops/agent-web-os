from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from typing import Any


def _dws_bin() -> str:
    path = shutil.which("dws")
    if not path:
        raise RuntimeError("未检测到钉钉 CLI (dws)，请先安装钉钉 CLI 后再使用本技能")
    return path


def _run(bin_path: str, args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        [bin_path, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    raw = (completed.stdout or "").strip()
    if completed.returncode != 0:
        err = (completed.stderr or raw or f"exit {completed.returncode}").strip()
        raise RuntimeError(err)
    if not raw:
        raise RuntimeError("dws 无输出")
    return json.loads(raw)


def _ensure_ready(bin_path: str) -> None:
    data = _run(bin_path, ["auth", "status", "--format", "json"])
    if not data.get("authenticated"):
        raise RuntimeError("钉钉 CLI 未登录，请先在本机执行 dws auth login 完成授权")


def _send(bin_path: str, group: str, content: str) -> dict[str, Any]:
    data = _run(
        bin_path,
        [
            "chat",
            "+send-to-group",
            "--group",
            group,
            "--content",
            content,
            "--yes",
            "--format",
            "json",
        ],
    )
    if data.get("success") is not True:
        raise RuntimeError(json.dumps(data, ensure_ascii=False))
    return data


def _dm(bin_path: str, name: str, content: str) -> dict[str, Any]:
    data = _run(
        bin_path,
        [
            "chat",
            "+dm",
            "--to",
            name,
            "--content",
            content,
            "--yes",
            "--format",
            "json",
        ],
    )
    if data.get("success") is not True:
        raise RuntimeError(json.dumps(data, ensure_ascii=False))
    return data


async def execute(services, params):
    del services
    content = str(params.get("content") or "").strip()
    if not content:
        return {"success": False, "error": "content 不能为空"}
    group = str(params.get("group") or "").strip()
    to = str(params.get("to") or "").strip()
    is_dm = bool(to)

    if is_dm:
        target_type, target = "dm", to
    elif group:
        target_type, target = "group", group
    else:
        return {"success": False, "error": "group 或 to 至少填一个"}
    try:
        bin_path = _dws_bin()
        await asyncio.to_thread(_ensure_ready, bin_path)
        if target_type == "group":
            result = await asyncio.to_thread(_send, bin_path, target, content)
        else:
            result = await asyncio.to_thread(_dm, bin_path, target, content)
    except (RuntimeError, json.JSONDecodeError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "content": json.dumps(result, ensure_ascii=False)}


def send_to_group(group: str, content: str) -> dict:
    """直接可调用的群发送函数。返回 {"success": True, "content": "<json>"} 或 raise。"""
    if not group or not group.strip():
        raise ValueError("group 不能为空")
    if not content or not content.strip():
        raise ValueError("content 不能为空")
    bin_path = _dws_bin()
    _ensure_ready(bin_path)
    data = _send(bin_path, group.strip(), content.strip())
    return {"success": True, "content": json.dumps(data, ensure_ascii=False)}


def send_to_user(name: str, content: str) -> dict:
    """直接可调用的按姓名单发函数（私聊）。返回 {"success": True, "content": "<json>"} 或 raise。"""
    if not name or not name.strip():
        raise ValueError("name 不能为空")
    if not content or not content.strip():
        raise ValueError("content 不能为空")
    bin_path = _dws_bin()
    _ensure_ready(bin_path)
    data = _dm(bin_path, name.strip(), content.strip())
    return {"success": True, "content": json.dumps(data, ensure_ascii=False)}


def main():
    import argparse
    p = argparse.ArgumentParser(description="钉钉消息（直接调用版）：群发或单发")
    p.add_argument("--group", help="群名（与 --to 二选一）")
    p.add_argument("--to", help="收件人姓名，走单聊（与 --group 二选一）")
    p.add_argument("--content", help="消息内容", required=True)
    a = p.parse_args()
    try:
        if a.to:
            res = send_to_user(a.to, a.content)
        elif a.group:
            res = send_to_group(a.group, a.content)
        else:
            raise ValueError("--group 或 --to 至少提供一个")
    except (RuntimeError, json.JSONDecodeError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print("ERROR:", exc)
        raise SystemExit(1)
    print("OK:", res["content"])


if __name__ == "__main__":
    main()
