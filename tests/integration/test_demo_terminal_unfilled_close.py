"""Queried terminal zero fills close only when no old exposure is being erased."""

from decimal import Decimal

import pytest

from integrations.binance_usdm.demo_snapshot import operational_snapshot
from packages.domain.contracts import PositionState
from tests.integration.test_demo import enter
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure


@pytest.mark.parametrize("status", ["CANCELED", "EXPIRED", "REJECTED"])
def test_queried_terminal_zero_fill_closes_without_exit_or_history_rewrite(demo_system, status):  # noqa: F811
    system, _ = configure(demo_system)
    store, c, fake = system
    signal, order = enter(system)
    before = store.read("DEMO-test")
    fake.orders[order.client_id]["status"] = status
    c.reconcile()
    after = store.read("DEMO-test")
    closed = after.orders[str(signal.signal_id)]
    assert closed.position_state == PositionState.CLOSED
    assert closed.filled == closed.remaining == closed.open_quantity == closed.actual_risk == 0
    assert closed.exit_id is None and closed.algo_id is None and closed.tp_algo_id is None
    assert after.requests == before.requests


def test_ambiguous_old_exposure_is_not_erased_by_zero_fill_terminal_row(demo_system):  # noqa: F811
    system, _ = configure(demo_system)
    store, c, fake = system
    signal, order = enter(system)
    fake.orders[order.client_id]["status"] = "CANCELED"

    def ambiguous(ledger):
        o = ledger.orders[str(signal.signal_id)]
        o.open_quantity = Decimal("0.0007")
        o.actual_risk = Decimal("50")
        o.position_state = PositionState.EXIT_PENDING
        o.exit_state = "UNKNOWN"

    store.change(c.lease, "TEST_LEGACY_AMBIGUITY", ambiguous)
    before = store.read("DEMO-test")
    remote = operational_snapshot(c.client, before, store.now())
    store.change(c.lease, "TEST_EXACT_MERGE", lambda ledger: c._merge(ledger, remote))
    after = store.read("DEMO-test").orders[str(signal.signal_id)]
    assert after.position_state == PositionState.EXIT_PENDING
    assert after.open_quantity == Decimal("0.0007") and after.actual_risk == Decimal("50")
    assert after.exit_state == "UNKNOWN"
