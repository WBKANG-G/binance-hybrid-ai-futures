"""Real localhost websocket handshake/framing; no Binance connection or credentials."""

import asyncio
from contextlib import asynccontextmanager

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integrations.binance_usdm.streams import run_reconnecting


def test_real_socket_disconnect_reconnect_and_message_order():
    actions = []

    async def run():
        attempt = 0

        async def handler(socket):
            nonlocal attempt
            attempt += 1
            await socket.send('{"fixture":' + str(attempt) + "}")
            await socket.close()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(_url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    actions.append("connected")
                    yield socket

            async def reconcile():
                actions.append("rest_reconciled")

            async def consume(raw):
                actions.append(raw)

            async def disconnect():
                actions.append("untrusted")

            async def pause(delay):
                actions.append("backoff")

            await run_reconnecting(
                "wss://fstream.binance.com/public/stream?streams=btcusdt@depth",
                connect=connector,
                reconcile=reconcile,
                consume=consume,
                disconnect=disconnect,
                sleep=pause,
                max_sessions=2,
            )

    asyncio.run(run())
    assert actions == [
        "connected",
        "rest_reconciled",
        '{"fixture":1}',
        "untrusted",
        "backoff",
        "connected",
        "rest_reconciled",
        '{"fixture":2}',
        "untrusted",
    ]
