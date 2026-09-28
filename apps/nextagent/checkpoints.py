"""文件检查点:每条用户消息一个点,记录「发这条消息之前」工作区里被 AI 动过的文件长什么样。

做法照 Cursor,不走 git:
- V0:一个文件在这场对话里第一次被 AI 动之前的内容,只记一次(v0.json → blob)。
- 检查点:用户消息落盘时,把所有已跟踪路径此刻的状态拍下来(cp/<id>.json)。
  一条消息的回合里第一次碰到某个新路径时,把它动手前的状态补进最近那个检查点,
  这样「恢复到这条消息之前」也能把它还回去。
- 内容按 sha256 存成 blob,同内容只存一份。
- 恢复到检查点 C:每个已跟踪路径取「seq >= C 的最早检查点里记录的状态」,没有就取 V0,
  然后按这个目标状态改盘。从头部第一次恢复前先给头部拍一个 latest 快照,供 Redo。

目录只记「存在 / 不存在」。恢复时不存在的目录只在它已经空了的情况下删,非空就报出来。
超过 BLOB_SIZE_LIMIT 的文件不存内容,恢复时报「未恢复」。
shell 命令改的文件不在跟踪范围内。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

from fm.backend.pathutil import resolve, to_fs

BLOB_SIZE_LIMIT = 32 * 1024 * 1024

KIND_MESSAGE = "message"
KIND_LATEST = "latest"


def _now_ms() -> int:
    return int(time.time() * 1000)


def tree_paths(full: str) -> list[str]:
    """一棵目录下所有路径(含自身)的虚拟路径,目录在前。文件级操作作用在目录上时用它展开。"""
    out: list[str] = [to_fs(full)]
    if not os.path.isdir(full):
        return out
    for root, dirs, files in os.walk(full):
        for name in dirs:
            out.append(to_fs(os.path.join(root, name)))
        for name in files:
            out.append(to_fs(os.path.join(root, name)))
    return out


def mapped_tree_paths(src_full: str, dst_full: str) -> list[str]:
    """把 src 树里每个路径映射到 dst 下对应位置(尚不存在),移动 / 复制的目标侧用它。"""
    out: list[str] = [to_fs(dst_full)]
    if not os.path.isdir(src_full):
        return out
    for root, dirs, files in os.walk(src_full):
        rel_root = os.path.relpath(root, src_full)
        base = dst_full if rel_root == "." else os.path.join(dst_full, rel_root)
        for name in dirs:
            out.append(to_fs(os.path.join(base, name)))
        for name in files:
            out.append(to_fs(os.path.join(base, name)))
    return out


def missing_ancestors(full: str) -> list[str]:
    """full 及其尚不存在的上级目录(makedirs 会一并创建的那些)。"""
    out: list[str] = []
    cur = full
    while not os.path.exists(cur):
        out.append(to_fs(cur))
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return out


def track_current(virt_paths: list[str]) -> None:
    """在当前回合里登记即将被改动的路径。不在回合里就没有会话可挂,不记。"""
    from .toolkit._context import current_turn

    ctx = current_turn()
    if ctx is None or ctx.store is None:
        return
    ctx.store.checkpoints.track(ctx.thread_id, virt_paths)


def track_before_write(full: str) -> None:
    """写一个文件之前:文件本身,以及 makedirs 会顺手建出来的上级目录。"""
    track_current(missing_ancestors(full) or [to_fs(full)])


def track_before_transfer(src_full: str, dst_full: str) -> None:
    """移动 / 复制 / 重命名之前:源树的每个路径,加目标树对应的每个路径。"""
    track_current(tree_paths(src_full) + mapped_tree_paths(src_full, dst_full))


class Checkpoints:
    def __init__(self, sessions_root: Path):
        self._root = Path(sessions_root)
        self._lock = threading.RLock()

    def _dir(self, thread_id: str) -> Path:
        return self._root / thread_id / "checkpoints"

    def _blob_path(self, thread_id: str, digest: str) -> Path:
        return self._dir(thread_id) / "blobs" / digest

    def _cp_path(self, thread_id: str, cp_id: str) -> Path:
        return self._dir(thread_id) / "cp" / (cp_id + ".json")

    def _state_path(self, thread_id: str) -> Path:
        return self._dir(thread_id) / "state.json"

    def _v0_path(self, thread_id: str) -> Path:
        return self._dir(thread_id) / "v0.json"

    @staticmethod
    def _read_json(path: Path, default):
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, obj) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def _read_state(self, thread_id: str) -> dict:
        return self._read_json(
            self._state_path(thread_id), {"seq": 0, "last": "", "latest": "", "cursor": ""}
        )

    def _write_state(self, thread_id: str, st: dict) -> None:
        self._write_json(self._state_path(thread_id), st)

    def _read_v0(self, thread_id: str) -> dict:
        return self._read_json(self._v0_path(thread_id), {})

    def _read_cp(self, thread_id: str, cp_id: str) -> dict:
        path = self._cp_path(thread_id, cp_id)
        if not path.is_file():
            raise FileNotFoundError(f"检查点不存在: {cp_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _all_cps(self, thread_id: str) -> list[dict]:
        cp_dir = self._dir(thread_id) / "cp"
        if not cp_dir.is_dir():
            return []
        cps = [json.loads(p.read_text(encoding="utf-8")) for p in cp_dir.glob("*.json")]
        cps.sort(key=lambda c: int(c["seq"]))
        return cps

    def _put_blob(self, thread_id: str, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self._blob_path(thread_id, digest)
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        return digest

    def _get_blob(self, thread_id: str, digest: str) -> bytes:
        path = self._blob_path(thread_id, digest)
        if not path.is_file():
            raise FileNotFoundError(f"检查点内容丢失: {digest}")
        return path.read_bytes()

    def _disk_state(self, thread_id: str, virt: str) -> dict:
        full = resolve(virt)
        if os.path.isdir(full):
            return {"kind": "dir", "exists": True}
        if not os.path.exists(full):
            return {"kind": "absent", "exists": False}
        size = os.path.getsize(full)
        if size > BLOB_SIZE_LIMIT:
            return {"kind": "file", "exists": True, "blob": None, "oversize": size}
        with open(full, "rb") as fh:
            data = fh.read()
        return {"kind": "file", "exists": True, "blob": self._put_blob(thread_id, data)}

    def track(self, thread_id: str, virt_paths: list[str]) -> None:
        """写操作动手之前调用:第一次见到的路径记 V0,并补进最近的检查点。"""
        paths = list(dict.fromkeys(str(p) for p in virt_paths if p))
        if not paths:
            return
        with self._lock:
            v0 = self._read_v0(thread_id)
            st = self._read_state(thread_id)
            cp = self._read_cp(thread_id, st["last"]) if st.get("last") else None
            v0_changed = False
            cp_changed = False
            for p in paths:
                in_v0 = p in v0
                in_cp = cp is None or p in cp["files"]
                if in_v0 and in_cp:
                    continue
                state = self._disk_state(thread_id, p)
                if not in_v0:
                    v0[p] = state
                    v0_changed = True
                if not in_cp:
                    cp["files"][p] = state
                    cp_changed = True
            if v0_changed:
                self._write_json(self._v0_path(thread_id), v0)
            if cp_changed:
                self._write_json(self._cp_path(thread_id, cp["id"]), cp)

    def create(self, thread_id: str, *, plan: dict | None, kind: str = KIND_MESSAGE) -> str:
        """给此刻拍一个检查点。kind=message 挂在用户消息上;kind=latest 是恢复前的头部快照。"""
        if kind not in (KIND_MESSAGE, KIND_LATEST):
            raise ValueError(f"未知检查点类型: {kind}")
        with self._lock:
            v0 = self._read_v0(thread_id)
            st = self._read_state(thread_id)
            files = {p: self._disk_state(thread_id, p) for p in v0}
            cp = {
                "id": uuid.uuid4().hex,
                "seq": int(st["seq"]) + 1,
                "kind": kind,
                "created_at": _now_ms(),
                "files": files,
                "plan": plan,
            }
            self._write_json(self._cp_path(thread_id, cp["id"]), cp)
            st["seq"] = cp["seq"]
            if kind == KIND_MESSAGE:
                st["last"] = cp["id"]
            else:
                old = str(st.get("latest") or "")
                if old:
                    old_path = self._cp_path(thread_id, old)
                    if old_path.is_file():
                        old_path.unlink()
                st["latest"] = cp["id"]
            self._write_state(thread_id, st)
            return cp["id"]

    def exists(self, thread_id: str, cp_id: str) -> bool:
        return bool(cp_id) and self._cp_path(thread_id, cp_id).is_file()

    def state(self, thread_id: str) -> dict:
        st = self._read_state(thread_id)
        return {"cursor": str(st.get("cursor") or ""), "hasLatest": bool(st.get("latest"))}

    def cursor(self, thread_id: str) -> str:
        return str(self._read_state(thread_id).get("cursor") or "")

    def clear_head(self, thread_id: str) -> None:
        """回退状态下发了新消息:后面的历史被砍掉,头部快照随之作废。"""
        with self._lock:
            st = self._read_state(thread_id)
            latest = str(st.get("latest") or "")
            st["cursor"] = ""
            st["latest"] = ""
            self._write_state(thread_id, st)
            if latest:
                path = self._cp_path(thread_id, latest)
                if path.is_file():
                    path.unlink()

    def restore(
        self, thread_id: str, cp_id: str, *, from_submit: bool, current_plan: dict | None
    ) -> dict:
        """把工作区改回检查点 cp_id 记录的状态。

        from_submit=True 是编辑重发那条路:恢复之后后面的历史就要被砍,不留 Redo。
        from_submit=False 是用户点恢复按钮:在头部就先拍 latest,光标指到 cp_id。
        返回改盘报告,plan 字段是该检查点当时的计划槽,由调用方写回会话。
        """
        with self._lock:
            target = self._read_cp(thread_id, cp_id)
            st = self._read_state(thread_id)
            if not from_submit and not st.get("cursor"):
                self.create(thread_id, plan=current_plan, kind=KIND_LATEST)
                st = self._read_state(thread_id)
            later = [c for c in self._all_cps(thread_id) if int(c["seq"]) >= int(target["seq"])]
            v0 = self._read_v0(thread_id)
            targets: dict[str, dict] = {}
            for p in v0:
                targets[p] = next((c["files"][p] for c in later if p in c["files"]), v0[p])
            report = self._apply(thread_id, targets)
            report["plan"] = target.get("plan")
            report["checkpointId"] = cp_id
            if from_submit:
                st["cursor"] = ""
                st["last"] = cp_id
                latest = str(st.get("latest") or "")
                st["latest"] = ""
                self._write_state(thread_id, st)
                if latest and latest != cp_id:
                    path = self._cp_path(thread_id, latest)
                    if path.is_file():
                        path.unlink()
            else:
                st["cursor"] = "" if target.get("kind") == KIND_LATEST else cp_id
                self._write_state(thread_id, st)
            return report

    def redo(self, thread_id: str, *, current_plan: dict | None) -> dict:
        """回到恢复之前的头部状态。"""
        with self._lock:
            st = self._read_state(thread_id)
            latest = str(st.get("latest") or "")
            if not latest:
                raise RuntimeError("没有可以撤销的恢复")
            return self.restore(thread_id, latest, from_submit=False, current_plan=current_plan)

    def _apply(self, thread_id: str, targets: dict[str, dict]) -> dict:
        written: list[str] = []
        deleted: list[str] = []
        dirs_created: list[str] = []
        dirs_removed: list[str] = []
        skipped: list[str] = []
        dir_pass: list[tuple[str, dict]] = []
        for virt, want in targets.items():
            full = resolve(virt)
            kind = want["kind"]
            if kind == "dir":
                dir_pass.append((virt, want))
                continue
            if kind == "file":
                if want.get("blob") is None:
                    skipped.append(f"{virt}(超过大小上限,未恢复)")
                    continue
                if os.path.isdir(full):
                    skipped.append(f"{virt}(目标位置现在是目录,未恢复)")
                    continue
                data = self._get_blob(thread_id, want["blob"])
                if os.path.isfile(full):
                    with open(full, "rb") as fh:
                        if hashlib.sha256(fh.read()).hexdigest() == want["blob"]:
                            continue
                os.makedirs(os.path.dirname(full), exist_ok=True)
                tmp = full + ".cp-restore.tmp"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, full)
                written.append(virt)
                continue
            if kind == "absent":
                if os.path.isdir(full):
                    dir_pass.append((virt, want))
                elif os.path.exists(full):
                    os.remove(full)
                    deleted.append(virt)
                continue
            raise RuntimeError(f"未知的检查点状态: {kind}")
        dir_pass.sort(key=lambda item: len(item[0]), reverse=True)
        for virt, want in dir_pass:
            full = resolve(virt)
            if want["kind"] == "dir":
                if not os.path.isdir(full):
                    if os.path.exists(full):
                        skipped.append(f"{virt}(目标位置现在是文件,未恢复为目录)")
                        continue
                    os.makedirs(full)
                    dirs_created.append(virt)
                continue
            if not os.path.isdir(full):
                continue
            if os.listdir(full):
                skipped.append(f"{virt}(目录非空,未删除)")
                continue
            os.rmdir(full)
            dirs_removed.append(virt)
        return {
            "written": written,
            "deleted": deleted,
            "dirsCreated": dirs_created,
            "dirsRemoved": dirs_removed,
            "skipped": skipped,
            "changed": written + deleted + dirs_created + dirs_removed,
        }


def restore_summary(report: dict) -> str:
    """恢复报告 → 给用户和模型看的一段说明。"""
    lines = ["已把工作区文件恢复到检查点状态。"]
    if report["written"]:
        lines.append("改回原内容 " + str(len(report["written"])) + " 个:" + "、".join(report["written"]))
    if report["deleted"]:
        lines.append("删除 " + str(len(report["deleted"])) + " 个:" + "、".join(report["deleted"]))
    if report["dirsCreated"]:
        lines.append("重建目录 " + str(len(report["dirsCreated"])) + " 个:" + "、".join(report["dirsCreated"]))
    if report["dirsRemoved"]:
        lines.append("删除目录 " + str(len(report["dirsRemoved"])) + " 个:" + "、".join(report["dirsRemoved"]))
    if report["skipped"]:
        lines.append("未处理 " + str(len(report["skipped"])) + " 项:" + "、".join(report["skipped"]))
    if len(lines) == 1:
        lines.append("文件本来就和检查点一致,没有改动。")
    return "\n".join(lines)
