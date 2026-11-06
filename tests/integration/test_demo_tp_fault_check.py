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
from services.execution import demo_tp_fault_check as module
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as validation_case  # noqa: F401


@pytest.fixture
def case(validation_case):  # noqa: F811
    store, c, fake, options = validation_case
    old = c.client
    c.client = module.TPFaultClient(
        old.settings, old.credentials, send=old.send, clock_ms=old.clock_ms
    )
    c.client.sync_clock()
    options["fill"] = True
    store.change(c.lease, "TEST_TP_FAULT_PAUSE", lambda ledger: setattr(ledger, "paused", True))
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


def test_signed_stop_retained_tp_build_not_sent_pause_then_operator_flat(case):  # noqa: F811
    store, c, fake, _ = case
    builder = core.build_protection
    before = store.read("DEMO-test")
    report, operation = asyncio.run(exercise(case))
    assert (
        report["tp_fault_check"] == "TP_BUILD_FAILURE_PAUSE_SL_RETAINED_OPERATOR_FLAT_VERIFIED"
    ), json.dumps(report)
    assert report["fault_injection_count"] == 1 and report["signed_stop_before_fault_verified"]
    assert report["entries_paused_after_fault"] and report["entry_gate_blocked_after_fault"]
    assert report["tp_not_sent_proof_verified"] and report["unexpected_tp_transport_attempts"] == 0
    assert report["new_entry_post_count"] == report["entry_post_attempts"] == 1
    assert report["reduce_only_post_attempts"] == 1 and report["operator_emergency_requested"]
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and report["exit_state"] == "FILLED"
    assert report["risk_state"] == "EMERGENCY" and report["final_paused"]
    assert report["failure"] == report["completion_failure"] == "NONE"
    assert report["history_and_baselines_preserved"]
    after = store.read("DEMO-test")
    order = after.orders[str(operation)]
    assert after.requests[order.tp_algo_id] == "NOT_SENT"
    assert after.request_no_send_evidence[order.tp_algo_id] == "LOCAL_VALIDATION_FAILED"
    assert order.position_state.value == "CLOSED" and order.open_quantity == order.remaining == 0
    assert after.daily_base == before.daily_base and after.weekly_base == before.weekly_base
    assert fake.positions["BTCUSDT"] == 0
    assert [p["type"] for p in posts(fake)] == ["LIMIT", "MARKET"]
    assert posts(fake)[1]["reduceOnly"] == "true"
    assert [p["type"] for p in posts(fake, "/fapi/v1/algoOrder")] == ["STOP_MARKET"]
    assert all(row["algoStatus"] != "NEW" for row in fake.algos.values())
    assert core.build_protection is builder


def test_no_fill_never_injects_and_still_cancels_flat(case):  # noqa: F811
    _, _, fake, options = case
    options["fill"] = False
    report, _ = asyncio.run(exercise(case, timeout=0.1))
    assert report["tp_fault_check"] == "NOT_ACCEPTED" and report["fault_injection_count"] == 0
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and len(posts(fake)) == 1
    assert report["reduce_only_post_attempts"] == 0


def test_same_completed_operation_recovers_without_entry_or_reinjection(case):  # noqa: F811
    _, _, fake, _ = case
    _, operation = asyncio.run(exercise(case))
    count = len(posts(fake))
    report, _ = asyncio.run(exercise(case, operation))
    assert report["failure"] == "RECOVERY_ONLY" and report["fault_injection_count"] == 0
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and len(posts(fake)) == count
    assert report["recovered_existing_intent"]


def test_stop_failure_never_triggers_tp_fault_or_claims_retained_stop(case):  # noqa: F811
    _, _, fake, _ = case
    fake.reject_stop = True
    report, _ = asyncio.run(exercise(case, timeout=0.25))
    assert report["tp_fault_check"] == "NOT_ACCEPTED"
    assert report["fault_injection_count"] == 0 and not report["signed_stop_before_fault_verified"]
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and fake.positions["BTCUSDT"] == 0


def test_guard_requires_its_own_fresh_signed_stop_query(case, monkeypatch):  # noqa: F811
    _, _, fake, _ = case
    original = module.pick

    def invalid(raw, keys):
        row = original(raw, keys)
        row["algoStatus"] = "CANCELED"
        return row

    monkeypatch.setattr(module, "pick", invalid)
    report, _ = asyncio.run(exercise(case))
    assert report["failure"] == "STOP_PROOF_NOT_VERIFIED"
    assert report["fault_injection_count"] == 0
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and fake.positions["BTCUSDT"] == 0
    assert [p["type"] for p in posts(fake, "/fapi/v1/algoOrder")] == ["STOP_MARKET"]


def test_unknown_emergency_exit_is_never_reposted_or_claimed_flat(case):  # noqa: F811
    _, _, fake, _ = case
    fake.defer_exit = True
    report, _ = asyncio.run(exercise(case))
    assert report["tp_fault_check"] == "NOT_ACCEPTED"
    assert report["flat_confirmation"] == "UNKNOWN"
    assert report["failure"] == "FLAT_NOT_CONFIRMED"
    assert report["reduce_only_post_attempts"] == 1
    assert len([p for p in posts(fake) if p.get("reduceOnly") == "true"]) == 1


def test_cash_transfer_cannot_be_hidden_as_test_accounting(case, monkeypatch):  # noqa: F811
    _, _, fake, _ = case
    original = module.confirm_exit

    async def drift(coordinator):
        fake.wallet += Decimal("0.1")
        fake.incomes.append(dict(incomeType="TRANSFER", asset="USDT", tranId=123, income="0.1"))
        return await original(coordinator)

    monkeypatch.setattr(module, "confirm_exit", drift)
    report, _ = asyncio.run(exercise(case))
    assert (
        report["tp_fault_check"] == "NOT_ACCEPTED" and not report["history_and_baselines_preserved"]
    )


def test_audit_failure_blocks_acceptance_and_runtime_patch_is_restored(case, monkeypatch):  # noqa: F811
    store, _, _, _ = case
    original = store.change
    builder = core.build_protection

    def fail(lease, kind, apply):
        if kind == "DEMO_TP_FAULT_CHECK_RESULT":
            raise OSError("PRIVATE_DATABASE_URL")
        return original(lease, kind, apply)

    monkeypatch.setattr(store, "change", fail)
    report, _ = asyncio.run(exercise(case))
    assert report["failure"] == "RESULT_AUDIT_FAILED" and report["tp_fault_check"] == "NOT_ACCEPTED"
    assert core.build_protection is builder and "PRIVATE_DATABASE_URL" not in json.dumps(report)


def test_prepare_failure_creates_no_entry_and_restores_builder(case):  # noqa: F811
    _, _, fake, options = case
    options["stale_candle"] = True
    builder = core.build_protection
    report, _ = asyncio.run(exercise(case))
    assert report["tp_fault_check"] == "NOT_ACCEPTED"
    assert report["new_entry_post_count"] == report["fault_injection_count"] == 0
    assert not posts(fake) and core.build_protection is builder
