import ctypes
import os
import posixpath
import re
import sys

from .settings import ROOT

IS_WINDOWS = sys.platform == "win32"

_DRIVE = re.compile(r"^([A-Za-z]):(?:[/\\](.*))?$", re.DOTALL)
_DRIVE_NO_ROOT = 1


def _clean_tail(tail: str) -> str:
    tail = posixpath.normpath("/" + tail.replace("\\", "/"))
    return "" if tail == "/" else tail.lstrip("/")


def normalize(path: str) -> str:
    if path is None:
        raise ValueError("path is required")
    raw = str(path).strip()
    if not raw:
        raise ValueError("path is required")
    raw = raw.replace("\\", "/")
    m = _DRIVE.match(raw)
    if m:
        if not IS_WINDOWS:
            raise ValueError(f"drive letters are not valid on this platform: {path!r}")
        letter = m.group(1).upper()
        tail = _clean_tail(m.group(2) or "")
        return f"{letter}:/{tail}" if tail else f"{letter}:/"
    if not raw.startswith("/"):
        raise ValueError(f"path must be absolute: {path!r}")
    tail = _clean_tail(raw)
    if IS_WINDOWS:
        if tail:
            raise ValueError(f"Windows paths must start with a drive letter: {path!r}")
        return "/"
    return "/" + tail


def is_computer_root(path: str) -> bool:
    return IS_WINDOWS and normalize(path) == "/"


def list_roots() -> list[str]:
    if not IS_WINDOWS:
        return ["/"]
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    if mask == 0:
        raise OSError("GetLogicalDrives 失败")
    get_type = ctypes.windll.kernel32.GetDriveTypeW
    out = []
    for i in range(26):
        if not (mask & (1 << i)):
            continue
        letter = chr(ord("A") + i)
        root = letter + ":\\"
        if get_type(root) == _DRIVE_NO_ROOT:
            continue
        if not os.path.isdir(root):
            continue
        out.append(letter + ":/")
    return out


def resolve(path: str) -> str:
    norm = normalize(path)
    if IS_WINDOWS:
        if norm == "/":
            raise ValueError("this-computer root is not a real directory")
        return norm.replace("/", "\\")
    return norm


def to_fs(full: str) -> str:
    full = str(full)
    if not (_DRIVE.match(full) if IS_WINDOWS else full.startswith("/")):
        full = os.path.abspath(full)
    if IS_WINDOWS:
        m = _DRIVE.match(full)
        if not m:
            raise ValueError(f"not a drive path: {full!r}")
        letter = m.group(1).upper()
        tail = _clean_tail(m.group(2) or "")
        return f"{letter}:/{tail}" if tail else f"{letter}:/"
    return normalize(full)


def parent_of(path: str) -> str:
    norm = normalize(path)
    if norm == "/":
        return "/"
    m = _DRIVE.match(norm)
    if m:
        tail = m.group(2) or ""
        if not tail:
            return "/"
        head = posixpath.dirname(tail)
        return f"{m.group(1)}:/{head}" if head else f"{m.group(1)}:/"
    head = posixpath.dirname(norm)
    return head or "/"


def is_root(path: str) -> bool:
    norm = normalize(path)
    return norm == "/" or bool(re.match(r"^[A-Z]:/$", norm))


WORKSPACE = to_fs(ROOT)


def from_agent_path(path: str, *, is_directory: bool = False) -> str:
    raw = "" if path is None else str(path).strip().replace("\\", "/")
    if not raw:
        if not is_directory:
            raise ValueError("A file path must not be empty or whitespace-only.")
        return WORKSPACE
    if _DRIVE.match(raw) or raw.startswith("/"):
        norm = normalize(raw)
    else:
        norm = normalize(WORKSPACE.rstrip("/") + "/" + raw)
    if not is_directory:
        if raw.endswith("/"):
            raise ValueError(f"Invalid path: {path!r}. A file path must not end with a path separator.")
        if is_root(norm):
            raise ValueError(f"Invalid path: {path!r}. A file path must not be a root directory.")
    return norm
