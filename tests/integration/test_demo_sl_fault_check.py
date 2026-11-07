"""Real PG/localhost WSS, signed SDK fixture, unchanged local no-send path."""

import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from services.execution import demo as core
from services.execution import demo_sl_fault_check as module
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as validation_case  # noqa: F401


@pytest.fixture
def case(validation_case):  # noqa: F811
    store, c, fake, options = validation_case
    old = c.client
    c.client = module.SLFaultClient(
        old.settings, old.credentials, send=old.send, clock_ms=old.clock_ms
    )
    c.client.sync_clock()
    options["fill"] = True
    store.change(c.lease, "TEST_SL_FAULT_PAUSE", lambda ledger: setattr(ledger, "paused", True))
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


def test_original_auto_exit_after_sl_local_failure_gate_and_no_send(case):  # noqa: F811
    store, c, fake, _ = case
    builder, observer = core.build_protection, c.client.before_exit
    before = store.read("DEMO-test")
    report, operation = asyncio.run(exercise(case))
    assert report["sl_fault_check"] == "SL_BUILD_FAILURE_GATE_AUTO_FLAT_VERIFIED", report
    assert report["fault_injection_count"] == 1 and report["entry_fill_scope_verified"]
    assert report["entries_paused_after_fault"] and report["entry_gate_blocked_after_fault"]
    assert report["sl_not_sent_proof_verified"]
    assert report["automatic_exit_observed_before_cleanup"]
    assert not report["fallback_operator_emergency_requested"]
    assert report["unexpected_protection_transport_attempts"] == 0
    assert report["new_entry_post_count"] == report["entry_post_attempts"] == 1
    assert report["reduce_only_post_attempts"] == 1
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and report["exit_state"] == "FILLED"
    assert report["failure"] == report["completion_failure"] == "NONE"
    assert report["risk_state"] == "EMERGENCY" and report["final_paused"]
    assert report["history_and_baselines_preserved"]
    after = store.read("DEMO-test")
    order = after.orders[str(operation)]
    assert after.requests[order.algo_id] == "NOT_SENT"
    assert after.request_no_send_evidence[order.algo_id] == "LOCAL_VALIDATION_FAILED"
    assert order.tp_algo_id is None and not order.protected
    assert order.position_state.value == "CLOSED" and order.open_quantity == order.remaining == 0
    assert after.daily_base == before.daily_base and after.weekly_base == before.weekly_base
    assert fake.positions["BTCUSDT"] == 0
    assert [p["type"] for p in posts(fake)] == ["LIMIT", "MARKET"]
    assert posts(fake)[1]["reduceOnly"] == "true"
    assert not posts(fake, "/fapi/v1/algoOrder")
    assert core.build_protection is builder and c.client.before_exit is observer


def test_no_fill_never_injects_and_cancels_flat(case):  # noqa: F811
    _, _, fake, options = case
    options["fill"] = False
    report, _ = asyncio.run(exercise(case, timeout=0.1))
    assert report["sl_fault_check"] == "NOT_ACCEPTED" and report["fault_injection_count"] == 0
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and len(posts(fake)) == 1
    assert report["reduce_only_post_attempts"] == 0
    assert not report["automatic_exit_observed_before_cleanup"]


def test_completed_operation_recovers_without_entry_or_reinjection(case):  # noqa: F811
    _, _, fake, _ = case
    _, operation = asyncio.run(exercise(case))
    count = len(posts(fake))
    report, _ = asyncio.run(exercise(case, operation))
    assert report["failure"] == "RECOVERY_ONLY" and report["fault_injection_count"] == 0
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and len(posts(fake)) == count
    assert report["recovered_existing_intent"]


def test_unknown_exit_is_not_reposted_or_accepted(case):  # noqa: F811
    _, _, fake, _ = case
    fake.defer_exit = True
    report, _ = asyncio.run(exercise(case, timeout=0.15))
    assert report["sl_fault_check"] == "NOT_ACCEPTED"
    assert report["flat_confirmation"] == "UNKNOWN" and report["reduce_only_post_attempts"] == 1
    assert len([p for p in posts(fake) if p.get("reduceOnly") == "true"]) == 1


