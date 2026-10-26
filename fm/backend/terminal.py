import asyncio
import json
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, WebSocket

from .pathutil import IS_WINDOWS, to_fs

router = APIRouter(prefix="/api/terminal")

_MARKER_A = "\x1b]133;A\x07"
_DONE_RE = re.compile(r"\x1b\]133;D;(-?\d+)\x07")
_READY_TIMEOUT = 20.0
_WATCH_GAP = 15.0

_PS_HOOK = (
    "$global:__fm_prev=${function:prompt}; "
    "$global:__fm_hist=$null; "
    "function global:prompt { "
    "$ok=$?; "
    "$code=1; if ($ok) { $code=0 }; "
    "$item=Get-History -Count 1; "
    "$hid=-1; if ($null -ne $item) { $hid=$item.Id }; "
    "if ($null -ne $global:__fm_hist -and $hid -ne $global:__fm_hist) { "
    "[Console]::Out.Write([char]27+']133;D;'+$code+[char]7) }; "
    "$global:__fm_hist=$hid; "
    "[Console]::Out.Write([char]27+']133;A'+[char]7); "
    "& $global:__fm_prev }"
)

_BASH_HOOK = (
    "__fm_prompt(){ local c=$?; "
    "if [ -n \"${__fm_armed:-}\" ]; then printf '\\033]133;D;%s\\007' \"$c\"; fi; "
    "__fm_armed=1; printf '\\033]133;A\\007'; }; "
    "PROMPT_COMMAND=\"__fm_prompt${PROMPT_COMMAND:+;$PROMPT_COMMAND}\"\r"
)


@router.get("/home")
def terminal_home():
    return {"path": to_fs(str(Path.home()))}


class _WinPty:
    def __init__(self, folder: str, rows: int, cols: int):
        from winpty import PtyProcess

        self.proc = PtyProcess.spawn(
            ["powershell.exe", "-NoLogo", "-NoExit", "-Command", _PS_HOOK],
            cwd=folder,
            dimensions=(rows, cols),
        )
        self.pid = self.proc.pid
        self._closed = False

    def read(self) -> str:
        return self.proc.read(4096)

    def write(self, text: str) -> None:
        if not self.proc.isalive():
            raise EOFError("Pty is closed")
        self.proc.write(text)

    def resize(self, rows: int, cols: int) -> None:
        self.proc.setwinsize(rows, cols)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.proc.close(force=True)

    def returncode(self) -> int:
        if self.proc.isalive():
            raise RuntimeError("shell 还在运行")
        code = self.proc.exitstatus
        if code is None:
            code = self.proc.wait()
        if code is None:
            raise RuntimeError("shell 已退出但没有退出码")
        return int(code)


def posix_shell_env() -> dict:
    env = os.environ.copy()
    if not env.get("HOME"):
        env["HOME"] = _passwd_home()
    if not env.get("USER"):
        import getpass
        env["USER"] = getpass.getuser()
    if not env.get("LOGNAME"):
        env["LOGNAME"] = env["USER"]
    return env


def _passwd_home() -> str:
    import pwd
    return pwd.getpwuid(os.getuid()).pw_dir


