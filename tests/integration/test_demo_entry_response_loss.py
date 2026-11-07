"""Real PG/localhost WSS, signed SDK fixture, unchanged local no-send path."""

import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from services.execution import demo_entry_response_loss as module
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as validation_case  # noqa: F401


@pytest.fixture
def case(validation_case):  # noqa: F811
    store, c, fake, options = validation_case
    old = c.client
    c.client = module.EntryLossClient(
        old.settings, old.credentials, send=old.send, clock_ms=old.clock_ms
    )
    c.client.sync_clock()
    options["fill"] = True
    store.change(c.lease, "TEST_ENTRY_LOSS_PAUSE", lambda ledger: setattr(ledger, "paused", True))
    return store, c, fake, options


async def exercise(case, operation=None, timeout=3):
    _, c, _, _ = case
    operation = operation or uuid4()

    async def handler(socket):
        try:
            while True:
                await socket.send(json.dumps({"e": "ACCOUNT_UPDATE", "E": c.client.clock_ms()}))
                await asyncio.sleep(0.01)
        except Exception:
            pass

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        @asynccontextmanager
        async def connector(url):
            async with connect(f"ws://127.0.0.1:{port}") as socket:
                yield socket

        return await module.run(
            c, operation, connector=connector, observation_timeout=timeout, poll_seconds=0.01
        ), operation


def test_unfilled_ack_loss_recovers_same_id_without_duplicate_then_flat(case):  # noqa: F811
    store, c, fake, options = case
    options["fill"] = False
    before = store.read("DEMO-test")
    report, operation = asyncio.run(exercise(case))
    assert report["entry_loss_check"] == "ENTRY_ACK_LOSS_SAME_ID_NO_DUPLICATE_FLAT_VERIFIED", report
    assert report["response_drop_count"] == 1 and report["unknown_intent_persisted"]
    assert report["entry_gate_blocked_while_unknown"] and report["same_id_signed_query_verified"]
    assert report["local_order_recovered_before_cleanup"] and report["same_id_query_count"] >= 1
    assert report["new_entry_post_count"] == report["entry_post_attempts"] == 1
    assert (
        report["reduce_only_post_attempts"] == 0 and report["flat_confirmation"] == "VERIFIED_FLAT"
    )
    assert report["failure"] == report["completion_failure"] == "NONE"
    assert report["risk_state"] == "NORMAL" and report["final_paused"]
    final = store.read("DEMO-test")
    order = final.orders[str(operation)]
    assert final.requests[order.client_id] == "UNKNOWN"
    assert order.state.value == "CANCELED" and order.position_state.value == "CLOSED"
    assert order.server_order_id == c.client.ack_server_id
    assert len(posts(fake)) == 1 and not posts(fake, "/fapi/v1/algoOrder")
    assert final.daily_base == before.daily_base and report["history_and_baselines_preserved"]
    assert c.client.before_recovery_read is None


def test_fill_after_lost_ack_uses_original_protection_and_exit(case):  # noqa: F811
    _, _, fake, _ = case
    report, _ = asyncio.run(exercise(case))
    assert report["entry_loss_check"] == "ENTRY_ACK_LOSS_SAME_ID_NO_DUPLICATE_FLAT_VERIFIED", report
    assert report["reduce_only_post_attempts"] == 1 and report["operator_emergency_requested"]
    assert report["exit_state"] == "FILLED" and fake.positions["BTCUSDT"] == 0
    assert [p["type"] for p in posts(fake)] == ["LIMIT", "MARKET"]
    assert [p["type"] for p in posts(fake, "/fapi/v1/algoOrder")] == [
        "STOP_MARKET",
        "TAKE_PROFIT_MARKET",
    ]


def test_completed_operation_never_submits_or_drops_again(case):  # noqa: F811
    _, _, fake, _ = case
    _, operation = asyncio.run(exercise(case))
    count = len(posts(fake))
    report, _ = asyncio.run(exercise(case, operation))
    assert report["failure"] == "RECOVERY_ONLY" and report["recovered_existing_intent"]
    assert len(posts(fake)) == count and report["entry_loss_check"] == "NOT_ACCEPTED"


def test_real_submit_failure_without_success_response_is_not_injection(case):  # noqa: F811
    _, _, fake, _ = case
    fake.lost_submit = True
    report, _ = asyncio.run(exercise(case, timeout=0.15))
    assert report["entry_loss_check"] == "NOT_ACCEPTED" and report["response_drop_count"] == 0
    assert report["new_entry_post_count"] == 1
    assert len([p for p in posts(fake) if p["type"] == "LIMIT"]) == 1


