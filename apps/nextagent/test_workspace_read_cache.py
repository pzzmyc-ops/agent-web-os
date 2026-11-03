from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[2]
if str(_PROJECT / "vendor") not in sys.path:
    sys.path.insert(0, str(_PROJECT / "vendor"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fm.backend.pathutil import WORKSPACE, resolve

from apps.nextagent.web_server import router


class WorkspaceReadCacheTests(unittest.TestCase):
    def setUp(self):
        self.name = "cache-probe-" + uuid.uuid4().hex + ".png"
        self.file = Path(resolve(WORKSPACE)) / self.name
        self.file.write_bytes(b"\x89PNG\r\n\x1a\nprobe-v1")
        self.addCleanup(self._remove)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def _remove(self):
        if self.file.exists():
            self.file.unlink()

    def test_workspace_read_revalidates_before_use(self):
        resp = self.client.get("/workspace/read", params={"path": self.name})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["cache-control"], "no-cache")

    def test_media_serve_revalidates_before_use(self):
        resp = self.client.get("/media/" + self.name)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["cache-control"], "no-cache")

    def test_reread_returns_the_file_now_on_disk(self):
        first = self.client.get("/workspace/read", params={"path": self.name})
        self.file.write_bytes(b"\x89PNG\r\n\x1a\nprobe-v2")
        second = self.client.get("/workspace/read", params={"path": self.name})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.content, b"\x89PNG\r\n\x1a\nprobe-v2")
        self.assertNotEqual(first.headers["etag"], second.headers["etag"])
