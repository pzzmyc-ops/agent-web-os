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
    def test_foreground_timeout_backgrounds_instead_of_killing(self):
        from apps.nextagent.maf_tools.process_registry import process_registry
        from apps.nextagent.maf_tools.terminal_tool import terminal_tool

        raw = terminal_tool(
            command="echo started; sleep 30",
            timeout=3,
            task_id="test_timeout_bg",
        )
        result = json.loads(raw)
        self.addCleanup(process_registry.kill_all, "test_timeout_bg")

        self.assertEqual(result["status"], "backgrounded")
        self.assertIsNone(result["exit_code"])
        self.assertIn("started", result["output"])
        session_id = result["session_id"]

        polled = process_registry.poll(session_id)
        self.assertEqual(polled["status"], "running")
        self.assertIn("started", polled["output_preview"])

        killed = process_registry.kill_process(session_id)
        self.assertEqual(killed["status"], "killed")
        self.assertEqual(process_registry.poll(session_id)["status"], "exited")

    def test_backgrounded_command_finishes_and_keeps_full_output(self):
        from apps.nextagent.maf_tools.process_registry import process_registry
        from apps.nextagent.maf_tools.terminal_tool import terminal_tool

        raw = terminal_tool(
            command="echo A; sleep 4; echo B",
            timeout=2,
            task_id="test_timeout_finish",
        )
        result = json.loads(raw)
        self.addCleanup(process_registry.kill_all, "test_timeout_finish")
        self.assertEqual(result["status"], "backgrounded")

        session_id = result["session_id"]
        waited = process_registry.wait(session_id, timeout=30)
        self.assertEqual(waited["status"], "exited")
        self.assertEqual(waited["exit_code"], 0)

        log = process_registry.read_log(session_id)["output"]
        self.assertIn("A", log)
        self.assertIn("B", log)
        self.assertNotIn("__HERMES_CWD_", log)


if __name__ == "__main__":
    unittest.main()
