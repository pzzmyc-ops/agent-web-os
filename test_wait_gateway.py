import asyncio
import unittest

from embed_boot import wait_gateway as _wait_gateway


class WaitGatewayTests(unittest.TestCase):
    def test_same_loop_answers(self):
        async def main():
            async def handle(reader, writer):
                await reader.read(4096)
                body = b"ok"
                head = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
                    b"Connection: close\r\n\r\n"
                )
                writer.write(head + body)
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            await _wait_gateway(f"http://127.0.0.1:{port}/models", attempts=3)
            server.close()
            await server.wait_closed()

        asyncio.run(main())

    def test_unreachable_raises(self):
        async def main():
            with self.assertRaises(RuntimeError):
                await _wait_gateway("http://127.0.0.1:1/models", attempts=1)

        asyncio.run(main())


if __name__ == "__main__":
    unittest.main()
