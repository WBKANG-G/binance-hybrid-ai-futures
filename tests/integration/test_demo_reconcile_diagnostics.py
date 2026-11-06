"""Fail-closed diagnostics and protection guards; no remote exchange access."""

import pytest

import services.execution.demo as module
from integrations.binance_usdm.demo import DemoFailure
from packages.domain.contracts import PositionState
from tests.integration.test_demo import (
    enter,
    posts,
    system,  # noqa: F401
)


@pytest.mark.parametrize("blocked", ["closed", "exit_pending", "zero"])
def test_protection_does_not_post_for_ineligible_position(system, blocked):  # noqa: F811
    store, c, fake = system
    sig, order = enter(system)
    fake.fill(order.client_id, order.quantity)
    c.reconcile()

    def block(ledger):
        current = ledger.orders[str(sig.signal_id)]
        current.algo_id = None
        current.tp_algo_id = None
        ledger.requests = {}
        if blocked == "closed":
            current.position_state = PositionState.CLOSED
        elif blocked == "exit_pending":
            current.position_state = PositionState.EXIT_PENDING
        else:
            current.open_quantity = 0

    store.change(c.lease, "TEST_BLOCK_PROTECTION", block)
    before = len(posts(fake, "/fapi/v1/algoOrder"))
    c._protect(str(sig.signal_id), "SL")
    c._protect(str(sig.signal_id), "TP")
    assert len(posts(fake, "/fapi/v1/algoOrder")) == before


def test_changed_snapshot_retains_emergency_exit_and_safe_reason(system, monkeypatch):  # noqa: F811
    store, c, fake = system
    _, order = enter(system)
    fake.fill(order.client_id, order.quantity)
    real = module.snapshot
    calls = 0

    def changing(*args):
        nonlocal calls
        result = real(*args)
        calls += 1
        if calls == 2:
            result.fingerprint = "changed"
        return result

    monkeypatch.setattr(module, "snapshot", changing)
    with pytest.raises(DemoFailure, match="changed mid-reconciliation"):
        c.reconcile()
    assert store.read("DEMO-test").reconciliation_failure_code == "SNAPSHOT_CHANGED"
    assert fake.positions["BTCUSDT"] == 0
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 0
    assert all(p["type"] == "LIMIT" or p.get("reduceOnly") == "true" for p in posts(fake))


@pytest.mark.parametrize("terminal", ["CANCELED", "REJECTED", "EXPIRED"])
def test_emergency_fallback_never_assigns_position_to_terminal_unfilled_entry(
    system,  # noqa: F811
    monkeypatch,
    terminal,
):
    from packages.domain.contracts import OrderState

    store, c, fake = system
    old_signal, old = enter(system)
    c.cancel(str(old_signal.signal_id))

    def set_terminal(ledger):
        ledger.orders[str(old_signal.signal_id)].state = OrderState(terminal)

    store.change(c.lease, "TEST_TERMINAL_UNFILLED", set_terminal)
    fake.orders[old.client_id]["status"] = terminal
    c.reconcile()
    new_signal, current = enter(system)
    fake.fill(current.client_id, current.quantity)
    original = module.snapshot
    calls = 0

    def changing(*args):
        nonlocal calls
        result = original(*args)
        calls += 1
        if calls == 2:
            result.fingerprint = "changed"
        return result

    monkeypatch.setattr(module, "snapshot", changing)
    with pytest.raises(DemoFailure, match="changed mid-reconciliation"):
        c.reconcile()
    state = store.read("DEMO-test")
    past = state.orders[str(old_signal.signal_id)]
    assert past.filled == 0 and past.open_quantity == 0 and past.exit_id is None
    assert past.state == OrderState(terminal)
    assert fake.positions["BTCUSDT"] == 0
    assert state.orders[str(new_signal.signal_id)].exit_id is not None
    assert state.paused and state.risk_state.value == "EMERGENCY"
    exits = [p for p in posts(fake) if p.get("reduceOnly") == "true"]
    assert len(exits) == 1


def test_unattributed_position_keeps_emergency_without_assigning_cancelled_order(
    system,  # noqa: F811
    monkeypatch,
):
    from decimal import Decimal

    store, c, fake = system
    sig, old = enter(system)
    c.cancel(str(sig.signal_id))
    fake.positions["BTCUSDT"] = old.quantity
    fake.prices["BTCUSDT"] = Decimal("100")
    original = module.snapshot
    calls = 0

    def changing(*args):
        nonlocal calls
        result = original(*args)
        calls += 1
        if calls == 2:
            result.fingerprint = "changed"
        return result

    monkeypatch.setattr(module, "snapshot", changing)
    start = len(posts(fake))
    with pytest.raises(DemoFailure, match="changed mid-reconciliation"):
        c.reconcile()
    ledger = store.read("DEMO-test")
    assert ledger.paused and ledger.risk_state.value == "EMERGENCY"
    assert ledger.orders[str(sig.signal_id)].open_quantity == 0
    assert fake.positions["BTCUSDT"] == old.quantity
    assert len(posts(fake)) == start
