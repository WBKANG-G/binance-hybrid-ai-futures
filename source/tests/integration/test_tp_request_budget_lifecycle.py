"""Candidate only: real isolated PG/local WSS; fake weights/orders/clock."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from integrations.binance_usdm.demo import PUBLIC, SIGNED_GET
from integrations.binance_usdm.demo_read_routing import TPReadRouting
from integrations.binance_usdm.demo_tp_budget import TPBudgetPlan, TPRequestBudget
from services.execution import demo_tp_trigger_check as tp_module
from services.execution.demo_tp_budget_candidate import (
    BudgetedTPTriggerCoordinator,
    CoalescedBudgetedTPTriggerCoordinator,
)
from tests.fixtures.tp_weight_model import GET_COSTS, Meter, seed_history
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import exercise_opposite, opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case, exercise  # noqa: F401


def candidate(
    scenario, *, cap=6000, spike=False, partial=False, dual=35, drop_route=None, coalesced=False
):
    store, old, fake, options = scenario
    seed_history(store, old, fake)
    sender = old.client.send
    meter = Meter(
        sender,
        fake,
        seed=0,
        unknown_cost=40,
        cap=cap,
        spike=spike,
        partial=partial,
        options=options,
        dual_cost=dual,
    )

    class Pool:
        def __call__(self, *args):
            return meter(*args)

        def close(self):
            pass

        def diagnostics(self):
            return {}

    route = TPReadRouting(meter, pool=Pool())
    costs = {("GET", p): GET_COSTS.get(p, 40) for p in PUBLIC | SIGNED_GET}
    costs["GET", "/fapi/v1/positionSide/dual"] = dual
    for method, path in (
        ("POST", "/fapi/v1/order"),
        ("DELETE", "/fapi/v1/order"),
        ("POST", "/fapi/v1/algoOrder"),
        ("DELETE", "/fapi/v1/algoOrder"),
        ("POST", "/fapi/v1/listenKey"),
        ("PUT", "/fapi/v1/listenKey"),
        ("DELETE", "/fapi/v1/listenKey"),
    ):
        costs[method, path] = 40
    if drop_route is not None:
        costs.pop(drop_route)
    budget = TPRequestBudget(route, TPBudgetPlan(cap, 1500, 3000, 300), costs)
    old.client.send = budget
    coordinator_type = (
        CoalescedBudgetedTPTriggerCoordinator if coalesced else BudgetedTPTriggerCoordinator
    )
    coordinator = coordinator_type(store, old.lease, old.client, budget)
    coordinator.client.sync_clock()
    coordinator.refresh_metadata()
    coordinator.reconcile()
    return (store, coordinator, fake, options), meter, budget, route, sender


def exercise_long(scenario, monkeypatch):
    original = tp_module.run

    async def explicit(coordinator, operation):
        # Real-time stress bound only; enough hints to force the budget stop.
        tp_module.OBSERVATION_SECONDS = 2
        return await original(coordinator, operation, entry_style="OPPOSITE_QUOTE")

    monkeypatch.setattr(tp_module, "run", explicit)
    return asyncio.run(exercise(scenario, monkeypatch))


def save(label, report, meter, budget):
    folder = os.getenv("TP_BUDGET_REPORT_DIR")
    if folder:
        p = Path(folder)
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{label}.json").write_text(
            json.dumps(
                {
                    "actual_venue_used": False,
                    "label": label,
                    "report": report,
                    "budget": budget.diagnostics(),
                    "synthetic_requests": meter.rows,
                },
                indent=2,
            )
            + "\n"
        )


@pytest.mark.parametrize("dual", [30, 35])
def test_observed_history_model_6000_refuses_entry_before_reservation(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
    dual,
):
    scenario, meter, budget, route, sender = candidate(opposite_case, dual=dual)
    store, c, fake, _ = scenario
    before = store.read("DEMO-test")
    try:
        report = exercise_opposite(scenario, monkeypatch)
    finally:
        route.close()
        c.client.send = sender
    after = store.read("DEMO-test")
    assert budget.stopped and not budget.diagnostics()["admitted"]
    assert budget.diagnostics()["stop_reason"] == "ENTRY_LIFECYCLE_NO_HEADROOM"
    assert report["new_entry_post_count"] == report["operator_exit_post_attempts"] == 0
    assert not posts(fake) and after.orders == before.orders and after.requests == before.requests
    assert report["tp_trigger_check"] == "NOT_ACCEPTED" and after.paused
    assert not any(fake.positions.values()) and all(r["status"] != 429 for r in meter.rows)
    lines = [json.loads(s) for s in capsys.readouterr().out.splitlines() if s.startswith("{")]
    final = next(r for r in lines if "completion_stage" in r)
    assert final["validation"] == "FINISHED_FLAT" and final["completion_failure"] == "NONE"
    assert final["session_failure"] == "TP_REQUEST_BUDGET_STOPPED"
    assert report["history_and_baselines_preserved"]
    save(f"actual-profile-model-6000-dual{dual}-no-entry", report, meter, budget)


@pytest.mark.parametrize("mode", ["tp", "no_tp", "partial", "unfilled", "sl"])
def test_synthetic_larger_limit_stops_routine_and_preserves_original_cleanup(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
    mode,
):
    options = opposite_case[3]
    if mode in {"no_tp", "partial"}:
        options["trigger"] = False
    if mode in {"partial", "unfilled"}:
        options["fill"] = False
    if mode == "unfilled":
        options["trigger"] = False
    if mode == "sl":
        options["trigger"] = "SL"
    # Counterfactual capacity for exercising active control, never the real limit.
    scenario, meter, budget, route, sender = candidate(
        opposite_case, cap=12000, partial=mode == "partial"
    )
    store, c, fake, _ = scenario
    before = store.read("DEMO-test")
    try:
        report = exercise_long(scenario, monkeypatch)
    finally:
        route.close()
        c.client.send = sender
    after = store.read("DEMO-test")
    assert report["new_entry_post_count"] == 1
    assert report["operator_exit_post_attempts"] <= 1
    assert not any(fake.positions.values()) and after.paused
    assert all(r["status"] != 429 for r in meter.rows)
    assert all(after.orders[k] == v for k, v in before.orders.items())
    assert all(after.requests[k] == v for k, v in before.requests.items())
    assert after.daily_base == before.daily_base and after.weekly_base == before.weekly_base
    assert report["history_and_baselines_preserved"]
    final = next(
        json.loads(s)
        for s in capsys.readouterr().out.splitlines()
        if s.startswith("{") and "completion_stage" in json.loads(s)
    )
    assert final["validation"] == "FINISHED_FLAT" and final["completion_failure"] == "NONE"
    assert budget.diagnostics()["phase"] == "CLEANUP"
    assert not budget.diagnostics()["cleanup_exceeded_model_limit"]
    if mode in {"no_tp", "partial"}:
        assert budget.stopped
        assert budget.diagnostics()["stop_reason"] == "ORDINARY_HEADROOM_EXHAUSTED"
        assert report["operator_exit_post_attempts"] == 1
    if budget.stopped:
        assert report["tp_trigger_check"] == "NOT_ACCEPTED"
    save(f"counterfactual-limit12000-{mode}", report, meter, budget)


def test_shared_ip_spike_retains_sdk_cooldown_and_reports_unknown(
    opposite_case,  # noqa: F811
    monkeypatch,
):
    opposite_case[3]["trigger"] = False
    scenario, meter, budget, route, sender = candidate(opposite_case, cap=12000, spike=True)
    store, c, fake, _ = scenario
    try:
        report = exercise_opposite(scenario, monkeypatch)
    finally:
        route.close()
        c.client.send = sender
    assert meter.spiked and any(r["status"] == 429 for r in meter.rows)
    assert any(fake.positions.values()) and store.read("DEMO-test").paused
    assert report["risk_state"] == "EMERGENCY" and report["flat_confirmation"] == "UNKNOWN"
    assert report["operator_exit_post_attempts"] == 0
    assert c.client.cooldown_until > c.client.clock_ms()
    first = next(i for i, r in enumerate(meter.rows) if r["status"] == 429)
    assert len(meter.rows) == first + 1
    save("shared-ip-spike-no-cooldown-bypass", report, meter, budget)


def test_suppressed_last_listen_key_close_budget_error_cannot_be_tp_acceptance(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
):
    # Deliberately omit one cleanup route in a generously-sized synthetic model.
    # This forces failure only in the private supervisor's suppressed last DELETE.
    scenario, meter, budget, route, sender = candidate(
        opposite_case, cap=100000, drop_route=("DELETE", "/fapi/v1/listenKey")
    )
    store, c, fake, _ = scenario
    try:
        report = exercise_opposite(scenario, monkeypatch)
    finally:
        route.close()
        c.client.send = sender
    assert budget.stopped and budget.diagnostics()["stop_reason"] == "ROUTE_COST_UNKNOWN"
    assert c.client.tp_fill_before_sl_delete and c.client.sl_cancel_query_verified
    assert report["tp_trigger_check"] == "NOT_ACCEPTED"
    assert not any(fake.positions.values()) and store.read("DEMO-test").paused
    final = next(
        json.loads(s)
        for s in capsys.readouterr().out.splitlines()
        if s.startswith("{") and "completion_stage" in json.loads(s)
    )
    assert final["validation"] == "FINISHED_FLAT"
    assert final["session_error"] and not final["filled_protection_and_flat_verified"]
    save("counterfactual-last-close-error-not-accepted", report, meter, budget)
