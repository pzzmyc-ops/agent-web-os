from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from agent_framework import Content, FunctionInvocationContext, tool

from apps.nextagent import tool_guard
from apps.nextagent.tool_guard import ToolGuardMiddleware, ToolTimeoutError, current_call_id, pop_presentation
from apps.nextagent.toolkit._context import TurnContext, bind_turn
from apps.nextagent.toolkit._registry import registry
from apps.nextagent.toolkit._spill import spill_text


def _ft(name: str):
    def fn() -> str:
        return name
    return tool(fn, name=name, description=name)


def _ctx(name: str, call_id: str = "") -> FunctionInvocationContext:
    return FunctionInvocationContext(function=_ft(name), arguments={}, metadata={"call_id": call_id})


class _Trace:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.gate = asyncio.Event()

    def body(self, label: str, *, hold: float = 0.0, wait_gate: bool = False):
        async def call_next() -> None:
            self.events.append(f"{label}:start")
            if wait_gate:
                await self.gate.wait()
            if hold:
                await asyncio.sleep(hold)
            self.events.append(f"{label}:end")
        return call_next


async def _run(mw: ToolGuardMiddleware, ctx: FunctionInvocationContext, call_next) -> None:
    async def body() -> None:
        await call_next()
        if ctx.result is None:
            ctx.result = "done"
    from apps.nextagent.toolkit._context import current_turn

    if current_turn() is not None:
        await mw.process(ctx, body)
        return
    with bind_turn(TurnContext(thread_id="t-guard", task_id="k-guard")):
        await mw.process(ctx, body)


