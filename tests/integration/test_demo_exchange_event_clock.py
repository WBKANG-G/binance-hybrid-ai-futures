"""Actual PG/local WS regression for the observed -6ms PC comparison defect."""

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

import services.execution.demo_validation as validation
from services.execution.demo_stream import session
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as clock_case  # noqa: F401


def shift_time(c, fake, offset):
    original = c.client.send

    def send(method, path, query, key):
        if path == "/fapi/v1/time":
            return fake.response({"serverTime": int(fake.at.timestamp() * 1000) + offset})
        return original(method, path, query, key)

    c.client.send = send
    c.client.sync_clock()


def test_verified_positive_six_ms_event_matches_exchange_clock(clock_case):  # noqa: F811
    store, c, fake, _ = clock_case
    shift_time(c, fake, 6)
    raw = {"e": "ALGO_UPDATE", "E": int(fake.at.timestamp() * 1000) + 6}
    before = raw.copy()
    c.event(raw)
    assert raw == before
    assert c.session_diagnostic.event_local_age_ms == -6
    assert c.session_diagnostic.event_clock_offset_ms == 6
    assert store.read("DEMO-test").reconciled


def test_full_validation_with_exchange_event_six_ms_ahead_of_pc_finishes_successfully(
    clock_case,  # noqa: F811
    monkeypatch,
    capsys,
):  # noqa: F811
    store, c, fake, options = clock_case
    options["fill"] = True
    monkeypatch.setattr(validation, "FLAT_CONFIRMATION_INTERVAL_SECONDS", 0)

    async def run():
        ready = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def handler(socket):
            try:
                await asyncio.wait_for(ready.wait(), timeout=2)
            except TimeoutError:
                return
            await socket.send(
                json.dumps({"e": "ALGO_UPDATE", "E": int(fake.at.timestamp() * 1000) + 6})
            )
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            async def local(coordinator, *, on_ready):
                def callback():
                    on_ready()
                    # Calibrate just before observing the exchange event. The
                    # fixture's frozen clock must not make entry quotes future.
                    shift_time(c, fake, 6)
                    loop.call_soon_threadsafe(ready.set)

                await session(coordinator, connector=connector, on_ready=callback)

            monkeypatch.setattr(validation, "session", local)
            await validation.validate_once(
                c, uuid4(), "LONG", observation_seconds=0.2, entry_style="OPPOSITE_QUOTE"
            )

    asyncio.run(run())
    output = capsys.readouterr().out
    assert '"filled_protection_and_flat_verified": true' in output
    assert '"session_error": false' in output
    assert c.session_diagnostic.received_events >= 1
    assert c.session_diagnostic.code == "NONE"
    assert len(posts(fake)) == 2 and posts(fake)[1]["reduceOnly"] == "true"
    assert not any(store.read("DEMO-test").positions.values())
