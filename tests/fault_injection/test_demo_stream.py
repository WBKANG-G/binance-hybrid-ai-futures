"""Actual localhost WSS-client plumbing, with synthetic session and REST state."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integrations.binance_usdm.demo import WS_HOST, DemoFailure
from services.execution.demo_stream import session
from tests.integration.test_demo import system  # noqa: F401 — isolated PostgreSQL fixture


def test_private_session_reconciles_on_connect_event_and_disconnect(system):  # noqa: F811
    store, c, fake = system

    async def exercise():
        async def handler(socket):
            await socket.send(
                json.dumps({"e": "ACCOUNT_UPDATE", "E": int(fake.at.timestamp() * 1000)})
            )
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def local_connector(secret_url):
                assert secret_url.get_secret_value().startswith(WS_HOST + "/private/ws/")
                logger = logging.Logger("synthetic-demo-ws")
                logger.disabled = True
                async with connect(f"ws://127.0.0.1:{port}", logger=logger) as websocket:
                    yield websocket

            await session(c, connector=local_connector, max_messages=1)

    asyncio.run(exercise())
    state = store.read("DEMO-test")
    assert not state.stream_ready and not state.reconciled
    calls = [(m, p) for m, p, params in fake.calls if p == "/fapi/v1/listenKey"]
    assert calls == [("POST", "/fapi/v1/listenKey"), ("DELETE", "/fapi/v1/listenKey")]


def test_private_socket_loss_blocks_entries_and_keeps_durable_state(system):  # noqa: F811
    import pytest

    store, c, fake = system

    @asynccontextmanager
    async def broken_connector(url):
        raise RuntimeError("synthetic private key must not leak")
        yield

    with pytest.raises(DemoFailure) as caught:
        asyncio.run(session(c, connector=broken_connector, max_messages=1))
    assert "synthetic" not in str(caught.value)
    assert not store.read("DEMO-test").stream_ready


def test_bounded_private_check_closes_session_without_order_submission(system, monkeypatch):  # noqa: F811
    import scripts.run_demo as cli

    store, coordinator, fake = system

    async def exercise():
        async def handler(socket):
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(secret_url):
                logger = logging.Logger("bounded-demo-ws")
                logger.disabled = True
                async with connect(f"ws://127.0.0.1:{port}", logger=logger) as websocket:
                    yield websocket

            async def local_session(c, *, on_ready):
                await session(c, connector=connector, on_ready=on_ready)

            monkeypatch.setattr(cli, "session", local_session)
            await cli.check_private(coordinator, seconds=2)

    asyncio.run(exercise())
    assert not store.read("DEMO-test").stream_ready
    assert not store.read("DEMO-test").orders
    writes = [(method, path) for method, path, params in fake.calls if method != "GET"]
    assert writes == [("POST", "/fapi/v1/listenKey"), ("DELETE", "/fapi/v1/listenKey")]
