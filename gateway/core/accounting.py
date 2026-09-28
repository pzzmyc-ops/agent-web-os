from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time

_B64_MIN_CHARS = 512
_BODY_MAX_CHARS = 262144

_DATA_URL_RE = re.compile(r"^data:([^;,]*)(;[^,]*)?,(.+)$", re.DOTALL)
_BARE_B64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")

from appconfig import load_config

_DB_PATH = load_config().usage_db
_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    username TEXT DEFAULT '',
    api_key TEXT DEFAULT '',
    kind TEXT NOT NULL,
    provider TEXT DEFAULT '',
    model TEXT DEFAULT '',
    operation TEXT DEFAULT '',
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    cost_currency TEXT DEFAULT 'USD',
    latency_ms INTEGER DEFAULT 0,
    success INTEGER DEFAULT 1,
    error_text TEXT DEFAULT '',
    request_body_json TEXT DEFAULT '',
    response_body_json TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_model_ts ON usage_events(model, ts);
CREATE INDEX IF NOT EXISTS idx_usage_username_ts ON usage_events(username, ts);
CREATE INDEX IF NOT EXISTS idx_usage_kind_ts ON usage_events(kind, ts);
"""


def _looks_base64(text: str) -> bool:
    """字符集不能带空格,否则纯小写英文长句(全落在 A-Za-z 和空格里)会被当成 base64
    剥掉;再要求长度是 4 的倍数,把剩下的误判压到可忽略。
    """
    compact = text.strip().replace("\r", "").replace("\n", "")
    return len(compact) % 4 == 0 and bool(_BARE_B64_RE.match(compact))


def _strip_value(value):
    """把长 base64 换成尺寸标记。

    记账要的是「谁在什么时候调了什么」,不是像素。对话开着图片直传时,一张图的
    data url 就是几 MB;媒体上游回的 b64_json 更大 —— 实测 253 条记录里 body
    占了 94.91 MB 的 98%,单条最大 22 MB。原文留着对统计毫无用处。
    """
    if isinstance(value, dict):
        return {k: _strip_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_value(v) for v in value]
    if not isinstance(value, str) or len(value) < _B64_MIN_CHARS:
        return value
    m = _DATA_URL_RE.match(value)
    if m and _looks_base64(m.group(3)):
        return f"data:{m.group(1)}{m.group(2) or ''},<stripped {len(m.group(3))} chars>"
    if _looks_base64(value):
        return f"<base64 stripped {len(value)} chars>"
    return value


def sanitize_body(value) -> str:
    """记账用的 body 文本:剥掉 base64,再按总长封顶。"""
    text = json.dumps(_strip_value(value), ensure_ascii=False)
    if len(text) <= _BODY_MAX_CHARS:
        return text
    return f"{text[:_BODY_MAX_CHARS]}...<truncated, total {len(text)} chars>"


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = _conn()
    conn.executescript(_SCHEMA)
    conn.commit()


def record_event(
    *,
    kind: str,
    username: str = "",
    api_key: str = "",
    provider: str = "",
    model: str = "",
    operation: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    cost: float = 0.0,
    cost_currency: str = "USD",
    latency_ms: int = 0,
    success: int = 1,
    error_text: str = "",
    request_body_json: str = "",
    response_body_json: str = "",
) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO usage_events (ts,username,api_key,kind,provider,model,operation,"
        "prompt_tokens,completion_tokens,total_tokens,cost,cost_currency,latency_ms,"
        "success,error_text,request_body_json,response_body_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            int(time.time()),
            username,
            api_key,
            kind,
            provider,
            model,
            operation,
            int(prompt_tokens),
            int(completion_tokens),
            int(total_tokens),
            float(cost),
            cost_currency,
            int(latency_ms),
            int(success),
            error_text[:2000],
            request_body_json,
            response_body_json,
        ),
    )
    conn.commit()
