"""Production coordinator/PG with a local WSS server and deterministic signed SDK fixture."""

import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from services.execution import demo_open_disconnect_check as module
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as validation_case  # noqa: F401


@pytest.fixture
def disconnect_case(validation_case):  # noqa: F811
    store, c, fake, options = validation_case
    source = c.client
    c.client = module.RestartClient(
        source.settings, source.credentials, send=source.send, clock_ms=source.clock_ms
    )
    c.client.sync_clock()
    store.change(c.lease, "TEST_SOCKET_PAUSED", lambda ledger: setattr(ledger, "paused", True))
    return store, c, fake, options


async def exercise(case, operation, *, before_reconnect=None, server_close=False, timeout=5):
    _, c, _, _ = case
    connections = 0

    async def handler(socket):
        if server_close:
            await socket.close(code=1011)
        else:
            await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        @asynccontextmanager
        async def connector(url):
            nonlocal connections
            connections += 1
            if connections == 2 and before_reconnect:
                before_reconnect()
            async with connect(f"ws://127.0.0.1:{port}") as socket:
                yield socket

        return await module.run(c, operation, connector=connector, phase_timeout=timeout)


def test_open_order_socket_loss_gate_reconnect_same_order_one_post_flat(disconnect_case):
    store, c, fake, _ = disconnect_case
    before = store.read("DEMO-test")
    operation = uuid4()
    report = asyncio.run(exercise(disconnect_case, operation))
    assert report["disconnect_check"] == "OPEN_ORDER_SOCKET_LOSS_CANCEL_RECONNECT_VERIFIED", (
        json.dumps(report)
    )
    assert report["entry_gate_blocked_after_disconnect"]
    assert report["same_order_observed_before_after"]
    assert report["injected_close_count"] == report["reconnect_count"] == 1
    assert report["new_entry_post_count"] == report["entry_post_attempts"] == 1
    assert report["failure"] == report["completion_failure"] == "NONE"
    assert report["flat_confirmation"] == "VERIFIED_FLAT"
    assert report["history_and_baselines_preserved"]
    assert (
        report["final_paused"]
        and not report["final_stream_ready"]
        and not report["final_reconciled"]
    )
    assert report["risk_state"] == "NORMAL" and len(posts(fake)) == 1
    after = store.read("DEMO-test")
    order = after.orders[str(operation)]
    assert order.state.value == "CANCELED" and order.position_state.value == "CLOSED"
    assert order.filled == order.remaining == order.open_quantity == 0
    assert after.requests.items() >= before.requests.items()
    assert after.daily_base == before.daily_base and after.weekly_base == before.weekly_base


def test_replayed_uuid_is_recovery_only_no_new_order_or_fault(disconnect_case):
    _, _, fake, _ = disconnect_case
    operation = uuid4()
    asyncio.run(exercise(disconnect_case, operation))
    count = len(posts(fake))
    report = asyncio.run(exercise(disconnect_case, operation))
    assert report["failure"] == "RECOVERY_ONLY"
    assert report["recovered_existing_intent"] and report["injected_close_count"] == 0
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and len(posts(fake)) == count


def test_early_socket_failure_cannot_claim_injected_disconnect(disconnect_case):
    _, _, fake, _ = disconnect_case
    report = asyncio.run(exercise(disconnect_case, uuid4(), server_close=True))
    assert report["disconnect_check"] == "NOT_ACCEPTED"
    assert report["injected_close_count"] == 0 and len(posts(fake)) == 0
    assert report["flat_confirmation"] == "VERIFIED_FLAT"


