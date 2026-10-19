import json
import os
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from fm.backend.pathutil import IS_WINDOWS
from fm.backend.terminal import (
    REGISTRY,
    HostedSession,
    _WinPty,
    _PosixPty,
    close_session,
    open_on_desktop,
    open_shell,
    posix_shell_env,
    router,
)


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
        value = line[len(token):].strip()
        mark = value.find("\x1b")
        if mark >= 0:
            value = value[:mark].strip()
        return value

    def test_shell_starts_in_folder_and_can_leave_it(self):
        session = open_shell(self.folder)
        self.addCleanup(session.close)
        session.resize(40, 200)
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
        mark = there.find("\x1b")
        if mark >= 0:
            there = there[:mark].strip()
        self.assertEqual(os.path.normcase(there), os.path.normcase(self.other))

    def test_close_stops_the_shell_process(self):
        session = open_shell(self.folder)
        pid = session.pid
        self.assertTrue(process_alive(pid))
        session.close()
        wait_dead(pid)

    def test_posix_shell_env_fills_home_when_missing(self):
        with patch.dict(os.environ, {"HOME": ""}, clear=False):
            with patch("fm.backend.terminal._passwd_home", return_value="/home/hhy"):
                env = posix_shell_env()
        self.assertEqual(env["HOME"], "/home/hhy")

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
                raw = text[text.rfind(token):].splitlines()[0][len(token):].strip()
                digits = ""
                for ch in raw:
                    if not ch.isdigit():
                        break
                    digits += ch
                pid = int(digits)
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
        value = line[len(token):].strip()
        mark = value.find("\x1b")
        if mark >= 0:
            value = value[:mark].strip()
        return value

    def test_start_rejects_a_file_path(self):
        with TestClient(terminal_app()) as client:
            resp = client.post("/api/terminal/start", params={"path": self.file})
            self.assertEqual(resp.status_code, 400)
            with client.websocket_connect("/api/terminal/ws?id=missing") as ws:
                with self.assertRaises(WebSocketDisconnect) as caught:
                    ws.receive_text()
            self.assertEqual(caught.exception.code, 1008)


def _echo(text: str) -> str:
    if IS_WINDOWS:
        return f"Write-Output '{text}'"
    return f"printf '%s\\n' '{text}'"


def _fail_cmd() -> str:
    if IS_WINDOWS:
        return "throw 'nope'"
    return "false"


def _sleep_cmd(seconds: int) -> str:
    if IS_WINDOWS:
        return f"Start-Sleep -Seconds {seconds}"
    return f"sleep {seconds}"


def _pwd_cmd() -> str:
    if IS_WINDOWS:
        return "Write-Output (Get-Location).Path"
    return "pwd"


def _cd_cmd(path: str) -> str:
    if IS_WINDOWS:
        return f"Set-Location -LiteralPath '{path}'"
    return f"cd '{path}'"


class Desk:
    def __init__(self):
        self.online = True
        self.fail_open = False
        self.opened = set()
        self.closed = []

    def online_fn(self, desktop_id):
        return bool(self.online) and desktop_id == "desk"

    def call(self, desktop_id, op, params):
        if not self.online_fn(desktop_id):
            raise RuntimeError(f"桌面 {desktop_id} 不在线")
        if op == "terminal.open":
            if self.fail_open:
                raise RuntimeError("打开失败")
            self.opened.add(params["sessionId"])
            return {"winId": "terminal:" + params["sessionId"]}
        if op == "terminal.mark":
            return {"winId": "terminal:" + params["sessionId"]}
        if op == "terminal.close":
            sid = params["sessionId"]
            if sid not in self.opened:
                raise RuntimeError("窗口不存在")
            self.opened.discard(sid)
            self.closed.append(sid)
            return {"winId": "terminal:" + sid}
        raise RuntimeError(op)


class SessionTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = os.path.join(self.temp.name, "start")
        self.other = os.path.join(self.temp.name, "other")
        os.makedirs(self.folder)
        os.makedirs(self.other)
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            if REGISTRY.has(session.id):
                REGISTRY.close(session.id)
            wait_dead(session.pid)

    def track(self, session):
        self.sessions.append(session)
        return session

    def ready(self):
        session = REGISTRY.create(self.folder)
        self.track(session)
        session.wait_ready(20)
        return session

    def test_enter_runs_and_marks_success(self):
        session = self.ready()
        session.submit_line(_echo("cr-ok"))
        result = session.wait_command(20)
        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("cr-ok", result["output"])
        self.assertNotIn("\x1b]133", result["output"])
        self.assertIn("\x1b]133;D;0\x07", session.text)

    def test_failed_command_marks_nonzero(self):
        session = self.ready()
        session.submit_line(_fail_cmd())
        result = session.wait_command(20)
        self.assertEqual(result["status"], "idle")
        self.assertNotEqual(result["exit_code"], 0)

    def test_directory_stays_in_one_session_only(self):
        first = self.ready()
        first.submit_line(_cd_cmd(self.other))
        moved = first.wait_command(20)
        self.assertEqual(moved["exit_code"], 0)
        first.submit_line(_pwd_cmd())
        there = first.wait_command(20)
        self.assertIn(os.path.normcase(self.other), os.path.normcase(there["output"]))
        second = self.ready()
        second.submit_line(_pwd_cmd())
        here = second.wait_command(20)
        here_text = os.path.normcase(here["output"])
        self.assertIn(os.path.normcase(self.folder), here_text)
        self.assertNotIn(os.path.normcase(self.other), here_text)

    def test_hook_install_is_not_in_the_buffer(self):
        session = self.ready()
        self.assertNotIn("__fm_prompt", session.text)
        self.assertNotIn("function global:prompt", session.text)
        self.assertIn("\x1b]133;A\x07", session.text)

    def test_submit_before_ready_fails(self):
        session = HostedSession.__new__(HostedSession)
        session.state = "starting"
        session.lock = threading.Lock()
        with self.assertRaises(RuntimeError):
            HostedSession.submit_line(session, "echo hi")

    def test_ready_timeout_kills_the_shell(self):
        self.temp._ignore_cleanup_errors = True
        stop = threading.Event()

        def blocked(self):
            stop.wait(5)
            raise EOFError("stopped")

        backend = _WinPty if IS_WINDOWS else _PosixPty
        with patch.object(backend, "read", blocked):
            session = REGISTRY.create(self.folder)
            pid = session.pid
            try:
                with self.assertRaises(TimeoutError):
                    session.wait_ready(0.3)
            finally:
                if REGISTRY.has(session.id):
                    REGISTRY.close(session.id)
                stop.set()
        wait_dead(pid)

    def test_without_desktop_does_not_start(self):
        before = {item["id"] for item in REGISTRY.list()}
        with self.assertRaises(RuntimeError):
            open_on_desktop(self.folder, "")
        self.assertEqual({item["id"] for item in REGISTRY.list()}, before)

    def test_one_online_desktop_is_used_when_none_was_named(self):
        desk = Desk()
        with patch("fm.backend.terminal.online_desktop_ids", lambda: ["desk"]), \
             patch("fm.backend.terminal._desktop_online", desk.online_fn), \
             patch("fm.backend.terminal._desktop_call", desk.call):
            session = self.track(open_on_desktop(self.folder, ""))
        self.assertEqual(session.desktop_id, "desk")
        self.assertIn(session.id, desk.opened)

    def test_several_online_desktops_are_not_guessed(self):
        before = {item["id"] for item in REGISTRY.list()}
        with patch("fm.backend.terminal.online_desktop_ids", lambda: ["desk-a", "desk-b"]):
            with self.assertRaises(RuntimeError):
                open_on_desktop(self.folder, "")
        self.assertEqual({item["id"] for item in REGISTRY.list()}, before)

    def test_named_online_desktop_is_used_when_several_are_online(self):
        desk = Desk()
        calls = []

        def online(desktop_id):
            return desktop_id == "desk-b"

        def call(desktop_id, op, params):
            calls.append(desktop_id)
            if desktop_id != "desk-b":
                raise RuntimeError(f"桌面 {desktop_id} 不在线")
            if op == "terminal.open":
                desk.opened.add(params["sessionId"])
                return {"winId": "terminal:" + params["sessionId"]}
            raise RuntimeError(op)

        with patch("fm.backend.terminal.online_desktop_ids", lambda: ["desk-a", "desk-b"]), \
             patch("fm.backend.terminal._desktop_online", online), \
             patch("fm.backend.terminal._desktop_call", call):
            session = self.track(open_on_desktop(self.folder, "desk-b", "chat"))
        self.assertEqual(session.desktop_id, "desk-b")
        self.assertEqual(calls, ["desk-b"])

    def test_offline_desktop_does_not_start(self):
        desk = Desk()
        desk.online = False
        before = {item["id"] for item in REGISTRY.list()}
        with patch("fm.backend.terminal._desktop_online", desk.online_fn):
            with self.assertRaises(RuntimeError):
                open_on_desktop(self.folder, "desk")
        self.assertEqual({item["id"] for item in REGISTRY.list()}, before)

    def test_failed_window_open_kills_the_shell(self):
        desk = Desk()
        desk.fail_open = True
        created = []
        real = REGISTRY.create

        def wrapped(*args, **kwargs):
            session = real(*args, **kwargs)
            created.append(session)
            return session

        with patch("fm.backend.terminal.online_desktop_ids", lambda: ["desk"]), \
             patch("fm.backend.terminal._desktop_online", desk.online_fn), \
             patch("fm.backend.terminal._desktop_call", desk.call), \
             patch.object(REGISTRY, "create", wrapped):
            with self.assertRaises(RuntimeError):
                open_on_desktop(self.folder, "desk", "chat")
        self.assertEqual(len(created), 1)
        self.assertFalse(REGISTRY.has(created[0].id))
        wait_dead(created[0].pid)

    def test_timeout_leaves_the_command_running(self):
        session = self.ready()
        session.submit_line(_sleep_cmd(4))
        early = session.wait_command(1)
        self.assertEqual(early["status"], "running")
        self.assertIsNone(early["exit_code"])
        self.assertTrue(process_alive(session.pid))
        done = session.wait_command(15)
        self.assertEqual(done["status"], "idle")
        self.assertEqual(done["exit_code"], 0)

    def test_submit_returns_while_command_runs(self):
        session = self.ready()
        session.submit_line(_sleep_cmd(2))
        self.assertEqual(session.state, "running")
        done = session.wait_command(15)
        self.assertEqual(done["status"], "idle")

    def test_second_submit_while_running_fails(self):
        session = self.ready()
        session.submit_line(_sleep_cmd(20))
        with self.assertRaises(RuntimeError):
            session.submit_line(_echo("no-second"))
        self.assertNotIn("no-second", session.text)
        REGISTRY.close(session.id)

    def test_raw_write_reaches_a_running_program(self):
        session = self.ready()
        exe = sys.executable
        if IS_WINDOWS:
            command = "& '" + exe + "' -c \"print('READY', flush=True); print(input())\""
        else:
            command = "'" + exe + "' -c 'print(\"READY\", flush=True); print(input())'"
        session.submit_line(command)
        deadline = time.time() + 20
        while "READY" not in session.text and time.time() < deadline:
            time.sleep(0.05)
        self.assertIn("READY", session.text)
        session.write_raw("hello\r")
        result = session.wait_command(20)
        self.assertIn("hello", result["output"])

    def test_eot_keeps_the_session_listed(self):
        session = self.ready()
        writes = []
        real = session.shell.write

        def wrap(text):
            writes.append(text)
            real(text)

        session.shell.write = wrap
        session.send_eot()
        self.assertEqual(writes, ["\x04"])
        self.assertIn(session.id, [item["id"] for item in REGISTRY.list()])

    def test_close_then_close_again_keeps_output(self):
        desk = Desk()
        with patch("fm.backend.terminal.online_desktop_ids", lambda: ["desk"]), \
             patch("fm.backend.terminal._desktop_online", desk.online_fn), \
             patch("fm.backend.terminal._desktop_call", desk.call):
            session = self.track(open_on_desktop(self.folder, "desk", "chat"))
            session.submit_line(_echo("before-close"))
            done = session.wait_command(20)
            self.assertIn("before-close", done["output"])
            closed = close_session(session.id, "desk")
            self.assertEqual(closed["status"], "closed")
            self.assertIn("before-close", closed["output"])
            self.assertIn(session.id, desk.closed)
            wait_dead(session.pid)
            again = close_session(session.id, "desk")
            self.assertEqual(again["status"], "already_closed")
            self.assertIn("before-close", again["output"])

    def test_interrupt_does_not_kill_the_shell(self):
        session = self.ready()
        session.submit_line(_sleep_cmd(20))
        result = session.wait_command(10, lambda: True)
        self.assertEqual(result["status"], "interrupted")
        self.assertTrue(process_alive(session.pid))

    def test_two_sessions_have_distinct_window_ids(self):
        first = self.ready()
        second = self.ready()
        self.assertEqual(first.win_id, "terminal:" + first.id)
        self.assertEqual(second.win_id, "terminal:" + second.id)
        self.assertNotEqual(first.win_id, second.win_id)
        ids = {item["winId"] for item in REGISTRY.list()}
        self.assertIn(first.win_id, ids)
        self.assertIn(second.win_id, ids)
