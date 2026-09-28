from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import Any, Callable

import psutil

_CREATE_SUSPENDED = 0x00000004


class ListenerJob:
    def __init__(self) -> None:
        self._job: Any = None
        if sys.platform == "win32":
            from ..maf_tools.environments.win_job import JobObject

            self._job = JobObject(kill_on_close=True)

    async def spawn(self, *args: str, env: dict[str, str] | None = None) -> asyncio.subprocess.Process:
        kwargs: dict[str, Any] = {}
        if env is not None:
            kwargs["env"] = env
        if self._job is not None:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | _CREATE_SUSPENDED
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        if self._job is not None:
            try:
                self._job.contain_pid(proc.pid)
            except Exception:
                proc.kill()
                await proc.wait()
                raise
        return proc


async def graceful_stop(proc: asyncio.subprocess.Process, label: str) -> None:
    if proc.returncode is not None:
        return
    if proc.stdin is not None:
        proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
        return
    except asyncio.TimeoutError:
        pass
    proc.kill()
    await asyncio.wait_for(proc.wait(), timeout=10)
    raise RuntimeError(label + " 关闭 stdin 后 10 秒未退出，已强制结束")


def _cmdline(proc: psutil.Process) -> list[str]:
    try:
        return [str(part) for part in (proc.cmdline() or [])]
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return []


def kill_stale(matcher: Callable[[list[str]], bool], label: str) -> list[int]:
    me = os.getpid()
    victims: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid"]):
        if proc.pid == me:
            continue
        argv = _cmdline(proc)
        if not argv:
            continue
        if matcher(argv):
            victims.append(proc)
    for proc in victims:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            continue
    gone, alive = psutil.wait_procs(victims, timeout=10)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            continue
    gone2, alive2 = psutil.wait_procs(alive, timeout=10)
    if alive2:
        raise RuntimeError(label + " 残留进程无法结束: " + ", ".join(str(p.pid) for p in alive2))
    return [p.pid for p in victims]


def kill_pid(pid: int, label: str) -> None:
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    proc.terminate()
    gone, alive = psutil.wait_procs([proc], timeout=10)
    if alive:
        proc.kill()
        gone2, alive2 = psutil.wait_procs(alive, timeout=10)
        if alive2:
            raise RuntimeError(label + f" 进程 {pid} 无法结束")


def is_dws_listener(argv: list[str]) -> bool:
    exe = os.path.basename(argv[0]).lower()
    if not exe.startswith("dws"):
        return False
    if "event" not in argv:
        return False
    return "+listen-im" in argv or "_bus" in argv or "consume" in argv


def is_lark_listener(argv: list[str]) -> bool:
    joined = " ".join(argv).lower()
    if "lark-cli" not in joined:
        return False
    if "event" not in argv:
        return False
    return "_bus" in argv or "consume" in argv