def test_fill_while_disconnected_uses_existing_protection_and_flat_never_accepts(disconnect_case):
    store, _, fake, _ = disconnect_case
    operation = uuid4()

    source = disconnect_case[1].client.request
    injected_fill = False

    def racing_cancel(method, path, params=None):
        nonlocal injected_fill
        if method == "DELETE" and path == "/fapi/v1/order" and not injected_fill:
            injected_fill = True
            order = store.read("DEMO-test").orders[str(operation)]
            fake.fill(order.client_id, order.quantity / 2, price=order.signal.suggested_limit_price)
        return source(method, path, params)

    disconnect_case[1].client.request = racing_cancel
    report = asyncio.run(exercise(disconnect_case, operation))
    assert report["disconnect_check"] == "NOT_ACCEPTED", report
    assert Decimal(report["filled_quantity"]) > 0
    assert fake.positions["BTCUSDT"] == 0
    assert report["flat_confirmation"] == "VERIFIED_FLAT"
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 2
    entries = [p for p in posts(fake) if p.get("reduceOnly") != "true"]
    assert len(entries) == 1


def test_reconnect_failure_still_cancels_original_order(disconnect_case):
    _, _, fake, _ = disconnect_case

    def fail():
        raise OSError("SECRET_PRIVATE_URL")

    report = asyncio.run(exercise(disconnect_case, uuid4(), before_reconnect=fail))
    assert report["disconnect_check"] == "NOT_ACCEPTED"
    assert report["flat_confirmation"] == "VERIFIED_FLAT"
    assert report["failure"] != "NONE" and "SECRET" not in json.dumps(report)
    assert len(posts(fake)) == 1


def test_duplicate_transport_attempt_is_blocked_and_rejected(disconnect_case, monkeypatch):
    _, c, fake, _ = disconnect_case
    original = c.submit
    count = 0

    def broken(key):
        nonlocal count
        count += 1
        if count == 1:
            return original(key)
        c.client.request("POST", "/fapi/v1/order", {"newClientOrderId": "SECRET"})

    monkeypatch.setattr(c, "submit", broken)
    report = asyncio.run(exercise(disconnect_case, uuid4()))
    assert report["failure"] == "DUPLICATE_ENTRY_ATTEMPT"
    assert report["entry_post_attempts"] == 2 and len(posts(fake)) == 1
    assert report["flat_confirmation"] == "VERIFIED_FLAT"


def test_callback_failure_retains_primary_reason_and_cleans_order(disconnect_case, monkeypatch):
    _, _, fake, _ = disconnect_case

    def failed(*args):
        raise module.RestartBlocked("ENTRY_NOT_OPEN_UNFILLED")

    monkeypatch.setattr(module, "open_order", failed)
    report = asyncio.run(exercise(disconnect_case, uuid4()))
    assert report["failure"] == "ENTRY_NOT_OPEN_UNFILLED"
    assert report["failure_stage"] == "VERIFY_OPEN_ORDER"
    assert report["injected_close_count"] == 0
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and len(posts(fake)) == 1


def test_history_or_baseline_change_blocks_acceptance(disconnect_case):
    store, c, fake, _ = disconnect_case

    def drift():
        fake.wallet += Decimal("0.1")
        fake.incomes.append(dict(incomeType="TRANSFER", asset="USDT", tranId=123, income="0.1"))

    report = asyncio.run(exercise(disconnect_case, uuid4(), before_reconnect=drift))
    assert report["disconnect_check"] == "NOT_ACCEPTED"
    assert not report["history_and_baselines_preserved"]
    assert store.read(c.lease.account_id).paused


def test_gate_not_blocked_cannot_reconnect_or_claim_pass(disconnect_case, monkeypatch):
    _, _, fake, _ = disconnect_case
    monkeypatch.setattr(module, "gate_blocked", lambda *args: False)
    report = asyncio.run(exercise(disconnect_case, uuid4()))
    assert report["failure"] == "DISCONNECT_GATE_NOT_LOCKED"
    assert report["reconnect_count"] == 0 and not report["entry_gate_blocked_after_disconnect"]
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and len(posts(fake)) == 1


