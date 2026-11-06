from decimal import Decimal
from uuid import uuid4, uuid5

import pytest
from packages.domain.contracts import PositionState

from test_flat_ack import approved, demo_system, m, recovery  # noqa: F401


@pytest.mark.parametrize(
    "fault",
    [
        "prior_receipt_missing",
        "prior_receipt_malformed",
        "prior_ready_changed",
        "prior_finally_missing",
        "prior_order_open",
        "prior_stop_unknown",
        "prior_stop_evidence_missing",
        "incident_ids_overlap",
        "day_changed",
        "latest_child_missing",
        "latest_quantity_changed",
    ],
)
def test_both_incidents_prior_receipt_and_period_are_required(approved, fault):  # noqa: F811
    store, c, _, reference = approved
    lease = store.acquire("DEMO-test", uuid4())

    def change(state):
        prior = state.orders[str(m.PRIOR_OPERATION)]
        latest = state.orders[str(m.LATEST_OPERATION)]
        if fault == "prior_receipt_missing":
            state.recovery_receipts.pop(m.PRIOR_RECEIPT)
        elif fault == "prior_receipt_malformed":
            state.recovery_receipts[m.PRIOR_RECEIPT] = "invalid"
        elif fault == "prior_ready_changed":
            state.operations[str(uuid5(m.PRIOR_OPERATION, "protected-worker-ready"))] = (
                "FULL_PROTECTED_READY"
            )
        elif fault == "prior_finally_missing":
            state.operations.pop(str(uuid5(m.PRIOR_OPERATION, "protected-worker-finally")))
        elif fault == "prior_order_open":
            prior.position_state = PositionState.PROTECTED
        elif fault == "prior_stop_unknown":
            state.requests[prior.algo_id] = "UNKNOWN"
        elif fault == "prior_stop_evidence_missing":
            state.validation_evidence[str(m.PRIOR_OPERATION)]["stop_verified"] = False
        elif fault == "incident_ids_overlap":
            latest.algo_id = prior.algo_id
        elif fault == "day_changed":
            state.daily_key = "2026-11-05"
        elif fault == "latest_child_missing":
            state.operations.pop(str(uuid5(m.LATEST_OPERATION, "protected-worker-failure")))
        else:
            latest.quantity = Decimal("0.0008")

    store.change(lease, "TEST_CHANGED_PROBE_RECOVERY_SCOPE", change)
    store.release(lease)
    before = store.read("DEMO-test")
    result = m.execute(store, c.client, reference, acknowledged=True, account="DEMO-test")
    assert result["recovery"] == "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY", result
    assert result["application_may_have_completed"] is False
    assert result["lease_acquired"] is False
    assert store.read("DEMO-test") == before
