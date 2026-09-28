from __future__ import annotations

import os
import re
import shutil
import subprocess

RG_TIMEOUT_SECONDS = 60
IGNORE_FILE_NAMES = (".cursorignore", ".cursorindexingignore", ".agentignore")
WORKSPACE_PRIVATE_DIRS = ("sessions", ".fm_dl_cache", ".index")
_PER_FILE_ERROR = re.compile(r"^rg: .+: .+\(os error \d+\)\s*$")


def search_base_args(workspace_root: str, search_root: str) -> list[str]:
    args = ["--hidden", "--no-require-git", "--glob", "!.git/**"]
    target = os.path.normcase(os.path.realpath(search_root))
    if target == os.path.normcase(workspace_root):
        for name in WORKSPACE_PRIVATE_DIRS:
            args += ["--glob", f"!{name}/**"]
    else:
        for name in WORKSPACE_PRIVATE_DIRS:
            private = os.path.normcase(os.path.join(workspace_root, name))
            if target == private or target.startswith(private + os.sep):
                raise RuntimeError(f"{name}/ 是 agent 的私有目录,不提供搜索")
    return args + workspace_ignore_args(workspace_root, names=(".cursorignore", ".agentignore"))


def workspace_ignore_args(root: str, *, names: tuple[str, ...] = IGNORE_FILE_NAMES) -> list[str]:
    args: list[str] = []
    for name in names:
        ignore_file = os.path.join(root, name)
        if os.path.isfile(ignore_file):
            args += ["--ignore-file", ignore_file]
    return args


def rg_path() -> str:
    found = shutil.which("rg")
    if not found:
        raise RuntimeError("找不到 ripgrep(rg),请安装 ripgrep 并确保 rg 在 PATH 上")
    return found


def run_rg(args: list[str], *, cwd: str | None = None, timeout: int = RG_TIMEOUT_SECONDS) -> tuple[int, str, str]:
    proc = subprocess.run(
        [rg_path(), *args],
        cwd=cwd,
        capture_output=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode == 2:
        fatal = [line for line in err.splitlines() if line.strip() and not _PER_FILE_ERROR.match(line.strip())]
        if fatal:
            raise RuntimeError("ripgrep 执行失败: " + " | ".join(fatal)[:1000])
        return (0 if out.strip() else 1), out, err
    return proc.returncode, out, err
