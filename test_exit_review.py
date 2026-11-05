import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import exercise_opposite, opposite_case  # noqa: F401
from tests.integration.test_demo_paused_period import financial_rows
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "exit_reader", Path(__file__).with_name("review_last_safety_exit.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def closed(opposite_case, monkeypatch):  # noqa: F811
    store, c, fake, options = opposite_case
    options["trigger"] = False
    operation = uuid4()
    report = exercise_opposite(opposite_case, monkeypatch, operation)
    assert report["risk_state"] == "EMERGENCY" and report["flat_confirmation"] == "VERIFIED_FLAT"
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
    return store, c, fake, operation


def test_signed_exit_and_terminal_protection_with_readonly_transactions(closed, monkeypatch):
    store, c, fake, op = closed
    before, count = financial_rows(store), len(fake.calls)
    original = m.account_row

    def readonly(conn, account):
        assert conn.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
        return original(conn, account)

    monkeypatch.setattr(m, "account_row", readonly)
    report = m.inspect(store.engine, "DEMO-test", op, c.client)
    assert report["remote_evidence"] == "SIGNED_ENTRY_EXIT_AND_CURRENT_EMPTY_ACCOUNT_VERIFIED", (
        report
    )
    assert report["finish_emergency_command_recorded"] is True
    assert report["risk_state"] == "EMERGENCY" and report["paused"] is True
    assert report["remote_exit_reduce_only_verified"]
    assert report["remote_stop_status"] == report["remote_target_status"] == "CANCELED"
    assert report["full_recovery_eligibility_reviewed"] is False
    assert report["account_owner_fence_lease_state_unchanged"] and not report["database_changed"]
    assert financial_rows(store) == before
    calls = fake.calls[count:]
    assert len(calls) == 10 and all(method == "GET" for method, _, _ in calls)
    assert all("asyn" not in path and "income" not in path for _, path, _ in calls)


def test_local_only_does_not_query_or_unlock(closed):
    store, _, fake, op = closed
    before, count = financial_rows(store), len(fake.calls)
    report = m.inspect(store.engine, "DEMO-test", op, None)
    assert report["remote_evidence"] == "NOT_CHECKED_CACHED_KEY_OR_CLOCK_UNAVAILABLE"
    assert report["audit_timeline"] and report["finish_emergency_command_recorded"]
    assert len(fake.calls) == count and financial_rows(store) == before


@pytest.mark.parametrize(
    "fault",
    [
        "entry_identity",
        "exit_reduce_only",
        "exit_quantity",
        "active_order",
        "active_protection",
        "live_position",
        "algo_identity",
        "query_failure",
    ],
)
def test_bad_remote_evidence_never_accepted_or_leaked(closed, monkeypatch, fault):
    store, c, fake, op = closed
    before = financial_rows(store)
    order = store.read("DEMO-test").orders[str(op)]
    original = c.client.request

    def tamper(method, path, params=None):
        if fault == "query_failure":
            raise ValueError("PRIVATE_URL_KEY_OR_RAW_FRAME")
        raw = original(method, path, params)
        if (
            fault == "entry_identity"
            and path == "/fapi/v1/order"
            and params["origClientOrderId"] == order.client_id
        ):
            return {**raw, "clientOrderId": "WRONG"}
        if (
            fault == "exit_reduce_only"
            and path == "/fapi/v1/order"
            and params["origClientOrderId"] == order.exit_id
        ):
            return {**raw, "reduceOnly": False}
        if (
            fault == "exit_quantity"
            and path == "/fapi/v1/order"
            and params["origClientOrderId"] == order.exit_id
        ):
            return {**raw, "executedQty": "0.0001"}
        if fault == "active_order" and path == "/fapi/v1/openOrders":
            return [{}]
        if fault == "active_protection" and path == "/fapi/v1/openAlgoOrders":
            return [{}]
        if fault == "live_position" and path == "/fapi/v3/positionRisk":
            return [{"symbol": "BTCUSDT", "positionAmt": "0.0007"}]
        if fault == "algo_identity" and path == "/fapi/v1/algoOrder":
            return {**raw, "clientAlgoId": "WRONG"}
        return raw

    monkeypatch.setattr(c.client, "request", tamper)
    report = m.inspect(store.engine, "DEMO-test", op, c.client)
    assert report["remote_evidence"] == "NOT_VERIFIED_KEEP_LOCK_DO_NOT_RETRY"
    assert "PRIVATE_URL_KEY_OR_RAW_FRAME" not in json.dumps(report)
    assert report.get("current_remote_empty_verified") is not True
    assert report["risk_state"] == "EMERGENCY" and financial_rows(store) == before


def test_write_enabled_client_not_queried(closed):
    store, c, fake, op = closed
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
    count = len(fake.calls)
    assert (
        m.inspect(store.engine, "DEMO-test", op, c.client)["remote_evidence"]
        == "NOT_VERIFIED_KEEP_LOCK_DO_NOT_RETRY"
    )
    assert len(fake.calls) == count
