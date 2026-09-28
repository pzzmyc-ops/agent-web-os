from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class SessionMessageRefreshTests(unittest.TestCase):
    def read_js(self, name):
        return (ROOT / "static" / "js" / name).read_text(encoding="utf-8")

    def test_switch_always_refreshes_selected_session(self):
        source = self.read_js("conversations.js")
        self.assertIn('reloadThreadHistory(c.id, "conversation_switch")', source)

    def test_reconnect_always_refreshes_current_session(self):
        source = self.read_js("message-handler.js")
        self.assertIn('reloadThreadHistory(bootstrapThreadId, "websocket_reconnect")', source)

    def test_history_render_cancellation_is_scoped_per_session(self):
        source = self.read_js("history.js")
        self.assertIn("var _historyRenderSeqByThread = {};", source)
        self.assertNotIn("var _historyRenderSeq = 0;", source)

    def test_failed_history_request_remains_retryable(self):
        source = self.read_js("history.js")
        failure_branch = source[source.index('.catch(function (e) {', source.index("export function loadHistory")):]
        self.assertIn("cs.historyLoaded = false;", failure_branch)


if __name__ == "__main__":
    unittest.main()
