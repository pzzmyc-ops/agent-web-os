import json
import shutil
import tempfile
import unittest
from pathlib import Path

from apps.nextagent.store import Store


class EventLogLineTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(str(self.root))
        self.thread = "t1"
        (self.root / self.thread).mkdir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, text: str) -> None:
        (self.root / self.thread / "events.jsonl").write_bytes(text.encode("utf-8"))

    def test_unicode_line_separators_stay_in_one_record(self):
        result = "A\u2028B\u2029C\u0085D"
        line = json.dumps({"id": 1, "type": "tool_result", "result": result}, ensure_ascii=False)
        self._write(line + "\r\n")
        events = self.store.load_events(self.thread)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["result"], result)

    def test_truncated_line_raises(self):
        self._write('{"id": 1, "result": "unterminated\n')
        with self.assertRaises(ValueError):
            self.store.load_events(self.thread)
