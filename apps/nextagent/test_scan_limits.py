import asyncio
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from fm.backend.settings import ROOT

from apps.nextagent import fm_store
from apps.nextagent.file_punct import _render_ls
from apps.nextagent.framework_compat import FileStoreEntry
from apps.nextagent.toolkit import glob as glob_tool
from apps.nextagent.toolkit import grep as grep_tool


class TempWorkspaceDir(unittest.TestCase):
    dir_name = ""
    file_count = 0

    @classmethod
    def setUpClass(cls):
        cls.root = Path(ROOT) / cls.dir_name
        if cls.root.exists():
            shutil.rmtree(cls.root)
        cls.root.mkdir(parents=True)
        for i in range(cls.file_count):
            (cls.root / f"f{i:04d}.txt").write_text("needle\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)


class ListChildrenCapTests(TempWorkspaceDir):
    dir_name = "_test_ls_cap"
    file_count = 30

    def capped(self, limit):
        store = fm_store.FmAgentFileStore()
        return asyncio.run(store.list_children_capped(self.dir_name, limit))

    def test_under_limit_returns_everything(self):
        entries, truncated = self.capped(100)
        self.assertEqual(len(entries), 30)
        self.assertFalse(truncated)

    def test_over_limit_stops_early_and_flags_truncation(self):
        entries, truncated = self.capped(10)
        self.assertEqual(len(entries), 10)
        self.assertTrue(truncated)

    def test_default_list_children_applies_the_cap(self):
        store = fm_store.FmAgentFileStore()
        with mock.patch.object(fm_store, "LS_MAX_ENTRIES", 7):
            entries = asyncio.run(store.list_children(self.dir_name))
        self.assertEqual(len(entries), 7)


class RenderLsTests(unittest.TestCase):
    def test_directories_carry_a_trailing_slash(self):
        entries = [
            FileStoreEntry("sub", FileStoreEntry.DIRECTORY),
            FileStoreEntry("a.txt", FileStoreEntry.FILE),
        ]
        rendered = _render_ls(entries, False)
        self.assertEqual(
            rendered.splitlines(),
            ["sub/", "a.txt", "[entries=2 truncated=false]"],
        )

    def test_truncated_listing_carries_a_note(self):
        rendered = _render_ls([FileStoreEntry("a.txt", FileStoreEntry.FILE)], True)
        lines = rendered.splitlines()
        self.assertEqual(lines[-2], "[entries=1 truncated=true]")
        self.assertTrue(lines[-1].startswith("Note: "))
        self.assertIn("glob", lines[-1])


class GlobTruncationTests(TempWorkspaceDir):
    dir_name = "_test_glob_cap"
    file_count = 12

    def test_full_result_has_no_note(self):
        out = glob_tool.glob("*.txt", self.dir_name)
        lines = out.splitlines()
        self.assertEqual(lines[-1], "[total_files=12 shown=12 truncated=false]")

    def test_client_truncation_reports_remaining_count(self):
        with mock.patch.object(glob_tool, "MAX_FILES", 5):
            out = glob_tool.glob("*.txt", self.dir_name)
        lines = out.splitlines()
        self.assertEqual(lines[-2], "[total_files=12 shown=5 truncated=true]")
        self.assertIn("还有 7 个文件没有显示", lines[-1])

    def test_scan_truncation_says_at_least(self):
        with mock.patch.object(glob_tool, "MAX_SCAN", 4), \
             mock.patch.object(glob_tool, "MAX_FILES", 5):
            out = glob_tool.glob("*.txt", self.dir_name)
        lines = out.splitlines()
        self.assertEqual(lines[-3], "[total_files=12 shown=4 truncated=true]")
        self.assertIn("至少还有 8 个文件没有显示", lines[-2])
        self.assertIn("排序结果不代表全局最新", lines[-1])


class GrepTruncationTests(TempWorkspaceDir):
    dir_name = "_test_grep_cap"
    file_count = 12

    def test_head_limit_hit_appends_note(self):
        out = grep_tool.grep("needle", path=self.dir_name, head_limit=5)
        lines = out.splitlines()
        self.assertIn("head_limit_applied=true", lines[-2])
        self.assertEqual(lines[-1], grep_tool.TRUNCATED_NOTE)

    def test_complete_result_has_no_note(self):
        out = grep_tool.grep("needle", path=self.dir_name, head_limit=50)
        lines = out.splitlines()
        self.assertIn("head_limit_applied=false", lines[-1])
        self.assertNotIn("Note:", out)


class TerminalTimeoutBackgroundTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.desk = _Desk()

    def _call(self, fn):
        from apps.nextagent.toolkit._context import TurnContext, bind_turn

        ctx = TurnContext(thread_id="t-scan", task_id="k-scan", desktop_id="desk")
        with bind_turn(ctx), \
             mock.patch("fm.backend.terminal.online_desktop_ids", lambda: ["desk"]), \
             mock.patch("fm.backend.terminal._desktop_online", self.desk.online_fn), \
             mock.patch("fm.backend.terminal._desktop_call", self.desk.call):
            return fn()

    def test_foreground_timeout_backgrounds_instead_of_killing(self):
        from fm.backend.pathutil import IS_WINDOWS
        from fm.backend.terminal import REGISTRY
        from apps.nextagent.toolkit.process import process
        from apps.nextagent.toolkit.terminal import terminal

        command = "Write-Output started; Start-Sleep -Seconds 8" if IS_WINDOWS else "echo started; sleep 8"

        def run():
            return json.loads(terminal(command=command, workdir=self.temp.name, timeout=1))

        result = self._call(run)
        session_id = result["session_id"]
        self.addCleanup(lambda: REGISTRY.close(session_id) if REGISTRY.has(session_id) else None)

        self.assertEqual(result["status"], "running")
        self.assertIsNone(result["exit_code"])
        self.assertIn("started", result["output"])

        def poll():
            return json.loads(process(action="poll", session_id=session_id))

        polled = self._call(poll)
        self.assertEqual(polled["status"], "running")
        self.assertIn("started", polled["output_preview"])

        def kill():
            return json.loads(process(action="kill", session_id=session_id))

        killed = self._call(kill)
        self.assertEqual(killed["status"], "closed")
        self.assertFalse(REGISTRY.has(session_id))

    def test_backgrounded_command_finishes_and_keeps_full_output(self):
        from fm.backend.pathutil import IS_WINDOWS
        from fm.backend.terminal import REGISTRY
        from apps.nextagent.toolkit.process import process
        from apps.nextagent.toolkit.terminal import terminal

        command = "Write-Output A; Start-Sleep -Seconds 3; Write-Output B" if IS_WINDOWS else "echo A; sleep 3; echo B"

        def run():
            return json.loads(terminal(command=command, workdir=self.temp.name, timeout=1))

        result = self._call(run)
        self.assertEqual(result["status"], "running")
        session_id = result["session_id"]
        self.addCleanup(lambda: REGISTRY.close(session_id) if REGISTRY.has(session_id) else None)

        def wait():
            return json.loads(process(action="wait", session_id=session_id, timeout=20))

        waited = self._call(wait)
        self.assertEqual(waited["status"], "idle")
        self.assertEqual(waited["exit_code"], 0)

        def log():
            return json.loads(process(action="log", session_id=session_id))

        logged = self._call(log)
        self.assertIn("A", logged["output"])
        self.assertIn("B", logged["output"])

    def test_turn_without_desktop_id_opens_on_the_online_desktop(self):
        from fm.backend.terminal import REGISTRY
        from apps.nextagent.toolkit._context import TurnContext, bind_turn
        from apps.nextagent.toolkit.terminal import terminal

        ctx = TurnContext(thread_id="t-scan", task_id="k-scan", desktop_id="")

        def run():
            return json.loads(terminal(command="", workdir=self.temp.name))

        with bind_turn(ctx), \
             mock.patch("fm.backend.terminal.online_desktop_ids", lambda: ["desk"]), \
             mock.patch("fm.backend.terminal._desktop_online", self.desk.online_fn), \
             mock.patch("fm.backend.terminal._desktop_call", self.desk.call):
            result = run()
        session_id = result["session_id"]
        self.addCleanup(lambda: REGISTRY.close(session_id) if REGISTRY.has(session_id) else None)
        self.assertEqual(result["error"], None)
        self.assertTrue(session_id)
        self.assertIn(session_id, self.desk.opened)


class TerminalOutputCapTests(unittest.TestCase):
    size = 300_000

    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.desk = _Desk()

    def _call(self, fn):
        from apps.nextagent.toolkit._context import TurnContext, bind_turn

        ctx = TurnContext(thread_id="t-cap", task_id="k-cap", desktop_id="desk")
        with bind_turn(ctx), \
             mock.patch("fm.backend.terminal.online_desktop_ids", lambda: ["desk"]), \
             mock.patch("fm.backend.terminal._desktop_online", self.desk.online_fn), \
             mock.patch("fm.backend.terminal._desktop_call", self.desk.call):
            return fn()

    def _assert_capped(self, payload):
        from apps.nextagent.toolkit.terminal import OUTPUT_HEAD_CHARS, OUTPUT_TAIL_CHARS

        self.assertLessEqual(len(payload["output"]), OUTPUT_HEAD_CHARS + OUTPUT_TAIL_CHARS + 300)
        self.assertGreater(payload["elided_chars"], 0)
        saved = Path(payload["output_file"])
        self.assertTrue(saved.is_file())
        self.assertIn("y" * self.size, saved.read_text(encoding="utf-8"))
        self.addCleanup(lambda: saved.unlink(missing_ok=True))

    def test_huge_single_line_is_capped_and_saved(self):
        from fm.backend.terminal import REGISTRY
        from apps.nextagent.toolkit.process import process
        from apps.nextagent.toolkit.terminal import terminal

        command = f"python -c \"print('y'*{self.size})\""

        def run():
            return json.loads(terminal(command=command, workdir=self.temp.name, timeout=60))

        result = self._call(run)
        session_id = result["session_id"]
        self.addCleanup(lambda: REGISTRY.close(session_id) if REGISTRY.has(session_id) else None)
        self.assertEqual(result["exit_code"], 0)
        self._assert_capped(result)

        logged = self._call(lambda: json.loads(process(action="log", session_id=session_id)))
        self._assert_capped(logged)

        killed = self._call(lambda: json.loads(process(action="kill", session_id=session_id)))
        self.assertEqual(killed["status"], "closed")
        self._assert_capped(killed)

    def test_short_output_is_untouched(self):
        from fm.backend.terminal import REGISTRY
        from apps.nextagent.toolkit.terminal import terminal

        def run():
            return json.loads(terminal(command="python -c \"print('short')\"", workdir=self.temp.name, timeout=60))

        result = self._call(run)
        session_id = result["session_id"]
        self.addCleanup(lambda: REGISTRY.close(session_id) if REGISTRY.has(session_id) else None)
        self.assertIn("short", result["output"])
        self.assertNotIn("output_file", result)
        self.assertNotIn("elided_chars", result)


class ReadSliceTests(unittest.TestCase):
    def test_small_file_is_returned_as_is(self):
        from apps.nextagent.toolkit._spill import read_slice

        self.assertEqual(read_slice("a\nb\n", "f.txt", None, None), "a\nb\n")

    def test_big_file_without_range_is_refused(self):
        from apps.nextagent.toolkit._spill import READ_MAX_CHARS, read_slice

        out = read_slice("x" * (READ_MAX_CHARS + 1), "f.txt", None, None)
        self.assertTrue(out.startswith("Could not read file 'f.txt'"))
        self.assertIn("offset", out)

    def test_range_returns_only_those_lines(self):
        from apps.nextagent.toolkit._spill import read_slice

        content = "".join(f"line{i}\n" for i in range(1, 11))
        out = read_slice(content, "f.txt", 3, 2)
        self.assertEqual(out, "line3\nline4\n\n[lines 3-4 of 10]")

    def test_huge_single_line_range_is_refused(self):
        from apps.nextagent.toolkit._spill import READ_MAX_CHARS, read_slice

        out = read_slice("y" * (READ_MAX_CHARS * 3), "f.txt", 1, 1)
        self.assertTrue(out.startswith("Could not read file"))
        self.assertIn("grep", out)

    def test_offset_past_end_is_refused(self):
        from apps.nextagent.toolkit._spill import read_slice

        out = read_slice("a\n", "f.txt", 5, None)
        self.assertIn("超出范围", out)


class FitMessagesTests(unittest.TestCase):
    def test_small_list_is_unchanged(self):
        from apps.nextagent.toolkit._spill import fit_messages

        payload = {"conversation_id": "c", "complete": True, "hasMore": False, "messages": [{"text": "hi"}]}
        self.assertEqual(json.loads(fit_messages(payload)), payload)

    def test_big_list_is_saved_to_file(self):
        from apps.nextagent.toolkit._spill import OUTPUT_LIMIT_CHARS, fit_messages

        messages = [{"text": "z" * 200} for _ in range(200)]
        payload = {"conversation_id": "c", "complete": True, "hasMore": False, "messages": messages}
        out = fit_messages(payload)
        data = json.loads(out)
        self.assertLess(len(out), OUTPUT_LIMIT_CHARS + 1000)
        self.assertEqual(data["message_count"], 200)
        saved = Path(data["output_file"])
        self.addCleanup(lambda: saved.unlink(missing_ok=True))
        self.assertEqual(json.loads(saved.read_text(encoding="utf-8")), payload)


class SkillResourceCapTests(unittest.TestCase):
    def test_big_resource_is_refused_with_path(self):
        import tempfile
        from apps.nextagent.hermes_skills import _read_within_skill
        from apps.nextagent.toolkit._spill import READ_MAX_CHARS

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "big.md").write_text("q" * (READ_MAX_CHARS + 1), encoding="utf-8")
            (root / "small.md").write_text("ok", encoding="utf-8")
            self.assertEqual(_read_within_skill(root, "small.md"), "ok")
            out = _read_within_skill(root, "big.md")
        self.assertTrue(out.startswith("Error: big.md"))
        self.assertIn("offset", out)


class _Desk:
    def __init__(self):
        self.opened = set()

    def online_fn(self, desktop_id):
        return desktop_id == "desk"

    def call(self, desktop_id, op, params):
        if desktop_id != "desk":
            raise RuntimeError(f"桌面 {desktop_id} 不在线")
        if op == "terminal.open":
            self.opened.add(params["sessionId"])
            return {"winId": "terminal:" + params["sessionId"]}
        if op == "terminal.mark":
            return {"winId": "terminal:" + params["sessionId"]}
        if op == "terminal.close":
            sid = params["sessionId"]
            if sid not in self.opened:
                raise RuntimeError("窗口不存在")
            self.opened.discard(sid)
            return {"winId": "terminal:" + sid}
        raise RuntimeError(op)


if __name__ == "__main__":
    unittest.main()
