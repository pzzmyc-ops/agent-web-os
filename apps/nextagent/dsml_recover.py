"""把 DeepSeek 泄漏成明文的 DSML 工具调用还原成结构化 function_call。

DeepSeek V4 的原生工具调用格式是 DSML:

    <｜DSML｜tool_calls>
    <｜DSML｜invoke name="fn">
    <｜DSML｜parameter name="x" string="true">value</｜DSML｜parameter>
    </｜DSML｜invoke>
    </｜DSML｜tool_calls>

本该由推理服务端解析成 OpenAI 的 tool_calls 字段。V4 Flash 会间歇性地输出畸形变体
(双竖线 <｜｜DSML｜｜、竖线后带空格、包裹标签写成 calls / toolcalls),服务端严格匹配
失败后整块当正文放行,finish_reason=stop。agent 循环看不到工具调用就结束这一轮,
表现为「跑到一半突然停下」。已知同类修复:openclaw #128882、cherry-studio #14747、
codex-relay dsml.rs。

DsmlStreamRecoverer 逐段接收 assistant 文本增量:普通文本原样放行;疑似 DSML 标记
的前缀先扣住;确认进入块后整块扣住直到闭合,再解析成 function_call。流结束时若块
未闭合,按已有内容尽力解析(用户决定:能包成合法工具就包)。
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from agent_framework import Content

_BARS = r"[|\uff5c]{1,2}"
_OPEN = rf"<{_BARS}DSML{_BARS}\s*"
_CLOSE = rf"</{_BARS}DSML{_BARS}\s*"
_WRAP = r"(?:tool_calls|toolcalls|tool-calls|calls)"

RE_WRAP_OPEN = re.compile(rf"{_OPEN}{_WRAP}\s*>")
RE_WRAP_CLOSE = re.compile(rf"{_CLOSE}{_WRAP}\s*>")
RE_INVOKE_OPEN = re.compile(
    rf'{_OPEN}invoke\s+name="([^"]*)"(?:\s+string="((?:[^"\\]|\\.)*)")?\s*(/?)>'
)
RE_INVOKE_CLOSE = re.compile(rf"{_CLOSE}invoke\s*>")
RE_PARAM_OPEN = re.compile(rf'{_OPEN}parameter\s+name="([^"]*)"(?:\s+string="(true|false)")?\s*>')
RE_PARAM_CLOSE = re.compile(rf"{_CLOSE}parameter\s*>")

_HEADS: tuple[str, ...] = tuple(
    f"<{bars}DSML{bars}{sp}{tag}"
    for bars in ("|", "\uff5c", "||", "\uff5c\uff5c")
    for sp in ("", " ")
    for tag in ("tool_calls", "toolcalls", "tool-calls", "calls", "invoke")
)
_HEAD_MAX = max(len(h) for h in _HEADS) + 1


def _could_be_head(fragment: str) -> bool:
    return any(h.startswith(fragment) for h in _HEADS)


def _match_head(fragment: str) -> str:
    for h in _HEADS:
        if fragment.startswith(h) and len(fragment) > len(h):
            return "wrap" if h.endswith(("calls",)) else "invoke"
    return ""


def _unescape_attr(raw: str) -> str:
    return raw.replace('\\"', '"').replace("\\\\", "\\")


def _coerce(value: str, is_string: bool) -> Any:
    if is_string:
        return value
    stripped = value.strip()
    try:
        return json.loads(stripped)
    except ValueError:
        return value


def _new_call_id() -> str:
    return f"call_dsml_{uuid.uuid4().hex[:24]}"


def parse_dsml_block(block: str) -> list[Content]:
    """把一个 DSML 块解析成 function_call 列表。

    缺闭合标签的 parameter / invoke 按到块末尾为止取值;结构性错误(invoke 没名字、
    嵌套了非自闭合的 invoke)直接抛。
    """
    pos = 0
    if m := RE_WRAP_OPEN.match(block, pos):
        pos = m.end()
    calls: list[Content] = []
    while True:
        inv = RE_INVOKE_OPEN.search(block, pos)
        if inv is None:
            break
        name = inv.group(1).strip()
        if not name:
            raise ValueError("DSML invoke 缺少 name")
        pos = inv.end()
        args: dict[str, Any] = {}
        if inv.group(3) == "/":
            calls.append(Content.from_function_call(call_id=_new_call_id(), name=name, arguments="{}"))
            continue
        while True:
            cands = [
                (RE_PARAM_OPEN.search(block, pos), "param"),
                (RE_INVOKE_OPEN.search(block, pos), "invoke"),
                (RE_INVOKE_CLOSE.search(block, pos), "close"),
            ]
            cands = [(m, kind) for m, kind in cands if m is not None]
            if not cands:
                break
            m, kind = min(cands, key=lambda c: c[0].start())
            if kind == "close":
                pos = m.end()
                break
            if kind == "param":
                pname = m.group(1)
                is_string = (m.group(2) or "true") == "true"
                end = RE_PARAM_CLOSE.search(block, m.end())
                if end is None:
                    args[pname] = _coerce(block[m.end():], is_string)
                    pos = len(block)
                    break
                args[pname] = _coerce(block[m.end():end.start()], is_string)
                pos = end.end()
                continue
            if m.group(3) != "/":
                raise ValueError(f"DSML invoke {name!r} 内部嵌套了非自闭合的 invoke {m.group(1)!r}")
            if m.group(2) is None:
                raise ValueError(f"DSML 自闭合 invoke {m.group(1)!r} 没有 string 属性")
            args[m.group(1)] = _unescape_attr(m.group(2))
            pos = m.end()
        calls.append(
            Content.from_function_call(
                call_id=_new_call_id(),
                name=name,
                arguments=json.dumps(args, ensure_ascii=False),
            )
        )
    return calls


class DsmlStreamRecoverer:
    def __init__(self) -> None:
        self._buf = ""
        self._in_block = False
        self._block_kind = ""
        self.recovered = 0

    def feed(self, delta: str) -> tuple[str, list[Content]]:
        """吃进一段文本增量,返回 (可放行的文本, 还原出的 function_call)。"""
        self._buf += delta
        out = ""
        calls: list[Content] = []
        while self._buf:
            if self._in_block:
                end = self._block_end()
                if end is None:
                    break
                block, self._buf = self._buf[:end], self._buf[end:]
                found = parse_dsml_block(block)
                self.recovered += len(found)
                calls.extend(found)
                self._in_block = False
                self._block_kind = ""
                continue
            i = self._buf.find("<")
            if i < 0:
                out += self._buf
                self._buf = ""
                break
            out += self._buf[:i]
            self._buf = self._buf[i:]
            kind = _match_head(self._buf)
            if kind:
                self._in_block = True
                self._block_kind = kind
                continue
            if len(self._buf) < _HEAD_MAX and _could_be_head(self._buf):
                break
            out += "<"
            self._buf = self._buf[1:]
        return out, calls

    def finish(self) -> tuple[str, list[Content]]:
        """流结束:未闭合的块尽力解析;不是块的残余当文本放行。"""
        buf, self._buf = self._buf, ""
        if not buf:
            return "", []
        if not self._in_block:
            return buf, []
        self._in_block = False
        self._block_kind = ""
        found = parse_dsml_block(buf)
        if not found:
            raise RuntimeError(f"模型输出了未闭合且无法解析的 DSML 工具调用块: {buf[:200]!r}")
        self.recovered += len(found)
        return "", found

    def _block_end(self) -> int | None:
        if self._block_kind == "wrap":
            m = RE_WRAP_CLOSE.search(self._buf)
            return m.end() if m else None
        m = RE_INVOKE_CLOSE.search(self._buf)
        return m.end() if m else None