class _PosixPty:
    def __init__(self, folder: str, rows: int, cols: int):
        import fcntl
        import pty
        import struct
        import subprocess
        import termios

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.proc = subprocess.Popen(
            ["bash", "--login", "-i"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=folder,
            env=posix_shell_env(),
            start_new_session=True,
        )
        os.close(slave)
        self.master = master
        self.pid = self.proc.pid
        self._pending = b""
        self._closed = False

    def read(self) -> str:
        while True:
            chunk = os.read(self.master, 4096)
            if not chunk:
                raise EOFError("Pty is closed")
            self._pending += chunk
            try:
                text = self._pending.decode("utf-8")
            except UnicodeDecodeError:
                continue
            self._pending = b""
            return text

    def write(self, text: str) -> None:
        os.write(self.master, text.encode("utf-8"))

    def resize(self, rows: int, cols: int) -> None:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        os.close(self.master)

    def returncode(self) -> int:
        code = self.proc.poll()
        if code is None:
            raise RuntimeError("shell 还在运行")
        return int(code)


class ShellSession:
    def __init__(self, backend):
        self._backend = backend

    @property
    def pid(self) -> int:
        return self._backend.pid

    def read(self) -> str:
        return self._backend.read()

    def write(self, text: str) -> None:
        self._backend.write(text)

    def resize(self, rows: int, cols: int) -> None:
        if not isinstance(rows, int) or not isinstance(cols, int) or rows <= 0 or cols <= 0:
            raise ValueError(f"bad size: {rows}x{cols}")
        self._backend.resize(rows, cols)

    def close(self) -> None:
        self._backend.close()

    def returncode(self) -> int:
        return self._backend.returncode()


def open_shell(path: str, rows: int = 24, cols: int = 80) -> ShellSession:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path is empty")
    folder = os.path.abspath(path)
    if not os.path.isdir(folder):
        raise ValueError(f"not a directory: {path}")
    backend = _WinPty(folder, rows, cols) if IS_WINDOWS else _PosixPty(folder, rows, cols)
    return ShellSession(backend)


def _model_text(text: str) -> str:
    from apps.nextagent.maf_tools.tools.ansi_strip import strip_ansi

    return strip_ansi(text)


def _strip_echo(text: str, command: str) -> str:
    if not command:
        return text
    lines = text.splitlines()
    if lines and lines[0].strip() == command.strip():
        return "\n".join(lines[1:])
    return text


class HostedSession:
    def __init__(self, folder: str, desktop_id: str = "", session_key: str = "", owner: str = ""):
        self.id = uuid.uuid4().hex
        self.path = to_fs(os.path.abspath(folder))
        self.desktop_id = desktop_id
        self.session_key = session_key
        self.owner = owner or ""
        self.shell = open_shell(folder)
        self.text = ""
        self.subscribers = []
        self.ended = None
        self.state = "starting"
        self.exit_code = None
        self.command = ""
        self.command_start = 0
        self.command_seq = 0
        self.notify_on_complete = False
        self.watch_patterns = []
        self.started_at = time.time()
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self._watch_next = 0.0
        self._notified = False
        self._command_session_key = ""
        self.result_ready = False
        self.result_taken = False
        self._trimmed = False
        self._latched_exit = None
        self._settle_gen = 0
        self.thread = threading.Thread(target=self._read, name="term-" + self.id[:8], daemon=True)
        self.thread.start()
        if not IS_WINDOWS:
            self.shell.write(_BASH_HOOK)

    @property
    def win_id(self) -> str:
        return "terminal:" + self.id

    def _absorb(self, chunk: str):
        notices = []
        broadcast = None
        if self.state == "starting":
            self.text += chunk
            index = self.text.find(_MARKER_A)
            if index < 0:
                return broadcast, notices
            if not self._trimmed:
                self.text = self.text[index:]
                self._trimmed = True
            self.ready.set()
            self.cond.notify_all()
            return broadcast, notices
        self.text += chunk
        broadcast = chunk
        if self.state == "running":
            notices.extend(self._watch(chunk))
            self._note_progress()
        self.cond.notify_all()
        return broadcast, notices

    def _watch(self, chunk: str) -> list:
        if not self.watch_patterns:
            return []
        now = time.time()
        if now < self._watch_next:
            return []
        matched = None
        line = ""
        for pat in self.watch_patterns:
            if pat and pat in chunk:
                matched = pat
                for row in chunk.splitlines():
                    if pat in row:
                        line = row
                        break
                break
        if matched is None:
            return []
        self._watch_next = now + _WATCH_GAP
        return [{
            "type": "watch_match",
            "session_id": self.id,
            "session_key": self._command_session_key,
            "command": self.command,
            "command_seq": self.command_seq,
            "pattern": matched,
            "output": line[:2000],
            "suppressed": 0,
        }]

    def _note_progress(self) -> None:
        match = _DONE_RE.search(self.text[self.command_start:])
        if match is not None and self._latched_exit is None:
            self._latched_exit = int(match.group(1))
        if self._latched_exit is None:
            return
        self._settle_gen += 1
        gen = self._settle_gen
        timer = threading.Timer(0.3, self._settle, args=(gen,))
        timer.daemon = True
        timer.start()

    def _settle(self, gen: int) -> None:
        notice = None
        with self.lock:
            if gen != self._settle_gen or self.state != "running" or self._latched_exit is None:
                return
            self.exit_code = self._latched_exit
            self.state = "idle"
            self.result_ready = True
            self.cond.notify_all()
            if self.notify_on_complete and not self._notified:
                self._notified = True
                notice = {
                    "type": "completion",
                    "session_id": self.id,
                    "session_key": self._command_session_key,
                    "command": self.command,
                    "command_seq": self.command_seq,
                    "exit_code": self.exit_code,
                    "completion_reason": "exited",
                    "termination_source": "",
                    "started_at": self.started_at,
                }
        if notice is not None:
            self._deliver(notice)
        push_label(self)

    def _read(self) -> None:
        try:
            while True:
                chunk = self.shell.read()
                if chunk == "":
                    continue
                with self.lock:
                    broadcast, notices = self._absorb(chunk)
                    subs = list(self.subscribers)
                if broadcast:
                    for subscriber in subs:
                        subscriber.put(broadcast)
                for notice in notices:
                    self._deliver(notice)
        except Exception as exc:
            with self.lock:
                self.ended = exc
                try:
                    self.exit_code = self.shell.returncode()
                except Exception as code_exc:
                    self.ended = code_exc
                else:
                    self.ended = None
                self.state = "exited"
                self.ready.set()
                self.cond.notify_all()
                note = self.ended if self.ended is not None else EOFError("shell exited")
                for subscriber in self.subscribers:
                    subscriber.put(note)

    def _deliver(self, notice: dict) -> None:
        from apps.nextagent.maf_tools.process_registry import process_registry

        process_registry.completion_queue.put(notice)

    def wait_ready(self, timeout: float) -> None:
        if not self.ready.wait(timeout):
            raise TimeoutError("终端在限定时间内没有就绪")
        time.sleep(0.4)
        with self.lock:
            if self.state == "exited":
                if self.ended is not None:
                    raise self.ended
                raise RuntimeError("终端在就绪前退出了")
            if self.state == "starting":
                self.state = "idle"
                self.cond.notify_all()

    def submit_line(self, line: str, *, notify: bool = False, patterns=None, session_key: str = "") -> None:
        if not isinstance(line, str) or not line.strip():
            raise ValueError("命令是空的")
        if "\n" in line or "\r" in line:
            raise ValueError("一次只能提交一行")
        with self.lock:
            if self.state != "idle":
                raise RuntimeError(f"会话现在是 {self.state}，不能提交新命令")
            self.command = line
            self.command_start = len(self.text)
            self.command_seq += 1
            self.exit_code = None
            self.notify_on_complete = bool(notify)
            self.watch_patterns = list(patterns or [])
            self._command_session_key = str(session_key or "").strip() or str(self.session_key or "").strip()
            self._notified = False
            self.result_ready = False
            self.result_taken = False
            self._latched_exit = None
            self._settle_gen += 1
            self.state = "running"
        self.shell.write(line + "\r")
        push_label(self)

    def write_raw(self, data: str) -> None:
        if not isinstance(data, str):
            raise ValueError("输入必须是字符串")
        with self.lock:
            if self.state not in ("idle", "running"):
                raise RuntimeError(f"会话现在是 {self.state}，不能输入")
        self.shell.write(data)

    def send_eot(self) -> None:
        self.write_raw("\x04")

    def command_view(self) -> tuple[str, int | None]:
        with self.lock:
            return self._command_view()

    def mark_result_seen(self) -> None:
        with self.lock:
            if self.state == "idle" and self.result_ready:
                self.result_taken = True

    def report_is_stale(self, command_seq: int, *, completion: bool) -> bool:
        with self.lock:
            if command_seq != self.command_seq:
                return True
            return completion and self.result_taken

    def _command_view(self) -> tuple[str, int | None]:
        suffix = self.text[self.command_start:]
        code = self.exit_code if self.exit_code is not None else self._latched_exit
        return _strip_echo(_model_text(suffix), self.command), code

    def visible_text(self) -> str:
        with self.lock:
            return _model_text(self.text)

    def read_from(self, offset: int) -> dict:
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("offset 不能为负")
        with self.lock:
            if offset > len(self.text):
                raise ValueError("offset 超出输出长度")
            return {
                "text": _model_text(self.text[offset:]),
                "offset": len(self.text),
                "state": self.state,
                "exit_code": None if self.state == "running" else self.exit_code,
            }

    def wait_command(self, timeout: float, interrupted=None) -> dict:
        if timeout is None or timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        deadline = time.monotonic() + timeout
        while True:
            with self.cond:
                if self.state == "idle":
                    if self.result_ready and not self.result_taken:
                        self.result_taken = True
                        output, code = self._command_view()
                        return {
                            "status": "idle",
                            "command": self.command,
                            "output": output,
                            "exit_code": code,
                        }
                    return {
                        "status": "idle",
                        "command": self.command,
                        "output": "",
                        "exit_code": None,
                    }
                if self.state == "exited":
                    if self.ended is not None:
                        raise self.ended
                    if self.exit_code is None:
                        raise RuntimeError("shell 已退出但没有退出码")
                    output, _code = self._command_view()
                    return {
                        "status": "exited",
                        "command": self.command,
                        "output": output,
                        "exit_code": self.exit_code,
                    }
                if self.state != "running":
                    raise RuntimeError(f"会话现在是 {self.state}")
                output, _code = self._command_view()
                if interrupted is not None and interrupted():
                    return {
                        "status": "interrupted",
                        "command": self.command,
                        "output": output,
                        "exit_code": None,
                    }
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {
                        "status": "running",
                        "command": self.command,
                        "output": output,
                        "exit_code": None,
                    }
                self.cond.wait(min(0.2, remaining))

    def subscribe(self):
        subscriber = queue.Queue()
        with self.lock:
            history = self.text
            ended = self.ended
            if self.state == "exited" and ended is None:
                ended = EOFError("shell exited")
            if ended is None:
                self.subscribers.append(subscriber)
        return subscriber, history, ended

    def unsubscribe(self, subscriber) -> None:
        with self.lock:
            if subscriber in self.subscribers:
                self.subscribers.remove(subscriber)
            subscriber.put(None)

    def write(self, text: str) -> None:
        self.shell.write(text)

    def resize(self, rows: int, cols: int) -> None:
        self.shell.resize(rows, cols)

    def close(self) -> None:
        self.shell.close()

    @property
    def pid(self) -> int:
        return self.shell.pid


class SessionRegistry:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}
        self.closed_output = {}

    def create(self, path: str, desktop_id: str = "", session_key: str = "", owner: str = "") -> HostedSession:
        session = HostedSession(path, desktop_id=desktop_id, session_key=session_key, owner=owner)
        with self.lock:
            self.sessions[session.id] = session
        return session

    def get(self, session_id: str) -> HostedSession:
        with self.lock:
            session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def has(self, session_id: str) -> bool:
        with self.lock:
            return session_id in self.sessions

    def close(self, session_id: str) -> str:
        with self.lock:
            session = self.sessions.pop(session_id, None)
        if session is None:
            raise KeyError(session_id)
        output = session.visible_text()
        session.close()
        with self.lock:
            session.state = "closed"
            self.closed_output[session_id] = output
        return output

    def list(self) -> list:
        with self.lock:
            sessions = list(self.sessions.values())
        rows = []
        for session in sessions:
            if session.state == "starting":
                continue
            rows.append({
                "id": session.id,
                "path": session.path,
                "pid": session.pid,
                "state": session.state,
                "desktopId": session.desktop_id,
                "winId": session.win_id,
                "sessionKey": session.session_key,
                "owner": session.owner,
            })
        return rows


