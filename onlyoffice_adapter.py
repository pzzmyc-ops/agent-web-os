from __future__ import annotations

import socket
from urllib.parse import urlparse

from embed_proxy import make_router
from fm.backend.settings import ONLYOFFICE_URL

PREFIX = "/onlyoffice-ds"
UPSTREAM = ONLYOFFICE_URL.rstrip("/")


def _replace_origins(text: str) -> str:
    origins = {
        UPSTREAM,
        UPSTREAM.replace("127.0.0.1", "localhost"),
        UPSTREAM.replace("localhost", "127.0.0.1"),
    }
    for origin in origins:
        text = text.replace(origin, PREFIX)
    return text


def _rewrite_content(raw: bytes, ctype: str) -> bytes:
    text = _replace_origins(raw.decode("utf-8"))
    return text.encode("utf-8")


def _rewrite_message(message: str) -> str:
    return _replace_origins(message)


def embed_url() -> str:
    return PREFIX + "/"


def available() -> bool:
    parsed = urlparse(ONLYOFFICE_URL)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def start(workspace: str) -> None:
    return


def stop() -> None:
    return


def install(app) -> None:
    app.include_router(make_router(
        PREFIX,
        UPSTREAM,
        rewrite=_rewrite_content,
        rewrite_ws=_rewrite_message,
    ))