class ConcurrencyGateTests(unittest.TestCase):
    def test_parallel_safe_tools_overlap(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            tr = _Trace()
            t1 = asyncio.create_task(_run(mw, _ctx("file_access_read"), tr.body("a", wait_gate=True)))
            t2 = asyncio.create_task(_run(mw, _ctx("file_access_ls"), tr.body("b", wait_gate=True)))
            await asyncio.sleep(0.01)
            self.assertEqual(sorted(tr.events), ["a:start", "b:start"])
            tr.gate.set()
            await asyncio.gather(t1, t2)
        asyncio.run(main())

    def test_exclusive_tools_serialize(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            tr = _Trace()
            t1 = asyncio.create_task(_run(mw, _ctx("unknown_write_tool"), tr.body("a", wait_gate=True)))
            await asyncio.sleep(0.01)
            t2 = asyncio.create_task(_run(mw, _ctx("another_write_tool"), tr.body("b")))
            await asyncio.sleep(0.01)
            self.assertEqual(tr.events, ["a:start"])
            tr.gate.set()
            await asyncio.gather(t1, t2)
            self.assertEqual(tr.events, ["a:start", "a:end", "b:start", "b:end"])
        asyncio.run(main())

    def test_exclusive_waits_for_running_parallel_tools(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            tr = _Trace()
            t1 = asyncio.create_task(_run(mw, _ctx("file_access_read"), tr.body("r", wait_gate=True)))
            await asyncio.sleep(0.01)
            t2 = asyncio.create_task(_run(mw, _ctx("terminal"), tr.body("w")))
            await asyncio.sleep(0.01)
            self.assertEqual(tr.events, ["r:start"])
            tr.gate.set()
            await asyncio.gather(t1, t2)
            self.assertEqual(tr.events, ["r:start", "r:end", "w:start", "w:end"])
        asyncio.run(main())

    def test_parallel_tool_waits_for_running_exclusive(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            tr = _Trace()
            t1 = asyncio.create_task(_run(mw, _ctx("terminal"), tr.body("w", wait_gate=True)))
            await asyncio.sleep(0.01)
            t2 = asyncio.create_task(_run(mw, _ctx("grep"), tr.body("r")))
            await asyncio.sleep(0.01)
            self.assertEqual(tr.events, ["w:start"])
            tr.gate.set()
            await asyncio.gather(t1, t2)
            self.assertEqual(tr.events, ["w:start", "w:end", "r:start", "r:end"])
        asyncio.run(main())

    def test_different_turns_do_not_block_each_other(self) -> None:
        async def in_turn(task_id: str, mw, ctx, body):
            with bind_turn(TurnContext(thread_id="t", task_id=task_id)):
                await _run(mw, ctx, body)

        async def main():
            mw = ToolGuardMiddleware()
            tr = _Trace()
            t1 = asyncio.create_task(in_turn("task-1", mw, _ctx("terminal"), tr.body("a", wait_gate=True)))
            await asyncio.sleep(0.01)
            t2 = asyncio.create_task(in_turn("task-2", mw, _ctx("terminal"), tr.body("b")))
            await asyncio.sleep(0.01)
            self.assertEqual(sorted(tr.events), ["a:start", "b:end", "b:start"])
            tr.gate.set()
            await asyncio.gather(t1, t2)
        asyncio.run(main())

    def test_gates_are_released_and_dropped(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            tr = _Trace()
            await _run(mw, _ctx("terminal"), tr.body("a"))
            await _run(mw, _ctx("grep"), tr.body("b"))
            self.assertEqual(tool_guard._gates, {})
        asyncio.run(main())

    def test_call_outside_a_turn_is_rejected(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            ctx = _ctx("grep")

            async def body() -> None:
                ctx.result = "x"
            with self.assertRaises(RuntimeError):
                await mw.process(ctx, body)
            self.assertEqual(tool_guard._gates, {})
        asyncio.run(main())

    def test_registry_flag_wins_over_builtin_list(self) -> None:
        self.assertTrue(tool_guard.is_parallel_safe("grep"))
        self.assertTrue(tool_guard.is_parallel_safe("glob"))
        self.assertTrue(tool_guard.is_parallel_safe("tool_help"))
        self.assertFalse(tool_guard.is_parallel_safe("terminal"))
        self.assertFalse(tool_guard.is_parallel_safe("clarify"))
        self.assertFalse(tool_guard.is_parallel_safe("file_access_write"))
        self.assertTrue(tool_guard.is_parallel_safe("file_access_read"))


class CallIdTests(unittest.TestCase):
    def test_call_id_visible_inside_body_and_cleared_after(self) -> None:
        seen: list[str] = []

        async def body() -> None:
            seen.append(current_call_id())

        async def main():
            mw = ToolGuardMiddleware()
            await _run(mw, _ctx("grep", call_id="call_123"), body)
            self.assertEqual(seen, ["call_123"])
            self.assertEqual(current_call_id(), "")
        asyncio.run(main())

    def test_parallel_bodies_see_their_own_call_id(self) -> None:
        seen: dict[str, str] = {}

        def body(label: str):
            async def call_next() -> None:
                await asyncio.sleep(0.01)
                seen[label] = current_call_id()
            return call_next

        async def main():
            mw = ToolGuardMiddleware()
            await asyncio.gather(
                _run(mw, _ctx("grep", call_id="c-a"), body("a")),
                _run(mw, _ctx("glob", call_id="c-b"), body("b")),
            )
            self.assertEqual(seen, {"a": "c-a", "b": "c-b"})
        asyncio.run(main())


class TimeoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        @registry.register(toolset="test", name="test_guard_slow", summary="x", description="x", timeout_seconds=0.05)
        def test_guard_slow() -> str:
            return "slow"

    def test_timeout_raises_readable_error(self) -> None:
        async def body() -> None:
            await asyncio.sleep(1.0)

        async def main():
            mw = ToolGuardMiddleware()
            with self.assertRaises(ToolTimeoutError) as cm:
                await _run(mw, _ctx("test_guard_slow"), body)
            self.assertIn("test_guard_slow", str(cm.exception))
            self.assertIn("0.05", str(cm.exception))
            self.assertEqual(tool_guard._gates, {})
        asyncio.run(main())

    def test_fast_call_within_timeout_passes(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            ctx = _ctx("test_guard_slow")

            async def body() -> None:
                ctx.result = "ok"
            await _run(mw, ctx, body)
            self.assertEqual(ctx.result, "ok")
        asyncio.run(main())

    def test_register_rejects_bad_contract(self) -> None:
        with self.assertRaises(ValueError):
            registry.register(toolset="test", name="bad_timeout", timeout_seconds=0)
        with self.assertRaises(ValueError):
            registry.register(toolset="test", name="bad_limit", max_result_chars=1)


class ResultLimitTests(unittest.TestCase):
    def test_oversized_string_result_is_spilled(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            ctx = _ctx("grep")

            async def body() -> None:
                ctx.result = "x" * 20_000

            with mock.patch.object(tool_guard, "spill_text", return_value={"text": "SPILLED"}) as sp:
                await _run(mw, ctx, body)
            self.assertEqual(ctx.result, "SPILLED")
            self.assertEqual(sp.call_args.args[1], 10_000)
        asyncio.run(main())

    def test_small_results_untouched(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            ctx2 = _ctx("grep")

            async def body2() -> None:
                ctx2.result = "short"
            await _run(mw, ctx2, body2)
            self.assertEqual(ctx2.result, "short")
        asyncio.run(main())

    def test_spill_text_respects_custom_limit(self) -> None:
        text = "A" * 300 + "B" * 300
        spilled = spill_text(text, 100)
        self.assertIsNotNone(spilled)
        try:
            self.assertTrue(spilled["text"].startswith("A" * 50))
            self.assertTrue(spilled["text"].endswith("B" * 50))
            self.assertEqual(spilled["elided_chars"], 500)
            self.assertIsNone(spill_text(text, 600))
        finally:
            from fm.backend.pathutil import resolve
            Path(resolve(spilled["output_file"])).unlink(missing_ok=True)

    def test_spill_text_rejects_tiny_limit(self) -> None:
        with self.assertRaises(ValueError):
            spill_text("abc", 1)

    def test_framework_wrapped_text_list_is_unwrapped_and_spilled(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            ctx = _ctx("file_access_read")

            async def body() -> None:
                ctx.result = [Content.from_text("x" * 20_000)]

            with mock.patch.object(tool_guard, "spill_text", return_value={"text": "SPILLED"}) as sp:
                await _run(mw, ctx, body)
            self.assertEqual(ctx.result, "SPILLED")
            self.assertEqual(len(sp.call_args.args[0]), 20_000)
        asyncio.run(main())

    def test_content_list_with_media_passes_through(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            ctx = _ctx("view_image")
            items = [Content.from_text("x" * 20_000), Content.from_data(b"\x89PNG", media_type="image/png")]

            async def body() -> None:
                ctx.result = items
            await _run(mw, ctx, body)
            self.assertIs(ctx.result, items)
        asyncio.run(main())


class RenderPresentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def render(args, value):
            return f"{args.get('q')}:{value['n']}"

        def present(args, value):
            return value.get("card")

        @registry.register(toolset="test", name="test_guard_struct", summary="x", description="x", render=render, present=present)
        def test_guard_struct(q: str = "") -> dict:
            return {}

        @registry.register(toolset="test", name="test_guard_plain_dict", summary="x", description="x")
        def test_guard_plain_dict() -> dict:
            return {}

    def test_dict_goes_through_render_and_present(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            ctx = FunctionInvocationContext(
                function=_ft("test_guard_struct"), arguments={"q": "hello"}, metadata={"call_id": "c-1"},
            )

            async def body() -> None:
                ctx.result = {"n": 7, "card": {"mediaType": "image", "urls": ["/a.png"]}}
            await _run(mw, ctx, body)
            self.assertEqual(ctx.result, "hello:7")
            self.assertEqual(pop_presentation("c-1"), {"mediaType": "image", "urls": ["/a.png"]})
            self.assertIsNone(pop_presentation("c-1"))
        asyncio.run(main())

    def test_present_returning_none_leaves_no_payload(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            ctx = FunctionInvocationContext(
                function=_ft("test_guard_struct"), arguments={"q": "x"}, metadata={"call_id": "c-2"},
            )

            async def body() -> None:
                ctx.result = {"n": 1}
            await _run(mw, ctx, body)
            self.assertEqual(ctx.result, "x:1")
            self.assertIsNone(pop_presentation("c-2"))
        asyncio.run(main())

    def test_dict_without_render_is_json(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            ctx = _ctx("test_guard_plain_dict", call_id="c-3")

            async def body() -> None:
                ctx.result = {"ok": True, "中": "文"}
            await _run(mw, ctx, body)
            self.assertEqual(ctx.result, '{"ok": true, "中": "文"}')
        asyncio.run(main())

    def test_none_result_is_an_error(self) -> None:
        async def main():
            mw = ToolGuardMiddleware()
            ctx = _ctx("test_guard_plain_dict")

            async def body() -> None:
                ctx.result = None
            with bind_turn(TurnContext(thread_id="t", task_id="k")), self.assertRaises(TypeError):
                await mw.process(ctx, body)
        asyncio.run(main())

    def test_registry_tools_return_raw_values(self) -> None:
        from apps.nextagent.toolkit._registry import ToolRegistry

        async def main():
            local = ToolRegistry()

            @local.register(toolset="test", name="raw_dict", summary="x", description="x")
            def raw_dict() -> dict:
                return {"a": 1}

            got = await raw_dict.invoke(arguments={})
            self.assertEqual(got, {"a": 1})
        asyncio.run(main())


class SingleTruncationTests(unittest.TestCase):
    def test_terminal_payload_is_spilled_once_through_the_guard(self) -> None:
        import json
        from apps.nextagent.toolkit.terminal import render_terminal

        value = {"output": "y" * 30_014, "exit_code": 0, "session_id": "s", "status": "idle", "error": None}
        created: list[str] = []
        real = tool_guard.spill_text

        def counting(text, limit=10_000):
            out = real(text, limit)
            if out is not None:
                created.append(out["output_file"])
            return out

        async def main():
            mw = ToolGuardMiddleware()
            ctx = FunctionInvocationContext(
                function=registry.get("terminal").tool, arguments={"command": "x"}, metadata={"call_id": "c-t"},
            )

            async def body() -> None:
                ctx.result = value
            with mock.patch("apps.nextagent.toolkit.terminal.spill_text", counting), \
                 mock.patch.object(tool_guard, "spill_text", counting):
                await _run(mw, ctx, body)
            data = json.loads(ctx.result)
            self.assertEqual(data["output_file"], created[0])
            self.assertEqual(len(created), 1)
            self.assertEqual(data["output"].count("省略"), 1)
            self.assertLess(len(ctx.result), registry.get("terminal").max_result_chars)
        try:
            asyncio.run(main())
        finally:
            from fm.backend.pathutil import resolve
            for f in created:
                Path(resolve(f)).unlink(missing_ok=True)

    def test_render_terminal_alone_matches_old_contract(self) -> None:
        import json
        from apps.nextagent.toolkit.terminal import render_terminal

        text = render_terminal({"command": "x"}, {"output": "short", "exit_code": 0})
        self.assertEqual(json.loads(text)["output"], "short")


class MediaProjectionTests(unittest.TestCase):
    def test_render_media_value_splits_model_text_and_card(self) -> None:
        from apps.nextagent.toolkit.media import present_shown, render_shown

        value = {"ok": True, "path": "D:/a.png", "kind": "image", "shown": True, "media": {"mediaType": "image"}}
        import json
        text = json.loads(render_shown({}, value))
        self.assertNotIn("media", text)
        self.assertEqual(text["path"], "D:/a.png")
        self.assertEqual(present_shown({}, value), {"mediaType": "image"})
        self.assertIsNone(present_shown({}, {"ok": False, "error": "x"}))

    def test_tracker_drops_presentation_for_interrupted_or_unknown_calls(self) -> None:
        from apps.nextagent.tool_tracker import ToolTracker

        class Emitter:
            async def turn(self, kind, data):
                pass

        class Store:
            def append_event(self, *a, **k):
                pass

        async def main():
            tr = ToolTracker(Emitter(), task_id="t", thread_id="th", store=Store())
            await tr.on_content(Content.from_function_call(call_id="c-open", name="render_media", arguments="{}"))
            tool_guard._presentations["c-open"] = {"mediaType": "image"}
            tool_guard._presentations["c-ghost"] = {"mediaType": "image"}
            with mock.patch("apps.nextagent.tool_tracker.notify_fs_changed"):
                await tr.on_content(Content.from_function_result(call_id="c-ghost", result="{}"))
            await tr.close_open()
            self.assertEqual(tool_guard._presentations, {})
        asyncio.run(main())

    def test_tracker_attaches_presentation_to_its_own_call(self) -> None:
        from apps.nextagent.tool_tracker import ToolTracker

        events: list[tuple[str, dict]] = []

        class Emitter:
            async def turn(self, kind, data):
                events.append((kind, data))

        class Store:
            def append_event(self, *a, **k):
                pass

        async def main():
            tr = ToolTracker(Emitter(), task_id="t", thread_id="th", store=Store())
            await tr.on_content(Content.from_function_call(call_id="c-a", name="render_media", arguments='{"path": "a"}'))
            await tr.on_content(Content.from_function_call(call_id="c-b", name="render_media", arguments='{"path": "b"}'))
            tool_guard._presentations["c-a"] = {"mediaType": "image", "urls": ["/a"]}
            tool_guard._presentations["c-b"] = {"mediaType": "image", "urls": ["/b"]}
            with mock.patch("apps.nextagent.tool_tracker.notify_fs_changed"):
                await tr.on_content(Content.from_function_result(call_id="c-b", result='{"ok": true}'))
                await tr.on_content(Content.from_function_result(call_id="c-a", result='{"ok": true}'))
            ends = [d for k, d in events if k == "block_end"]
            self.assertEqual([e["media"]["urls"] for e in ends], [["/b"], ["/a"]])
            self.assertEqual(ends[0]["media"]["bgTaskId"], ends[0]["blockId"])
            self.assertEqual(ends[0]["result"], '{"ok": true}')
            self.assertEqual(tool_guard._presentations, {})
        asyncio.run(main())
