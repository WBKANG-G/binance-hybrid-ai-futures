"""Phase attribution and income-pagination stress; no production source changes."""

import json
import os
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from services.execution import demo as demo_module
from services.execution import demo_validation as validation
from tests.fixtures.tp_phase_audit import PhaseAudit
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import exercise_opposite, opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_request_budget_lifecycle import candidate

CASES = [
    (mode, pages, "finalization", False, False, 100000)
    for mode in ["tp", "no_tp", "partial", "unfilled"]
    for pages in [1, 3, 10]
] + [
    ("no_tp", 3, "preparation", False, False, 100000),
    ("no_tp", 10, "preparation", False, False, 100000),
    ("no_tp", 10, "finalization", True, False, 100000),
    ("no_tp", 1, "finalization", False, True, 100000),
    ("no_tp", 1, "finalization", False, False, 6000),
]


@pytest.mark.parametrize("mode,pages,when,full_limit,spike,capacity", CASES)
def test_lifecycle_phase_attribution_and_reserve_sensitivity(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
    mode,
    pages,
    when,
    full_limit,
    spike,
    capacity,
):
    if mode in {"no_tp", "partial"}:
        opposite_case[3]["trigger"] = False
    if mode in {"partial", "unfilled"}:
        opposite_case[3]["fill"] = False
    if mode == "unfilled":
        opposite_case[3]["trigger"] = False
    scenario, meter, budget, route, sender = candidate(
        opposite_case,
        cap=capacity,
        partial=mode == "partial",
        spike=spike,
        preparation_cost=712,
    )
    store, c, fake, _ = scenario
    before = store.read("DEMO-test")
    # Bootstrap is unchanged and initially one page. Escalation starts only after
    # bootstrap or at begin_finalization, as specified by the case label.
    audit = PhaseAudit(budget.original, budget._costs)
    audit.import_bootstrap(meter.rows)
    budget.original = audit
    paging = {"pages": pages if when == "preparation" else 1, "full": False}
    original_sender = meter.sender
    ignored_income = [{"incomeType": "COMMISSION"}] * 1000

    def income_pages(method, path, query, key):
        response = original_sender(method, path, query, key)
        if method == "GET" and path == "/fapi/v1/income":
            params = parse_qs(query.get_secret_value())
            page = int(params["page"][0])
            full = page < paging["pages"] or (paging["full"] and page == paging["pages"])
            return fake.response(ignored_income if full else [])
        return response

    meter.sender = income_pages
    for method, phase in [
        ("connected", "SESSION_READY"),
        ("event", "OBSERVATION"),
        ("before_entry_prepare", "EARLY_CHECK"),
        ("resume_entries", "RESUME"),
        ("reserve", "RESERVATION"),
        ("submit", "SUBMISSION_PROTECTION"),
        ("confirm_flat", "FLAT_CONFIRMATION"),
    ]:
        monkeypatch.setattr(c, method, audit.instrument(phase, getattr(c, method)))
    monkeypatch.setattr(validation, "prepare", audit.instrument("PREPARATION", validation.prepare))
    original_begin = c.begin_finalization

    def begin():
        if when == "finalization":
            paging.update(pages=pages, full=full_limit)
        audit.begin_finalization()
        return original_begin()

    monkeypatch.setattr(c, "begin_finalization", begin)
    historical = demo_module.historical_snapshot
    proofs = []

    def counted(*args, **kwargs):
        proofs.append(True)
        return historical(*args, **kwargs)

    monkeypatch.setattr(demo_module, "historical_snapshot", counted)
    try:
        report = exercise_opposite(scenario, monkeypatch)
    finally:
        route.close()
        c.client.send = sender
    output = capsys.readouterr().out
    final = next(
        json.loads(s)
        for s in output.splitlines()
        if s.startswith("{") and "completion_stage" in json.loads(s)
    )
    result = audit.report()
    assert result["complete_cost_model"] and not result["trace_truncated"]
    assert result["attempts"] == len(meter.rows)
    assert result["known_assumed_total_weight"] == sum(r["assumed_weight"] for r in meter.rows)
    assert sum(result["phase_attempts"].values()) == result["attempts"]
    after = store.read("DEMO-test")
    assert after.paused and report["history_and_baselines_preserved"]
    assert all(after.orders[k] == v for k, v in before.orders.items())
    assert all(after.requests[k] == v for k, v in before.requests.items())
    assert len([p for p in posts(fake) if p.get("reduceOnly") != "true"]) <= 1
    assert len([p for p in posts(fake) if p.get("reduceOnly") == "true"]) <= 1
    if spike:
        assert any(fake.positions.values()) and report["flat_confirmation"] == "UNKNOWN"
        assert report["risk_state"] == "EMERGENCY" and report["operator_exit_post_attempts"] == 0
        i = next(i for i, r in enumerate(meter.rows) if r["status"] == 429)
        assert i + 1 == len(meter.rows)
    elif full_limit:
        assert final["validation"] != "FINISHED_FLAT"
        assert report["tp_trigger_check"] == "NOT_ACCEPTED"
        assert final["completion_failure"] != "NONE"
    else:
        assert not any(fake.positions.values()) and final["validation"] == "FINISHED_FLAT"
        assert final["completion_failure"] == "NONE" and len(proofs) == 5
        assert all(r["status"] != 429 for r in meter.rows)
        if mode == "tp" and capacity > 6000:
            assert (
                report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
            )
    if capacity == 6000:
        assert report["new_entry_post_count"] == 0 and budget.stopped
    weights = result["phase_assumed_weights"]
    result.update(
        {
            "mode": mode,
            "income_pages": pages,
            "escalation_after": when,
            "page10_also_full": full_limit,
            "shared_spike": spike,
            "counterfactual_capacity": capacity,
            "full_historical_flat_snapshots": len(proofs),
            "entry_protection_phase_weight": weights.get("RESERVATION", 0)
            + weights.get("SUBMISSION_PROTECTION", 0),
            "cleanup_phase_weight": weights.get("FINALIZATION", 0)
            + weights.get("FLAT_CONFIRMATION", 0),
            "entry_protection_assumption": 1500,
            "cleanup_reserve_assumption": 3000,
            "validation_completion": final["validation"],
            "tp_result": report["tp_trigger_check"],
            "flat_report": report["flat_confirmation"],
            "position_nonzero_at_stop": any(fake.positions.values()),
            "risk_state": report["risk_state"],
            "new_entry_posts": report["new_entry_post_count"],
            "reduce_exit_posts": report["operator_exit_post_attempts"],
        }
    )
    result["entry_assumption_exceeded"] = result["entry_protection_phase_weight"] > 1500
    result["cleanup_assumption_exceeded"] = result["cleanup_phase_weight"] > 3000
    result["model_verdict"] = (
        "INCOMPLETE_PAGINATION_NO_FLAT_PROOF"
        if full_limit
        else "RATE_LIMIT_EXPOSURE_UNKNOWN"
        if spike
        else "REAL_LIMIT_MODEL_ENTRY_BLOCKED"
        if capacity == 6000
        else "ALLOWANCE_COUNTEREXAMPLE_DO_NOT_DEPLOY"
        if result["entry_assumption_exceeded"] or result["cleanup_assumption_exceeded"]
        else "NO_EXCEEDANCE_IN_THIS_CASE_NOT_AN_UPPER_BOUND"
    )
    if not full_limit and not spike and capacity > 6000:
        if mode in {"no_tp", "partial"} and pages >= 3:
            assert result["cleanup_assumption_exceeded"]
        if when == "preparation" and pages >= 3:
            assert result["entry_assumption_exceeded"]
        if mode in {"no_tp", "partial"} and pages == 10:
            assert result["cleanup_phase_weight"] > 6000
    folder = os.getenv("TP_PHASE_REPORT_DIR")
    if folder:
        p = Path(folder)
        p.mkdir(parents=True, exist_ok=True)
        name = f"{mode}-pages{pages}-{when}-full{full_limit}-spike{spike}-cap{capacity}.json"
        (p / name).write_text(json.dumps(result, indent=2) + "\n")
