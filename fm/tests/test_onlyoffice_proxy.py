import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIWebSocketRoute

import onlyoffice_adapter
from fm.backend import onlyoffice


class OnlyOfficeProxyTests(unittest.TestCase):
    def test_adapter_installs_http_and_websocket_routes(self):
        app = FastAPI()
        onlyoffice_adapter.install(app)
        paths = [route.path for route in app.routes]
        self.assertIn("/onlyoffice-ds/{path:path}", paths)
        self.assertTrue(any(
            isinstance(route, APIWebSocketRoute)
            and route.path == "/onlyoffice-ds/{path:path}"
            for route in app.routes
        ))

    def test_adapter_rewrites_upstream_urls(self):
        content = b"http://127.0.0.1:8180/a http://localhost:8180/b"
        rewritten = onlyoffice_adapter._rewrite_content(content, "application/javascript")
        self.assertEqual(rewritten, b"/onlyoffice-ds/a /onlyoffice-ds/b")

    def test_adapter_rewrites_websocket_urls(self):
        message = '42["resource",{"url":"http://127.0.0.1:8180/cache/files/Editor.bin"}]'
        rewritten = onlyoffice_adapter._rewrite_message(message)
        self.assertEqual(
            rewritten,
            '42["resource",{"url":"/onlyoffice-ds/cache/files/Editor.bin"}]',
        )

    def test_adapter_enables_websocket_rewrite(self):
        app = FastAPI()
        with patch.object(onlyoffice_adapter, "make_router", return_value=APIRouter()) as make_router:
            onlyoffice_adapter.install(app)
        self.assertIs(
            make_router.call_args.kwargs["rewrite_ws"],
            onlyoffice_adapter._rewrite_message,
        )

    def test_editor_config_uses_same_origin_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            full = os.path.join(directory, "demo.docx")
            with open(full, "wb") as file:
                file.write(b"docx")
            with patch.object(onlyoffice, "resolve", return_value=full), patch.object(onlyoffice, "to_rel", return_value="/demo.docx"):
                data = onlyoffice.onlyoffice_config("/demo.docx")["data"]
        self.assertEqual(data["documentServer"], "/onlyoffice-ds")
        self.assertEqual(
            data["apiJs"],
            "/onlyoffice-ds/web-apps/apps/api/documents/api.js",
        )


if __name__ == "__main__":
    unittest.main()
