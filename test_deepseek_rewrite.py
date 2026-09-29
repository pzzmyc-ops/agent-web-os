import unittest

from deepseek_adapter import _rewrite_web


class DeepSeekRewriteTests(unittest.TestCase):
    def test_script_constants_stay_absolute(self):
        raw = (
            'const EVENTS_ROUTE = "/plugins/events".slice(1);\n'
            'const REMOTE_STREAM_MUX_PATH = "/api/remote.mux";\n'
            "const API_PATH = \"/api\";\n"
        ).encode("utf-8")
        text = _rewrite_web(raw, "text/javascript").decode("utf-8")
        self.assertIn('"/plugins/events".slice(1)', text)
        self.assertIn('"/api/remote.mux"', text)
        self.assertIn('const API_PATH = "/api"', text)
        self.assertNotIn("/deepseek/plugins/events", text)
        self.assertNotIn("/deepseek/api/remote.mux", text)

    def test_html_keeps_base_and_prefix_script(self):
        raw = b'<html><head><base href="/"></head><body><div id="root"></div></body></html>'
        text = _rewrite_web(raw, "text/html").decode("utf-8")
        self.assertIn('<base href="/deepseek/">', text)
        self.assertIn("var prefix='/deepseek'", text)
        self.assertIn("path.indexOf('/api/')===0", text)