def test_flat_confirmation_failure_never_reports_success(disconnect_case, monkeypatch):
    async def unknown(*args):
        return "UNKNOWN"

    monkeypatch.setattr(module, "confirm_exit", unknown)
    report = asyncio.run(exercise(disconnect_case, uuid4()))
    assert report["disconnect_check"] == "NOT_ACCEPTED"
    assert report["failure"] == "FLAT_NOT_CONFIRMED"
    assert report["same_order_observed_before_after"]


def test_result_audit_failure_blocks_acceptance(disconnect_case, monkeypatch):
    store, _, _, _ = disconnect_case
    original = store.change

    def broken(lease, marker, update):
        if marker == "DEMO_OPEN_WS_FAULT_RESULT":
            raise OSError("PRIVATE_DATABASE_URL")
        return original(lease, marker, update)

    monkeypatch.setattr(store, "change", broken)
    report = asyncio.run(exercise(disconnect_case, uuid4()))
    assert report["failure"] == "RESULT_AUDIT_FAILED"
    assert report["disconnect_check"] == "NOT_ACCEPTED"
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and "PRIVATE" not in json.dumps(report)


def test_lost_entry_ack_is_queried_without_second_post(disconnect_case):
    _, _, fake, _ = disconnect_case
    fake.lost_submit = True
    report = asyncio.run(exercise(disconnect_case, uuid4()))
    assert len(posts(fake)) == 1
    assert report["new_entry_post_count"] == 1
    assert report["flat_confirmation"] == "VERIFIED_FLAT"


def test_completed_uuid_audit_marker_is_not_overwritten_by_recovery(disconnect_case):
    from uuid import uuid5

    store, _, _, _ = disconnect_case
    operation = uuid4()
    result = asyncio.run(exercise(disconnect_case, operation))
    assert result["disconnect_check"] == "OPEN_ORDER_SOCKET_LOSS_CANCEL_RECONNECT_VERIFIED"
    tag = str(uuid5(operation, "socket-result"))
    before = store.read("DEMO-test").operations[tag]
    asyncio.run(exercise(disconnect_case, operation))
    assert store.read("DEMO-test").operations[tag] == before == "OPEN_WS_DISCONNECT:VERIFIED"


def test_receiver_failure_during_second_session_shutdown_is_not_hidden(
    disconnect_case, monkeypatch
):
    original = module.session
    calls = 0

    async def late_failure(*args, **kwargs):
        nonlocal calls
        calls += 1
        index = calls
        try:
            return await original(*args, **kwargs)
        finally:
            if index == 2:
                args[0].session_failed = True

    monkeypatch.setattr(module, "session", late_failure)
    report = asyncio.run(exercise(disconnect_case, uuid4()))
    assert report["disconnect_check"] == "NOT_ACCEPTED"
    assert report["failure"] == "SECOND_SESSION_FAILED"
    assert report["flat_confirmation"] == "VERIFIED_FLAT"


def test_prior_completed_order_caps_and_audit_history_are_preserved(disconnect_case):
    store, c, _, _ = disconnect_case
    first = uuid4()
    assert asyncio.run(exercise(disconnect_case, first))["failure"] == "NONE"
    before = store.read("DEMO-test")
    source = c.client
    c.client = module.RestartClient(
        source.settings, source.credentials, send=source.send, clock_ms=source.clock_ms
    )
    c.client.sync_clock()
    second = uuid4()
    report = asyncio.run(exercise(disconnect_case, second))
    assert report["disconnect_check"] == "OPEN_ORDER_SOCKET_LOSS_CANCEL_RECONNECT_VERIFIED"
    after = store.read("DEMO-test")
    assert after.orders[str(first)] == before.orders[str(first)]
    assert after.entry_quantity_caps[str(first)] == before.entry_quantity_caps[str(first)]
    assert all(after.requests[k] == v for k, v in before.requests.items())
    assert all(after.operations[k] == v for k, v in before.operations.items())
