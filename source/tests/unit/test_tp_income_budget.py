"""Pagination minimums reject known under-budgeting; never authorize orders."""

import json

import pytest

from integrations.binance_usdm.demo_snapshot import MAX_INCOME_PAGES
from integrations.binance_usdm.demo_tp_budget import TPBudgetPlan, TPBudgetStopped, TPRequestBudget
from services.execution import demo_validation as validation
from services.execution.demo_validation_limits import FLAT_CONFIRMATION_ATTEMPTS
from tests.unit.test_tp_request_budget import (
    COSTS,
    ORDER,
    TIME,
    call,
    setup,  # noqa: F401
)


def profiled(setup, *, entry=3120, cleanup=24960, income=30):  # noqa: F811
    old, clock, calls, response = setup
    costs = dict(COSTS)
    if income is not None:
        costs["GET", "/fapi/v1/income"] = income
    budget = TPRequestBudget(
        old.original, TPBudgetPlan(100000, entry, cleanup, 300), costs, monotonic=lambda: clock[0]
    )
    call(budget, TIME)
    return budget, clock, calls, response


def test_maximum_pages_and_attempts_match_actual_source_limits(setup):  # noqa: F811
    b, _, calls, _ = profiled(setup)
    assert (
        MAX_INCOME_PAGES == 10
        and FLAT_CONFIRMATION_ATTEMPTS == validation.FLAT_CONFIRMATION_ATTEMPTS == 4
    )
    assert b.precheck_income_allowances(712) == 1522
    d = b.diagnostics()
    p = d["income_profile"]
    assert p["required_entry_assumption"] == 3120
    assert p["cleanup_per_attempt_assumption"] == 6240
    assert p["required_cleanup_assumption"] == 24960
    assert p["max_income_pages"] == 10 and p["flat_confirmation_attempts"] == 4
    assert not p["is_proven_upper_bound"]
    assert len(calls) == 1 and d["used_conservative_model"] == 1 and not d["admitted"]
    with pytest.raises(TPBudgetStopped):
        call(b, ORDER, "POST", "type=LIMIT")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "entry,cleanup", [(1500, 3000), (3119, 24960), (3120, 24959), (3120, 6240)]
)
def test_one_page_or_one_attempt_allowance_is_rejected_before_network(setup, entry, cleanup):  # noqa: F811
    b, _, calls, _ = profiled(setup, entry=entry, cleanup=cleanup)
    with pytest.raises(TPBudgetStopped):
        b.precheck_income_allowances(712)
    assert b.diagnostics()["stop_reason"] == "INCOME_PAGINATION_ALLOWANCE_INSUFFICIENT"
    assert not b.diagnostics()["admitted"] and len(calls) == 1


def test_larger_explicit_income_cost_increases_required_allowances(setup):  # noqa: F811
    b, _, calls, _ = profiled(setup, income=40)
    with pytest.raises(TPBudgetStopped):
        b.precheck_income_allowances(712)
    p = b.diagnostics()["income_profile"]
    assert p["required_preparation_assumption"] == 1792
    assert p["required_entry_assumption"] == 3660 and p["required_cleanup_assumption"] == 29280
    assert len(calls) == 1


def test_missing_route_cost_cannot_fall_back_to_cheaper_assumption(setup):  # noqa: F811
    b, _, calls, _ = profiled(setup, income=None)
    with pytest.raises(TPBudgetStopped):
        b.precheck_income_allowances(712)
    assert b.diagnostics()["stop_reason"] == "INCOME_ROUTE_COST_UNMODELED" and len(calls) == 1


@pytest.mark.parametrize("age", [-1, 5.001, 60])
def test_stale_metadata_or_local_minute_cannot_enable_profile(setup, age):  # noqa: F811
    b, clock, calls, _ = profiled(setup)
    clock[0] = age
    with pytest.raises(TPBudgetStopped):
        b.precheck_income_allowances(712)
    assert len(calls) == 1 and not b.diagnostics()["admitted"]


@pytest.mark.parametrize("scope", ["admitted", "cleanup", "stopped"])
def test_scope_cannot_be_reopened_by_rechecking_page_costs(setup, scope):  # noqa: F811
    b, _, calls, _ = profiled(setup)
    if scope == "admitted":
        b.admit_entry()
    elif scope == "cleanup":
        b.begin_cleanup()
    else:
        with pytest.raises(TPBudgetStopped):
            b.precheck_preparation(100000)
    with pytest.raises(TPBudgetStopped):
        b.precheck_income_allowances(712)
    assert len(calls) == 1


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, None, "712"])
def test_invalid_preparation_argument_neither_sends_nor_grants(setup, value):  # noqa: F811
    b, _, calls, _ = profiled(setup)
    with pytest.raises(ValueError):
        b.precheck_income_allowances(value)
    assert len(calls) == 1 and not b.diagnostics()["admitted"]


def test_profile_pass_still_requires_original_preparation_and_entry_headroom(setup):  # noqa: F811
    from integrations.binance_usdm.rest import RESTResponse

    b, _, calls, response = profiled(setup)
    preparation = b.precheck_income_allowances(712)
    b.precheck_preparation(preparation)
    assert not b.diagnostics()["admitted"]
    response[0] = RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "80000"})
    call(b, TIME)
    with pytest.raises(TPBudgetStopped):
        b.admit_entry()
    assert b.diagnostics()["stop_reason"] == "ENTRY_LIFECYCLE_NO_HEADROOM"
    assert len(calls) == 2 and "private" not in json.dumps(b.diagnostics())
