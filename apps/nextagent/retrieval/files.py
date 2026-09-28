from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from ..toolkit._rg import WORKSPACE_PRIVATE_DIRS, run_rg, workspace_ignore_args

MAX_FILE_BYTES = 1_000_000
BUILTIN_IGNORE_DIRS = (
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".cache",
)
BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svgz", ".psd", ".tif", ".tiff", ".heic",
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac",
    ".mp4", ".webm", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".m4v",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".whl",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc", ".pyd", ".o", ".a", ".lib", ".class",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
    ".sqlite", ".db", ".sqlite3", ".parquet", ".feather", ".npy", ".npz", ".pkl", ".pickle",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".safetensors", ".ckpt", ".pt", ".pth", ".onnx", ".gguf",
    ".iso", ".img", ".dmg", ".msi",
})


@dataclass(frozen=True)
class IndexableFile:
    rel_path: str
    full_path: str
    size: int
    mtime_ns: int


def canon_path(path: str) -> str:
    return os.path.realpath(path).replace("\\", "/")


def path_key(path: str) -> str:
    return path.lower() if os.name == "nt" else path


def is_under(path: str, parent: str) -> bool:
    p = path_key(path)
    base = path_key(parent).rstrip("/")
    return p == base or p.startswith(base + "/")


def _rg_file_list(root: str, exclude_rel: list[str]) -> list[str]:
    args = ["--files", "--hidden", "--no-require-git", "--follow", "--glob", "!.git/**"]
    for name in BUILTIN_IGNORE_DIRS:
        args += ["--glob", f"!**/{name}/**"]
    for name in WORKSPACE_PRIVATE_DIRS:
        args += ["--glob", f"!{name}/**"]
    for rel in exclude_rel:
        args += ["--glob", f"!{rel}/**"]
    args += workspace_ignore_args(root)
    _code, out, _err = run_rg(args, cwd=root, timeout=600)
    return [line for line in out.splitlines() if line.strip()]


def _looks_binary(full_path: str) -> bool:
    with open(full_path, "rb") as handle:
        head = handle.read(8192)
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def enumerate_files(root: str, exclude_abs: list[str]) -> list[IndexableFile]:
    root_c = canon_path(root)
    exclude_rel: list[str] = []
    for ex in exclude_abs:
        ex_c = canon_path(ex)
        if is_under(ex_c, root_c) and path_key(ex_c) != path_key(root_c):
            exclude_rel.append(ex_c[len(root_c):].lstrip("/"))
    out: list[IndexableFile] = []
    for rel in _rg_file_list(root_c, exclude_rel):
        rel_posix = rel.replace("\\", "/")
        ext = os.path.splitext(rel_posix)[1].lower()
        if ext in BINARY_EXTENSIONS:
            continue
        full = root_c + "/" + rel_posix
        try:
            st = os.stat(full)
        except OSError:
            continue
        if st.st_size == 0 or st.st_size > MAX_FILE_BYTES:
            continue
        out.append(IndexableFile(rel_path=rel_posix, full_path=full, size=st.st_size, mtime_ns=st.st_mtime_ns))
    return out


def sha256_of(full_path: str) -> str:
    digest = hashlib.sha256()
    with open(full_path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_text_or_none(full_path: str) -> str | None:
    if _looks_binary(full_path):
        return None
    with open(full_path, "rb") as handle:
        raw = handle.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
