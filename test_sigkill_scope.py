import json
from uuid import uuid4, uuid5

import pytest

from test_flat_ack import approved, demo_system, m, recovery  # noqa: F401


@pytest.mark.parametrize(
    "fault",
    [
        "latest_finally",
        "latest_child_failure",
        "extra_entry",
        "early_exit",
        "missing_counts",
        "intermediate_ready_missing",
        "intermediate_finally_missing",
        "earlier_receipt_missing",
        "intermediate_order_open",
        "intermediate_protection_unverified",
    ],
)
def test_sigkill_profile_and_all_three_cohorts_are_bound_before_apply(approved, fault):  # noqa: F811
    store, c, _, reference = approved
    lease = store.acquire("DEMO-test", uuid4())

    def mutate(state):
        def marker(operation, name):
            return str(uuid5(operation, "protected-worker-" + name))

        if fault == "latest_finally":
            state.operations[marker(m.LATEST_OPERATION, "finally")] = "FINALLY_RAN"
        elif fault == "latest_child_failure":
            state.operations[marker(m.LATEST_OPERATION, "failure")] = '{"code":"NONE"}'
        elif fault in {"extra_entry", "early_exit"}:
            state.operations[marker(m.LATEST_OPERATION, "counts")] = json.dumps(
                {"entry": 2 if fault == "extra_entry" else 1, "exit": int(fault == "early_exit")}
            )
        elif fault == "missing_counts":
            state.operations.pop(marker(m.LATEST_OPERATION, "counts"))
        elif fault == "intermediate_ready_missing":
            state.operations.pop(marker(m.INTERMEDIATE_OPERATION, "ready"))
        elif fault == "intermediate_finally_missing":
            state.operations.pop(marker(m.INTERMEDIATE_OPERATION, "finally"))
        elif fault == "earlier_receipt_missing":
            state.recovery_receipts.pop(m.EARLIER_RECEIPT)
        elif fault == "intermediate_order_open":
            from packages.domain.contracts import PositionState

            state.orders[str(m.INTERMEDIATE_OPERATION)].position_state = PositionState.PROTECTED
        else:
            order = state.orders[str(m.INTERMEDIATE_OPERATION)]
            state.requests[order.tp_algo_id] = "UNKNOWN"

    store.change(lease, "TEST_SIGKILL_RECOVERY_SCOPE_CHANGED", mutate)
    store.release(lease)
    before = store.read("DEMO-test")
    result = m.execute(store, c.client, reference, acknowledged=True, account="DEMO-test")
    assert result["recovery"] == "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY", result
    assert not result["application_may_have_completed"]
    assert result["lease_acquired"] is False
    assert store.read("DEMO-test") == before
