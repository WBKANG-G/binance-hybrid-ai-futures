"""Durable proof is recorded only before a POST was attempted, never retroactively."""

from decimal import Decimal as D

import pytest

import services.execution.demo as module
from integrations.binance_usdm.demo import DemoUnknown
from integrations.binance_usdm.demo_snapshot import snapshot
from packages.domain.contracts import RiskState
from tests.integration.test_demo import (
    enter,
    posts,
    system,  # noqa: F401
)


def open_unprotected(system):  # noqa: F811
    store, c, fake = system
    sig, order = enter(system)
    fake.fill(order.client_id, order.quantity)
    remote = snapshot(c.client, store.read("DEMO-test"), store.now())
    store.change(c.lease, "TEST_UNPROTECTED_FILL", lambda ledger: c._merge(ledger, remote))
    return store, c, fake, str(sig.signal_id)


def test_local_protection_validation_failure_is_proven_not_sent(system, monkeypatch):  # noqa: F811
    store, c, fake, key = open_unprotected(system)

    def invalid(*args, **kwargs):
        raise ValueError("synthetic invalid protection")

    monkeypatch.setattr(module, "build_protection", invalid)
    c._protect(key, "SL")
    ledger = store.read("DEMO-test")
    identifier = ledger.orders[key].algo_id
    assert ledger.requests[identifier] == "NOT_SENT"
    assert ledger.request_no_send_evidence[identifier] == "LOCAL_VALIDATION_FAILED"
    assert not posts(fake, "/fapi/v1/algoOrder")
    assert ledger.risk_state == RiskState.EMERGENCY and ledger.paused
    c._protect(key, "SL")
    assert not posts(fake, "/fapi/v1/algoOrder")


@pytest.mark.parametrize(
    "amount,reason", [(D("0"), "POSITION_ALREADY_FLAT"), (D("-0.1"), "POSITION_DIRECTION_MISMATCH")]
)
def test_exit_skipped_before_post_has_durable_evidence(system, amount, reason):  # noqa: F811
    store, c, fake, key = open_unprotected(system)
    fake.positions["BTCUSDT"] = amount

    def emergency(ledger):
        ledger.risk_state = RiskState.EMERGENCY
        ledger.paused = True

    store.change(c.lease, "TEST_EMERGENCY", emergency)
    before = len(posts(fake))
    c._flatten()
    ledger = store.read("DEMO-test")
    identifier = ledger.orders[key].exit_id
    assert ledger.requests[identifier] == "NOT_SENT"
    assert ledger.request_no_send_evidence[identifier] == reason
    assert len(posts(fake)) == before
    assert ledger.risk_state == RiskState.EMERGENCY and ledger.paused
    c._flatten()
    assert len(posts(fake)) == before


def test_post_failure_remains_unknown_without_no_send_evidence(system):  # noqa: F811
    store, c, fake, key = open_unprotected(system)
    original = c.client.send

    def lose_response(method, path, query, api_key):
        response = original(method, path, query, api_key)
        if method == "POST" and path == "/fapi/v1/algoOrder":
            raise DemoUnknown("synthetic lost reply")
        return response

    c.client.send = lose_response
    c._protect(key, "SL")
    ledger = store.read("DEMO-test")
    identifier = ledger.orders[key].algo_id
    assert identifier not in ledger.request_no_send_evidence
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 1
    # A subsequent authenticated query may verify the existing algo; no retry POST.
    assert ledger.requests[identifier] in {"UNKNOWN", "VERIFIED"}
    c._protect(key, "SL")
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 1


def test_old_unknown_request_is_not_reclassified(system):  # noqa: F811
    store, c, fake = system

    def historical(ledger):
        ledger.requests["DS-historical"] = "UNKNOWN"
        ledger.risk_state = RiskState.EMERGENCY
        ledger.paused = True

    store.change(c.lease, "TEST_OLD_UNKNOWN", historical)
    c.reconcile()
    ledger = store.read("DEMO-test")
    assert ledger.requests["DS-historical"] == "UNKNOWN"
    assert "DS-historical" not in ledger.request_no_send_evidence
    assert ledger.risk_state == RiskState.EMERGENCY and ledger.paused
    assert not posts(fake)
