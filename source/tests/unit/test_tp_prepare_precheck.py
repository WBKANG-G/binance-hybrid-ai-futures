"""Early refusal never grants entry, debits forecast credit, or weakens final checks."""

import pytest

from integrations.binance_usdm.demo_tp_budget import TPBudgetStopped
from services.execution.demo_tp_budget_candidate import PrecheckedBudgetedTPTriggerCoordinator
from tests.unit.test_tp_request_budget import (
    ORDER,
    TIME,
    call,
    setup,  # noqa: F401
)


def test_passing_precheck_has_no_wire_or_credit_or_entry_permission(setup):  # noqa: F811
    b, _, calls, _ = setup
    call(b, TIME)
    before = b.diagnostics()
    b.precheck_preparation(20)
    after = b.diagnostics()
    assert len(calls) == 1 and not after["admitted"]
    assert before["used_conservative_model"] == after["used_conservative_model"] == 1
    assert after["preparation_prechecks"] == 1 and after["last_preparation_assumption"] == 20
    with pytest.raises(TPBudgetStopped):
        call(b, ORDER, "POST", "type=LIMIT")
    assert len(calls) == 1


def test_precheck_stops_when_preparation_plus_lifecycle_cannot_fit(setup):  # noqa: F811
    b, _, calls, _ = setup
    call(b, TIME)
    with pytest.raises(TPBudgetStopped):
        b.precheck_preparation(40)  # 1 + 40 + entry20 > ordinary ceiling60.
    d = b.diagnostics()
    assert d["stop_reason"] == "PREPARATION_LIFECYCLE_NO_HEADROOM"
    assert len(calls) == 1 and not d["admitted"] and d["used_conservative_model"] == 1


def test_exact_boundary_pass_is_rechecked_by_real_admission_after_header_jump(setup):  # noqa: F811
    from integrations.binance_usdm.rest import RESTResponse

    b, _, calls, response = setup
    call(b, TIME)
    b.precheck_preparation(39)  # Exactly fits the planning predicate.
    response[0] = RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "50"})
    call(b, TIME)
    with pytest.raises(TPBudgetStopped):
        b.admit_entry()
    assert len(calls) == 2 and b.diagnostics()["stop_reason"] == "ENTRY_LIFECYCLE_NO_HEADROOM"


def test_underestimated_preparation_still_uses_original_per_request_and_admission_gates(setup):  # noqa: F811
    b, _, calls, _ = setup
    call(b, TIME)
    b.precheck_preparation(1)
    for _ in range(4):
        call(b)
    assert b.diagnostics()["used_conservative_model"] == 41
    with pytest.raises(TPBudgetStopped):
        b.admit_entry()
    assert len(calls) == 5 and not b.diagnostics()["admitted"]


@pytest.mark.parametrize("age", [-1, 5.001, 60])
def test_stale_or_backward_header_refuses_before_planning(setup, age):  # noqa: F811
    b, clock, calls, _ = setup
    call(b, TIME)
    clock[0] = age
    with pytest.raises(TPBudgetStopped):
        b.precheck_preparation(1)
    assert len(calls) == 1
    assert b.diagnostics()["stop_reason"] == "PREPARATION_WEIGHT_METADATA_UNAVAILABLE"


@pytest.mark.parametrize("phase", ["cleanup", "admitted", "stopped"])
def test_preparation_cannot_reopen_closed_or_already_admitted_scope(setup, phase):  # noqa: F811
    b, _, calls, _ = setup
    call(b, TIME)
    if phase == "cleanup":
        b.begin_cleanup()
    elif phase == "admitted":
        b.admit_entry()
    else:
        with pytest.raises(TPBudgetStopped):
            b.precheck_preparation(100)
    before = len(calls)
    with pytest.raises(TPBudgetStopped):
        b.precheck_preparation(1)
    assert len(calls) == before


@pytest.mark.parametrize("cost", [True, False, 0, -1, 1.1, float("nan"), "10", None])
def test_invalid_preparation_cost_neither_grants_nor_sends(setup, cost):  # noqa: F811
    b, _, calls, _ = setup
    with pytest.raises(ValueError):
        b.precheck_preparation(cost)
    assert not calls and not b.diagnostics()["admitted"]
    assert b.diagnostics()["preparation_prechecks"] == 0
    with pytest.raises(ValueError):
        PrecheckedBudgetedTPTriggerCoordinator(
            None, None, None, None, preparation_cost_assumption=cost
        )


def test_repeated_concurrent_prechecks_cannot_reserve_or_mint_credit(setup):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor

    b, _, calls, _ = setup
    call(b, TIME)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(b.precheck_preparation, [10] * 40))
    assert len(calls) == 1 and b.diagnostics()["preparation_prechecks"] == 40
    assert b.diagnostics()["used_conservative_model"] == 1 and not b.diagnostics()["admitted"]
    b.admit_entry()
    call(b, ORDER, "POST", "type=LIMIT")
    assert len(calls) == 2
