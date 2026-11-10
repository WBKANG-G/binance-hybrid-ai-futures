"""Report fixtures classify returned evidence; they do not establish financial proof."""

# ruff: noqa: F811
import pytest

from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly, open_session  # noqa: F401
from tests.integration.test_quota_startup_review import snapshot
from tests.unit.test_quota_startup_read_only import files

CASES = [
    ("not_started", "NOT_STARTED"),
    ("active", "IN_PROGRESS"),
    ("limit", "LIMIT_REACHED_PROOF_UNCONFIRMED"),
    ("cancel", "CALLER_CANCELLED_PROOF_UNCONFIRMED"),
    ("deadline", "START_DEADLINE_PROOF_UNCONFIRMED"),
    ("verified", "PROOF_VERIFIED_REVIEW_ONLY"),
    ("seal_failed", "PROOF_VERIFIED_QUOTA_SEAL_FAILED"),
    ("session_failed", "PROOF_VERIFIED_SESSION_FAILED"),
    ("bridge_degraded", "PROOF_VERIFIED_RUNTIME_DEGRADED"),
    ("binding", "CONTEXT_REJECTED_PROOF_UNCONFIRMED"),
    ("operation", "OPERATION_FAILED_PROOF_UNCONFIRMED"),
]


@pytest.mark.parametrize("scenario,outcome", CASES)
def test_report_distinguishes_proof_seal_and_failure_without_writes(assembly, scenario, outcome):
    session = open_session(assembly, allow_synthetic_model_execution=True)
    c = session.coordinator
    if scenario != "not_started":
        assert c._confirmation_claim.claim()
        c.confirmation_stop_reason = "RUNNING"
        if scenario != "active":
            c._confirmation_claim.finish()
    if scenario in {"verified", "seal_failed", "session_failed", "bridge_degraded"}:
        # Input report state only; no confirm_flat call or synthetic signed proof.
        c.budgeted_flat_confirmation = "VERIFIED_FLAT"
        c.confirmation_stop_reason = "ORIGINAL_PROOF_VERIFIED"
    if scenario == "seal_failed":
        c.quota_seal_failed = True
        c.session_failed = True
        c.quota_bridge.degraded = True
    if scenario == "session_failed":
        c.session_failed = True
    if scenario == "bridge_degraded":
        c.quota_bridge.degraded = True
    if scenario == "limit":
        c.confirmation_stop_reason = "ATTEMPT_LIMIT_REACHED"
    if scenario == "cancel":
        c.confirmation_stop_reason = "CALLER_CANCELLED"
    if scenario == "deadline":
        c.confirmation_stop_reason = "START_DEADLINE_REACHED"
    if scenario in {"binding", "operation"}:
        c.confirmation_stop_reason = "ORIGINAL_OPERATION_FAILED"
        c.confirmation_binding_rejections = int(scenario == "binding")
    before = (
        snapshot(assembly[0]),
        files(assembly[8].order_directory),
        files(session.budget.shared.directory),
    )
    try:
        report = c.confirmation_diagnostics()
        assert report.get("reported_outcome") == outcome
        assert report.get("execution_approved") is False
        assert report.get("next_action") == "KEEP_PAUSED_REVIEW_ONLY"
        assert report.get("quota_seal_verified_from_records") is False
        assert snapshot(assembly[0]) == before[0]
        assert files(assembly[8].order_directory) == before[1]
        assert files(session.budget.shared.directory) == before[2]
    finally:
        if scenario == "active":
            c._confirmation_claim.finish()
        session.close()
