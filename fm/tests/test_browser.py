import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest

from fastapi.testclient import TestClient

from fm.backend.browser import router
from fastapi import FastAPI


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><head><title>t</title></head><body><a target=\"_blank\" href=\"/out\">go</a></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


class BrowserProxyTests(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        host, port = self.httpd.server_address
        self.origin = f"http://{host}:{port}/"
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_blank_target_is_left_for_the_shell_to_open(self):
        resp = self.client.get("/api/browser/page", params={"url": self.origin})
        self.assertEqual(resp.status_code, 200)
        text = resp.text
        self.assertIn('<base href="' + self.origin + '">', text)
        self.assertIn("browser-tab", text)
        self.assertLess(text.find("<script>"), text.find("target=\"_blank\""))

    def test_non_http_url_is_rejected(self):
        resp = self.client.get("/api/browser/page", params={"url": "file:///etc/passwd"})
        self.assertEqual(resp.status_code, 400)
