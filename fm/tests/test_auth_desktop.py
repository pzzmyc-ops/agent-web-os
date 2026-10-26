import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import fm.backend.desktop as desktop
import webauth


def auth_app() -> FastAPI:
    app = FastAPI()

    @app.get("/agent")
    async def agent_page():
        return {"ok": True, "page": "agent"}

    @app.get("/api/v1/conversations")
    async def conversations():
        return {"ok": True}

    @app.websocket("/api/v1/ws")
    async def chat_ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"type": "ready"})

    webauth.install_webauth(app, "secret-pass")
    return app


def desktop_app() -> FastAPI:
    app = FastAPI()
    app.include_router(desktop.router)
    return app


class AuthTests(unittest.TestCase):
    def setUp(self):
        webauth._tokens.clear()
        self.client = TestClient(auth_app())
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        webauth._tokens.clear()

    def test_api_without_cookie_is_401(self):
        res = self.client.get("/api/v1/conversations")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["error"], "未登录")

    def test_html_without_cookie_redirects_to_login(self):
        res = self.client.get("/agent", headers={"accept": "text/html"}, follow_redirects=False)
        self.assertEqual(res.status_code, 303)
        self.assertTrue(res.headers["location"].startswith("/login?next="))

    def test_login_page_is_public(self):
        res = self.client.get("/login")
        self.assertEqual(res.status_code, 200)
        self.assertIn("密码", res.text)

    def test_wrong_password_is_401(self):
        res = self.client.post("/api/auth/login", json={"password": "nope"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["error"], "密码错误")

    def test_login_then_api_and_logout(self):
        res = self.client.post("/api/auth/login", json={"password": "secret-pass"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["ok"], True)
        self.assertIn(webauth.COOKIE_NAME, res.headers["set-cookie"])
        self.assertIn("httponly", res.headers["set-cookie"].lower())

        res = self.client.get("/api/v1/conversations")
        self.assertEqual(res.status_code, 200)

        res = self.client.get("/api/auth/status")
        self.assertEqual(res.json()["authenticated"], True)

        res = self.client.post("/api/auth/logout")
        self.assertEqual(res.status_code, 200)
        res = self.client.get("/api/v1/conversations")
        self.assertEqual(res.status_code, 401)

    def test_public_gateway_is_not_blocked(self):
        res = self.client.get("/api/llm-proxy/v1/models")
        self.assertNotEqual(res.status_code, 401)

    def test_websocket_without_cookie_is_rejected(self):
        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect("/api/v1/ws") as ws:
                ws.receive_json()
        self.assertEqual(caught.exception.code, 1008)

    def test_websocket_with_cookie_is_accepted(self):
        res = self.client.post("/api/auth/login", json={"password": "secret-pass"})
        self.assertEqual(res.status_code, 200)
        cookie = self.client.cookies.get(webauth.COOKIE_NAME)
        self.assertTrue(cookie)
        with self.client.websocket_connect(
            "/api/v1/ws",
            headers={"cookie": webauth.COOKIE_NAME + "=" + cookie},
        ) as ws:
            self.assertEqual(ws.receive_json()["type"], "ready")


class DesktopSlotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._state_path = desktop.STATE_PATH
        self._active = desktop._active_id
        desktop.STATE_PATH = Path(self.tmp.name) / "desktop_state.json"
        desktop._desktops.clear()
        desktop._active_id = ""
        self.client = TestClient(desktop_app())
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        desktop._desktops.clear()
        desktop._active_id = self._active
        desktop.STATE_PATH = self._state_path
        self.tmp.cleanup()

    def test_second_screen_evicts_the_first_and_only_the_new_one_can_save(self):
        with self.client.websocket_connect("/api/desktop/screen?desktopId=desk-a") as first:
            ready = first.receive_json()
            self.assertEqual(ready["type"], "ready")
            self.assertEqual(ready["desktopId"], "desk-a")

            hold = threading.Event()
            box = {}

            def open_second():
                try:
                    with self.client.websocket_connect("/api/desktop/screen?desktopId=desk-b") as second:
                        box["ready"] = second.receive_json()
                        hold.wait(timeout=3)
                except Exception as exc:
                    box["error"] = exc

            thread = threading.Thread(target=open_second)
            thread.start()
            evicted = first.receive_json()
            self.assertEqual(evicted["type"], "evicted")
            hold.set()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertNotIn("error", box)
            self.assertEqual(box["ready"]["desktopId"], "desk-b")

        stale = self.client.put(
            "/api/desktop/state",
            json={"desktopId": "desk-a", "state": {"windows": [{"app": "explorer"}]}},
        )
        self.assertEqual(stale.status_code, 409)

        saved = self.client.put(
            "/api/desktop/state",
            json={
                "desktopId": "desk-b",
                "state": {"windows": [{"app": "explorer", "z": 1}], "iconPos": {}, "rects": {}},
            },
        )
        self.assertEqual(saved.status_code, 200)
        loaded = self.client.get("/api/desktop/state")
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["state"]["windows"][0]["app"], "explorer")
        self.assertEqual(loaded.json()["activeDesktopId"], "desk-b")
        self.assertEqual(list(desktop._desktops), [])


class AuthEdgeTests(unittest.TestCase):
    def setUp(self):
        webauth._tokens.clear()
        self.app = auth_app()
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        webauth._tokens.clear()

    def test_empty_password_install_is_rejected(self):
        app = FastAPI()
        with self.assertRaises(RuntimeError):
            webauth.install_webauth(app, "")

    def test_forged_cookie_is_401(self):
        res = self.client.get("/api/v1/conversations", headers={"cookie": "fm_auth=forged"})
        self.assertEqual(res.status_code, 401)

    def test_malformed_cookie_header_is_401(self):
        res = self.client.get("/api/v1/conversations", headers={"cookie": "fm_auth"})
        self.assertEqual(res.status_code, 401)

    def test_login_body_must_be_object(self):
        with self.assertRaises(RuntimeError):
            self.client.post(
                "/api/auth/login",
                content=b"[]",
                headers={"content-type": "application/json"},
            )
        with self.assertRaises(json.JSONDecodeError):
            self.client.post(
                "/api/auth/login",
                content=b"",
                headers={"content-type": "application/json"},
            )

    def test_non_string_password_does_not_pass(self):
        res = self.client.post("/api/auth/login", json={"password": 12345})
        self.assertEqual(res.status_code, 401)

    def test_unicode_password_roundtrip(self):
        webauth._password = "密 码"
        res = self.client.post("/api/auth/login", json={"password": "密 码"})
        self.assertEqual(res.status_code, 200)
        res = self.client.get("/api/v1/conversations")
        self.assertEqual(res.status_code, 200)

    def test_two_logins_are_independent(self):
        with TestClient(self.app) as other:
            first = self.client.post("/api/auth/login", json={"password": "secret-pass"})
            second = other.post("/api/auth/login", json={"password": "secret-pass"})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            token = self.client.cookies.get(webauth.COOKIE_NAME)
            self.client.post("/api/auth/logout")
            res = self.client.get(
                "/api/v1/conversations",
                headers={"cookie": webauth.COOKIE_NAME + "=" + token},
            )
            self.assertEqual(res.status_code, 401)
            res = other.get("/api/v1/conversations")
            self.assertEqual(res.status_code, 200)

    def test_websocket_rejects_bad_cookie_and_accepts_after_logout_fails(self):
        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect(
                "/api/v1/ws",
                headers={"cookie": "fm_auth=nope"},
            ) as ws:
                ws.receive_json()
        self.assertEqual(caught.exception.code, 1008)
        res = self.client.post("/api/auth/login", json={"password": "secret-pass"})
        self.assertEqual(res.status_code, 200)
        token = self.client.cookies.get(webauth.COOKIE_NAME)
        self.client.post("/api/auth/logout")
        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect(
                "/api/v1/ws",
                headers={"cookie": webauth.COOKIE_NAME + "=" + token},
            ) as ws:
                ws.receive_json()
        self.assertEqual(caught.exception.code, 1008)

    def test_public_prefixes_do_not_swallow_neighbors(self):
        self.assertNotEqual(self.client.get("/api/onlyoffice/file").status_code, 401)
        self.assertNotEqual(self.client.get("/api/onlyoffice/callback").status_code, 401)
        self.assertNotEqual(self.client.post("/api/v1/media/invoke").status_code, 401)
        self.assertEqual(self.client.get("/api/onlyoffice/config").status_code, 401)
        self.assertEqual(self.client.get("/hermes-extra").status_code, 401)
        self.assertNotEqual(self.client.get("/hermes/index.html").status_code, 401)
        self.assertEqual(self.client.get("/api/llm-proxy-extra").status_code, 401)

    def test_html_redirect_keeps_query(self):
        res = self.client.get(
            "/agent?feishu_webhook=1",
            headers={"accept": "text/html"},
            follow_redirects=False,
        )
        self.assertEqual(res.status_code, 303)
        self.assertIn("feishu_webhook", res.headers["location"])

    def test_screen_websocket_requires_login(self):
        app = FastAPI()
        app.include_router(desktop.router)
        webauth.install_webauth(app, "secret-pass")
        with TestClient(app) as client:
            with self.assertRaises(WebSocketDisconnect) as caught:
                with client.websocket_connect("/api/desktop/screen?desktopId=desk-a") as ws:
                    ws.receive_json()
            self.assertEqual(caught.exception.code, 1008)
            client.post("/api/auth/login", json={"password": "secret-pass"})
            cookie = client.cookies.get(webauth.COOKIE_NAME)
            with client.websocket_connect(
                "/api/desktop/screen?desktopId=desk-a",
                headers={"cookie": webauth.COOKIE_NAME + "=" + cookie},
            ) as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")


class DesktopEdgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._state_path = desktop.STATE_PATH
        self._active = desktop._active_id
        desktop.STATE_PATH = Path(self.tmp.name) / "desktop_state.json"
        desktop._desktops.clear()
        desktop._active_id = ""
        self.client = TestClient(desktop_app())
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        desktop._desktops.clear()
        desktop._active_id = self._active
        desktop.STATE_PATH = self._state_path
        self.tmp.cleanup()

    def test_empty_desktop_id_is_rejected(self):
        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect("/api/desktop/screen?desktopId=") as ws:
                ws.receive_json()
        self.assertEqual(caught.exception.code, 1008)
        self.assertEqual(desktop._desktops, {})
        self.assertEqual(desktop._active_id, "")

    def test_missing_state_file_is_empty_and_not_shared(self):
        first = desktop._load_state()
        first["windows"].append({"app": "explorer"})
        first["iconPos"]["a"] = [1, 2]
        second = desktop._load_state()
        self.assertEqual(second["windows"], [])
        self.assertEqual(second["iconPos"], {})

    def test_corrupt_state_file_errors(self):
        desktop.STATE_PATH.write_text("{", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            self.client.get("/api/desktop/state")
        desktop.STATE_PATH.write_text("[]", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.client.get("/api/desktop/state")

    def test_save_rules(self):
        res = self.client.put("/api/desktop/state", json={"desktopId": "   ", "state": {}})
        self.assertEqual(res.status_code, 400)
        res = self.client.put("/api/desktop/state", json={"desktopId": "desk-a", "state": {}})
        self.assertEqual(res.status_code, 409)
        res = self.client.put("/api/desktop/state", json={"desktopId": "desk-a", "state": []})
        self.assertEqual(res.status_code, 422)
        with self.client.websocket_connect("/api/desktop/screen?desktopId=desk-a") as ws:
            self.assertEqual(ws.receive_json()["type"], "ready")
            res = self.client.put(
                "/api/desktop/state",
                json={"desktopId": "  desk-a  ", "state": {"windows": [], "iconPos": {}, "rects": {}}},
            )
            self.assertEqual(res.status_code, 200)

    def test_same_id_reconnect_evicts_the_previous_socket(self):
        evicted = []
        original = desktop.DesktopConn.evict

        async def spy(conn, reason):
            evicted.append(conn.desktop_id)
            await original(conn, reason)

        desktop.DesktopConn.evict = spy
        first = self.client.websocket_connect("/api/desktop/screen?desktopId=desk-a")
        first.__enter__()
        try:
            self.assertEqual(first.receive_json()["type"], "ready")
            second = self.client.websocket_connect("/api/desktop/screen?desktopId=desk-a")
            second.__enter__()
            try:
                self.assertEqual(second.receive_json()["type"], "ready")
                self.assertEqual(evicted, ["desk-a"])
                self.assertEqual(first.receive_json()["type"], "evicted")
                self.assertEqual(list(desktop._desktops), ["desk-a"])
            finally:
                second.__exit__(None, None, None)
        finally:
            desktop.DesktopConn.evict = original
            first.__exit__(None, None, None)
        self.assertEqual(desktop._desktops, {})

    def test_concurrent_claims_cannot_both_stay_online(self):
        entered = threading.Semaphore(0)
        release = threading.Semaphore(0)
        original = desktop.DesktopConn.evict

        async def slow(conn, reason):
            entered.release()
            await asyncio_wait(release)
            await original(conn, reason)

        desktop.DesktopConn.evict = slow
        holder = {}

        def hold(name, desktop_id):
            try:
                sock = self.client.websocket_connect("/api/desktop/screen?desktopId=" + desktop_id)
                sock.__enter__()
                holder[name] = sock
                holder[name + "_msg"] = sock.receive_json()
            except Exception as exc:
                holder[name + "_err"] = exc

        try:
            anchor = threading.Thread(target=hold, args=("a", "desk-a"))
            anchor.start()
            anchor.join(timeout=3)
            self.assertEqual(holder["a_msg"]["type"], "ready")

            challenger = threading.Thread(target=hold, args=("b", "desk-b"))
            challenger.start()
            self.assertTrue(entered.acquire(timeout=3))
            other = threading.Thread(target=hold, args=("c", "desk-c"))
            other.start()
            time.sleep(0.3)
            self.assertNotIn("desk-c", desktop._desktops)
            release.release()
            self.assertTrue(entered.acquire(timeout=3))
            self.assertNotIn("desk-b", desktop._desktops)
            self.assertNotIn("desk-c", desktop._desktops)
            release.release()
            challenger.join(timeout=3)
            other.join(timeout=3)
            self.assertFalse(challenger.is_alive())
            self.assertFalse(other.is_alive())
            self.assertEqual(len(desktop._desktops), 1)
            self.assertIn("desk-c", desktop._desktops)
        finally:
            desktop.DesktopConn.evict = original
            for _ in range(4):
                release.release()
            for name in ("a", "b", "c"):
                sock = holder.get(name)
                if sock is not None:
                    sock.__exit__(None, None, None)

    def test_inflight_command_fails_when_desktop_is_replaced(self):
        ready = threading.Event()
        call_done = threading.Event()
        box = {}

        def hold():
            try:
                with self.client.websocket_connect("/api/desktop/screen?desktopId=desk-a") as ws:
                    ws.receive_json()
                    ready.set()
                    while True:
                        ws.receive_json()
            except Exception as exc:
                box["hold"] = exc

        def call():
            try:
                desktop.exec_on_desktop("desk-a", "explorer.open", {})
                box["call"] = None
            except Exception as exc:
                box["call"] = exc
            call_done.set()

        holder = threading.Thread(target=hold)
        holder.start()
        self.assertTrue(ready.wait(timeout=3))
        caller = threading.Thread(target=call)
        caller.start()
        pending = False
        for _ in range(50):
            conn = desktop._desktops.get("desk-a")
            if conn is not None and conn._pending:
                pending = True
                break
            time.sleep(0.02)
        self.assertTrue(pending)
        with self.client.websocket_connect("/api/desktop/screen?desktopId=desk-b") as ws:
            self.assertEqual(ws.receive_json()["type"], "ready")
        self.assertTrue(call_done.wait(timeout=3))
        self.assertIsInstance(box["call"], desktop.DesktopError)
        holder.join(timeout=3)

    def test_concurrent_saves_keep_a_valid_file(self):
        desktop._active_id = "desk-a"
        for _ in range(30):
            barrier = threading.Barrier(2)
            errors = []

            def write(marker, gate=barrier, found=errors):
                try:
                    gate.wait(timeout=3)
                    desktop._save_state({"windows": [{"app": marker}], "iconPos": {}, "rects": {}})
                except Exception as exc:
                    found.append(exc)

            threads = [threading.Thread(target=write, args=(name,)) for name in ("one", "two")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)
            self.assertEqual(errors, [])
            saved = json.loads(desktop.STATE_PATH.read_text(encoding="utf-8"))
            self.assertIn(saved["windows"][0]["app"], ("one", "two"))


def asyncio_wait(sem):
    import asyncio
    return asyncio.to_thread(sem.acquire)


class DesktopClaimTests(unittest.TestCase):
    def test_top_window_claims_a_desktop_and_iframe_does_not(self):
        script = r"""
import { decideDesktop, chatCloseAction, staleSocketMessage } from "./apps/nextagent/static/js/desktop-link.js";
function check(cond, text) {
  if (!cond) throw new Error(text);
}
let top = decideDesktop(true, "", "stored", "generated");
check(top.connect === true && top.desktopId === "stored", "top uses stored id");
top = decideDesktop(true, "from-url", "stored", "generated");
check(top.connect === true && top.desktopId === "from-url", "top uses url id and still connects");
top = decideDesktop(true, "", "", "generated");
check(top.connect === true && top.desktopId === "generated", "top generates id");
let inner = decideDesktop(false, "from-parent", "stored", "generated");
check(inner.connect === false && inner.desktopId === "from-parent", "iframe does not connect");
let threw = false;
try { decideDesktop(false, "", "stored", "generated"); } catch (e) { threw = true; }
check(threw, "iframe without id must throw");
check(chatCloseAction(1008, false) === "login", "auth close goes to login");
check(chatCloseAction(1006, true) === "stop", "evicted does not reconnect");
check(chatCloseAction(1006, false) === "reconnect", "network close reconnects");
check(staleSocketMessage(1, 2) === true, "old socket is stale");
check(staleSocketMessage(1, 1) === false, "current socket is live");
console.log("ok");
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd="d:/mafagent",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
