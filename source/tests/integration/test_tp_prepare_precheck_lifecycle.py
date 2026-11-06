"""Preparation-only reduction; real isolated PG/WSS, synthetic orders/costs."""

import json

import pytest

from services.execution import demo as demo_module
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import exercise_opposite, opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_request_budget_lifecycle import candidate, save


def preparation_assumption(dual):
    # Explicit synthetic assumption: resume snapshot + preview pair; three clock
    # GETs; public metadata1 and book/mark/kline40 each. Not a proven venue bound.
    return 3 * (196 if dual == 35 else 191) + 3 + 1 + 3 * 40


@pytest.mark.parametrize("dual", [30, 35])
@pytest.mark.parametrize("early", [False, True])
def test_real_limit_model_refuses_without_price_planning_and_preserves_full_flat_proof(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
    dual,
    early,
):
    scenario, meter, budget, route, sender = candidate(
        opposite_case,
        cap=6000,
        dual=dual,
        preparation_cost=preparation_assumption(dual) if early else None,
    )
    store, c, fake, _ = scenario
    before = store.read("DEMO-test")
    historical = demo_module.historical_snapshot
    proof = []

    def counted(*args, **kwargs):
        proof.append(True)
        return historical(*args, **kwargs)

    monkeypatch.setattr(demo_module, "historical_snapshot", counted)
    try:
        report = exercise_opposite(scenario, monkeypatch)
    finally:
        route.close()
        c.client.send = sender
    final = next(
        json.loads(s)
        for s in capsys.readouterr().out.splitlines()
        if s.startswith("{") and "completion_stage" in json.loads(s)
    )
    assert budget.stopped and not budget.diagnostics()["admitted"]
    assert (
        not posts(fake)
        and report["new_entry_post_count"] == report["operator_exit_post_attempts"] == 0
    )
    assert len(proof) == 5 and final["validation"] == "FINISHED_FLAT"
    assert final["completion_failure"] == "NONE" and final["session_error"]
    assert report["tp_trigger_check"] == "NOT_ACCEPTED"
    after = store.read("DEMO-test")
    assert after.orders == before.orders and after.requests == before.requests
    assert after.daily_base == before.daily_base and after.weekly_base == before.weekly_base
    assert report["history_and_baselines_preserved"] and after.paused
    assert not any(fake.positions.values()) and all(r["status"] != 429 for r in meter.rows)
    market = [
        r
        for r in meter.rows
        if r["path"] in {"/fapi/v1/ticker/bookTicker", "/fapi/v1/premiumIndex", "/fapi/v1/klines"}
    ]
    if early:
        assert not market
        assert final["validation_stage"] == "ENTRY_BUDGET_PRECHECK"
        assert budget.diagnostics()["stop_reason"] == "PREPARATION_LIFECYCLE_NO_HEADROOM"
        assert budget.diagnostics()["preparation_prechecks"] == 1
    else:
        assert len(market) == 3 and final["validation_stage"] == "RESERVE_ENTRY"
        assert budget.diagnostics()["stop_reason"] == "ENTRY_LIFECYCLE_NO_HEADROOM"
    report["full_historical_flat_snapshot_calls"] = len(proof)
    report["synthetic_http_attempts"] = len(meter.rows)
    report["synthetic_model_total_weight"] = sum(r["assumed_weight"] for r in meter.rows)
    save(f"preparation-6000-dual{dual}-early{early}", report, meter, budget)


