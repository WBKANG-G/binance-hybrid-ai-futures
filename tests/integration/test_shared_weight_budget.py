"""Shared admission occurs before the original SDK transport; no exchange access."""

# ruff: noqa: F811
from uuid import uuid4

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo_tp_admission import TPAdmissionStopped, TPModelProfile
from integrations.binance_usdm.demo_tp_weight_model import MODELED_TP_ROUTE_COSTS
from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred, weight_scope
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_shared_weight import (
    STARTUP_HOLD,
    SharedWeightJournal,
    SharedWeightStopped,
)
from services.execution.demo_shared_weight_budget import SharedManagedOrderWindowBudget
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.unit.test_durable_order_quota import QuotaClock


@pytest.fixture
def budget(managed, tmp_path):
    store, c, bridge, proxy, q, clock, path = managed
    folder = tmp_path / "shared-weight"
    folder.mkdir(mode=0o700)
    timer = QuotaClock()
    journal = SharedWeightJournal.create(
        folder,
        "DEMO-shared",
        uuid4(),
        monotonic=timer.mono,
        wall_clock=timer.utc,
        boot=lambda: "test-boot",
    )
    timer.seconds = STARTUP_HOLD
    calls = []

    def sender(method, path, query, key):
        calls.append((method, path))
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "1"})

    b = SharedManagedOrderWindowBudget(
        sender, TPModelProfile(0), MODELED_TP_ROUTE_COSTS, bridge=bridge, shared=journal
    )
    return b, journal, timer, calls, store, c


def test_capacity_denial_happens_before_transport_without_poisoning_original_metadata(budget):
    b, journal, timer, calls, *_ = budget
    journal.reserve(5060, "ORDINARY")
    with pytest.raises(TPAdmissionStopped):
        b("GET", "/fapi/v1/time", SecretStr(""), None)
    assert calls == [] and not b.untrusted
    assert journal.view()["pending_weight"] == 5060
    # Original cleanup clock is allowed to use emergency headroom.
    b("GET", "/fapi/v1/time", SecretStr(""), None)
    assert len(calls) == 1


def test_proof_capacity_defers_without_stopping_original_budget(budget):
    b, journal, timer, calls, *_ = budget
    journal.reserve(5572, "PROOF")
    with weight_scope("PROOF"), pytest.raises(ProofDeferred):
        b("GET", "/fapi/v1/time", SecretStr(""), None)
    assert not b.stopped and not b.untrusted and calls == []


def test_original_local_rejection_retains_charge_but_not_pending_send(budget):
    b, journal, timer, calls, *_ = budget
    with pytest.raises(TPAdmissionStopped):
        b("GET", "/fapi/v1/accountConfig", SecretStr(""), None)
    assert not calls and not b.untrusted
    assert journal.view()["pressure"] == 5
    assert journal.view()["pending_attempts"] == 0


def test_default_real_execution_gate_remains_disabled(budget):
    b, journal, timer, calls, store, c = budget
    before = store.read(c.lease.account_id)
    b("GET", "/fapi/v1/time", SecretStr(""), None)
    b.configure(
        {
            "rateLimits": [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": 1,
                    "limit": 6000,
                },
                {"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 300},
                {"rateLimitType": "ORDERS", "interval": "MINUTE", "intervalNum": 1, "limit": 1200},
            ]
        }
    )
    with pytest.raises(TPAdmissionStopped):
        b.preflight()
    assert calls == [("GET", "/fapi/v1/time")] and not b.synthetic
    assert store.read(c.lease.account_id) == before


def test_lost_response_keeps_pending_charge_and_stops_all_participants(budget):
    b, journal, timer, calls, *_ = budget

    def lost(*args):
        raise TimeoutError("synthetic lost response")

    b.raw_sender = lost
    with pytest.raises(TimeoutError):
        b("GET", "/fapi/v1/time", SecretStr(""), None)
    assert b.untrusted
    with pytest.raises(SharedWeightStopped, match="STOPPED"):
        journal.reserve(1, "RESCUE")
    import sqlite3

    with sqlite3.connect(journal.path) as connection:
        assert connection.execute(
            "SELECT SUM(weight) FROM attempts WHERE done IS NULL"
        ).fetchone() == (1,)


def test_transport_cannot_be_called_without_a_reserved_context(budget):
    b, journal, timer, calls, *_ = budget
    with pytest.raises(SharedWeightStopped, match="CONTEXT"):
        b.sender("GET", "/fapi/v1/time", SecretStr(""), None)
    assert calls == [] and journal.view()["pressure"] == 0


def test_complete_proof_cost_is_checked_against_shared_pressure(budget):
    b, journal, timer, calls, *_ = budget
    journal.reserve(4000, "RESCUE")
    with pytest.raises(ProofDeferred):
        b.require_proof(2000)
    assert not calls and not b.stopped and not b.untrusted
