from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from apps.nextagent.dsml_recover import DsmlStreamRecoverer, parse_dsml_block

B = "\uff5c"

CANONICAL = (
    f"<{B}DSML{B}tool_calls>\n"
    f'<{B}DSML{B}invoke name="terminal">\n'
    f'<{B}DSML{B}parameter name="command" string="true">dir</{B}DSML{B}parameter>\n'
    f'<{B}DSML{B}parameter name="timeout" string="false">30</{B}DSML{B}parameter>\n'
    f"</{B}DSML{B}invoke>\n"
    f"</{B}DSML{B}tool_calls>"
)

VARIANTS = {
    "double_bar": CANONICAL.replace(f"{B}DSML{B}", f"{B}{B}DSML{B}{B}"),
    "ascii_bar": CANONICAL.replace(B, "|"),
    "space_after_bar": CANONICAL.replace(f"{B}DSML{B}", f"{B}DSML{B} "),
    "wrap_calls": CANONICAL.replace("tool_calls", "calls"),
    "wrap_toolcalls": CANONICAL.replace("tool_calls", "toolcalls"),
    "wrap_tool_dash_calls": CANONICAL.replace("tool_calls", "tool-calls"),
}


def _calls_of(recoverer: DsmlStreamRecoverer, text: str, chunk: int) -> tuple[str, list]:
    out = ""
    calls = []
    for i in range(0, len(text), chunk):
        t, c = recoverer.feed(text[i:i + chunk])
        out += t
        calls.extend(c)
    t, c = recoverer.finish()
    out += t
    calls.extend(c)
    return out, calls


class ParseBlockTests(unittest.TestCase):
    def _assert_terminal_call(self, calls: list) -> None:
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].type, "function_call")
        self.assertEqual(calls[0].name, "terminal")
        args = json.loads(calls[0].arguments)
        self.assertEqual(args, {"command": "dir", "timeout": 30})

    def test_canonical(self) -> None:
        self._assert_terminal_call(parse_dsml_block(CANONICAL))

    def test_variants(self) -> None:
        for label, text in VARIANTS.items():
            with self.subTest(label):
                self._assert_terminal_call(parse_dsml_block(text))

    def test_unclosed_parameter_and_invoke(self) -> None:
        text = (
            f"<{B}DSML{B}tool_calls>\n"
            f'<{B}DSML{B}invoke name="grep">\n'
            f'<{B}DSML{B}parameter name="pattern" string="true">foo'
        )
        calls = parse_dsml_block(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "grep")
        self.assertEqual(json.loads(calls[0].arguments), {"pattern": "foo"})

    def test_self_closing_invoke(self) -> None:
        text = f'<{B}DSML{B}tool_calls><{B}DSML{B}invoke name="plan_read"/></{B}DSML{B}tool_calls>'
        calls = parse_dsml_block(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "plan_read")
        self.assertEqual(json.loads(calls[0].arguments), {})

    def test_multiple_invokes(self) -> None:
        text = (
            f"<{B}DSML{B}tool_calls>"
            f'<{B}DSML{B}invoke name="a"><{B}DSML{B}parameter name="x">1</{B}DSML{B}parameter></{B}DSML{B}invoke>'
            f'<{B}DSML{B}invoke name="b"><{B}DSML{B}parameter name="y" string="false">[1, 2]</{B}DSML{B}parameter></{B}DSML{B}invoke>'
            f"</{B}DSML{B}tool_calls>"
        )
        calls = parse_dsml_block(text)
        self.assertEqual([c.name for c in calls], ["a", "b"])
        self.assertEqual(json.loads(calls[0].arguments), {"x": "1"})
        self.assertEqual(json.loads(calls[1].arguments), {"y": [1, 2]})

    def test_invoke_without_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_dsml_block(f'<{B}DSML{B}invoke name="">x</{B}DSML{B}invoke>')


class StreamRecovererTests(unittest.TestCase):
    def test_plain_text_passthrough(self) -> None:
        out, calls = _calls_of(DsmlStreamRecoverer(), "你好 <b>加粗</b> 1 < 2", 3)
        self.assertEqual(out, "你好 <b>加粗</b> 1 < 2")
        self.assertEqual(calls, [])

    def test_text_then_block_in_small_chunks(self) -> None:
        for label, text in {"canonical": CANONICAL, **VARIANTS}.items():
            for chunk in (1, 4, 17, 1000):
                with self.subTest(label=label, chunk=chunk):
                    rec = DsmlStreamRecoverer()
                    out, calls = _calls_of(rec, "先说一句。" + text + "后面的话", chunk)
                    self.assertEqual(out, "先说一句。后面的话")
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[0].name, "terminal")
                    self.assertEqual(rec.recovered, 1)

    def test_unclosed_block_recovered_on_finish(self) -> None:
        text = (
            f"<{B}DSML{B}tool_calls>\n"
            f'<{B}DSML{B}invoke name="terminal">\n'
            f'<{B}DSML{B}parameter name="command" string="true">ls'
        )
        rec = DsmlStreamRecoverer()
        out, calls = _calls_of(rec, text, 5)
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0].arguments), {"command": "ls"})

    def test_bare_invoke_without_wrapper(self) -> None:
        text = f'<{B}DSML{B}invoke name="glob"><{B}DSML{B}parameter name="pattern" string="true">*.py</{B}DSML{B}parameter></{B}DSML{B}invoke>'
        out, calls = _calls_of(DsmlStreamRecoverer(), text, 7)
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "glob")

    def test_unclosed_unparseable_block_raises(self) -> None:
        rec = DsmlStreamRecoverer()
        rec.feed(f"<{B}DSML{B}tool_calls>\n乱七八糟")
        with self.assertRaises(RuntimeError):
            rec.finish()
