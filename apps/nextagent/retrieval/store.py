from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    sha TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    vector BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
CREATE TABLE IF NOT EXISTS sources (
    path TEXT PRIMARY KEY,
    added_at TEXT NOT NULL,
    last_sync TEXT NOT NULL
);
"""


def _path_key(path: str) -> str:
    return path.lower() if os.name == "nt" else path


def _under(path: str, prefix: str) -> bool:
    p = _path_key(path)
    base = _path_key(prefix).rstrip("/")
    return p == base or p.startswith(base + "/")


@dataclass(frozen=True)
class FileRecord:
    path: str
    sha: str
    size: int
    mtime_ns: int
    chunk_count: int


@dataclass(frozen=True)
class SourceRecord:
    path: str
    added_at: str
    last_sync: str


@dataclass(frozen=True)
class SearchHit:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float


class VectorStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._matrix: np.ndarray | None = None
        self._matrix_ids: list[int] = []
        self._matrix_paths: list[str] = []
        self._matrix_version = -1

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def files(self, prefix: str = "") -> dict[str, FileRecord]:
        rows = self._conn.execute("SELECT path, sha, size, mtime_ns, chunk_count FROM files").fetchall()
        out = {r[0]: FileRecord(*r) for r in rows}
        if not prefix:
            return out
        return {p: rec for p, rec in out.items() if _under(p, prefix)}

    def counts(self) -> tuple[int, int]:
        files = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        chunks = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return int(files), int(chunks)

    def sources(self) -> list[SourceRecord]:
        rows = self._conn.execute("SELECT path, added_at, last_sync FROM sources ORDER BY added_at").fetchall()
        return [SourceRecord(*r) for r in rows]

    def add_source(self, path: str, added_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sources(path, added_at, last_sync) VALUES(?, ?, '')",
                (path, added_at),
            )
            self._conn.commit()

    def set_source_sync(self, path: str, when: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE sources SET last_sync=? WHERE path=?", (when, path))
            self._conn.commit()

    def remove_source(self, path: str) -> int:
        removed = self.remove_prefix(path)
        with self._lock:
            self._conn.execute("DELETE FROM sources WHERE path=?", (path,))
            self._conn.commit()
        return removed

    def remove_prefix(self, prefix: str) -> int:
        paths = [p for p in self.files(prefix)]
        if not paths:
            return 0
        with self._lock:
            self._conn.executemany("DELETE FROM chunks WHERE path=?", [(p,) for p in paths])
            self._conn.executemany("DELETE FROM files WHERE path=?", [(p,) for p in paths])
            self._conn.commit()
        return len(paths)

    def source_counts(self) -> dict[str, tuple[int, int]]:
        rows = self._conn.execute("SELECT path, chunk_count FROM files").fetchall()
        out: dict[str, tuple[int, int]] = {}
        for src in self.sources():
            files = 0
            chunks = 0
            for path, chunk_count in rows:
                if _under(path, src.path):
                    files += 1
                    chunks += int(chunk_count)
            out[src.path] = (files, chunks)
        return out

    def remove_file(self, path: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chunks WHERE path=?", (path,))
            self._conn.execute("DELETE FROM files WHERE path=?", (path,))
            self._conn.commit()

    def replace_file(
        self,
        record: FileRecord,
        chunks: list[tuple[int, int, str]],
        vectors: list[list[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise RuntimeError(f"块数 {len(chunks)} 与向量数 {len(vectors)} 不一致: {record.path}")
        with self._lock:
            self._conn.execute("DELETE FROM chunks WHERE path=?", (record.path,))
            self._conn.executemany(
                "INSERT INTO chunks(path, start_line, end_line, text, vector) VALUES(?, ?, ?, ?, ?)",
                [
                    (record.path, start, end, text, np.asarray(vec, dtype=np.float32).tobytes())
                    for (start, end, text), vec in zip(chunks, vectors)
                ],
            )
            self._conn.execute(
                "INSERT INTO files(path, sha, size, mtime_ns, chunk_count) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET sha=excluded.sha, size=excluded.size, "
                "mtime_ns=excluded.mtime_ns, chunk_count=excluded.chunk_count",
                (record.path, record.sha, record.size, record.mtime_ns, record.chunk_count),
            )
            self._conn.commit()

    def update_file_stat(self, record: FileRecord) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE files SET size=?, mtime_ns=? WHERE path=?",
                (record.size, record.mtime_ns, record.path),
            )
            self._conn.commit()

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chunks")
            self._conn.execute("DELETE FROM files")
            self._conn.execute("DELETE FROM meta")
            self._conn.commit()
            self._matrix = None
            self._matrix_version = -1

    def bump_version(self) -> None:
        current = int(self.get_meta("version") or 0)
        self.set_meta("version", str(current + 1))

    def _load_matrix(self) -> None:
        version = int(self.get_meta("version") or 0)
        if self._matrix is not None and version == self._matrix_version:
            return
        rows = self._conn.execute("SELECT id, path, vector FROM chunks ORDER BY id").fetchall()
        if not rows:
            self._matrix = np.zeros((0, 0), dtype=np.float32)
            self._matrix_ids = []
            self._matrix_paths = []
            self._matrix_version = version
            return
        vectors = np.stack([np.frombuffer(r[2], dtype=np.float32) for r in rows])
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._matrix = vectors / norms
        self._matrix_ids = [int(r[0]) for r in rows]
        self._matrix_paths = [str(r[1]) for r in rows]
        self._matrix_version = version

    def search(self, query_vec: list[float], *, top_k: int, path_prefix: str = "") -> list[SearchHit]:
        with self._lock:
            self._load_matrix()
            matrix = self._matrix
            ids = self._matrix_ids
            paths = self._matrix_paths
        if matrix is None or matrix.size == 0:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        qn = np.linalg.norm(q)
        if qn == 0:
            raise RuntimeError("查询向量全为零")
        scores = matrix @ (q / qn)
        if path_prefix:
            mask = np.array([_under(p, path_prefix) for p in paths], dtype=bool)
            scores = np.where(mask, scores, -np.inf)
        k = min(top_k, len(ids))
        order = np.argpartition(-scores, k - 1)[:k]
        order = order[np.argsort(-scores[order])]
        chosen = [(ids[i], float(scores[i])) for i in order if np.isfinite(scores[i])]
        if not chosen:
            return []
        placeholders = ",".join("?" for _ in chosen)
        rows = self._conn.execute(
            f"SELECT id, path, start_line, end_line, text FROM chunks WHERE id IN ({placeholders})",
            [cid for cid, _ in chosen],
        ).fetchall()
        by_id = {int(r[0]): r for r in rows}
        hits: list[SearchHit] = []
        for cid, score in chosen:
            r = by_id[cid]
            hits.append(SearchHit(path=r[1], start_line=int(r[2]), end_line=int(r[3]), text=r[4], score=score))
        return hits

    def close(self) -> None:
        self._conn.close()
