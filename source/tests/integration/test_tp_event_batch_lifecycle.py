"""Opt-in batching comparison; real PG/local WS and synthetic venue only."""

import json

import pytest

from services.execution import demo as demo_module
from services.risk.paper import RiskRejected
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_request_budget_lifecycle import candidate, exercise_long, save


@pytest.mark.parametrize("coalesced", [False, True])
@pytest.mark.parametrize("mode", ["no_tp", "tp", "partial", "unfilled", "spike", "real_limit"])
def test_batch_preserves_proof_and_limits_with_measured_synthetic_read_count(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
    coalesced,
    mode,
):
    if mode in {"no_tp", "partial", "spike"}:
        opposite_case[3]["trigger"] = False
    if mode in {"partial", "unfilled"}:
        opposite_case[3]["fill"] = False
    if mode == "unfilled":
        opposite_case[3]["trigger"] = False
    # 100000 is an unlimited counterfactual to compare complete traces. 6000
    # explicitly tests actual-profile admission; no real quota is changed.
    scenario, meter, budget, route, sender = candidate(
        opposite_case,
        cap=6000 if mode == "real_limit" else 100000,
        partial=mode == "partial",
        spike=mode == "spike",
        coalesced=coalesced,
    )
    store, c, fake, _ = scenario
    before = store.read("DEMO-test")
    delayed = []
    proof_calls = []
    original_proof = demo_module.historical_snapshot

    def counted_proof(*args, **kwargs):
        proof_calls.append(True)
        return original_proof(*args, **kwargs)

    monkeypatch.setattr(demo_module, "historical_snapshot", counted_proof)
    if coalesced:
        original = c.prepare_event_batch

        def observe():
            delay = original()
            if delay:
                state = store.read("DEMO-test")
                assert not state.reconciled and not state.stream_ready
                with pytest.raises(RiskRejected):
                    c._fresh(state)
                delayed.append(delay)
            return delay

        monkeypatch.setattr(c, "prepare_event_batch", observe)
    try:
        report = exercise_long(scenario, monkeypatch)
    finally:
        route.close()
        c.client.send = sender
    after = store.read("DEMO-test")
    assert all(after.orders[k] == v for k, v in before.orders.items())
    assert all(after.requests[k] == v for k, v in before.requests.items())
    assert after.daily_base == before.daily_base and after.weekly_base == before.weekly_base
    assert report["history_and_baselines_preserved"] and after.paused
    final = next(
        json.loads(s)
        for s in capsys.readouterr().out.splitlines()
        if s.startswith("{") and "completion_stage" in json.loads(s)
    )
    if mode == "spike":
        assert meter.spiked and any(fake.positions.values())
        assert report["flat_confirmation"] == "UNKNOWN" and report["risk_state"] == "EMERGENCY"
        assert report["operator_exit_post_attempts"] == 0
        first = next(i for i, r in enumerate(meter.rows) if r["status"] == 429)
        assert len(meter.rows) == first + 1
    else:
        assert final["validation"] == "FINISHED_FLAT" and final["completion_failure"] == "NONE"
        assert not any(fake.positions.values())
        assert all(r["status"] != 429 for r in meter.rows)
        assert len(proof_calls) == 5  # Full original flat evidence, unchanged.
    if mode == "real_limit":
        assert report["new_entry_post_count"] == 0 and budget.stopped and not delayed
    else:
        assert report["new_entry_post_count"] == 1 and report["operator_exit_post_attempts"] <= 1
    if mode == "tp":
        assert report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
    if coalesced and mode == "no_tp":
        assert delayed and report["operator_exit_post_attempts"] == 1
    if mode in {"partial", "unfilled"}:
        assert not delayed  # Never slow protection installation or entry expiry.
    report["full_historical_flat_snapshot_calls"] = len(proof_calls)
    report["batch_count_synthetic"] = len(delayed)
    report["batch_max_delay_seconds"] = max(delayed, default=0)
    report["comparison_coalesced"] = coalesced
    report["synthetic_http_attempts"] = len(meter.rows)
    report["synthetic_model_total_weight"] = sum(r["assumed_weight"] for r in meter.rows)
    save(f"event-batch-{mode}-coalesced{coalesced}", report, meter, budget)
