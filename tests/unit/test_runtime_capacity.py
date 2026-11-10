"""Boundary feasibility is necessary, never sufficient for execution admission."""

from types import SimpleNamespace

import pytest

from integrations.binance_usdm.demo_tp_admission import TPModelProfile
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_runtime_capacity import (
    capacity_plan,
    proof_batch_weight,
    require_runtime_capacity,
)
from services.execution.demo_tp_budget_integration import BudgetedTPTriggerCoordinator


@pytest.mark.parametrize("history", range(129))
def test_capacity_uses_exact_unchanged_original_proof(history):
    profile = TPModelProfile(history)
    coordinator = object.__new__(BudgetedTPTriggerCoordinator)
    coordinator.admission_budget = SimpleNamespace(profile=profile)
    assert proof_batch_weight(profile) == coordinator.proof_batch
    plan = capacity_plan(profile)
    assert plan["proof_fits_empty_window"] is (history <= 5)
    assert not plan["execution_approved"] and not plan["request_cost_upper_bounds_proven"]
    assert plan["current_pressure_checked"] is False
    if history <= 5:
        require_runtime_capacity(profile)
    else:
        with pytest.raises(LocalQuotaStopped, match="PROOF_BATCH_CANNOT_FIT"):
            require_runtime_capacity(profile)


def test_five_history_fits_and_six_does_not_even_without_other_pressure():
    five = capacity_plan(TPModelProfile(5))
    six = capacity_plan(TPModelProfile(6))
    assert five["proof_required"] == 5442 and five["proof_slack"] == 258
    assert six["proof_required"] == 6030 and six["proof_slack"] == -330
    assert six["entry_fits_empty_window"] is True


def test_larger_headroom_is_never_silently_reduced_to_manufacture_a_fit():
    p = TPModelProfile(5, headroom=1000)
    plan = capacity_plan(p)
    assert plan["effective_ceiling"] == 5000 and not plan["proof_fits_empty_window"]
    with pytest.raises(LocalQuotaStopped, match="PROOF_BATCH_CANNOT_FIT"):
        require_runtime_capacity(p)


def test_smaller_headroom_never_raises_shared_ceiling():
    plan = capacity_plan(TPModelProfile(6, headroom=1))
    assert plan["effective_ceiling"] == 5700 and not plan["proof_fits_empty_window"]


def test_waiting_two_minutes_cannot_make_an_oversized_batch_fit():
    from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred
    from tests.unit.test_tp_window_budget import setup

    budget, now, calls, _ = setup()
    batch = proof_batch_weight(TPModelProfile(6))
    for elapsed in [0, 60.001, 120.002]:
        now[0] = elapsed
        with pytest.raises(ProofDeferred):
            budget.require_proof(batch)
    assert len(calls) == 1 and not budget.entry_sent
