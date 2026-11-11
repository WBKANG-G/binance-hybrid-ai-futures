"""One-window feasibility of unchanged synthetic proof assumptions, never approval."""

from typing import Any

from integrations.binance_usdm.demo_tp_admission import TPModelProfile
from integrations.binance_usdm.demo_tp_window_budget import WindowTPBudget
from packages.persistence.demo_order_quota import quota_require
from packages.persistence.demo_shared_weight import CEILING, RESERVES


def proof_batch_weight(profile: TPModelProfile) -> int:
    # The original complete historical proof. Never split/omit its snapshots.
    return (
        (6 + profile.historical_orders) * profile.snapshot
        + 80 * (profile.historical_orders + 1)
        + 38
    )


def capacity_plan(profile: TPModelProfile) -> dict[str, Any]:
    ceiling = min(6000 - profile.headroom, CEILING)
    proof = proof_batch_weight(profile)
    proof_reserve = max(WindowTPBudget.WATCH, RESERVES["PROOF"])
    entry_reserve = max(WindowTPBudget.RESCUE, RESERVES["ORDINARY"])
    return dict(
        policy="SYNTHETIC_SINGLE_WINDOW_FEASIBILITY_V1",
        historical_orders=profile.historical_orders,
        published_limit_assumption=6000,
        effective_ceiling=ceiling,
        proof_batch_weight=proof,
        proof_reserve=proof_reserve,
        proof_required=proof + proof_reserve,
        proof_slack=ceiling - proof - proof_reserve,
        proof_fits_empty_window=proof + proof_reserve <= ceiling,
        entry_required=profile.entry + entry_reserve,
        entry_fits_empty_window=profile.entry + entry_reserve <= ceiling,
        current_pressure_checked=False,
        request_cost_upper_bounds_proven=False,
        execution_approved=False,
    )


def require_runtime_capacity(profile: TPModelProfile) -> None:
    plan = capacity_plan(profile)
    quota_require(plan["proof_fits_empty_window"], "RUNTIME_PROOF_BATCH_CANNOT_FIT")
    quota_require(plan["entry_fits_empty_window"], "RUNTIME_ENTRY_BATCH_CANNOT_FIT")