def test_fallback_flat_cannot_count_as_original_automatic_response(case, monkeypatch):  # noqa: F811
    _, c, fake, _ = case
    original, command = c._flatten, c.command
    allow_fallback = [False]

    def block_auto():
        if allow_fallback[0]:
            original()

    def mark_operator(operation, name):
        if name == "EMERGENCY_FLATTEN":
            allow_fallback[0] = True
        return command(operation, name)

    monkeypatch.setattr(c, "_flatten", block_auto)
    monkeypatch.setattr(c, "command", mark_operator)
    report, _ = asyncio.run(exercise(case, timeout=0.15))
    assert report["sl_fault_check"] == "NOT_ACCEPTED"
    assert not report["automatic_exit_observed_before_cleanup"]
    assert report["fallback_operator_emergency_requested"]
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and fake.positions["BTCUSDT"] == 0


def test_cash_transfer_cannot_be_hidden(case, monkeypatch):  # noqa: F811
    _, _, fake, _ = case
    original = module.confirm_exit

    async def drift(coordinator):
        fake.wallet += Decimal("0.1")
        fake.incomes.append(dict(incomeType="TRANSFER", asset="USDT", tranId=123, income="0.1"))
        return await original(coordinator)

    monkeypatch.setattr(module, "confirm_exit", drift)
    report, _ = asyncio.run(exercise(case))
    assert (
        report["sl_fault_check"] == "NOT_ACCEPTED" and not report["history_and_baselines_preserved"]
    )


def test_audit_failure_blocks_acceptance_and_restores_hooks(case, monkeypatch):  # noqa: F811
    store, c, _, _ = case
    original = store.change
    builder, observer = core.build_protection, c.client.before_exit

    def fail(lease, kind, apply):
        if kind == "DEMO_SL_FAULT_CHECK_RESULT":
            raise OSError("PRIVATE_DATABASE_URL")
        return original(lease, kind, apply)

    monkeypatch.setattr(store, "change", fail)
    report, _ = asyncio.run(exercise(case))
    assert report["failure"] == "RESULT_AUDIT_FAILED" and report["sl_fault_check"] == "NOT_ACCEPTED"
    assert core.build_protection is builder and c.client.before_exit is observer
    assert "PRIVATE_DATABASE_URL" not in json.dumps(report)


def test_prepare_failure_never_submits_and_restores_hooks(case):  # noqa: F811
    _, c, fake, options = case
    options["stale_candle"] = True
    builder, observer = core.build_protection, c.client.before_exit
    report, _ = asyncio.run(exercise(case))
    assert report["sl_fault_check"] == "NOT_ACCEPTED"
    assert report["new_entry_post_count"] == report["fault_injection_count"] == 0
    assert not posts(fake) and core.build_protection is builder
    assert c.client.before_exit is observer


def test_diagnostic_read_failure_does_not_block_original_exit(case, monkeypatch):  # noqa: F811
    store, _, fake, _ = case
    original = store.read
    once = [False]

    def fail_at_exit(account):
        ledger = original(account)
        if not once[0] and any(o.exit_state == "SUBMITTING" for o in ledger.orders.values()):
            once[0] = True
            raise OSError("PRIVATE_DATABASE_URL")
        return ledger

    monkeypatch.setattr(store, "read", fail_at_exit)
    report, _ = asyncio.run(exercise(case, timeout=0.25))
    assert once[0] and report["sl_fault_check"] == "NOT_ACCEPTED"
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and fake.positions["BTCUSDT"] == 0
    assert report["reduce_only_post_attempts"] == 1
    assert "PRIVATE_DATABASE_URL" not in json.dumps(report)


def test_unknown_exit_same_operation_never_posts_again(case):  # noqa: F811
    _, _, fake, _ = case
    fake.defer_exit = True
    _, operation = asyncio.run(exercise(case, timeout=0.15))
    count = len(posts(fake))
    report, _ = asyncio.run(exercise(case, operation, timeout=0.15))
    assert report["failure"] == "RECOVERY_ONLY" and report["sl_fault_check"] == "NOT_ACCEPTED"
    assert report["flat_confirmation"] == "UNKNOWN" and len(posts(fake)) == count
