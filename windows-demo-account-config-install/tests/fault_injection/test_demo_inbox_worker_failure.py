"""A bad message during a real row-locked worker must drain before failure audit."""

import asyncio
import json
import threading
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.exc import SQLAlchemyError
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integrations.binance_usdm.demo import DemoFailure
from services.execution.demo_stream import session
from tests.integration.test_demo import system  # noqa: F401


def test_receiver_failure_during_db_worker_does_not_race_pause_or_failure_audit(
    system,  # noqa: F811
    monkeypatch,  # noqa: F811
):  # noqa: F811
    store, c, fake = system
    started, release = threading.Event(), threading.Event()
    original_change = store.change
    errors = []

    def change(*args):
        try:
            return original_change(*args)
        except SQLAlchemyError as error:
            errors.append(getattr(getattr(error, "orig", None), "sqlstate", None))
            raise

    monkeypatch.setattr(store, "change", change)

    def event(raw):
        def held(ledger):
            started.set()
            assert release.wait(5)

        store.change(c.lease, "TEST_RECEIVER_FAILURE_WITH_LOCK", held)

    monkeypatch.setattr(c, "event", event)

    async def run():
        async def handler(socket):
            await socket.send(
                json.dumps({"e": "ACCOUNT_UPDATE", "E": int(fake.at.timestamp() * 1000)})
            )
            assert await asyncio.to_thread(started.wait, 5)
            await socket.send(
                json.dumps({"e": "PRIVATE_UNKNOWN", "E": int(fake.at.timestamp() * 1000)})
            )
            await asyncio.sleep(2.2)
            assert c.session_failed  # Entry admission closes before DB worker is released.
            release.set()
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            try:
                with pytest.raises(DemoFailure):
                    await session(c, connector=connector)
            finally:
                release.set()

    asyncio.run(run())
    assert not errors and store.read("DEMO-test").paused
    assert c.session_diagnostic.code == "EVENT_UNKNOWN"


def test_cancel_while_receiver_is_draining_still_propagates_cancellation(system, monkeypatch):  # noqa: F811
    from services.execution.demo_event_inbox import EventInbox

    store, c, fake = system
    original = EventInbox.run

    async def run():
        stopping = asyncio.Event()
        release = asyncio.Event()

        async def delayed_stop(self, websocket):
            try:
                await original(self, websocket)
            except asyncio.CancelledError:
                stopping.set()
                await release.wait()
                raise

        monkeypatch.setattr(EventInbox, "run", delayed_stop)

        async def handler(socket):
            await socket.send(
                json.dumps({"e": "ACCOUNT_UPDATE", "E": int(fake.at.timestamp() * 1000)})
            )
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            task = asyncio.create_task(session(c, connector=connector, max_messages=1))
            try:
                await asyncio.wait_for(stopping.wait(), 5)
                task.cancel()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()

    asyncio.run(run())
    assert not c.session_failed and store.read("DEMO-test").paused
