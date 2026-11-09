"""Fenced physical rescue cannot manufacture financial reconciliation or retry exits."""

# ruff: noqa: F811
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_tp_window_budget import (
    ProofDeferred,
    WindowTPBudget,
    weight_scope,
)
from packages.domain.contracts import PositionState
from services.execution.demo_tp_budget_integration import BudgetedTPTriggerCoordinator
from services.execution.demo_tp_uncertain_cancel_candidate import UncertainCancellationClient
from services.risk.paper import RiskRejected
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario


@pytest.fixture
def rescue(opposite_case):
    s, oldbudget, meter, route, old, history = scenario(opposite_case)
    store, prior, fake, options = s
    b = WindowTPBudget(
        oldbudget.sender, oldbudget.profile, oldbudget.costs, allow_synthetic_model_execution=True
    )
    source = prior.client
    client = UncertainCancellationClient(
        source.settings, source.credentials, send=b, clock_ms=source.clock_ms
    )
    c = BudgetedTPTriggerCoordinator(store, prior.lease, client, b)
    b("GET", "/fapi/v1/time", SecretStr(""), None)
    b.configure(
        {
            "rateLimits": [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": 1,
                    "limit": 6000,
                }
            ]
        }
    )
    b.preflight()
    b.begin_cleanup()
    key = next(iter(history))
    c._own_operation = UUID(key)
    order = history[key]
    b.owned_entry_id, b.owned_quantity, b.owned_side = order.client_id, order.quantity, "BUY"
    b.entry_sent = True  # Explicit synthetic pre-existing possible-send evidence.

    def exposed(ledger):
        current = ledger.orders[key]
        current.open_quantity = Decimal(".0007")
        current.position_state = PositionState.OPEN_UNPROTECTED
        ledger.positions["BTCUSDT"] = Decimal(".0007")
        ledger.reconciled = False

    store.change(c.lease, "SYNTHETIC_EXPOSURE", exposed)
    fake.positions["BTCUSDT"] = Decimal(".0007")
    yield store, c, b, meter, fake, key
    route.close()


@pytest.mark.parametrize("mode", ["flat", "pending", "lost"])
def test_one_fenced_exit_and_no_false_flat_or_history_merge(rescue, mode):
    store, c, b, meter, fake, key = rescue
    before = store.read(c.lease.account_id)
    fake.defer_exit = mode == "pending"
    fake.lost_submit = mode == "lost"
    try:
        assert c.command(uuid4(), "EMERGENCY_FLATTEN") == "UNKNOWN"
    except DemoFailure:
        assert mode == "lost"
    after = store.read(c.lease.account_id)
    assert after.paused and not after.reconciled
    assert after.orders[key].filled == before.orders[key].filled
    assert after.orders[key].position_state != PositionState.CLOSED
    assert after.value == before.value
    assert after.daily_base == before.daily_base and after.weekly_base == before.weekly_base
    try:
        c.command(uuid4(), "EMERGENCY_FLATTEN")
    except DemoFailure:
        assert mode == "lost"
    exits = [r for r in meter.rows if r["method"] == "POST" and r["path"] == "/fapi/v1/order"]
    assert len(exits) == 1 and b.peak <= 5700
    assert c.budgeted_flat_confirmation == "UNKNOWN"


@pytest.mark.parametrize("fault", ["foreign", "direction", "size", "fence"])
def test_unowned_or_unfenced_position_never_gets_exit_post(rescue, fault):
    store, c, b, meter, fake, key = rescue
    if fault == "foreign":
        fake.positions["ETHUSDT"] = Decimal("1")
    elif fault == "direction":
        fake.positions["BTCUSDT"] = Decimal("-.0007")
    elif fault == "size":
        fake.positions["BTCUSDT"] = Decimal(".0008")
    else:
        c.lease = c.lease.model_copy(update={"fence": c.lease.fence + 1})
    with pytest.raises((DemoFailure, RiskRejected)):
        c.command(uuid4(), "EMERGENCY_FLATTEN")
    assert not any(r["method"] == "POST" for r in meter.rows)
    assert c.budgeted_flat_confirmation == "UNKNOWN"


def test_local_proof_deferral_precedes_database_and_exchange_calls(rescue, monkeypatch):
    store, c, b, meter, fake, key = rescue
    with weight_scope("PROOF"):
        for _ in range(80):
            b("GET", "/fapi/v1/openOrders", SecretStr(""), None)
    count = len(meter.rows)

    def no_database(*args, **kwargs):
        raise AssertionError("Must defer before opening a fenced transaction")

    monkeypatch.setattr(store, "change", no_database)
    with pytest.raises(ProofDeferred):
        c._confirm_flat_checked()
    assert len(meter.rows) == count


@pytest.mark.parametrize("status", ["NEW", "PARTIALLY_FILLED", "CANCELED"])
def test_zero_position_with_uncertain_entry_cannot_release_rescue_or_wait(rescue, status):
    store, c, b, meter, fake, key = rescue

    def pending(ledger):
        ledger.orders[key].remaining = Decimal(".0004")

    store.change(c.lease, "SYNTHETIC_PENDING_ENTRY", pending)
    fake.positions["BTCUSDT"] = Decimal(0)
    fake.orders[b.owned_entry_id]["status"] = status
    with weight_scope("WATCH"):
        if status == "CANCELED":
            assert c._physical_position()[0] == 0 and b.flat_observed
        else:
            with pytest.raises(DemoFailure, match="not terminal"):
                c._physical_position()
            assert not b.flat_observed
    # Even terminal cancellation alone never changes financial/remaining state.
    assert store.read(c.lease.account_id).orders[key].remaining == Decimal(".0004")
    assert c.budgeted_flat_confirmation == "UNKNOWN"