def test_wrong_same_id_server_proof_is_rejected(case, monkeypatch):  # noqa: F811
    _, _, _, options = case
    options["fill"] = False
    original = module.pick

    def wrong(raw, keys):
        row = original(raw, keys)
        row["orderId"] = "999999999"
        return row

    monkeypatch.setattr(module, "pick", wrong)
    report, _ = asyncio.run(exercise(case, timeout=0.15))
    assert (
        report["entry_loss_check"] == "NOT_ACCEPTED" and not report["same_id_signed_query_verified"]
    )
    assert report["flat_confirmation"] == "VERIFIED_FLAT"


def test_unknown_exit_is_never_reposted_or_claimed_flat(case):  # noqa: F811
    _, _, fake, _ = case
    fake.defer_exit = True
    report, _ = asyncio.run(exercise(case))
    assert report["entry_loss_check"] == "NOT_ACCEPTED" and report["flat_confirmation"] == "UNKNOWN"
    assert len([p for p in posts(fake) if p.get("reduceOnly") == "true"]) == 1


def test_audit_failure_blocks_and_restores_lookup_observer(case, monkeypatch):  # noqa: F811
    store, c, _, options = case
    options["fill"] = False
    original = store.change

    def fail(lease, kind, apply):
        if kind == "DEMO_ENTRY_LOSS_CHECK_RESULT":
            raise OSError("PRIVATE_DATABASE_URL")
        return original(lease, kind, apply)

    monkeypatch.setattr(store, "change", fail)
    report, _ = asyncio.run(exercise(case))
    assert (
        report["failure"] == "RESULT_AUDIT_FAILED" and report["entry_loss_check"] == "NOT_ACCEPTED"
    )
    assert c.client.before_recovery_read is None and "PRIVATE_DATABASE_URL" not in json.dumps(
        report
    )


def test_prepare_failure_never_submits(case):  # noqa: F811
    _, c, fake, options = case
    options["stale_candle"] = True
    report, _ = asyncio.run(exercise(case))
    assert report["entry_loss_check"] == "NOT_ACCEPTED"
    assert report["new_entry_post_count"] == report["response_drop_count"] == 0
    assert not posts(fake) and c.client.before_recovery_read is None


def test_partial_fill_after_lost_ack_is_protected_and_residual_cancelled(case):  # noqa: F811
    _, _, fake, options = case
    options["fill_quantity"] = Decimal("0.02")
    report, _ = asyncio.run(exercise(case))
    assert report["entry_loss_check"] == "ENTRY_ACK_LOSS_SAME_ID_NO_DUPLICATE_FLAT_VERIFIED", report
    assert Decimal(report["filled_quantity"]) == Decimal("0.02")
    assert report["same_id_signed_query_verified"] and fake.positions["BTCUSDT"] == 0
    assert len(posts(fake)) == 2 and posts(fake)[1]["quantity"] == "0.02"
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 2


def test_unknown_proof_diagnostic_error_cannot_prevent_signed_query_or_cleanup(case, monkeypatch):  # noqa: F811
    _, c, fake, options = case
    options["fill"] = False
    original = c._fresh

    def diagnostic_error(ledger):
        if any(o.state.value == "UNKNOWN" for o in ledger.orders.values()):
            raise OSError("PRIVATE_DATABASE_URL")
        return original(ledger)

    monkeypatch.setattr(c, "_fresh", diagnostic_error)
    report, _ = asyncio.run(exercise(case))
    assert report["entry_loss_check"] == "NOT_ACCEPTED"
    assert (
        report["same_id_signed_query_verified"] and report["flat_confirmation"] == "VERIFIED_FLAT"
    )
    assert len(posts(fake)) == 1 and "PRIVATE_DATABASE_URL" not in json.dumps(report)


def test_unknown_exit_same_operation_cannot_generate_another_entry_or_exit(case):  # noqa: F811
    _, _, fake, _ = case
    fake.defer_exit = True
    _, operation = asyncio.run(exercise(case))
    count = len(posts(fake))
    report, _ = asyncio.run(exercise(case, operation))
    assert report["failure"] == "RECOVERY_ONLY" and report["entry_loss_check"] == "NOT_ACCEPTED"
    assert report["flat_confirmation"] == "UNKNOWN" and len(posts(fake)) == count
