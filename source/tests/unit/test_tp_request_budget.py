"""Budget accounting and concurrency; no venue, keys, DB or sleeps."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_tp_budget import (
    TPBudgetPlan,
    TPBudgetStopped,
    TPRequestBudget,
    published_weight_limit,
)
from integrations.binance_usdm.rest import RESTResponse
from services.execution.demo_validation import safe_failure

TIME = "/fapi/v1/time"
READ = "/fapi/v3/positionRisk"
ORDER = "/fapi/v1/order"
PLAN = TPBudgetPlan(100, 20, 30, 10)
COSTS = {("GET", TIME): 1, ("GET", READ): 10, ("POST", ORDER): 2}
EMPTY = SecretStr("")


@pytest.fixture
def setup():
    clock = [0.0]
    calls = []
    response = [RESTResponse(200, b"{}", {"X-MBX-USED-WEIGHT-1M": "0"})]

    def send(*args):
        calls.append(args)
        return response[0]

    budget = TPRequestBudget(send, PLAN, COSTS, monotonic=lambda: clock[0])
    return budget, clock, calls, response


def call(budget, path=READ, method="GET", query=""):
    return budget(method, path, SecretStr(query), None)


def test_budget_stops_ordinary_before_cleanup_and_never_refunds(setup):
    b, clock, calls, response = setup
    call(b, TIME)
    b.admit_entry()
    for _ in range(5):
        call(b)
    with pytest.raises(TPBudgetStopped):
        call(b)
    assert len(calls) == 6 and b.diagnostics()["used_conservative_model"] == 51
    clock[0] += 60  # A local minute rollover is never new credit.
    with pytest.raises(TPBudgetStopped):
        call(b, TIME)
    b.begin_cleanup()
    call(b, ORDER, "POST", "reduceOnly=true")
    assert len(calls) == 7 and b.diagnostics()["phase"] == "CLEANUP"
    assert b.diagnostics()["resets_or_refunds"] == 0


@pytest.mark.parametrize("header", [None, "-1", "1.0", "secret-cookie", "9" * 13])
def test_bad_header_stops_ordinary_but_not_original_cleanup(setup, header):
    b, _, calls, response = setup
    response[0] = RESTResponse(
        200, b"{}", {} if header is None else {"X-MBX-USED-WEIGHT-1M": header}
    )
    call(b, TIME)
    with pytest.raises(TPBudgetStopped):
        b.admit_entry()
    with pytest.raises(TPBudgetStopped):
        call(b)
    b.begin_cleanup()
    call(b)
    assert len(calls) == 2 and b.diagnostics()["metadata_untrusted"]


@pytest.mark.parametrize("status", [418, 429])
def test_actual_limit_response_returned_unchanged_for_sdk_and_stops_ordinary(setup, status):
    b, _, calls, response = setup
    raw = RESTResponse(status, b"not-json", {"Retry-After": "120", "X-MBX-USED-WEIGHT-1M": "99"})
    response[0] = raw
    assert call(b, TIME) is raw
    with pytest.raises(TPBudgetStopped):
        call(b, TIME)
    assert len(calls) == 1 and b.diagnostics()["metadata_untrusted"]


def test_header_highwater_and_local_estimate_never_decrease_after_reset(setup):
    b, clock, _, response = setup
    response[0] = RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "40"})
    call(b, TIME)
    response[0] = RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "1"})
    clock[0] = 60
    call(b, TIME)
    assert b.diagnostics()["used_conservative_model"] == 41
    with pytest.raises(TPBudgetStopped):
        b.admit_entry()  # 41+20 exceeds ordinary ceiling60.


@pytest.mark.parametrize("age", [-1, 5.001, 60])
def test_stale_or_backwards_metadata_cannot_admit_or_read(setup, age):
    b, clock, calls, _ = setup
    call(b, TIME)
    clock[0] = age
    with pytest.raises(TPBudgetStopped):
        b.admit_entry()
    assert len(calls) == 1


def test_stale_header_can_be_refreshed_before_any_stop(setup):
    b, clock, _, _ = setup
    call(b, TIME)
    clock[0] = 60
    call(b, TIME)
    b.admit_entry()
    assert b.diagnostics()["admitted"]


def test_entry_needs_explicit_single_admission_and_is_forbidden_after_finalization(setup):
    b, _, calls, _ = setup
    call(b, TIME)
    b.admit_entry()
    call(b, ORDER, "POST", "type=LIMIT")
    with pytest.raises(TPBudgetStopped):
        b.admit_entry()
    b.begin_cleanup()
    for query in ("type=LIMIT", "reduceOnly=false", "reduceOnly=true&reduceOnly=false"):
        with pytest.raises(TPBudgetStopped):
            call(b, ORDER, "POST", query)
    call(b, ORDER, "POST", "reduceOnly=true")
    assert len(calls) == 3


def test_no_admission_no_entry_and_no_implicit_unknown_cost(setup):
    b, _, calls, _ = setup
    call(b, TIME)
    with pytest.raises(TPBudgetStopped):
        call(b, ORDER, "POST", "type=LIMIT")
    b.begin_cleanup()
    with pytest.raises(TPBudgetStopped):
        call(b, "/unknown")
    assert len(calls) == 1


def test_failed_send_keeps_charge_and_prevents_normal_retry():
    calls = []

    def fail(*args):
        calls.append(args)
        raise OSError("private-payload-must-not-appear")

    b = TPRequestBudget(fail, PLAN, COSTS)
    with pytest.raises(OSError):
        call(b, TIME)
    with pytest.raises(TPBudgetStopped):
        call(b, TIME)
    assert len(calls) == 1 and b.diagnostics()["used_conservative_model"] == 1
    assert "private" not in str(b.diagnostics())


def test_atomic_accounting_prevents_concurrent_spending_of_reserve(setup):
    b, _, calls, _ = setup
    call(b, TIME)

    def attempt(_):
        try:
            call(b)
            return True
        except TPBudgetStopped:
            return False

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(attempt, range(40)))
    assert sum(results) == 5 and len(calls) == 6
    assert b.diagnostics()["used_conservative_model"] == 51


def test_cleanup_counts_cost_but_does_not_block_original_risk_reduction(setup):
    b, _, calls, response = setup
    call(b, TIME)
    response[0] = RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "101"})
    b.begin_cleanup()
    for _ in range(12):
        call(b)
    assert len(calls) == 13
    d = b.diagnostics()
    assert d["cleanup_exceeded_model_limit"] and d["cleanup_assumed_weight"] == 120
    assert d["added_retries"] == 0 and not d["shared_ip_capacity_reserved"]
    assert safe_failure(TPBudgetStopped("secret")) == "TP_REQUEST_BUDGET_STOPPED"
    assert safe_failure(DemoFailure("secret")) == "DEMO_SESSION_OR_QUERY_FAILED"


@pytest.mark.parametrize(
    "field", ["published_limit", "entry_and_protection", "cleanup_reserve", "headroom"]
)
@pytest.mark.parametrize("value", [True, 0, -1, 1.5])
def test_invalid_integer_plan_rejected(field, value):
    with pytest.raises(ValueError):
        replace(PLAN, **{field: value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1, 6])
def test_invalid_header_freshness_rejected(value):
    with pytest.raises(ValueError):
        replace(PLAN, header_max_age_seconds=value)


@pytest.mark.parametrize("cost", [True, 0, -1, 1.5])
def test_no_invalid_route_costs(cost):
    with pytest.raises(ValueError):
        TPRequestBudget(lambda *args: None, PLAN, {("GET", TIME): cost})


def test_plan_cannot_consume_entire_limit():
    with pytest.raises(ValueError):
        TPBudgetPlan(60, 20, 30, 10)


@pytest.mark.parametrize("limit", [True, 0, -1, "6000", None])
def test_published_limit_needs_exact_positive_integer(limit):
    row = {
        "rateLimitType": "REQUEST_WEIGHT",
        "interval": "MINUTE",
        "intervalNum": 1,
        "limit": limit,
    }
    with pytest.raises(TPBudgetStopped):
        published_weight_limit({"rateLimits": [row]})


def test_published_limit_needs_one_unambiguous_minute_row():
    row = {"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "intervalNum": 1, "limit": 6000}
    assert published_weight_limit({"rateLimits": [row]}) == 6000
    for data in ({}, None, {"rateLimits": []}, {"rateLimits": [row, row]}):
        with pytest.raises(TPBudgetStopped):
            published_weight_limit(data)
