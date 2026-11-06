"""Real PG/local WS: coalesce a virtual 31-second backlog without extending freshness."""

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

import services.execution.demo_validation as validation
from services.execution.demo_stream import session
from services.risk.paper import RiskRejected
from tests.integration.test_demo import posts, signal, snapshot
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as inbox_case  # noqa: F401


@pytest.mark.parametrize("slow_stage", ["callback", "rest"])
def test_fresh_hints_during_slow_callback_or_rest_finish_with_protection_and_flat(
    inbox_case,  # noqa: F811
    monkeypatch,
    capsys,
    slow_stage,  # noqa: F811
):  # noqa: F811
    store, c, fake, options = inbox_case
    options["fill"] = True
    monkeypatch.setattr(validation, "FLAT_CONFIRMATION_INTERVAL_SECONDS", 0)
    release = threading.Event()
    event_waiting = threading.Event()
    began = threading.Event()
    original_connected = c.connected

    async def run():

        async def send(socket, kind="ORDER_TRADE_UPDATE"):
            await socket.send(json.dumps({"e": kind, "E": int(fake.at.timestamp() * 1000)}))
            await asyncio.sleep(0.02)  # Give the independent receiver actual delivery time.

        async def handler(socket):
            assert await asyncio.to_thread(began.wait, 5)
            await send(socket)
            if slow_stage == "rest":
                assert await asyncio.to_thread(event_waiting.wait, 5)
                await send(socket, "ACCOUNT_UPDATE")  # Old hint waits behind slow REST.
            fake.at += timedelta(seconds=20)
            await send(socket, "ACCOUNT_UPDATE")
            fake.at += timedelta(seconds=11)
            await send(socket, "ALGO_UPDATE")
            release.set()
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            held = False

            def connected():
                nonlocal held
                result = original_connected()
                if slow_stage == "rest" and began.is_set() and not held:
                    held = True
                    event_waiting.set()
                    assert release.wait(5)
                return result

            monkeypatch.setattr(c, "connected", connected)

            async def local_session(coordinator, *, on_ready):
                def callback():
                    on_ready()
                    began.set()
                    if slow_stage == "callback":
                        assert release.wait(5)

                await session(coordinator, connector=connector, on_ready=callback)

            monkeypatch.setattr(validation, "session", local_session)
            try:
                await validation.validate_once(
                    c, uuid4(), "LONG", observation_seconds=0.4, entry_style="OPPOSITE_QUOTE"
                )
            finally:
                release.set()

    asyncio.run(run())
    output = capsys.readouterr().out
    assert '"filled_protection_and_flat_verified": true' in output
    assert '"session_error": false' in output
    assert c.session_diagnostic.received_events >= 3
    assert c.session_diagnostic.coalesced_events >= 1
    assert c.session_diagnostic.event_receive_age_ms in (None, 0)
    assert c.session_diagnostic.code == "NONE"
    assert len(posts(fake)) == 2 and posts(fake)[1]["reduceOnly"] == "true"
    assert all(a["algoStatus"] != "NEW" for a in fake.algos.values())
    assert not any(store.read("DEMO-test").positions.values())


def test_receiver_failure_flag_blocks_reserve_and_resume_before_transport(inbox_case):  # noqa: F811
    store, c, fake, _ = inbox_case

    def unpause(ledger):
        ledger.paused = False

    store.change(c.lease, "TEST_READY_FOR_ADMISSION", unpause)
    c.session_failed = True
    count = len(posts(fake))
    with pytest.raises(RiskRejected):
        c.reserve(signal(environment="DEMO"), snapshot())
    with pytest.raises(RiskRejected):
        c.resume_entries(uuid4())
    assert len(posts(fake)) == count and not store.read("DEMO-test").orders


def test_receiver_failure_during_metadata_refresh_blocks_post_and_keeps_intent(
    inbox_case,  # noqa: F811
    monkeypatch,
):  # noqa: F811
    from integrations.binance_usdm.demo import DemoFailure

    store, c, fake, _ = inbox_case
    store.change(
        c.lease, "TEST_ALLOW_ONE_ADMISSION", lambda ledger: setattr(ledger, "paused", False)
    )
    sig = signal(environment="DEMO")
    order = c.reserve(sig, snapshot())
    original = c.refresh_metadata

    def refresh():
        original()
        c.session_failed = True

    monkeypatch.setattr(c, "refresh_metadata", refresh)
    try:
        c.submit(str(sig.signal_id))
    except DemoFailure:
        pass
    after = store.read("DEMO-test")
    assert not posts(fake)
    assert after.orders[str(sig.signal_id)].remaining == order.quantity
    assert after.requests[order.client_id] == "UNKNOWN"
    assert after.request_no_send_evidence[order.client_id] == "LOCAL_VALIDATION_FAILED"
    # Durable UNKNOWN is never retried as another entry POST.
    c.session_failed = False
    c.submit(str(sig.signal_id))
    assert not posts(fake)
