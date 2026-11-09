"""Local websocket plus real PostgreSQL: failures stay failures, trade gates unchanged."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integrations.binance_usdm.demo import DemoFailure
from packages.persistence.demo import events
from services.execution.demo_stream import session
from tests.integration.test_demo import system  # noqa: F401


@pytest.mark.parametrize(
    "kind,code,stage",
    [
        ("unknown", "EVENT_UNKNOWN", "EVENT"),
        ("stale", "EVENT_TIME_INVALID", "EVENT"),
        ("future", "EVENT_TIME_INVALID", "EVENT"),
        ("json", "EVENT_JSON_INVALID", "PARSE"),
        ("object", "EVENT_OBJECT_INVALID", "PARSE"),
        ("oversized", "EVENT_OVERSIZED", "RECEIVE"),
        ("closed", "SOCKET_CLOSED", "RECEIVE"),
        ("rest", "SNAPSHOT_CHANGED", "EVENT"),
    ],
)
def test_failed_session_records_safe_cause_before_cleanup(system, monkeypatch, kind, code, stage):  # noqa: F811
    store, c, fake = system
    if kind == "rest":

        def failed(raw):
            raise DemoFailure("DEMO REST snapshots changed mid-reconciliation")

        monkeypatch.setattr(c, "event", failed)

    async def run():
        async def handler(socket):
            if kind == "closed":
                await socket.close(reason="PRIVATE_SECRET_URL")
                return
            stamp = int(fake.at.timestamp() * 1000)
            payload = json.dumps({"e": "ACCOUNT_UPDATE", "E": stamp})
            if kind == "unknown":
                payload = json.dumps({"e": "PRIVATE_SECRET_KEY", "E": stamp})
            elif kind == "stale":
                payload = json.dumps({"e": "ACCOUNT_UPDATE", "E": stamp - 30001})
            elif kind == "future":
                payload = json.dumps({"e": "ACCOUNT_UPDATE", "E": stamp + 1})
            elif kind == "json":
                payload = "PRIVATE_SECRET_INVALID_JSON"
            elif kind == "object":
                payload = '["PRIVATE_SECRET"]'
            elif kind == "oversized":
                payload = "x" * 1048577
            await socket.send(payload)
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}", max_size=None) as socket:
                    yield socket

            with pytest.raises(DemoFailure):
                await session(c, connector=connector, max_messages=1)

    asyncio.run(run())
    after = store.read("DEMO-test")
    diag = after.last_private_session_failure
    assert diag is not None and diag.code == code and diag.stage == stage
    assert c.session_failed and c.session_diagnostic == diag
    assert after.paused and not after.stream_ready and not after.reconciled
    assert "PRIVATE_SECRET" not in diag.model_dump_json()
    if kind == "stale":
        assert diag.event_age_ms == 30001
    if kind == "future":
        assert diag.event_age_ms == -1
    if kind == "unknown":
        assert diag.event_kind == "UNKNOWN"
    with store.engine.connect() as conn:
        payload = conn.execute(
            select(events.c.payload).where(events.c.kind == "DEMO_PRIVATE_SESSION_FAILED")
        ).scalar_one()
    assert "PRIVATE_SECRET" not in payload
    assert not any(path == "/fapi/v1/order" and method == "POST" for method, path, _ in fake.calls)


def test_no_failure_diagnostic_is_fabricated_on_normal_cancellation(system):  # noqa: F811
    store, c, fake = system

    async def run():
        async def handler(socket):
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            task = asyncio.create_task(session(c, connector=connector))
            await asyncio.sleep(0.1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
    assert not c.session_failed and c.session_diagnostic.code == "NONE"
    assert store.read("DEMO-test").last_private_session_failure is None


def test_session_creation_failure_is_diagnosed_without_printing_secret(system, monkeypatch):  # noqa: F811
    store, c, _ = system

    def failed(method):
        raise DemoFailure("PRIVATE_SECRET")

    monkeypatch.setattr(c.client, "listen_key", failed)
    with pytest.raises(DemoFailure):
        asyncio.run(session(c))
    assert c.session_failed
    assert c.session_diagnostic.stage == "SESSION_CREATE"
    assert c.session_diagnostic.code == "DEMO_QUERY_OR_SESSION"
    assert "PRIVATE_SECRET" not in store.read("DEMO-test").model_dump_json()


def test_buffered_event_over_thirty_seconds_remains_rejected_and_is_identified(system):  # noqa: F811
    from datetime import timedelta

    store, c, fake = system
    original = int(fake.at.timestamp() * 1000)

    async def run():
        async def handler(socket):
            await socket.send(json.dumps({"e": "ACCOUNT_UPDATE", "E": original}))
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            def delayed_callback():
                fake.at += timedelta(seconds=31)

            with pytest.raises(DemoFailure):
                await session(c, connector=connector, on_ready=delayed_callback, max_messages=1)

    asyncio.run(run())
    assert c.session_diagnostic.code == "EVENT_TIME_INVALID"
    assert c.session_diagnostic.event_kind == "ACCOUNT_UPDATE"
    assert c.session_diagnostic.event_age_ms == 31000
    assert not store.read("DEMO-test").stream_ready