REGISTRY = SessionRegistry()


def _enqueue(loop, target, item) -> None:
    loop.call_soon_threadsafe(target.put_nowait, item)


def online_desktop_ids() -> list[str]:
    from .desktop import _online

    ids = []
    for row in _online():
        desktop_id = str(row.get("desktopId") or "").strip()
        if desktop_id:
            ids.append(desktop_id)
    return ids


def pick_desktop(preferred: str = "") -> str:
    online = online_desktop_ids()
    if not online:
        raise RuntimeError("桌面页面没开着")
    if len(online) == 1:
        return online[0]
    preferred = str(preferred or "").strip()
    if preferred in online:
        return preferred
    raise RuntimeError("有多块在线桌面，不能确定操作哪一块")


def _desktop_online(desktop_id: str) -> bool:
    from .desktop import screen_online

    return screen_online(desktop_id)


def _desktop_call(desktop_id: str, op: str, params: dict) -> dict:
    from .desktop import exec_on_desktop

    return exec_on_desktop(desktop_id, op, params)


def _window_missing(exc: Exception) -> bool:
    return "不存在" in str(exc)


def window_label(session: HostedSession) -> str:
    return "终端 · " + sign_text(session.session_key, session.owner, session.state)


def sign_text(session_key: str, owner: str, state: str) -> str:
    held = "有人·" + (owner or session_key) if session_key else ""
    if state == "running":
        return (held + " · 忙碌") if held else "忙碌"
    if state == "idle":
        return (held + " · 空闲") if held else "无人"
    if held:
        return held
    return "无人"


