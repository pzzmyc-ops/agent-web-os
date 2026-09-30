import asyncio
import json
import os
import queue
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, WebSocket

from .pathutil import IS_WINDOWS, to_fs

router = APIRouter(prefix="/api/terminal")


@router.get("/home")
def terminal_home():
    return {"path": to_fs(str(Path.home()))}


class _WinPty:
    def __init__(self, folder: str, rows: int, cols: int):
        from winpty import PtyProcess

        self.proc = PtyProcess.spawn(
            ["powershell.exe", "-NoLogo"],
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


def open_shell(path: str, rows: int = 24, cols: int = 80) -> ShellSession:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path is empty")
    folder = os.path.abspath(path)
    if not os.path.isdir(folder):
        raise ValueError(f"not a directory: {path}")
    backend = _WinPty(folder, rows, cols) if IS_WINDOWS else _PosixPty(folder, rows, cols)
    return ShellSession(backend)


class HostedSession:
    def __init__(self, folder: str):
        self.id = uuid.uuid4().hex
        self.path = to_fs(os.path.abspath(folder))
        self.shell = open_shell(folder)
        self.chunks = []
        self.subscribers = []
        self.ended = None
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._read, name="term-" + self.id[:8], daemon=True)
        self.thread.start()

    def _read(self) -> None:
        try:
            while True:
                chunk = self.shell.read()
                if chunk == "":
                    continue
                with self.lock:
                    self.chunks.append(chunk)
                    for subscriber in self.subscribers:
                        subscriber.put(chunk)
        except Exception as exc:
            with self.lock:
                self.ended = exc
                for subscriber in self.subscribers:
                    subscriber.put(exc)

    def subscribe(self):
        subscriber = queue.Queue()
        with self.lock:
            history = "".join(self.chunks)
            ended = self.ended
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

    def create(self, path: str) -> HostedSession:
        session = HostedSession(path)
        with self.lock:
            self.sessions[session.id] = session
        return session

    def get(self, session_id: str) -> HostedSession:
        with self.lock:
            session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def close(self, session_id: str) -> None:
        with self.lock:
            session = self.sessions.pop(session_id, None)
        if session is None:
            raise KeyError(session_id)
        session.close()

    def list(self) -> list:
        with self.lock:
            sessions = list(self.sessions.values())
        return [{"id": session.id, "path": session.path} for session in sessions]


REGISTRY = SessionRegistry()


def _enqueue(loop, target, item) -> None:
    loop.call_soon_threadsafe(target.put_nowait, item)


@router.post("/start")
def terminal_start(path: str = Query()):
    try:
        session = REGISTRY.create(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": session.id, "path": session.path}


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
                REGISTRY.close(session.id)
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
