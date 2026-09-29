from __future__ import annotations

import asyncio


def _read_url(url: str) -> None:
    import urllib.request
    with urllib.request.urlopen(url, timeout=1) as resp:
        resp.read()


async def wait_gateway(url: str, attempts: int = 50) -> None:
    last = ""
    for _ in range(attempts):
        try:
            await asyncio.to_thread(_read_url, url)
            return
        except Exception as exc:
            last = str(exc)
            await asyncio.sleep(0.1)
    raise RuntimeError("网关未就绪: " + last)