def set_occupancy(session: HostedSession, mark: str, session_key: str, owner: str = "") -> None:
    mark = str(mark or "").strip()
    if mark not in {"有人", "无人"}:
        raise RuntimeError("mark 只能是有人或无人")
    session_key = str(session_key or "").strip()
    with session.lock:
        if session.session_key and session.session_key != session_key:
            who = session.owner or "别人"
            raise RuntimeError(f"这个终端有人（{who}），不能改牌子")
        if mark == "有人":
            if not session_key:
                raise RuntimeError("没有对话身份，不能标有人")
            session.session_key = session_key
            session.owner = owner or session_key
        else:
            session.session_key = ""
            session.owner = ""
    push_label(session)


def push_label(session: HostedSession) -> None:
    desktop_id = str(session.desktop_id or "").strip()
    if not desktop_id or not _desktop_online(desktop_id):
        return
    try:
        _desktop_call(desktop_id, "terminal.mark", {
            "sessionId": session.id,
            "label": window_label(session),
        })
    except Exception as exc:
        if _window_missing(exc):
            return
        raise


def open_on_desktop(path: str, desktop_id: str, session_key: str = "", owner: str = "") -> HostedSession:
    desktop_id = pick_desktop(desktop_id)
    if not _desktop_online(desktop_id):
        raise RuntimeError(f"桌面 {desktop_id} 不在线")
    session = None
    try:
        session = REGISTRY.create(path, desktop_id=desktop_id, session_key=session_key, owner=owner)
        session.wait_ready(_READY_TIMEOUT)
        _desktop_call(desktop_id, "terminal.open", {
            "sessionId": session.id,
            "path": session.path,
            "label": window_label(session),
        })
    except Exception:
        if session is not None:
            sid = session.id
            try:
                REGISTRY.close(sid)
            except KeyError:
                pass
            try:
                _desktop_call(desktop_id, "terminal.close", {"sessionId": sid})
            except Exception as close_exc:
                if not _window_missing(close_exc):
                    raise
        raise
    return session


