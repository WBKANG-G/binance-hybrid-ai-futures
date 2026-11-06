"""A paused EMERGENCY can be observed without recovery approval or trade POSTs."""

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

import scripts.observe_demo_private as observer
from integrations.binance_usdm.demo import DemoFailure
from services.execution.demo_stream import session
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case  # noqa: F401
from tests.integration.test_demo_period_review import period_case  # noqa: F401


def test_observation_keeps_emergency_and_baselines_and_submits_no_orders(period_case, monkeypatch):  # noqa: F811
    store, c, fake, *_ = period_case
    monkeypatch.setattr(observer, "ACCOUNT", "DEMO-test")
    original = c.client.send

    def send(method, path, query, key):
        if path == "/fapi/v1/listenKey":
            return fake(method, path, query, key)
        return original(method, path, query, key)

    c.client = observer.ObservationClient(
        c.client.settings.model_copy(update={"demo_trading_enabled": True}),
        c.client.credentials,
        send=send,
        clock_ms=c.client.clock_ms,
    )
    c.client.sync_clock()
    before = store.read("DEMO-test")
    start = len(fake.calls)

    async def run():
        async def handler(socket):
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            async def local(coordinator, *, on_ready):
                await session(coordinator, connector=connector, on_ready=on_ready)

            monkeypatch.setattr(observer, "session", local)
            await observer.observe(c, seconds=0.2)

    asyncio.run(run())
    after = store.read("DEMO-test")
    assert after.risk_state == before.risk_state and after.paused
    assert after.daily_key == before.daily_key and after.weekly_key == before.weekly_key
    assert after.daily_base == before.daily_base and after.weekly_base == before.weekly_base
    assert after.high_water == before.high_water and after.requests == before.requests
    writes = [(m, p) for m, p, _ in fake.calls[start:] if m != "GET"]
    assert writes == [("POST", "/fapi/v1/listenKey"), ("DELETE", "/fapi/v1/listenKey")]


def test_observer_refuses_local_occupancy_before_session(period_case, monkeypatch):  # noqa: F811
    store, c, fake, *_ = period_case
    monkeypatch.setattr(observer, "ACCOUNT", "DEMO-test")

    def occupied(ledger):
        ledger.positions["BTCUSDT"] = Decimal(1)

    store.change(c.lease, "TEST_OCCUPIED", occupied)
    with pytest.raises(DemoFailure):
        observer.empty_paused(store)
