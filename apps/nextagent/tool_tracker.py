"""工具生命周期的统一出口:function_call / function_result → 工具块事件 + 事件日志。

所有工具的开始 / 完成事件都在这里产生,单个工具不碰协议;新增工具不用做任何事。

桌面刷新:任何工具跑完都调文件管理器的刷新接口(fm.backend.desktop.notify_fs_changed),
由文件管理器自己把在线的桌面页刷一遍。这里不猜哪个工具改了哪个目录。
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from fm.backend.desktop import notify_fs_changed

from . import session_log
from .turn_protocol import TurnEmitter, now_ms

AsyncHook = Callable[[], Awaitable[None]]


async def _noop() -> None:
    return None


class ToolTracker:
    """把流里的 function_call / function_result 翻译成块事件 + 文件变更事件。

    实测的流形状:

        update N    function_call(call_id="call_x", name="file_access_write", arguments="")
        update N+1  function_call(call_id="",       name="",  arguments="{")
        update N+2  function_call(call_id="",       name="",  arguments='"file')
        ...                                       ← 参数逐字符片到达,标识字段是空的
        update M    function_result(call_id="call_x", result="File '…' written.")

    参数片不带 call_id,只能累积到「最后一个带 call_id 的调用」上。

    block_open 故意发两次:第一次在调用刚出现时(input 还是空,前端立刻显示
    waiting + 工具名,用户马上有反馈),第二次在结果到达时补上解析好的 input。
    前端 upsertBlock 对 input 是合并语义(blocks-renderer.js:732),所以两次
    open 是幂等的,不用改前端。

    事件同时进 TurnEventBus:这样断线重连(turn_resume)和页面刷新(tasks_snapshot)
    时前端可以把工具块重建出来,而不是只剩文本气泡。

    落盘也在这里:tool_call 和 tool_result 是两条独立事件。以前调用和结果被打包成一条
    assistant 记录里的 blocks 字符串,而调用先落盘、结果后到达 —— 结果就再也写不回去,
    历史里那次调用永远停在「open」,模型下一轮拿不到它的返回值。现在结果到达时自己
    追加一行,不需要回头改任何已经写下的东西。

    on_split:普通工具出现时调用,由文本块层关掉正在显示的文字块并把已说的部分落盘;
    on_persist:编排类(静默)工具出现时调用,只落盘不关块。
    """

    def __init__(
        self,
        emitter: TurnEmitter,
        *,
        task_id: str,
        thread_id: str,
        store: Any,
        message_id: str = "",
        on_split: AsyncHook = _noop,
        on_persist: AsyncHook = _noop,
    ):
        self._em = emitter
        self._task_id = task_id
        self._thread_id = thread_id
        self._store = store
        self._message_id = message_id
        self._calls: dict[str, dict[str, Any]] = {}
        self._current = ""
        self._n = 0
        self._on_split = on_split
        self._on_persist = on_persist

    @property
    def thread_id(self) -> str:
        return self._thread_id

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def store(self) -> Any:
        return self._store

    async def on_content(self, content: Any) -> bool:
        """处理一个 content。返回 True 表示已消费,调用方不必再当文本处理。"""
        ctype = getattr(content, "type", None)
        if ctype == "function_call":
            await self._on_call(content)
            return True
        if ctype == "function_result":
            await self._on_result(content)
            return True
        return False

    @property
    def has_open_calls(self) -> bool:
        return bool(self._calls)

    async def close_open(self) -> None:
        """回合结束时把还没收到结果的工具块收尾,避免前端留一个永远转圈的块。

        同时补一条 tool_result 事件:只有调用没有结果的一半消息发给上游会被判 400。
        以前的做法是投影时把没跑完的调用整块丢掉,模型下一轮就会把同一件事重做一遍。
        """
        for call_id, call in list(self._calls.items()):
            self._log_call(call_id, call)
            end_data = {
                "blockId": call["blockId"], "kind": "tool", "name": call["name"],
                "status": "error", "result": "未收到工具结果(回合被中断)",
                "durationMs": now_ms() - call["startedAt"],
            }
            if not call.get("silent_ui"):
                await self._em.turn("block_end", end_data)
            self._log_result(call_id, call, end_data)
        self._calls.clear()

    def _log_call(self, call_id: str, call: dict[str, Any]) -> None:
        """写 tool_call 事件。一次调用只写一条 —— 参数收全时写,没收全就在结果到达时写。"""
        if call.get("logged"):
            return
        call["logged"] = True
        self._store.append_event(
            self._thread_id,
            session_log.EV_TOOL_CALL,
            task_id=self._task_id,
            call_id=call_id,
            block_id=call["blockId"],
            name=call["name"],
            arguments=self._parse_args(call["args"]),
            extra_content=call.get("extra_content"),
        )

    def _log_result(self, call_id: str, call: dict[str, Any], end_data: dict[str, Any]) -> None:
        self._store.append_event(
            self._thread_id,
            session_log.EV_TOOL_RESULT,
            task_id=self._task_id,
            call_id=call_id,
            block_id=call["blockId"],
            name=call["name"],
            result=str(end_data.get("result") or ""),
            status=str(end_data.get("status") or "done"),
            duration_ms=end_data.get("durationMs"),
            completed_at=end_data.get("completedAt"),
            media=end_data.get("media"),
        )

    def current_block(self) -> dict[str, Any] | None:
        """正在执行的那个工具调用(blockId / name / startedAt)。

        给 clarify 用:工具函数被框架调用时拿不到自己的 call_id,而前端要求把问题
        补在**它自己的工具块**上。工具体执行时,它的 function_call 已经完整流完,
        所以 self._current 就是它 —— 这是唯一能把两者对上的时机。

        并行工具调用时 _current 可能不是调用方那个块。mafagent 目前是顺序执行工具,
        且 clarify 会阻塞整轮(至多一个在飞),所以够用;真要并行,得由框架把 call_id
        传进工具上下文才能彻底解决。
        """
        if not self._current:
            return None
        return self._calls.get(self._current)

    async def patch_block(self, block_id: str, patch: dict[str, Any]) -> None:
        """给一个已开的块补字段。

        发 block_patch 而不是 block_open —— 前端 blockOpen 只转发
        blockId/kind/name/input/status/mediaSkill 五个字段,不认识的会丢掉。
        blockPatch 会合并 clarify、重新触发 renderBlock。
        """
        call = self._calls.get(self._current) if self._current else None
        data = {"blockId": block_id, "kind": "tool", **patch}
        if call is not None:
            data["name"] = call["name"]
        await self._em.turn("block_patch", data)

    @staticmethod
    def _arg_fragment(content: Any) -> str:
        args = getattr(content, "arguments", None)
        if args is None:
            return ""
        if isinstance(args, str):
            return args
        try:
            return json.dumps(args, ensure_ascii=False)
        except Exception:
            return ""

    @staticmethod
    def _parse_complete_args(raw: str) -> dict | None:
        """只在参数已经是完整 JSON 时返回解析结果,残缺分片返回 None。"""
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    async def _send_input_when_complete(self, call_id: str, call: dict[str, Any]) -> None:
        """参数一收全就补进工具块,不等工具执行完。

        模型必须先把整个调用输出完,框架才会去执行它 —— 所以 JSON 刚好能解析的
        那一刻,命令还没开始跑。在这里补发,长时间执行的 terminal 才能在执行期间
        就显示出自己在跑什么,而不是结束了才揭晓。

        tool_call 事件也在这里落盘:调用确实已经发生了,进程要是死在执行中途,
        重启时的补齐扫描才知道有一次调用没拿到结果。
        """
        if call.get("inputSent"):
            return
        parsed = self._parse_complete_args(call["args"])
        if parsed is None:
            return
        call["inputSent"] = True
        if not call.get("silent_ui"):
            open_data = {
                "blockId": call["blockId"], "kind": "tool", "name": call["name"], "input": parsed,
            }
            await self._em.turn("block_open", open_data)
        self._log_call(call_id, call)

    @staticmethod
    def _parse_args(raw: str) -> dict:
        raw = (raw or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except Exception:
            return {"_raw": raw[:400]}
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    @staticmethod
    def _lift_media(raw: str, block_id: str) -> tuple[str, dict[str, Any] | None]:
        """把工具结果里的 `_media` 提走 → (给模型看的结果, 给前端渲染的载荷)。

        render_media 把渲染载荷塞在返回 JSON 的 `_media` 键里(见 toolkit/media.py)。
        载荷贴到**这次调用自己**的 block_end 上,同时从模型看到的结果里删掉 —— 那段
        内容是给前端渲染用的,留着模型会把 URL 和路径再复述一遍。

        bgTaskId / mediaTaskId 在这里补:前端拿它定位媒体区、做版本号,而工具函数
        执行时拿不到自己的 blockId。

        不是媒体结果就原样返回,一个字节都不动 —— 普通工具结果不能因为路过这里被
        重新序列化。
        """
        if not isinstance(raw, str) or "_media" not in raw:
            return raw, None
        try:
            parsed = json.loads(raw)
        except Exception:
            return raw, None
        if not isinstance(parsed, dict):
            return raw, None
        media = parsed.get("_media")
        if not isinstance(media, dict):
            return raw, None
        rest = {k: v for k, v in parsed.items() if k != "_media"}
        payload = {**media, "bgTaskId": block_id, "mediaTaskId": block_id}
        return json.dumps(rest, ensure_ascii=False), payload

    async def _on_call(self, content: Any) -> None:
        call_id = str(getattr(content, "call_id", "") or "")
        name = str(getattr(content, "name", "") or "")
        frag = self._arg_fragment(content)

        if call_id and name:
            silent_ui = session_log.is_orchestration_tool(name)
            if silent_ui:
                await self._on_persist()
            else:
                await self._on_split()
            self._n += 1
            block_id = f"tool:{self._task_id}:{self._n}"
            props = getattr(content, "additional_properties", None) or {}
            extra = props.get("extra_content")
            self._calls[call_id] = {
                "name": name,
                "args": frag,
                "blockId": block_id,
                "startedAt": now_ms(),
                "inputSent": False,
                "silent_ui": silent_ui,
            }
            if isinstance(extra, dict) and extra:
                self._calls[call_id]["extra_content"] = extra
            self._current = call_id
            if not silent_ui:
                open_data = {"blockId": block_id, "kind": "tool", "name": name, "input": {}}
                if isinstance(extra, dict) and extra:
                    open_data["extra_content"] = extra
                await self._em.turn("block_open", open_data)
            await self._send_input_when_complete(call_id, self._calls[call_id])
            return

        call = self._calls.get(self._current)
        if call is not None and frag:
            call["args"] += frag
            await self._send_input_when_complete(self._current, call)

    async def _on_result(self, content: Any) -> None:
        call_id = str(getattr(content, "call_id", "") or "")
        key = call_id
        call = self._calls.pop(call_id, None)
        if call is None and self._current:
            key = self._current
            call = self._calls.pop(self._current, None)
        if call is None:
            return

        exc = getattr(content, "exception", None)
        result = getattr(content, "result", None)
        text = "" if result is None else (result if isinstance(result, str) else str(result))
        status = "error" if exc else "done"
        parsed = self._parse_args(call["args"])
        text, media = self._lift_media(text, call["blockId"])
        if call.get("silent_ui"):
            now = now_ms()
            end_data = {
                "blockId": call["blockId"],
                "kind": "tool",
                "name": call["name"],
                "status": status,
                "result": str(exc) if exc else text,
                "durationMs": now - call["startedAt"],
                "completedAt": now,
            }
            self._log_call(key, call)
            self._log_result(key, call, end_data)
            return

        open_data = {"blockId": call["blockId"], "kind": "tool", "name": call["name"], "input": parsed}
        if call.get("extra_content"):
            open_data["extra_content"] = call["extra_content"]
        await self._em.turn("block_open", open_data)
        now = now_ms()
        end_data = {
            "blockId": call["blockId"],
            "kind": "tool",
            "name": call["name"],
            "status": status,
            "result": str(exc) if exc else text,
            "durationMs": now - call["startedAt"],
            "completedAt": now,
        }
        if media is not None:
            end_data["media"] = media
        await self._em.turn("block_end", end_data)
        # 调用事件必须先在日志里(参数没收全时 _send_input_when_complete 没写过),
        # 结果紧跟其后 —— 投影靠这个顺序,不靠时间戳
        self._log_call(key, call)
        self._log_result(key, call, end_data)
        notify_fs_changed()
