from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

import httpx
import yaml

from .config import load_config

_SKILLS_ROOT = Path(load_config().skills_dir).resolve()
HUB_INSTALL_DIR = _SKILLS_ROOT
HUB_STATE_DIR = _SKILLS_ROOT / ".hub"
LOCK_FILE = HUB_STATE_DIR / "lock.json"
_REF_RE = re.compile(
    r"(?:\$\{HERMES_SKILL_DIR\}/)?((?:references|templates|scripts|assets|examples)/[A-Za-z0-9._/\-]+)"
)
_DEFAULT_TAPS = (
    {"repo": "openai/skills", "path": "skills"},
    {"repo": "anthropics/skills", "path": "skills"},
    {"repo": "huggingface/skills", "path": "skills"},
)
_TIMEOUT = 20.0
_TAR_TIMEOUT = 60.0
_MAX_REDIRECTS = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_name(raw: str) -> str:
    text = (raw or "").strip().lower().replace(" ", "-").replace("_", "-")
    text = re.sub(r"[^a-z0-9-]", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text:
        raise ValueError("技能名无效")
    return text[:64]


def _safe_rel(rel: str) -> str:
    text = (rel or "").replace("\\", "/").strip().lstrip("/")
    parts = [p for p in text.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        raise ValueError(f"非法路径: {rel}")
    return "/".join(parts)


def _assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"只允许 http/https: {url}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL 无主机名: {url}")
    if host.lower() in ("localhost",):
        raise ValueError(f"禁止本地地址: {url}")
    infos = socket.getaddrinfo(host, None)
    if not infos:
        raise ValueError(f"无法解析主机: {host}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"禁止内网地址: {url}")


def _http_get(url: str, *, headers: dict[str, str] | None = None, timeout: float | None = None) -> httpx.Response:
    current = url
    hdrs = {"User-Agent": "nextagent-skills-hub"}
    if headers:
        hdrs.update(headers)
    for _ in range(_MAX_REDIRECTS + 1):
        _assert_public_url(current)
        resp = httpx.get(current, headers=hdrs, timeout=timeout or _TIMEOUT, follow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            if not loc:
                raise ValueError(f"重定向无 Location: {current}")
            current = str(httpx.URL(current).join(loc))
            continue
        if resp.status_code != 200:
            raise ValueError(f"HTTP {resp.status_code}: {current}")
        return resp
    raise ValueError(f"重定向过多: {url}")


def _parse_frontmatter(content: str) -> dict[str, Any]:
    if not (content or "").startswith("---"):
        return {}
    match = re.search(r"\n---\s*\n", content[3:])
    if not match:
        return {}
    data = yaml.safe_load(content[3:match.start() + 3])
    return data if isinstance(data, dict) else {}


def _load_lock() -> dict[str, Any]:
    if not LOCK_FILE.is_file():
        return {"version": 1, "installed": {}}
    data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("lock.json 格式错误")
    data.setdefault("version", 1)
    data.setdefault("installed", {})
    return data


def _save_lock(data: dict[str, Any]) -> None:
    HUB_STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _bundle_hash(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(files[rel])
    return h.hexdigest()


def _write_bundle(name: str, files: dict[str, bytes], meta: dict[str, Any]) -> Path:
    target = HUB_INSTALL_DIR / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for rel, data in files.items():
        safe = _safe_rel(rel)
        dest = target / safe
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    lock = _load_lock()
    lock["installed"][name] = {
        **meta,
        "install_path": str(target),
        "files": sorted(files),
        "content_hash": _bundle_hash(files),
        "installed_at": _now(),
        "updated_at": _now(),
    }
    _save_lock(lock)
    return target


def _name_taken_elsewhere(name: str) -> str | None:
    target = HUB_INSTALL_DIR / name
    if target.is_dir() and name not in _load_lock().get("installed", {}):
        return str(target)
    return None


def _parse_github_identifier(identifier: str) -> tuple[str, str]:
    text = identifier.strip()
    if text.startswith("github:"):
        text = text[7:]
    if text.startswith("https://github.com/"):
        rest = text[len("https://github.com/"):].strip("/")
        parts = rest.split("/")
        if len(parts) < 2:
            raise ValueError(f"GitHub 标识无效: {identifier}")
        owner, repo = parts[0], parts[1]
        path_parts = parts[2:]
        if path_parts and path_parts[0] in ("tree", "blob"):
            path_parts = path_parts[2:]
        path = "/".join(path_parts).rstrip("/")
        if not path:
            raise ValueError(f"GitHub 标识缺少技能路径: {identifier}")
        return f"{owner}/{repo}", path
    parts = text.split("/")
    if len(parts) < 3:
        raise ValueError(f"GitHub 标识应为 owner/repo/path: {identifier}")
    return f"{parts[0]}/{parts[1]}", "/".join(parts[2:]).rstrip("/")


def _tar_inner_path(name: str) -> str:
    parts = name.replace("\\", "/").split("/")
    return "/".join(parts[1:]) if len(parts) > 1 else ""


def _github_tarball(repo: str) -> bytes:
    last: Exception | None = None
    for branch in ("main", "master"):
        url = f"https://codeload.github.com/{repo}/tar.gz/refs/heads/{branch}"
        try:
            return _http_get(url, timeout=_TAR_TIMEOUT).content
        except ValueError as exc:
            last = exc
            if "HTTP 404" not in str(exc):
                raise
    raise last if last else ValueError(f"无法下载 {repo} 压缩包")


def _github_download_dir(repo: str, path: str) -> dict[str, bytes]:
    import io
    import tarfile

    root = path.rstrip("/")
    prefix = root + "/"
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(_github_tarball(repo)), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            inner = _tar_inner_path(member.name)
            if inner != root and not inner.startswith(prefix):
                continue
            rel = "" if inner == root else _safe_rel(inner[len(prefix):])
            if not rel:
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                raise ValueError(f"无法解压: {member.name}")
            files[rel] = extracted.read()
    if "SKILL.md" not in files:
        raise ValueError(f"{repo}/{path} 压缩包中没有 SKILL.md")
    return files


def _github_catalog(repo: str, path: str) -> list[tuple[str, str]]:
    import io
    import tarfile

    prefix = path.strip("/")
    items: list[tuple[str, str]] = []
    with tarfile.open(fileobj=io.BytesIO(_github_tarball(repo)), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            inner = _tar_inner_path(member.name)
            if not inner.endswith("/SKILL.md"):
                continue
            skill_dir = inner[: -len("/SKILL.md")]
            if prefix and skill_dir != prefix and not skill_dir.startswith(prefix + "/"):
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                raise ValueError(f"无法解压: {member.name}")
            items.append((skill_dir, extracted.read().decode("utf-8")))
    return items


def _referenced_relpaths(skill_md: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _REF_RE.finditer(skill_md):
        rel = _safe_rel(match.group(1))
        if rel not in seen:
            seen.add(rel)
            found.append(rel)
    return found


def fetch_github(identifier: str) -> dict[str, Any]:
    repo, path = _parse_github_identifier(identifier)
    files = _github_download_dir(repo, path)
    if "SKILL.md" not in files:
        raise ValueError(f"{identifier} 没有 SKILL.md")
    text = files["SKILL.md"].decode("utf-8")
    fm = _parse_frontmatter(text)
    name = _sanitize_name(str(fm.get("name") or path.split("/")[-1]))
    return {
        "name": name,
        "description": str(fm.get("description") or ""),
        "source": "github",
        "identifier": f"{repo}/{path}",
        "files": files,
    }


def fetch_url(url: str) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"不是 URL: {url}")
    skill_url = url
    if not skill_url.endswith("SKILL.md"):
        skill_url = url.rstrip("/") + "/SKILL.md"
    skill_bytes = _http_get(skill_url).content
    text = skill_bytes.decode("utf-8")
    fm = _parse_frontmatter(text)
    slug = urlparse(skill_url).path.rstrip("/").split("/")[-2] if skill_url.endswith("/SKILL.md") else "skill"
    name = _sanitize_name(str(fm.get("name") or slug))
    files: dict[str, bytes] = {"SKILL.md": skill_bytes}
    base = skill_url[: -len("SKILL.md")]
    for rel in _referenced_relpaths(text):
        files[rel] = _http_get(base + rel).content
    return {
        "name": name,
        "description": str(fm.get("description") or ""),
        "source": "url",
        "identifier": skill_url,
        "files": files,
    }


def fetch_well_known(identifier: str) -> dict[str, Any]:
    raw = identifier[len("well-known:"):] if identifier.startswith("well-known:") else identifier
    if not raw.startswith(("http://", "https://")):
        raise ValueError(f"well-known 标识无效: {identifier}")
    parsed = urlparse(raw)
    clean = parsed._replace(fragment="").geturl()
    if clean.endswith("/index.json"):
        if not parsed.fragment:
            raise ValueError("index.json 标识需要 #skill-name")
        base = clean[: -len("/index.json")]
        skill_name = parsed.fragment
        index_url = clean
        skill_url = f"{base}/{skill_name}"
    elif "/.well-known/skills/" in clean:
        skill_url = clean[: -len("/SKILL.md")] if clean.endswith("/SKILL.md") else clean.rstrip("/")
        base, skill_name = skill_url.rsplit("/", 1)
        index_url = f"{base}/index.json"
    else:
        raise ValueError(f"不是 well-known 技能 URL: {identifier}")
    index = _http_get(index_url).json()
    skills = index.get("skills") if isinstance(index, dict) else None
    if not isinstance(skills, list):
        raise ValueError(f"index.json 无 skills 列表: {index_url}")
    entry = next((e for e in skills if isinstance(e, dict) and e.get("name") == skill_name), None)
    if entry is None:
        raise ValueError(f"index 中没有技能 {skill_name}")
    file_list = entry.get("files") or ["SKILL.md"]
    if not isinstance(file_list, list) or "SKILL.md" not in file_list:
        file_list = ["SKILL.md"] + [f for f in file_list if f != "SKILL.md"]
    files: dict[str, bytes] = {}
    for rel in file_list:
        safe = _safe_rel(str(rel))
        files[safe] = _http_get(f"{skill_url.rstrip('/')}/{safe}").content
    text = files["SKILL.md"].decode("utf-8")
    fm = _parse_frontmatter(text)
    name = _sanitize_name(str(fm.get("name") or skill_name))
    return {
        "name": name,
        "description": str(fm.get("description") or entry.get("description") or ""),
        "source": "well-known",
        "identifier": f"well-known:{skill_url}",
        "files": files,
    }


def _classify(identifier: str) -> str:
    text = identifier.strip()
    if text.startswith("well-known:") or "/.well-known/skills" in text:
        return "well-known"
    if text.startswith(("http://", "https://")):
        if "github.com/" in text:
            return "github"
        return "url"
    if text.startswith("github:"):
        return "github"
    if len(text.split("/")) >= 3:
        return "github"
    raise ValueError(f"无法识别来源: {identifier}")


def fetch_bundle(identifier: str) -> dict[str, Any]:
    kind = _classify(identifier)
    if kind == "github":
        return fetch_github(identifier)
    if kind == "well-known":
        return fetch_well_known(identifier)
    return fetch_url(identifier)


def search_skills(query: str, limit: int = 10) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        raise ValueError("query 不能为空")
    if q.startswith(("http://", "https://")):
        if "github.com/" in q:
            bundle = fetch_bundle(q)
            return {
                "ok": True,
                "skills": [{
                    "name": bundle["name"],
                    "description": bundle["description"],
                    "source": bundle["source"],
                    "identifier": bundle["identifier"],
                }],
            }
        if q.endswith("SKILL.md"):
            bundle = fetch_url(q)
            return {
                "ok": True,
                "skills": [{
                    "name": bundle["name"],
                    "description": bundle["description"],
                    "source": bundle["source"],
                    "identifier": bundle["identifier"],
                }],
            }
        if q.endswith("/index.json"):
            index_url = q
        elif "/.well-known/skills/" in q:
            base = q.split("/.well-known/skills", 1)[0] + "/.well-known/skills"
            index_url = base + "/index.json"
        else:
            index_url = q.rstrip("/") + "/.well-known/skills/index.json"
        data = _http_get(index_url).json()
        skills = data.get("skills") if isinstance(data, dict) else None
        if not isinstance(skills, list):
            raise ValueError(f"index.json 无 skills 列表: {index_url}")
        items = []
        for entry in skills:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            name = str(entry["name"])
            desc = str(entry.get("description") or "")
            items.append({
                "name": name,
                "description": desc,
                "source": "well-known",
                "identifier": f"well-known:{index_url[: -len('/index.json')]}/{name}",
            })
            if len(items) >= limit:
                break
        return {"ok": True, "skills": items}
    if "/" in q:
        bundle = fetch_bundle(q)
        return {
            "ok": True,
            "skills": [{
                "name": bundle["name"],
                "description": bundle["description"],
                "source": bundle["source"],
                "identifier": bundle["identifier"],
            }],
        }
    needle = q.lower()
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for tap in _DEFAULT_TAPS:
        repo = tap["repo"]
        path = tap["path"]
        try:
            catalog = _github_catalog(repo, path)
        except Exception as exc:
            errors.append(f"{repo}: {exc}")
            continue
        for skill_dir, md in catalog:
            ident = f"{repo}/{skill_dir}"
            dirname = skill_dir.rsplit("/", 1)[-1]
            fm = _parse_frontmatter(md)
            desc = str(fm.get("description") or "")
            name = _sanitize_name(str(fm.get("name") or dirname))
            blob = f"{name} {desc} {skill_dir}".lower()
            if needle not in blob:
                continue
            results.append({
                "name": name,
                "description": desc,
                "source": "github",
                "identifier": ident,
            })
            if len(results) >= limit:
                return {"ok": True, "skills": results, "errors": errors}
    if not results and errors:
        raise ValueError("搜索失败: " + "; ".join(errors))
    return {"ok": True, "skills": results, "errors": errors}


def install_skill(identifier: str) -> dict[str, Any]:
    bundle = fetch_bundle(identifier)
    name = bundle["name"]
    taken = _name_taken_elsewhere(name)
    if taken:
        raise ValueError(f"技能名 '{name}' 已被占用: {taken}")
    target = _write_bundle(
        name,
        bundle["files"],
        {
            "source": bundle["source"],
            "identifier": bundle["identifier"],
            "description": bundle["description"],
        },
    )
    return {
        "ok": True,
        "action": "installed",
        "name": name,
        "path": str(target),
        "source": bundle["source"],
        "identifier": bundle["identifier"],
        "files": sorted(bundle["files"]),
    }


def uninstall_skill(name: str) -> dict[str, Any]:
    safe = _sanitize_name(name)
    lock = _load_lock()
    if safe not in lock.get("installed", {}):
        raise ValueError(f"不是 Hub 安装的技能: {safe}")
    target = HUB_INSTALL_DIR / safe
    if target.exists():
        shutil.rmtree(target)
    lock["installed"].pop(safe, None)
    _save_lock(lock)
    return {"ok": True, "action": "uninstalled", "name": safe}


def list_installed() -> dict[str, Any]:
    lock = _load_lock()
    items = []
    for name, entry in (lock.get("installed") or {}).items():
        items.append({"name": name, **entry})
    return {"ok": True, "skills": items}


def inspect_skill(identifier: str) -> dict[str, Any]:
    bundle = fetch_bundle(identifier)
    return {
        "ok": True,
        "name": bundle["name"],
        "description": bundle["description"],
        "source": bundle["source"],
        "identifier": bundle["identifier"],
        "files": sorted(bundle["files"]),
    }
