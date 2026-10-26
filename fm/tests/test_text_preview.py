import unittest
from pathlib import Path

from fm.backend.api import TEXT_EXT


class TextPreviewExtTests(unittest.TestCase):
    def test_jsonl_is_text(self):
        self.assertIn(".jsonl", TEXT_EXT)

    def test_text_editor_opens_jsonl(self):
        source = Path("fm/frontend/js/apps/aceEditor.js").read_text(encoding="utf-8")
        start = source.index("const EXT = [")
        end = source.index("];", start)
        block = source[start:end]
        self.assertIn('"jsonl"', block)
