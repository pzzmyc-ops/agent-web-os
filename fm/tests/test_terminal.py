import json
import os
import threading
import time
import unittest
import uuid
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from fm.backend.pathutil import IS_WINDOWS
from fm.backend.terminal import open_shell, router


def terminal_app():
    app = FastAPI()
    app.include_router(router)
    return app


def print_command(token: str, expression: str) -> str:
    left, right = token[:8], token[8:]
    if IS_WINDOWS:
        return f"Write-Output ('{left}'+'{right}'+({expression}))\r\n"
    return f"printf '%s%s%s\\n' '{left}' '{right}' \"$({expression})\"\n"


def process_alive(pid: int) -> bool:
    if pid <= 0:
        raise ValueError(f"bad pid: {pid}")
    if IS_WINDOWS:
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        if not ok:
            raise OSError(f"GetExitCodeProcess failed for {pid}")
        return code.value == 259
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_dead(pid: int, timeout: float = 3) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if not process_alive(pid):
            return
        time.sleep(0.1)
    raise AssertionError(f"process {pid} is still alive")


class Output:
    def __init__(self, read):
        self.text = ""
        self.error = None
        self.cond = threading.Condition()
        self.thread = threading.Thread(target=self._run, args=(read,), daemon=True)
        self.thread.start()

    def _run(self, read):
        try:
            while True:
                chunk = read()
                with self.cond:
                    self.text += chunk
                    self.cond.notify_all()
        except Exception as exc:
            with self.cond:
                self.error = exc
                self.cond.notify_all()

    def wait_for(self, marker: str, timeout: float) -> str:
        end = time.time() + timeout
        with self.cond:
            while marker not in self.text:
                if self.error is not None:
                    raise self.error
                remaining = end - time.time()
                if remaining <= 0:
                    raise AssertionError(self.text[-2000:])
                self.cond.wait(remaining)
            return self.text


class TerminalTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = os.path.join(self.temp.name, "start")
        self.other = os.path.join(self.temp.name, "other")
        os.makedirs(self.folder)
        os.makedirs(self.other)
        self.file = os.path.join(self.folder, "a.txt")
        with open(self.file, "w", encoding="utf-8") as handle:
            handle.write("x")

    def ask(self, output: Output, session, expression: str, timeout: float = 20) -> str:
        token = uuid.uuid4().hex
        session.write(print_command(token, expression))
        text = output.wait_for(token, timeout)
        line = text[text.rfind(token):].splitlines()[0]
        return line[len(token):].strip()

    def test_shell_starts_in_folder_and_can_leave_it(self):
        session = open_shell(self.folder)
        self.addCleanup(session.close)
        output = Output(session.read)
        here = self.ask(output, session, "(Get-Location).Path" if IS_WINDOWS else "pwd")
        self.assertEqual(os.path.normcase(here), os.path.normcase(self.folder))
        user_expr = "$env:USERNAME" if IS_WINDOWS else "id -un"
        self.assertEqual(self.ask(output, session, user_expr), os.environ["USERNAME"] if IS_WINDOWS else os.environ["USER"])
        session.resize(30, 110)
        width_expr = "$host.UI.RawUI.WindowSize.Width" if IS_WINDOWS else "tput cols"
        self.assertEqual(self.ask(output, session, width_expr), "110")
        token = uuid.uuid4().hex
        left, right = token[:8], token[8:]
        if IS_WINDOWS:
            session.write(
                f"Set-Location -LiteralPath '{self.other}'; Write-Output ('{left}'+'{right}'+(Get-Location).Path)\r\n"
            )
        else:
            session.write(f"cd '{self.other}'; printf '%s%s%s\\n' '{left}' '{right}' \"$(pwd)\"\n")
        text = output.wait_for(token, 20)
        there = text[text.rfind(token):].splitlines()[0][len(token):].strip()
        self.assertEqual(os.path.normcase(there), os.path.normcase(self.other))

    def test_close_stops_the_shell_process(self):
        session = open_shell(self.folder)
        pid = session.pid
        self.assertTrue(process_alive(pid))
        session.close()
        wait_dead(pid)

    def test_home_is_the_user_directory(self):
        with TestClient(terminal_app()) as client:
            resp = client.get("/api/terminal/home")
            self.assertEqual(resp.status_code, 200)
            got = os.path.normcase(os.path.abspath(resp.json()["path"]))
            self.assertEqual(got, os.path.normcase(os.path.abspath(os.path.expanduser("~"))))

    def test_missing_or_file_path_is_rejected(self):
        with self.assertRaises(ValueError):
            open_shell(os.path.join(self.temp.name, "missing"))
        with self.assertRaises(ValueError):
            open_shell(self.file)
        with self.assertRaises(ValueError):
            open_shell("   ")

    def test_refresh_keeps_the_shell_and_replays_output(self):
        with TestClient(terminal_app()) as client:
            started = client.post("/api/terminal/start", params={"path": self.folder})
            self.assertEqual(started.status_code, 200)
            session_id = started.json()["id"]
            with client.websocket_connect("/api/terminal/ws?id=" + session_id) as ws:
                output = Output(ws.receive_text)
                token = uuid.uuid4().hex
                expr = "$PID" if IS_WINDOWS else "echo $$"
                ws.send_bytes(print_command(token, expr).encode("utf-8"))
                text = output.wait_for(token, 20)
                pid = int(text[text.rfind(token):].splitlines()[0][len(token):].strip())
                self.assertTrue(process_alive(pid))
                ws.send_text(json.dumps({"type": "resize", "cols": 110, "rows": 30}))
                width = "$host.UI.RawUI.WindowSize.Width" if IS_WINDOWS else "tput cols"
                self.assertEqual(self.ask_ws(output, ws, width), "110")
            self.assertTrue(process_alive(pid))
            listed = client.get("/api/terminal/active")
            self.assertEqual(listed.status_code, 200)
            self.assertIn(session_id, [item["id"] for item in listed.json()["sessions"]])
            with client.websocket_connect("/api/terminal/ws?id=" + session_id) as again:
                replay = Output(again.receive_text)
                text = replay.wait_for(token, 20)
                self.assertTrue(text.startswith("\x00"))
                again.send_text(json.dumps({"type": "close"}))
            wait_dead(pid)
            left = client.get("/api/terminal/active")
            self.assertNotIn(session_id, [item["id"] for item in left.json()["sessions"]])

    def ask_ws(self, output: Output, ws, expression: str) -> str:
        token = uuid.uuid4().hex
        ws.send_bytes(print_command(token, expression).encode("utf-8"))
        text = output.wait_for(token, 20)
        line = text[text.rfind(token):].splitlines()[0]
        return line[len(token):].strip()

    def test_start_rejects_a_file_path(self):
        with TestClient(terminal_app()) as client:
            resp = client.post("/api/terminal/start", params={"path": self.file})
            self.assertEqual(resp.status_code, 400)
            with client.websocket_connect("/api/terminal/ws?id=missing") as ws:
                with self.assertRaises(WebSocketDisconnect) as caught:
                    ws.receive_text()
            self.assertEqual(caught.exception.code, 1008)
