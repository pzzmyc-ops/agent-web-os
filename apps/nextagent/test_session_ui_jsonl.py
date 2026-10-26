import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from apps.nextagent.store import Store
from apps.nextagent.ws_chat import _history_snapshot, _stored_turn, publish_session_ui


def _models(preferred=""):
    ids = ["model-a", "model-b"]
    want = (preferred or "").strip()
    if want in ids:
        return want, ids
    return ids[0], ids


class SessionUiJsonlTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(str(self.root))
        self.thread = self.store.create_thread("界面状态")
        self.models = patch("apps.nextagent.config.resolve_default_chat_model", side_effect=_models)
        self.models.start()

    def tearDown(self):
        self.models.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def _lines(self):
        text = (self.root / self.thread / "events.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _frontend_ui(self, **overrides):
        ui = {
            "planMode": True,
            "agentProfile": "model-b",
            "thinking": True,
            "reasoningEffort": "high",
            "mediaPassthrough": True,
            "agentSpeak": False,
            "selectedSkill": "desktop-control",
            "composerText": "还没发出去",
            "attachments": [],
        }
        ui.update(overrides)
        return ui

    def test_frontend_save_rewrites_the_ui_zone_on_disk(self):
        self.store.append_event(self.thread, "user_message", content="你好")
        self.store.save_memory(self.thread, "记得这个会话")
        self.store.save_session_ui(self.thread, self._frontend_ui())

        lines = self._lines()
        self.assertEqual(lines[0]["zone"], "ui")
        self.assertEqual(lines[0]["ui"]["agentProfile"], "model-b")
        self.assertEqual(lines[0]["ui"]["mediaPassthrough"], True)
        self.assertEqual(lines[0]["ui"]["planMode"], True)
        self.assertEqual(lines[0]["ui"]["agentSpeak"], False)
        self.assertEqual(lines[0]["ui"]["thinking"], True)
        self.assertEqual(lines[0]["ui"]["reasoningEffort"], "high")
        self.assertEqual(sum(1 for row in lines if row.get("zone") == "ui"), 1)
        self.assertEqual(self.store.load_memory(self.thread), "记得这个会话")
        self.assertEqual(self.store.load_events(self.thread)[0]["content"], "你好")

        other = Store(str(self.root))
        self.assertEqual(other.load_session_ui(self.thread)["composerText"], "还没发出去")

    def test_second_save_replaces_the_same_zone(self):
        self.store.save_session_ui(self.thread, self._frontend_ui())
        nxt = self._frontend_ui(planMode=False, mediaPassthrough=False, reasoningEffort="low")
        self.store.save_session_ui(self.thread, nxt)
        ui = self.store.load_session_ui(self.thread)
        self.assertEqual(ui["planMode"], False)
        self.assertEqual(ui["mediaPassthrough"], False)
        self.assertEqual(ui["reasoningEffort"], "low")
        self.assertEqual(ui["agentProfile"], "model-b")
        self.assertEqual(ui["agentSpeak"], False)
        self.assertEqual(sum(1 for row in self._lines() if row.get("zone") == "ui"), 1)

    def test_turn_uses_the_file_not_the_request(self):
        self.store.save_session_ui(self.thread, self._frontend_ui())
        request = {
            "planMode": False,
            "mediaPassthrough": False,
            "agentProfile": "model-a",
            "thinking": False,
            "reasoning_effort": "low",
            "agentSpeak": True,
        }
        settings = _stored_turn(self.store, self.thread)
        self.assertEqual(settings["model"], "model-b")
        self.assertEqual(settings["media_passthrough"], True)
        self.assertEqual(settings["plan_mode"], True)
        self.assertEqual(settings["thinking"], True)
        self.assertEqual(settings["effort"], "high")
        self.assertEqual(settings["agent_speak"], False)
        self.assertNotEqual(settings["model"], request["agentProfile"])
        self.assertNotEqual(settings["plan_mode"], request["planMode"])
        self.assertNotEqual(settings["media_passthrough"], request["mediaPassthrough"])

    def test_thinking_off_drops_effort_and_missing_thinking_stays_unset(self):
        self.store.save_session_ui(self.thread, self._frontend_ui(thinking=False, reasoningEffort="high"))
        off = self.store.turn_settings(self.thread)
        self.assertEqual(off["thinking"], False)
        self.assertIsNone(off["effort"])

        ui = self._frontend_ui()
        del ui["thinking"]
        del ui["reasoningEffort"]
        self.store.save_session_ui(self.thread, ui)
        unset = self.store.turn_settings(self.thread)
        self.assertIsNone(unset["thinking"])
        self.assertIsNone(unset["effort"])

    def test_unknown_model_in_the_file_is_replaced_by_the_catalog_default(self):
        self.store.save_session_ui(self.thread, self._frontend_ui(agentProfile="not-a-model"))
        settings = self.store.turn_settings(self.thread)
        self.assertEqual(settings["model"], "model-a")
        self.assertEqual(self.store.load_session_ui(self.thread)["agentProfile"], "model-a")

    def test_defaults_when_the_ui_zone_is_empty(self):
        settings = self.store.turn_settings(self.thread)
        self.assertEqual(settings["media_passthrough"], False)
        self.assertEqual(settings["plan_mode"], False)
        self.assertEqual(settings["agent_speak"], True)
        self.assertEqual(settings["model"], "model-a")
        self.assertIsNone(settings["thinking"])

    def test_non_object_ui_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.store.save_session_ui(self.thread, ["planMode"])

    def test_history_snapshot_carries_the_saved_ui(self):
        self.store.save_session_ui(self.thread, self._frontend_ui())
        frame = _history_snapshot(self.store, self.thread, "mid")
        self.assertEqual(frame["data"]["sessionUi"]["agentProfile"], "model-b")
        self.assertEqual(frame["data"]["sessionUi"]["mediaPassthrough"], True)
        self.assertEqual(frame["data"]["threadId"], self.thread)

    def test_publish_pushes_the_file_to_connected_clients(self):
        self.store.save_session_ui(self.thread, self._frontend_ui(planMode=False))
        sent = []

        async def fake_broadcast(obj):
            sent.append(obj)

        async def run():
            with patch("apps.nextagent.ws_chat.broadcast", fake_broadcast):
                await publish_session_ui(self.store, self.thread)

        asyncio.run(run())
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["type"], "session_ui")
        self.assertEqual(sent[0]["data"]["threadId"], self.thread)
        self.assertEqual(sent[0]["data"]["sessionUi"]["planMode"], False)
        self.assertEqual(sent[0]["data"]["sessionUi"]["agentProfile"], "model-b")

    def test_text_and_edit_paths_read_stored_turn(self):
        source = Path("apps/nextagent/ws_chat.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("stored = _stored_turn(store, thread_id)"), 2)
        self.assertNotIn('data.get("planMode")', source)
        self.assertNotIn('data.get("mediaPassthrough")', source)
        self.assertNotIn('data.get("agentProfile")', source)
        self.assertNotIn('data.get("reasoning_effort")', source)


class FrontendPersistGateTests(unittest.TestCase):
    def test_browser_does_not_write_until_the_file_has_been_loaded(self):
        script = r"""
globalThis.location = new URL("http://127.0.0.1");
globalThis.document = {
  createElement: function () {
    return { className: "", dataset: {}, style: {} };
  },
  getElementById: function () { return null; }
};
const calls = [];
globalThis.fetch = async function (url, init) {
  calls.push({ url: String(url), body: JSON.parse(init.body) });
  return { ok: true, status: 200, json: async function () { return { ok: true }; } };
};
const stateMod = await import("./apps/nextagent/static/js/state.js");
const uiMod = await import("./apps/nextagent/static/js/session-ui.js");
stateMod.state.currentConversationId = "thread-1";
uiMod.bindSessionUi({
  collect: function () {
    return {
      planMode: true,
      agentProfile: "model-b",
      thinking: true,
      reasoningEffort: "high",
      mediaPassthrough: true,
      agentSpeak: false,
      selectedSkill: "",
      composerText: "",
      attachments: []
    };
  },
  apply: function (ui, opts) {
    globalThis.applied = { ui: ui, opts: opts || {} };
  }
});
await uiMod.persistSessionUiNow();
if (calls.length !== 0) throw new Error("wrote before the file was loaded: " + calls.length);
uiMod.applySessionUi({
  planMode: false,
  agentProfile: "model-a",
  thinking: false,
  reasoningEffort: "low",
  mediaPassthrough: false,
  agentSpeak: true
}, { keepComposer: true });
if (!globalThis.applied.opts.keepComposer) throw new Error("keepComposer was dropped");
if (globalThis.applied.ui.agentProfile !== "model-a") throw new Error("apply did not receive the file");
await uiMod.persistSessionUiNow();
if (calls.length !== 1) throw new Error("expected one write after load, got " + calls.length);
const sent = calls[0].body.sessionUi;
if (sent.agentProfile !== "model-b") throw new Error("collect was not what got written");
if (sent.mediaPassthrough !== true) throw new Error("media flag missing");
if (sent.planMode !== true) throw new Error("plan flag missing");
if (sent.agentSpeak !== false) throw new Error("speak flag missing");
if (calls[0].body.threadId !== "thread-1") throw new Error("thread missing");
console.log("ok");
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=str(_PROJECT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
