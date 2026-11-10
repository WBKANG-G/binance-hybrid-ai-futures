"""Flat resource waits keep original financial reconciliation and all charges."""

# ruff: noqa: F811
from decimal import Decimal
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred
from integrations.binance_usdm.rest import RESTResponse
from packages.domain.contracts import OrderState, PositionState
from services.execution.demo_runtime_confirmation import RuntimeConfirmationCoordinator
from services.execution.models import PaperOrder
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly  # noqa: F401
from tests.integration.test_paper import signal
from tests.integration.test_paper import snapshot as gate_snapshot
from tests.integration.test_runtime_header_provenance import prepared


@pytest.fixture
def observing(assembly, monkeypatch):
    calls = []

    def send(method, path, query, key):
        calls.append(path)
        return RESTResponse(200, b'{"serverTime":1000000}', {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    c = session.coordinator
    c.client.clock_ms = lambda: 1000000
    c.client.sync_clock()
    flow = uuid4()

    def seed(ledger):
        ledger.paused = False
        ledger.orders[str(flow)] = PaperOrder(
            client_id="DE-" + flow.hex[:30],
            signal=signal(
                signal_id=flow, environment="DEMO", purpose="DEMO_VALIDATION", strategy=None
            ),
            snapshot=gate_snapshot(),
            group="BTC",
            quantity=".0007",
            quantity_step=".0001",
            price_tick=".1",
            loss_per_unit="1",
            budget="1",
            remaining="0",
            state=OrderState.FILLED,
            position_state=PositionState.CLOSED,
        )

    assembly[0].change(c.lease, "TEST_OWNED_CLOSED_OBSERVATION", seed)
    c._own_operation = flow
    b.flat_observed = True
    b.owned_entry_id = "DE-" + flow.hex[:30]
    b.owned_quantity = Decimal(".0007")
    b.owned_side = "BUY"
    c._observation_clock = assembly[3].mono
    c._observation_sleep = lambda delay: setattr(
        assembly[3], "seconds", assembly[3].seconds + delay
    )
    resumed = []
    watches = []
    monkeypatch.setattr(
        RuntimeConfirmationCoordinator, "connected", lambda self: resumed.append(True)
    )

    def watch():
        watches.append(assembly[3].seconds)
        return Decimal(0), Decimal(0)

    monkeypatch.setattr(c, "_physical_position", watch)
    yield assembly, session, c, b, calls, resumed, watches
    session.close()


def test_fitting_observation_runs_original_reconciliation_once_without_pause(
    observing, monkeypatch
):
    a, s, c, b, calls, resumed, watches = observing
    checks = []
    monkeypatch.setattr(b, "require_proof", lambda cost: checks.append(cost))
    before = a[0].read(c.lease.account_id)
    c.connected()
    assert checks == [b.profile.preparation + 512] and resumed == [True] and not watches
    assert a[0].read(c.lease.account_id) == before and not c.observation_deferrals


def test_transient_deferral_pauses_and_watches_without_financial_shortcut(observing, monkeypatch):
    a, s, c, b, calls, resumed, watches = observing
    count = [0]

    def require(cost):
        count[0] += 1
        if count[0] <= 2:
            raise ProofDeferred

    monkeypatch.setattr(b, "require_proof", require)
    before = a[0].read(c.lease.account_id)
    c.connected()
    after = a[0].read(c.lease.account_id)
    assert resumed == [True] and len(watches) == 1 and c.observation_deferrals == 2 and after.paused
    before.paused = True
    assert before == after
    assert c.budgeted_flat_confirmation == "UNKNOWN"
    assert (
        c.confirmation_diagnostics()["observation_resource_wait"]["flat_watch_is_financial_proof"]
        is False
    )


def test_real_shared_credit_waits_for_expiry_instead_of_refunding_a_charge(observing):
    a, s, c, b, calls, resumed, watches = observing
    token = b.shared.reserve(4000, "PROOF")
    b.shared.finish_unobserved(token, status=200)
    started = a[3].seconds
    before = a[0].read(c.lease.account_id)
    c.connected()
    assert 60 < a[3].seconds - started < 120 and resumed == [True]
    assert len(watches) >= 6 and c.observation_deferrals >= 60
    assert not b.stopped and not b.untrusted
    assert b.shared.view()["pending_attempts"] == 0
    assert a[0].read(c.lease.account_id).orders == before.orders
    assert c.budgeted_flat_confirmation == "UNKNOWN"


@pytest.mark.parametrize("fault", ["exposure", "unfilled", "foreign_position", "no_ownership"])
def test_wait_never_hides_exposure_or_missing_ownership(observing, monkeypatch, fault):
    a, s, c, b, calls, resumed, watches = observing

    def defer(cost):
        raise ProofDeferred

    monkeypatch.setattr(b, "require_proof", defer)
    if fault == "exposure":
        monkeypatch.setattr(c, "_physical_position", lambda: (Decimal(".0001"), Decimal("80000")))
    elif fault == "no_ownership":
        c._own_operation = None
        # Direct resource-wait entry must refuse too; normal connected preserves its original path.
    else:

        def change(ledger):
            if fault == "unfilled":
                ledger.orders[str(c._own_operation)].remaining = Decimal(".0001")
            else:
                ledger.positions["ETHUSDT"] = Decimal(1)

        a[0].change(c.lease, "TEST_EXPOSURE_RETURNED", change)
    with pytest.raises(DemoFailure):
        c._wait_for_observation_credit()
    assert not resumed and c.budgeted_flat_confirmation == "UNKNOWN"


@pytest.mark.parametrize("clock", ["advancing", "stalled", "backward"])
def test_resource_wait_has_bounded_attempts_and_no_original_reconcile_on_failure(
    observing, monkeypatch, clock
):
    a, s, c, b, calls, resumed, watches = observing

    def defer(cost):
        raise ProofDeferred

    monkeypatch.setattr(b, "require_proof", defer)
    if clock == "stalled":
        c._observation_sleep = lambda delay: None
    elif clock == "backward":
        c._observation_sleep = lambda delay: setattr(a[3], "seconds", a[3].seconds - delay)
    with pytest.raises((DemoFailure, ValueError)):
        c._wait_for_observation_credit()
    assert c.observation_deferrals <= 120 and not resumed
    assert c.budgeted_flat_confirmation == "UNKNOWN"


def test_credit_returning_after_deadline_never_starts_reconciliation(observing, monkeypatch):
    a, s, c, b, calls, resumed, watches = observing

    def slow_credit(cost):
        a[3].seconds += 121

    monkeypatch.setattr(b, "require_proof", slow_credit)
    with pytest.raises(DemoFailure, match="deadline"):
        c._wait_for_observation_credit()
    assert not resumed and c.budgeted_flat_confirmation == "UNKNOWN"


def test_binding_loss_during_wait_refuses_before_another_query(observing, monkeypatch):
    from packages.persistence.demo_order_quota import LocalQuotaStopped

    a, s, c, b, calls, resumed, watches = observing

    def defer(cost):
        raise ProofDeferred

    monkeypatch.setattr(b, "require_proof", defer)
    original = c.client.send

    def lose_binding(delay):
        c.client.send = lambda *args: None
        a[3].seconds += delay

    c._observation_sleep = lose_binding
    calls.clear()
    try:
        with pytest.raises(LocalQuotaStopped):
            c._wait_for_observation_credit()
        assert not calls and not resumed and c.observation_deferrals == 1
    finally:
        c.client.send = original


def test_failed_clock_refresh_cannot_resume_or_fake_flat_proof(observing, monkeypatch):
    a, s, c, b, calls, resumed, watches = observing
    a[3].seconds += 6
    c.client.clock_ms = lambda: 2000000  # Sender's synthetic time is now outside SDK bounds.
    before = a[0].read(c.lease.account_id)
    with pytest.raises(DemoFailure):
        c._wait_for_observation_credit()
    assert not resumed and b.untrusted and b.stopped
    assert a[0].read(c.lease.account_id) == before
    assert c.budgeted_flat_confirmation == "UNKNOWN"
