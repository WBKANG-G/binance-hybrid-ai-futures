"""Impossible one-window confirmations must be refused before a writer opens."""

# ruff: noqa: F811
from uuid import uuid4

import pytest

from packages.domain.contracts import OrderState, PositionState
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution import demo_managed_runtime as runtime
from services.execution.models import PaperOrder
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly, open_session  # noqa: F401
from tests.integration.test_paper import signal
from tests.integration.test_paper import snapshot as gate_snapshot
from tests.integration.test_quota_startup_review import snapshot
from tests.unit.test_quota_startup_read_only import files


@pytest.mark.parametrize("history", [6, 34, 128])
def test_impossible_proof_stops_before_journal_open_or_transport(assembly, monkeypatch, history):
    store, c, _, _, _, _, _, calls, storage, _ = assembly

    def history_rows(ledger):
        for _ in range(history):
            flow = uuid4()
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

    store.change(c.lease, "TEST_CLOSED_HISTORY", history_rows)
    before = snapshot(store), files(storage.order_directory), files(storage.shared_directory)
    opened = []
    original = runtime.DurableOrderQuota

    def record_open(*args, **kwargs):
        opened.append(kwargs.get("read_only", False))
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "DurableOrderQuota", record_open)
    with pytest.raises(LocalQuotaStopped, match="RUNTIME_PROOF_BATCH_CANNOT_FIT"):
        session = open_session(assembly, allow_synthetic_model_execution=True)
        session.close()
    assert not opened and not calls
    assert before == (
        snapshot(store),
        files(storage.order_directory),
        files(storage.shared_directory),
    )


def test_fitting_plan_is_bound_to_the_coordinators_actual_proof(assembly):
    from services.execution.demo_runtime_capacity import proof_batch_weight

    session = open_session(assembly, allow_synthetic_model_execution=True)
    try:
        plan = session.budget.diagnostics()["capacity_feasibility"]
        assert plan["proof_fits_empty_window"] and plan["entry_fits_empty_window"]
        assert plan["proof_batch_weight"] == session.coordinator.proof_batch
        assert plan["proof_batch_weight"] == proof_batch_weight(session.budget.profile)
        assert not plan["execution_approved"] and not plan["current_pressure_checked"]
        assert not assembly[7]
    finally:
        session.close()
