from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from typing import Any


def _lark_bin() -> str:
    path = shutil.which("lark-cli")
    if not path:
        raise RuntimeError("未检测到飞书 CLI (lark-cli)，请先安装飞书 CLI 后再使用本技能")
    return path


def _lark_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("HERMES_HOME", None)
    return env


def _run(bin_path: str, args: list[str], timeout: int = 60) -> dict[str, Any]:
    completed = subprocess.run(
        [bin_path, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
        env=_lark_env(),
    )
    raw = (completed.stdout or "").strip()
    if completed.returncode != 0:
        err = (completed.stderr or raw or f"exit {completed.returncode}").strip()
        raise RuntimeError(err)
    if not raw:
        raise RuntimeError("lark-cli 无输出")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("lark-cli 返回不是对象")
    return data


def _ok(bin_path: str, args: list[str], timeout: int = 60) -> dict[str, Any]:
    data = _run(bin_path, args, timeout)
    if data.get("ok") is not True:
        raise RuntimeError(json.dumps(data, ensure_ascii=False))
    return data


def _ensure_ready(bin_path: str) -> None:
    data = _run(bin_path, ["auth", "status", "--json"], 30)
    identities = data.get("identities")
    if not isinstance(identities, dict):
        raise RuntimeError("auth status 没有 identities: " + json.dumps(data, ensure_ascii=False))
    bot = identities.get("bot")
    if not isinstance(bot, dict):
        raise RuntimeError("auth status 没有 identities.bot: " + json.dumps(data, ensure_ascii=False))
    if bot.get("available") is not True or bot.get("status") != "ready":
        raise RuntimeError("飞书 CLI 未登录，请先在本机完成 lark-cli 授权")


def _chat_id_by_name(bin_path: str, name: str) -> str:
    data = _ok(
        bin_path,
        ["im", "+chat-search", "--as", "bot", "--query", name, "--format", "json"],
        30,
    )
    payload = data.get("data")
    if not isinstance(payload, dict):
        raise RuntimeError("chat-search 没有 data: " + json.dumps(data, ensure_ascii=False))
    chats = payload.get("chats")
    if not isinstance(chats, list):
        raise RuntimeError("chat-search 没有 chats: " + json.dumps(data, ensure_ascii=False))
    exact: list[str] = []
    for item in chats:
        if not isinstance(item, dict):
            raise RuntimeError("群不是对象: " + json.dumps(item, ensure_ascii=False))
        if str(item.get("name") or "").strip() != name:
            continue
        cid = item.get("chat_id")
        if not isinstance(cid, str) or not cid.strip():
            raise RuntimeError("群没有 chat_id: " + json.dumps(item, ensure_ascii=False))
        exact.append(cid.strip())
    if len(exact) == 0:
        raise RuntimeError("没有找到同名群: " + name)
    if len(exact) > 1:
        raise RuntimeError("群名对应多个群: " + name)
    return exact[0]


def _user_id_by_name(bin_path: str, name: str) -> str:
    data = _ok(
        bin_path,
        ["contact", "+search-user", "--query", name, "--format", "json"],
        30,
    )
    payload = data.get("data")
    if not isinstance(payload, dict):
        raise RuntimeError("search-user 没有 data: " + json.dumps(data, ensure_ascii=False))
    users = payload.get("users")
    if not isinstance(users, list):
        raise RuntimeError("search-user 没有 users: " + json.dumps(data, ensure_ascii=False))
    exact: list[str] = []
    for item in users:
        if not isinstance(item, dict):
            raise RuntimeError("用户不是对象: " + json.dumps(item, ensure_ascii=False))
        if str(item.get("localized_name") or "").strip() != name:
            continue
        oid = str(item.get("open_id") or "").strip()
        if not oid:
            raise RuntimeError("用户没有 open_id: " + json.dumps(item, ensure_ascii=False))
        exact.append(oid)
    if len(exact) == 0:
        raise RuntimeError("没有找到同名用户: " + name)
    if len(exact) > 1:
        raise RuntimeError("姓名对应多人: " + name)
    return exact[0]


def _send(bin_path: str, group: str, content: str) -> dict[str, Any]:
    chat_id = _chat_id_by_name(bin_path, group)
    return _ok(
        bin_path,
        [
            "im",
            "+messages-send",
            "--as",
            "bot",
            "--chat-id",
            chat_id,
            "--markdown",
            content,
            "--format",
            "json",
        ],
        60,
    )


def _dm(bin_path: str, name: str, content: str) -> dict[str, Any]:
    user_id = _user_id_by_name(bin_path, name)
    return _ok(
        bin_path,
        [
            "im",
            "+messages-send",
            "--as",
            "user",
            "--user-id",
            user_id,
            "--markdown",
            content,
            "--format",
            "json",
        ],
        60,
    )


async def execute(services, params):
    del services
    content = str(params.get("content") or "").strip()
    if not content:
        return {"success": False, "error": "content 不能为空"}
    group = str(params.get("group") or "").strip()
    to = str(params.get("to") or "").strip()
    if to:
        target_type, target = "dm", to
    elif group:
        target_type, target = "group", group
    else:
        return {"success": False, "error": "group 或 to 至少填一个"}
    try:
        bin_path = _lark_bin()
        await asyncio.to_thread(_ensure_ready, bin_path)
        if target_type == "group":
            result = await asyncio.to_thread(_send, bin_path, target, content)
        else:
            result = await asyncio.to_thread(_dm, bin_path, target, content)
    except (RuntimeError, json.JSONDecodeError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "content": json.dumps(result, ensure_ascii=False)}


def send_to_group(group: str, content: str) -> dict:
    if not group or not group.strip():
        raise ValueError("group 不能为空")
    if not content or not content.strip():
        raise ValueError("content 不能为空")
    bin_path = _lark_bin()
    _ensure_ready(bin_path)
    data = _send(bin_path, group.strip(), content.strip())
    return {"success": True, "content": json.dumps(data, ensure_ascii=False)}


def send_to_user(name: str, content: str) -> dict:
    if not name or not name.strip():
        raise ValueError("name 不能为空")
    if not content or not content.strip():
        raise ValueError("content 不能为空")
    bin_path = _lark_bin()
    _ensure_ready(bin_path)
    data = _dm(bin_path, name.strip(), content.strip())
    return {"success": True, "content": json.dumps(data, ensure_ascii=False)}


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--group")
    p.add_argument("--to")
    p.add_argument("--content", required=True)
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