def close_session(session_id: str, desktop_id: str) -> dict:
    try:
        session = REGISTRY.get(session_id)
    except KeyError:
        output = REGISTRY.closed_output.get(session_id)
        if output is None:
            raise KeyError(session_id)
        return {"status": "already_closed", "output": output, "session_id": session_id}
    output = session.visible_text()
    try:
        desktop_id = pick_desktop(desktop_id or session.desktop_id)
    except RuntimeError:
        if not online_desktop_ids():
            REGISTRY.close(session_id)
        raise
    if not _desktop_online(desktop_id):
        REGISTRY.close(session_id)
        raise RuntimeError(f"桌面 {desktop_id} 不在线，会话已停")
    try:
        _desktop_call(desktop_id, "terminal.close", {"sessionId": session_id})
    except Exception as exc:
        if not _window_missing(exc):
            raise
        if REGISTRY.has(session_id):
            REGISTRY.close(session_id)
        saved = REGISTRY.closed_output.get(session_id, output)
        return {"status": "already_closed", "output": saved, "session_id": session_id}
    if REGISTRY.has(session_id):
        REGISTRY.close(session_id)
    saved = REGISTRY.closed_output.get(session_id, output)
    return {"status": "closed", "output": saved, "session_id": session_id}


def focus_session(session: HostedSession, desktop_id: str = "") -> None:
    target = pick_desktop(desktop_id or session.desktop_id)
    if not _desktop_online(target):
        raise RuntimeError(f"桌面 {target} 不在线")
    session.desktop_id = target
    _desktop_call(target, "terminal.open", {
        "sessionId": session.id,
        "path": session.path,
    })


