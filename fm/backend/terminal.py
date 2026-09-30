import asyncio
import json
import os
import threading
from pathlib import Path

from fastapi import APIRouter, WebSocket

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


def _put(loop, queue, item) -> None:
    loop.call_soon_threadsafe(queue.put_nowait, item)


def _apply_control(session: ShellSession, text: str) -> None:
    payload = json.loads(text)
    if payload.get("type") != "resize":
        raise ValueError(f"unknown message: {text}")
    session.resize(payload["rows"], payload["cols"])


@router.websocket("/ws")
async def terminal_ws(websocket: WebSocket):
    folder = websocket.query_params.get("path", "")
    await websocket.accept()
    try:
        session = open_shell(folder)
    except ValueError as exc:
        await websocket.close(code=1008, reason=str(exc)[:120])
        return
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def reader():
        try:
            while True:
                chunk = session.read()
                if chunk == "":
                    continue
                _put(loop, queue, chunk)
        except Exception as exc:
            _put(loop, queue, exc)

    threading.Thread(target=reader, name="term-read", daemon=True).start()

    async def send_output():
        while True:
            item = await queue.get()
            if isinstance(item, Exception):
                if isinstance(item, EOFError):
                    return
                raise item
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
            _apply_control(session, text)

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
        session.close()
