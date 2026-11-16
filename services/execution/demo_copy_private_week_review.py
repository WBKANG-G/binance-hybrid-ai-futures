"""2026-11-16 synthetic copy/private-key review across day AND week boundaries.

Observation-only acknowledgement never adopts a day/week baseline or account identity.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.engine import Engine

from packages.persistence.demo_read_audit import require
from services.execution.demo_copy_baseline_evidence import review_copy_baseline_requirements
from services.execution.demo_copy_evidence_rehearsal import rehearse_synthetic_copy_evidence
from services.execution.demo_copy_volatile_credentials import PrivateReadCredentialLease
from services.execution.demo_runtime_storage import RuntimeClocks

if TYPE_CHECKING:
    from services.execution.demo_copy_private_preparation import SyntheticPrivatePreparation

REVIEW_DAY = "2026-11-16"
REVIEW_WEEK = "2026-11-16"


def rehearse_private_week_copy(
    read_engine: Engine,
    write_engine: Engine,
    account: str,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    expected_snapshot_sha256: str,
    archive_directory: Path,
    synthetic_body: bytes,
    lease: PrivateReadCredentialLease | SyntheticPrivatePreparation,
    *,
    copy_only: bool = False,
    synthetic_only: bool = False,
    acknowledge_week_boundary_observation: bool = False,
    clocks: RuntimeClocks | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    require(
        copy_only is True
        and synthetic_only is True
        and acknowledge_week_boundary_observation is True,
        "PRIVATE_WEEK_EXPLICIT_SYNTHETIC_OBSERVATION_REQUIRED",
    )
    if type(lease) is not PrivateReadCredentialLease:
        from services.execution.demo_copy_private_preparation import SyntheticPrivatePreparation

        require(type(lease) is SyntheticPrivatePreparation, "PRIVATE_WEEK_LEASE_REQUIRED")
        lease.verify()

    clock = clocks or RuntimeClocks()
    try:
        before = review_copy_baseline_requirements(
            read_engine,
            account,
            directory,
            scope,
            journal,
            installation,
            expected_snapshot_sha256,
            copy_only=True,
            clocks=clock,
        )
        require(
            before["current_day_kst"] == REVIEW_DAY and before["current_week_kst"] == REVIEW_WEEK,
            "PRIVATE_WEEK_CURRENT_REVIEW_PERIOD_MISMATCH",
        )
        require(
            before["transition_kind"] == "DAY_AND_WEEK_SEPARATE_REVIEW"
            and before["stored_day_kst"] == "2026-11-13"
            and before["stored_week_kst"] == "2026-11-09",
            "PRIVATE_WEEK_SOURCE_PERIOD_MISMATCH",
        )
        result = rehearse_synthetic_copy_evidence(
            read_engine,
            write_engine,
            account,
            directory,
            scope,
            journal,
            installation,
            expected_snapshot_sha256,
            archive_directory,
            synthetic_body,
            copy_only=True,
            synthetic_only=True,
            clocks=clock,
            private_lease=lease,
            progress=progress,
        )
        after = review_copy_baseline_requirements(
            read_engine,
            account,
            directory,
            scope,
            journal,
            installation,
            result["after_snapshot_sha256"],
            copy_only=True,
            clocks=clock,
        )
        require(
            (
                after["stored_day_kst"],
                after["stored_week_kst"],
                after["current_day_kst"],
                after["current_week_kst"],
            )
            == ("2026-11-13", "2026-11-09", REVIEW_DAY, REVIEW_WEEK),
            "PRIVATE_WEEK_PERIOD_CHANGED",
        )
        return dict(
            private_week_copy_review="SYNTHETIC_PRIVATE_COPY_VERIFIED_PERIODS_UNCHANGED",
            review_day_kst=REVIEW_DAY,
            review_week_kst=REVIEW_WEEK,
            stored_day_kst=after["stored_day_kst"],
            stored_week_kst=after["stored_week_kst"],
            day_and_week_evidence_still_required=True,
            week_boundary_acknowledged_for_observation_only=True,
            pipeline=result,
            operator_attestation_received=False,
            baseline_source_accepted=False,
            baseline_adoption_approved=False,
            daily_baseline_changed=False,
            weekly_baseline_changed=False,
            remote_account_identity_verified=False,
            installation_ready=False,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
        )
    finally:
        lease.close()