@router.post("/start")
def terminal_start(path: str = Query()):
    session = None
    try:
        session = REGISTRY.create(path)
        session.wait_ready(_READY_TIMEOUT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        if session is not None:
            try:
                REGISTRY.close(session.id)
            except KeyError:
                pass
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": session.id, "path": session.path, "winId": session.win_id}


@router.get("/active")
def terminal_active():
    return {"sessions": REGISTRY.list()}


@router.post("/close")
def terminal_close(id: str = Query()):
    try:
        REGISTRY.close(id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no session: {id}")
    return {"id": id}


@router.websocket("/ws")
async def terminal_ws(websocket: WebSocket):
    session_id = websocket.query_params.get("id", "")
    try:
        session = REGISTRY.get(session_id)
    except KeyError as exc:
        await websocket.accept()
        await websocket.close(code=1008, reason=str(exc)[:120])
        return
    await websocket.accept()
    subscriber, history, ended = session.subscribe()
    await websocket.send_text("\x00" + history)
    if ended is not None:
        await websocket.close()
        return
    loop = asyncio.get_running_loop()
    incoming = asyncio.Queue()

    def pump():
        while True:
            item = subscriber.get()
            _enqueue(loop, incoming, item)
            if item is None or isinstance(item, Exception):
                return

    threading.Thread(target=pump, name="term-ws-" + session.id[:8], daemon=True).start()

    async def send_output():
        while True:
            item = await incoming.get()
            if item is None or isinstance(item, Exception):
                return
            await websocket.send_text(item)

    async def take_input():
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                session.write(message["bytes"].decode("utf-8"))
                continue
            text = message.get("text")
            if text is None:
                raise ValueError("empty websocket message")
            payload = json.loads(text)
            if payload.get("type") == "close":
                try:
                    REGISTRY.close(session.id)
                except KeyError:
                    pass
                return
            if payload.get("type") != "resize":
                raise ValueError(f"unknown message: {text}")
            session.resize(payload["rows"], payload["cols"])

    send_task = asyncio.create_task(send_output())
    take_task = asyncio.create_task(take_input())
    try:
        done, pending = await asyncio.wait(
            {send_task, take_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
    finally:
        session.unsubscribe(subscriber)