@pytest.mark.parametrize("mode", ["tp", "no_tp", "partial", "unfilled", "spike"])
def test_passing_forecast_keeps_all_original_preparation_and_execution_checks(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
    mode,
):
    if mode in {"no_tp", "partial", "spike"}:
        opposite_case[3]["trigger"] = False
    if mode in {"partial", "unfilled"}:
        opposite_case[3]["fill"] = False
    if mode == "unfilled":
        opposite_case[3]["trigger"] = False
    # Unlimited counterfactual: verifies preservation, not real6000 readiness.
    scenario, meter, budget, route, sender = candidate(
        opposite_case,
        cap=100000,
        partial=mode == "partial",
        spike=mode == "spike",
        preparation_cost=preparation_assumption(35),
    )
    store, c, fake, _ = scenario
    snapshots = []
    original = demo_module.snapshot

    def counted(*args, **kwargs):
        snapshots.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(demo_module, "snapshot", counted)
    try:
        report = exercise_opposite(scenario, monkeypatch)
    finally:
        route.close()
        c.client.send = sender
    assert budget.diagnostics()["preparation_prechecks"] == 1
    assert budget.diagnostics()["admitted"] and report["new_entry_post_count"] == 1
    assert len(snapshots) >= 9  # Original startup, reserve, submit, protection/reconciliation.
    assert {r["path"] for r in meter.rows} >= {
        "/fapi/v1/ticker/bookTicker",
        "/fapi/v1/premiumIndex",
        "/fapi/v1/klines",
    }
    assert report["history_and_baselines_preserved"] and store.read("DEMO-test").paused
    final = next(
        json.loads(s)
        for s in capsys.readouterr().out.splitlines()
        if s.startswith("{") and "completion_stage" in json.loads(s)
    )
    if mode == "spike":
        assert any(fake.positions.values()) and report["risk_state"] == "EMERGENCY"
        assert (
            report["flat_confirmation"] == "UNKNOWN" and report["operator_exit_post_attempts"] == 0
        )
        first = next(i for i, r in enumerate(meter.rows) if r["status"] == 429)
        assert len(meter.rows) == first + 1
    else:
        assert not any(fake.positions.values()) and final["validation"] == "FINISHED_FLAT"
    if mode == "tp":
        assert report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
    save(f"preparation-counterfactual100000-{mode}", report, meter, budget)


def test_optimistic_preparation_forecast_does_not_bypass_later_reservation_gate(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
):
    # Counterfactual6500 separates the early and final predicates. Deliberately
    # under-estimate preparation; actual requests must still cause final refusal.
    scenario, meter, budget, route, sender = candidate(
        opposite_case,
        cap=6500,
        preparation_cost=1,
    )
    store, c, fake, _ = scenario
    try:
        report = exercise_opposite(scenario, monkeypatch)
    finally:
        route.close()
        c.client.send = sender
    final = next(
        json.loads(s)
        for s in capsys.readouterr().out.splitlines()
        if s.startswith("{") and "completion_stage" in json.loads(s)
    )
    assert budget.diagnostics()["preparation_prechecks"] == 1
    assert budget.diagnostics()["stop_reason"] == "ENTRY_LIFECYCLE_NO_HEADROOM"
    assert final["validation_stage"] == "RESERVE_ENTRY" and final["validation"] == "FINISHED_FLAT"
    assert not posts(fake) and not budget.diagnostics()["admitted"]
    assert not any(fake.positions.values()) and store.read("DEMO-test").paused
    assert report["tp_trigger_check"] == "NOT_ACCEPTED"
    save("preparation-counterfactual6500-underestimate-no-bypass", report, meter, budget)


@pytest.mark.parametrize("change", ["account_changed", "quote_expired"])
def test_passing_precheck_cannot_skip_original_account_or_quote_verification(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
    change,
):
    from datetime import timedelta
    from decimal import Decimal

    scenario, meter, budget, route, sender = candidate(
        opposite_case,
        cap=100000,
        preparation_cost=preparation_assumption(35),
    )
    store, c, fake, _ = scenario
    original = meter.sender
    changed = []

    def alter(method, path, query, key):
        response = original(method, path, query, key)
        if path == "/fapi/v1/premiumIndex" and not changed:
            changed.append(True)
            if change == "account_changed":
                fake.wallet -= Decimal("1")
            else:
                fake.at += timedelta(seconds=6)
        return response

    meter.sender = alter
    try:
        report = exercise_opposite(scenario, monkeypatch)
    finally:
        route.close()
        c.client.send = sender
    final = next(
        json.loads(s)
        for s in capsys.readouterr().out.splitlines()
        if s.startswith("{") and "completion_stage" in json.loads(s)
    )
    assert changed and budget.diagnostics()["preparation_prechecks"] == 1
    assert not posts(fake) and report["new_entry_post_count"] == 0
    assert final["session_error"] and final["validation_stage"] == "PREPARE_ENTRY"
    assert not budget.diagnostics()["admitted"] and not any(fake.positions.values())
    assert store.read("DEMO-test").paused and report["tp_trigger_check"] == "NOT_ACCEPTED"
    save(f"preparation-original-guard-{change}", report, meter, budget)
