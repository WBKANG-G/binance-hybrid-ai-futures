"""A complete proposed partition and feasible math never grant archive or execution authority."""

import hashlib

import pytest

from scripts.review_demo_history_coverage import report
from services.execution.demo_history_coverage_candidate import (
    HistoricalRequest,
    classify_request,
    model_bounds,
    review_coverage,
)


def refs(n):
    return tuple(hashlib.sha256(str(i).encode()).hexdigest() for i in range(n))


def test_current_34_history_stays_in_current_runtime_and_fails_capacity():
    records = refs(34)
    result = review_coverage(records, records, ())
    assert result["current_runtime_uses_all_retained_orders"]
    assert result["current_runtime_plan"]["proof_required"] == 27366
    assert result["current_runtime_plan"]["entry_required"] == 5920
    assert not result["current_runtime_plan"]["proof_fits_empty_window"]
    assert not result["current_runtime_plan"]["entry_fits_empty_window"]
    assert result["minimum_history_certificates_for_hypothetical_fit"] == 29
    assert result["accepted_archive_certificates"] == 0
    assert not result["proposed_partition_applied"] and not result["execution_approved"]


def test_hypothetical_29_archives_and_five_live_still_have_no_authority():
    records = refs(34)
    result = review_coverage(records, records[:5], records[5:])
    assert result["all_retained_references_covered"] and result["history_rows_deleted"] == 0
    assert result["hypothetical_plan_not_used_for_admission"]["proof_required"] == 5442
    assert result["hypothetical_plan_not_used_for_admission"]["proof_fits_empty_window"]
    assert "ARCHIVE_EVIDENCE_AUTHORITY_NOT_IMPLEMENTED" in result["current_execution_blockers"]
    assert result["current_runtime_plan"]["historical_orders"] == 34
    assert result["accepted_archive_certificates"] == 0 and not result["archive_adoption_approved"]
    assert not result["execution_approved"] and not result["orders_enabled"]


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "overlap", "foreign", "bad-format", "over-scope"]
)
def test_reference_coverage_cannot_silently_omit_or_substitute_history(fault):
    records = refs(34)
    live = records[:5]
    archive = records[5:]
    if fault == "missing":
        archive = archive[1:]
    elif fault == "duplicate":
        archive = archive + (archive[0],)
    elif fault == "overlap":
        archive = archive + (live[0],)
    elif fault == "foreign":
        archive = archive + ("f" * 64,)
    elif fault == "bad-format":
        archive = archive + ("PRIVATE_INPUT",)
    else:
        records = refs(129)
        live = records
        archive = ()
    with pytest.raises(ValueError, match="HISTORY_REVIEW_"):
        review_coverage(records, live, archive)


@pytest.mark.parametrize(
    "role,state,observed,corrected",
    [
        ("ENTRY", "UNKNOWN", "FILLED", False),
        ("STOP_LOSS", "UNKNOWN", "NOT_FOUND_UNRESOLVED", True),
        ("EXIT", "SUBMITTING", "NOT_FOUND_UNRESOLVED", True),
        ("ENTRY", "UNKNOWN", "NONE", False),
    ],
)
def test_closed_local_or_corrected_receipt_never_rewrites_stored_request(
    role, state, observed, corrected
):
    item = HistoricalRequest(role, state, True, observed, corrected)
    result = classify_request(item)
    assert result["stored_state"] == result["proposed_stored_state"] == state
    assert not result["request_state_write_authorized"] and not result["remote_resolution_verified"]
    assert not result["archive_authorized"] and not result["acknowledgement_is_remote_proof"]
    assert not result["execution_approved"]


@pytest.mark.parametrize("count", [0, 1, 5, 6, 29, 30, 34, 128])
def test_every_proposal_keeps_default_denial_and_original_model_assumptions(count):
    records = refs(count)
    result = review_coverage(records, records, ())
    assert result["current_runtime_plan"]["historical_orders"] == count
    assert result["current_runtime_plan"]["effective_ceiling"] == 5700
    assert result["current_runtime_plan"]["proof_fits_empty_window"] == (count <= 5)
    assert result["current_runtime_plan"]["entry_fits_empty_window"] == (count <= 29)
    assert (
        not result["execution_approved"] and result["order_writes"] == result["http_attempts"] == 0
    )


def test_recorded_screenshot_counts_are_explicitly_not_live_verified_inputs():
    result = report()
    assert result["order_references_are_synthetic_placeholders"]
    assert "NOT_FRESH_VERIFICATION" in result["observation_source"]
    assert [x["stored_state"] for x in result["historical_requests"]] == [
        "UNKNOWN",
        "UNKNOWN",
        "UNKNOWN",
        "SUBMITTING",
    ]
    assert not result["activation_approved"] and not result["policy_changes_applied"]
    bounds = model_bounds()
    assert bounds["max_history_for_proof"] == bounds["max_history_for_both"] == 5
    assert bounds["max_history_for_entry"] == 29


def test_default_runtime_refuses_before_accessing_providers():
    from packages.persistence.demo_order_quota import LocalQuotaStopped
    from services.execution.demo_managed_runtime import assemble_managed_runtime

    with pytest.raises(LocalQuotaStopped, match="RUNTIME_REAL_EXECUTION_DISABLED"):
        assemble_managed_runtime(None, None, None, None, None)


def test_synthetic_switch_cannot_approve_the_default_real_sender(monkeypatch):
    from integrations.binance_usdm.demo import transport
    from packages.persistence.demo_order_quota import LocalQuotaStopped
    from services.execution.demo_managed_runtime import assemble_managed_runtime

    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    with pytest.raises(LocalQuotaStopped, match="RUNTIME_SYNTHETIC_CONTEXT_REQUIRED"):
        assemble_managed_runtime(
            None, None, None, transport, None, allow_synthetic_model_execution=True
        )
