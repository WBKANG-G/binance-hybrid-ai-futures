"""Pending probes cannot authorize success, clear risk, mutate orders or bypass fencing."""

# ruff: noqa: F811
from decimal import Decimal

import pytest

from integrations.binance_usdm.demo import DemoFailure, DemoNotFound, DemoRateLimited
from integrations.binance_usdm.rest import RESTResponse
from packages.domain.contracts import RiskState
from services.execution.demo_tp_admission_candidate import AdmissionTPTriggerCoordinator
from services.risk.paper import RiskRejected
from tests.fixtures.tp_staged_confirmation import staged_coordinator
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario


@pytest.fixture
def pending(opposite_case, monkeypatch):
    s, b, m, route, old, _ = scenario(opposite_case, cap=100000)
    store, prior, fake, _ = s
    c = staged_coordinator(store, prior, b)
    # Seed the explicit post-full-confirmation condition, not a real proof.
    store.change(
        c.lease, "SYNTHETIC_POST_FULL_PENDING", lambda ledger: setattr(ledger, "reconciled", True)
    )
    assert c._locally_flat(store.read("DEMO-test"))
    c._pending_after_full = True
    c.client.acknowledged_cleanup_ids.update(fake.algos)
    fake.positions["BTCUSDT"] = Decimal("0")
    b.begin_cleanup()
    fake.algos[next(k for k in fake.algos if k.startswith("DS-"))]["algoStatus"] = "NEW"
    delegated = []

    def original_required(self):
        delegated.append(True)
        self.flat_confirmation_stage = "QUERY"
        self.flat_confirmation_diagnostic = {}
        return "UNKNOWN"

    monkeypatch.setattr(AdmissionTPTriggerCoordinator, "confirm_flat", original_required)
    try:
        yield store, c, fake, b, m, delegated
    finally:
        route.close()
        c.client.send = old


def test_pending_protection_only_defers_and_preserves_all_ledger_fields(pending):
    store, c, fake, b, m, delegated = pending
    before = store.read("DEMO-test")
    assert c.confirm_flat() == "UNKNOWN"
    assert c.pending_confirmation_deferrals == 1 and not delegated
    assert store.read("DEMO-test") == before
    assert all(r["method"] == "GET" for r in m.rows)
    assert all(
        r["path"] in {"/fapi/v1/time", "/fapi/v3/positionRisk", "/fapi/v1/algoOrder"}
        for r in m.rows
    )
    assert any(a["algoStatus"] == "NEW" for a in fake.algos.values())


def test_terminal_status_must_delegate_to_original_full_proof(pending):
    _, c, fake, _, m, delegated = pending
    for row in fake.algos.values():
        row["algoStatus"] = "CANCELED"
    assert c.confirm_flat() == "UNKNOWN" and delegated == [True]
    assert not c.pending_confirmation_deferrals
    assert all(r["method"] == "GET" for r in m.rows)


@pytest.mark.parametrize(
    "change", ["unpaused", "unreconciled", "local_exposure", "remaining", "open_quantity"]
)
def test_changed_local_state_requires_original_full_proof_before_any_probe(pending, change):
    store, c, _, _, m, delegated = pending

    def update(ledger):
        order = next(iter(ledger.orders.values()))
        if change == "unpaused":
            ledger.paused = False
        elif change == "unreconciled":
            ledger.reconciled = False
        elif change == "local_exposure":
            ledger.positions["BTCUSDT"] = Decimal("0.0001")
        elif change == "remaining":
            order.remaining = Decimal("0.0001")
        else:
            order.open_quantity = Decimal("0.0001")

    store.change(c.lease, "SYNTHETIC_LOCAL_CHANGE", update)
    assert c.confirm_flat() == "UNKNOWN" and delegated == [True] and not m.rows


def test_global_foreign_position_is_not_ignored(pending):
    _, c, fake, _, m, delegated = pending
    fake.positions["ETHUSDT"] = Decimal("0.1")
    assert c.confirm_flat() == "UNKNOWN" and delegated == [True]
    assert all(r["path"] != "/fapi/v1/algoOrder" for r in m.rows)


