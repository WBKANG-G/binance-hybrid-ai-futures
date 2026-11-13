"""Offline coverage proposal only. No archive authority, runtime caller or state writes."""

import re
from dataclasses import dataclass
from typing import Any, Literal

from integrations.binance_usdm.demo_tp_admission import TPModelProfile
from services.execution.demo_runtime_capacity import capacity_plan


@dataclass(frozen=True)
class HistoricalRequest:
    role: Literal["ENTRY", "STOP_LOSS", "EXIT"]
    stored_state: Literal["UNKNOWN", "SUBMITTING", "CANCEL_REQUESTED", "NOT_FOUND_UNRESOLVED"]
    locally_closed: bool
    observed_status: Literal["FILLED", "NOT_FOUND_UNRESOLVED", "NONE"]
    cancelled_zero_fill_correction_matches: bool = False

    def __post_init__(self) -> None:
        if (
            self.role not in {"ENTRY", "STOP_LOSS", "EXIT"}
            or self.stored_state
            not in {"UNKNOWN", "SUBMITTING", "CANCEL_REQUESTED", "NOT_FOUND_UNRESOLVED"}
            or self.observed_status not in {"FILLED", "NOT_FOUND_UNRESOLVED", "NONE"}
        ):
            raise ValueError("HISTORY_REVIEW_REQUEST_SCOPE_INVALID")
        if (
            type(self.locally_closed) is not bool
            or type(self.cancelled_zero_fill_correction_matches) is not bool
        ):
            raise ValueError("HISTORY_REVIEW_FACT_TYPE_INVALID")


def classify_request(item: HistoricalRequest) -> dict[str, Any]:
    if item.locally_closed and item.role == "ENTRY" and item.observed_status == "FILLED":
        category = "CLOSED_ENTRY_OBSERVATION_PENDING_RECONCILIATION"
    elif item.locally_closed and item.cancelled_zero_fill_correction_matches:
        category = "CLOSED_CORRECTED_HISTORY_REQUEST_OUTCOME_UNRESOLVED"
    else:
        category = "UNRESOLVED_REQUEST_REQUIRES_EVIDENCE"
    return dict(
        role=item.role,
        stored_state=item.stored_state,
        review_category=category,
        proposed_stored_state=item.stored_state,
        request_state_write_authorized=False,
        remote_resolution_verified=False,
        archive_authorized=False,
        acknowledgement_is_remote_proof=False,
        execution_approved=False,
    )


def _refs(values: tuple[str, ...]) -> set[str]:
    if (
        not isinstance(values, tuple)
        or len(values) > 128
        or any(not isinstance(v, str) or re.fullmatch("[0-9a-f]{64}", v) is None for v in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError("HISTORY_REVIEW_REFERENCE_SET_INVALID")
    return set(values)


def model_bounds() -> dict[str, Any]:
    plans = [capacity_plan(TPModelProfile(i)) for i in range(129)]
    both = [
        p["historical_orders"]
        for p in plans
        if p["proof_fits_empty_window"] and p["entry_fits_empty_window"]
    ]
    return dict(
        model_scope="UNCHANGED_SYNTHETIC_SINGLE_WINDOW_ASSUMPTIONS_ONLY",
        max_history_for_proof=max(
            p["historical_orders"] for p in plans if p["proof_fits_empty_window"]
        ),
        max_history_for_entry=max(
            p["historical_orders"] for p in plans if p["entry_fits_empty_window"]
        ),
        max_history_for_both=max(both),
        effective_ceiling=5700,
        venue_capacity_verified=False,
        execution_approved=False,
    )


def review_coverage(
    retained: tuple[str, ...], live_queries: tuple[str, ...], archive_proposals: tuple[str, ...]
) -> dict[str, Any]:
    all_refs, live, archive = _refs(retained), _refs(live_queries), _refs(archive_proposals)
    if live & archive or live | archive != all_refs:
        raise ValueError("HISTORY_REVIEW_COVERAGE_NOT_EXACT")
    full = capacity_plan(TPModelProfile(len(all_refs)))
    hypothetical = capacity_plan(TPModelProfile(len(live)))
    bounds = model_bounds()
    blockers = []
    if not full["proof_fits_empty_window"]:
        blockers.append("CURRENT_FULL_HISTORY_PROOF_CANNOT_FIT")
    if not full["entry_fits_empty_window"]:
        blockers.append("CURRENT_FULL_HISTORY_ENTRY_CANNOT_FIT")
    if archive:
        blockers.append("ARCHIVE_EVIDENCE_AUTHORITY_NOT_IMPLEMENTED")
    blockers.append("OPERATIONAL_RUNTIME_IMPLEMENTATION_AND_ADMISSION_STILL_REQUIRED")
    return dict(
        history_coverage_review="OFFLINE_PROPOSAL_KEEP_PAUSED",
        retained_history_count=len(all_refs),
        proposed_live_query_count=len(live),
        proposed_archive_count=len(archive),
        all_retained_references_covered=True,
        history_rows_deleted=0,
        proposed_partition_applied=False,
        current_runtime_uses_all_retained_orders=True,
        current_runtime_plan=full,
        hypothetical_plan_not_used_for_admission=hypothetical,
        unchanged_model_bounds=bounds,
        minimum_history_certificates_for_hypothetical_fit=max(
            0, len(all_refs) - bounds["max_history_for_both"]
        ),
        accepted_archive_certificates=0,
        archive_evidence_verifier_implemented=False,
        archive_adoption_approved=False,
        current_execution_blockers=blockers,
        raw_order_and_request_records_preserved=True,
        database_changed=False,
        filesystem_changed=False,
        http_attempts=0,
        order_writes=0,
        orders_enabled=False,
        live_trading_enabled=False,
        execution_approved=False,
    )
