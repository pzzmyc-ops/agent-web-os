import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apps.nextagent.groupchat.dingtalk_cli import DingTalkCliAdapter


class DownloadFilesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.dest = self.root / "imgs"
        self.dest.mkdir()
        self.adapter = DingTalkCliAdapter(
            self.root / "workspace",
            log=lambda *_: None,
            on_mention=lambda *_: None,
            on_clarify=lambda *_: False,
        )
        self.adapter._cid_identity["cid1"] = "prof"
        self.calls = []

        def fake_dws(args, timeout, cwd=None, identity=""):
            self.calls.append({"args": args, "cwd": Path(cwd), "identity": identity})
            return {
                "messages": [],
                "resourceDownloads": {
                    "ok": True,
                    "downloads": [
                        {"localPath": "a.png"},
                        {"localPath": str(self.dest / "b.png")},
                    ],
                },
            }

        self._patch = mock.patch(
            "apps.nextagent.groupchat.dingtalk_cli._dws_json",
            fake_dws,
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_download_uses_destination_as_cwd(self):
        payload = self.adapter.download_files("cid1", "2026-09-24 11:10:00", "2026-09-24 12:10:00", str(self.dest))
        call = self.calls[0]
        self.assertEqual(call["cwd"], self.dest)
        args = call["args"]
        self.assertEqual(args[args.index("--output-dir") + 1], ".")
        self.assertNotIn(str(self.dest), args)
        self.assertEqual(
            payload["files"],
            [str(self.dest / "a.png"), str(self.dest / "b.png")],
        )

    def test_relative_output_dir_raises(self):
        with self.assertRaises(RuntimeError):
            self.adapter.download_files("cid1", "", "", "temp/imgs")
        self.assertEqual(self.calls, [])
