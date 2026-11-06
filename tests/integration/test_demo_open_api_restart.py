"""Independent supervisor stays alive across a synthetic API instance replacement."""

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from services.execution import demo_api_restart_check as module
from services.execution.demo_stream import session
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as validation_case  # noqa: F401


@pytest.fixture
def restart_case(validation_case):  # noqa: F811
    store, c, fake, options = validation_case
    source = c.client
    c.client = module.RestartClient(
        source.settings, source.credentials, send=source.send, clock_ms=source.clock_ms
    )
    c.client.sync_clock()
    store.change(c.lease, "TEST_RESTART_PAUSED", lambda ledger: setattr(ledger, "paused", True))
    return store, c, fake, options


async def exercise(case, operation, reader):
    _, c, _, _ = case

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

        return await module.run(
            c,
            operation,
            instance_reader=reader,
            wait_seconds=0.1,
            poll_seconds=0.01,
            session_call=local,
        )


def test_one_open_order_survives_instance_change_then_cancels_flat_without_duplicate(restart_case):
    store, c, fake, _ = restart_case
    operation = uuid4()
    identities = iter([str(uuid4()), str(uuid4())])
    before = store.read("DEMO-test")
    report = asyncio.run(exercise(restart_case, operation, lambda: next(identities)))
    assert report["restart_check"] == "OPEN_ORDER_API_RESTART_VERIFIED", json.dumps(report)
    assert (
        report["flat_confirmation"] == "VERIFIED_FLAT"
        and report["same_order_observed_before_after"]
    )
    assert report["entry_post_attempts"] == report["new_entry_post_count"] == 1
    assert len(posts(fake)) == 1 and posts(fake)[0]["type"] == "LIMIT"
    order = store.read("DEMO-test").orders[str(operation)]
    assert order.filled == 0 and order.remaining == 0 and order.open_quantity == 0
    assert order.position_state.value == "CLOSED" and order.state.value == "CANCELED"
    assert order.quantity == c.client.allowed_quantity
    assert order.signal.suggested_limit_price < order.snapshot.bid
    assert store.read("DEMO-test").daily_base == before.daily_base
    assert store.read("DEMO-test").paused


def test_unchanged_api_cannot_claim_restart_and_still_cleans_up(restart_case):
    _, _, fake, _ = restart_case
    identity = str(uuid4())
    report = asyncio.run(exercise(restart_case, uuid4(), lambda: identity))
    assert report["restart_check"] == "NOT_ACCEPTED"
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and len(posts(fake)) == 1


def test_fill_during_window_is_not_open_order_restart_acceptance(restart_case):
    from decimal import Decimal

    store, c, fake, options = restart_case
    old, new = str(uuid4()), str(uuid4())
    count = 0

    def reader():
        nonlocal count
        count += 1
        if count == 1:
            return old
        current = next(o for o in store.read("DEMO-test").orders.values() if o.remaining)
        fake.fill(
            current.client_id,
            current.quantity,
            price=Decimal(fake.orders[current.client_id]["price"]),
        )
        return new

    report = asyncio.run(exercise(restart_case, uuid4(), reader))
    assert report["restart_check"] == "NOT_ACCEPTED"
    assert report["flat_confirmation"] == "VERIFIED_FLAT"
    assert fake.positions["BTCUSDT"] == 0
    assert len(posts(fake)) == 2
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 2


def test_existing_completed_uuid_only_recovers_and_never_submits_again(restart_case):
    _, _, fake, _ = restart_case
    operation = uuid4()
    ids = iter([str(uuid4()), str(uuid4())])
    asyncio.run(exercise(restart_case, operation, lambda: next(ids)))

    def forbidden():
        pytest.fail("existing intent recovery must not depend on API availability")

    report = asyncio.run(exercise(restart_case, operation, forbidden))
    assert report["recovered_existing_intent"] and report["new_entry_post_count"] == 1
    assert report["restart_check"] == "NOT_ACCEPTED" and len(posts(fake)) == 1


def test_preview_cannot_send_entry_and_leaves_paused(restart_case, monkeypatch):
    from scripts.observe_demo_private import ObservationClient

    store, c, fake, _ = restart_case
    source = c.client
    c.client = ObservationClient(
        source.settings, source.credentials, send=source.send, clock_ms=source.clock_ms
    )
    c.client.sync_clock()
    before = store.read("DEMO-test")
    start = len(fake.calls)
    monkeypatch.setattr(module, "api_instance", lambda: str(uuid4()))

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

            return await module.preview(c, session_call=local)

    report = asyncio.run(run())
    assert report["preview"] == "OPEN_API_RESTART_NO_ENTRY" and report["orders_enabled"] is False
    after = store.read("DEMO-test")
    assert after.orders == before.orders and after.requests == before.requests and after.paused
    assert [(m, p) for m, p, _ in fake.calls[start:] if m != "GET"] == [
        ("POST", "/fapi/v1/listenKey"),
        ("DELETE", "/fapi/v1/listenKey"),
    ]


def test_api_downtime_then_new_instance_is_observed_without_entry_retry(restart_case):
    _, _, fake, _ = restart_case
    old, new = str(uuid4()), str(uuid4())
    count = 0

    def reader():
        nonlocal count
        count += 1
        if count == 1:
            return old
        if count == 2:
            raise module.RestartBlocked("API_INSTANCE_UNAVAILABLE")
        return new

    report = asyncio.run(exercise(restart_case, uuid4(), reader))
    assert report["restart_check"] == "OPEN_ORDER_API_RESTART_VERIFIED"
    assert report["api_instance_after"] == new and len(posts(fake)) == 1


def test_duplicate_submit_bug_is_blocked_by_transport_and_never_accepted(restart_case, monkeypatch):
    _, c, fake, _ = restart_case
    original = c.submit
    count = 0

    def broken(key):
        nonlocal count
        count += 1
        if count == 1:
            return original(key)
        order = c.store.read(c.lease.account_id).orders[key]
        c.client.request("POST", "/fapi/v1/order", {"newClientOrderId": order.client_id})

    monkeypatch.setattr(c, "submit", broken)
    ids = iter([str(uuid4()), str(uuid4())])
    report = asyncio.run(exercise(restart_case, uuid4(), lambda: next(ids)))
    assert report["restart_check"] == "NOT_ACCEPTED"
    assert report["entry_post_attempts"] == 2 and len(posts(fake)) == 1
    assert report["flat_confirmation"] == "VERIFIED_FLAT"


def test_deposit_basis_change_cannot_be_hidden_below_existing_high_water(restart_case):
    from decimal import Decimal

    store, c, fake, _ = restart_case
    store.change(
        c.lease,
        "TEST_PRIOR_HIGH_WATER",
        lambda ledger: setattr(ledger, "high_water", Decimal("10001")),
    )
    old, new = str(uuid4()), str(uuid4())
    count = 0

    def reader():
        nonlocal count
        count += 1
        if count == 1:
            return old
        fake.wallet += Decimal("0.1")
        fake.incomes.append(dict(incomeType="TRANSFER", asset="USDT", tranId=123, income="0.1"))
        return new

    report = asyncio.run(exercise(restart_case, uuid4(), reader))
    assert report["restart_check"] == "NOT_ACCEPTED"
    assert report["history_and_baselines_preserved"] is False
    assert store.read("DEMO-test").paused
