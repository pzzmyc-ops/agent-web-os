from __future__ import annotations

import asyncio
import unittest

from gateway.adapters.base import upstream_client


class UpstreamClientTests(unittest.TestCase):
    def test_chat_client_asks_for_plain_body(self) -> None:
        async def check() -> str:
            async with upstream_client() as client:
                return client.headers["accept-encoding"]

        encoding = asyncio.run(check())
        self.assertEqual(encoding, "identity")
        self.assertNotIn("gzip", encoding)


if __name__ == "__main__":
    unittest.main()
