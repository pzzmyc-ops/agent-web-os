"""Minimal hermes_constants for mafagent."""
import os
from pathlib import Path


def get_hermes_home() -> Path:
    p = Path(__file__).resolve().parent.parent / "data" / "hermes"
    p.mkdir(parents=True, exist_ok=True)
    return p


def display_hermes_home() -> str:
    return str(get_hermes_home())


def get_real_home(env: dict | None = None) -> str:
    """真实的用户主目录(不是 hermes 的 profile 目录)。

    hermes 原版有一整套 profile / 容器 / HOME 修复策略;mafagent 是单用户跑在宿主机上,
    只需要一个可用的 HOME:优先环境里的 HOME / USERPROFILE,再退回 Path.home()。
    """
    env = env or {}
    for key in ("HOME", "USERPROFILE"):
        val = str(env.get(key) or os.getenv(key, "")).strip()
        if val:
            return val
    try:
        return str(Path.home())
    except Exception:
        return os.getcwd()


def get_subprocess_home(env: dict | None = None) -> str | None:
    """子进程要不要改 HOME:mafagent 只在 HOME 缺失时补一个,其余保持原样。"""
    env = env or {}
    if str(env.get("HOME") or "").strip():
        return None
    return get_real_home(env)


def apply_subprocess_home_env(env: dict) -> None:
    """就地补齐子进程的 HOME / HERMES_REAL_HOME —— terminal 工具每条命令都会调这个。"""
    real_home = get_real_home(env)
    if real_home:
        env.setdefault("HERMES_REAL_HOME", real_home)
    home = get_subprocess_home(env)
    if home:
        env["HOME"] = home