@pytest.mark.parametrize(
    "fault", ["wrong_id", "wrong_kind", "unknown_status", "malformed_positions"]
)
def test_bad_response_requires_full_proof_instead_of_skipping_it(pending, fault):
    _, c, fake, b, _, delegated = pending
    sender = b.sender

    def broken(method, path, query, key):
        response = sender(method, path, query, key)
        if path == "/fapi/v3/positionRisk" and fault == "malformed_positions":
            return RESTResponse(200, b'[{"positionAmt": "0"}]', response.headers)
        if path == "/fapi/v1/algoOrder" and fault != "malformed_positions":
            import json

            row = json.loads(response.body)
            row[
                {
                    "wrong_id": "clientAlgoId",
                    "wrong_kind": "orderType",
                    "unknown_status": "algoStatus",
                }[fault]
            ] = "UNTRUSTED"
            return RESTResponse(200, json.dumps(row).encode(), response.headers)
        return response

    b.sender = broken
    assert c.confirm_flat() == "UNKNOWN" and delegated == [True]
    assert not c.pending_confirmation_deferrals


@pytest.mark.parametrize("status", [418, 429, 500, 404])
def test_probe_http_failure_preserves_sdk_failure_and_never_deletes(pending, status):
    store, c, fake, b, _, delegated = pending
    before = store.read("DEMO-test")
    calls = []

    def failure(method, path, query, key):
        calls.append((method, path))
        return RESTResponse(
            status, b'{"code":-2013}', {"x-mbx-used-weight-1m": "1", "retry-after": "120"}
        )

    b.sender = failure
    expected = (
        DemoRateLimited if status in {418, 429} else DemoNotFound if status == 404 else DemoFailure
    )
    with pytest.raises(expected):
        c.confirm_flat()
    assert store.read("DEMO-test") == before and not delegated
    assert calls == [("GET", "/fapi/v1/time")]
    if status in {418, 429}:
        assert c.client.cooldown_until > c.client.clock_ms()
    assert any(a["algoStatus"] == "NEW" for a in fake.algos.values())


def test_fenced_owner_cannot_even_run_pending_probe(pending):
    _, c, _, _, m, delegated = pending
    c.lease = c.lease.model_copy(update={"fence": c.lease.fence + 1})
    with pytest.raises(RiskRejected):
        c.confirm_flat()
    assert not m.rows and not delegated


@pytest.mark.parametrize(
    "risk",
    [RiskState.DAILY_LOCK, RiskState.WEEKLY_LOCK, RiskState.DRAWDOWN_LOCK, RiskState.EMERGENCY],
)
def test_pending_probe_preserves_existing_risk_locks(pending, risk):
    store, c, _, _, _, delegated = pending
    store.change(
        c.lease, "SYNTHETIC_EXISTING_LOCK", lambda ledger: setattr(ledger, "risk_state", risk)
    )
    before = store.read("DEMO-test")
    assert c.confirm_flat() == "UNKNOWN" and not delegated
    assert store.read("DEMO-test") == before


def test_unacknowledged_pending_cancel_retains_original_full_retry(pending):
    _, c, _, _, _, delegated = pending
    c.client.acknowledged_cleanup_ids.clear()
    assert c.confirm_flat() == "UNKNOWN" and delegated == [True]
    assert not c.pending_confirmation_deferrals


def test_failed_delete_is_never_recorded_as_acknowledged(pending):
    _, c, fake, b, _, delegated = pending
    identifier = next(k for k in fake.algos if k.startswith("DS-"))
    assert identifier in c.client.acknowledged_cleanup_ids
    sender = b.sender

    def reject(method, path, query, key):
        if method == "DELETE":
            return RESTResponse(500, b'{"code":-1}', {"x-mbx-used-weight-1m": "1"})
        return sender(method, path, query, key)

    b.sender = reject
    c.client.sync_clock()
    with pytest.raises(DemoFailure):
        c.client.request("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": identifier})
    assert identifier not in c.client.acknowledged_cleanup_ids
    assert c.confirm_flat() == "UNKNOWN" and delegated == [True]
    assert fake.algos[identifier]["algoStatus"] == "NEW"


def test_first_attempt_cannot_start_with_a_cheap_pending_probe(pending):
    _, c, _, _, m, delegated = pending
    c._pending_after_full = False
    assert c.confirm_flat() == "UNKNOWN" and delegated == [True]
    assert not m.rows and not c._pending_after_full
