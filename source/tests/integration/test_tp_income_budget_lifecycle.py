"""Pagination budget candidate: actual isolated PG/WS, synthetic venue only."""

import json
import os
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from integrations.binance_usdm.demo_snapshot import INCOME_PAGE_SIZE, MAX_INCOME_PAGES
from integrations.binance_usdm.demo_tp_budget import TPBudgetPlan
from services.execution import demo as demo_module
from services.execution.demo_validation_limits import FLAT_CONFIRMATION_ATTEMPTS
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import exercise_opposite, opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_request_budget_lifecycle import candidate


def run_income_case(
    scenario,
    monkeypatch,
    capsys,
    *,
    mode="no_tp",
    pages=10,
    old_allowance=False,
    cap=100000,
    repeat=False,
    full=False,
    downgrade=False,
    spike=False,
):
    if mode in {"no_tp", "partial"}:
        scenario[3]["trigger"] = False
    if mode in {"partial", "unfilled"}:
        scenario[3]["fill"] = False
    if mode == "unfilled":
        scenario[3]["trigger"] = False
    built, meter, budget, route, sender = candidate(
        scenario,
        cap=cap,
        partial=mode == "partial",
        spike=spike,
        preparation_cost=712,
        income_guard=True,
        entry_allowance=1500 if old_allowance else 3120,
        cleanup_allowance=3000 if old_allowance else 24960,
    )
    store, c, fake, _ = built
    before = store.read("DEMO-test")
    paging = {"pages": 1, "full": False}
    original_sender = meter.sender
    records = [{"incomeType": "COMMISSION"}] * INCOME_PAGE_SIZE

    def income(method, path, query, key):
        response = original_sender(method, path, query, key)
        if method == "GET" and path == "/fapi/v1/income":
            page = int(parse_qs(query.get_secret_value())["page"][0])
            filled = page < paging["pages"] or (paging["full"] and page == paging["pages"])
            return fake.response(records if filled else [])
        return response

    meter.sender = income
    original_begin = c.begin_finalization

    def begin():
        paging["pages"] = pages
        paging["full"] = full
        return original_begin()

    monkeypatch.setattr(c, "begin_finalization", begin)
    proofs = []
    original_history = demo_module.historical_snapshot

    def historical(*args, **kwargs):
        proofs.append(True)
        return original_history(*args, **kwargs)

    monkeypatch.setattr(demo_module, "historical_snapshot", historical)
    confirmations = []
    original_confirm = c.confirm_flat

    def confirm():
        result = original_confirm()
        confirmations.append(result)
        # Synthetic lost confirmation: all original evidence is still queried,
        # but acceptance is withheld until the last bounded observation attempt.
        if repeat and len(confirmations) < FLAT_CONFIRMATION_ATTEMPTS:
            return "UNKNOWN"
        return result

    monkeypatch.setattr(c, "confirm_flat", confirm)
    if downgrade:
        original_reserve = c.reserve

        def reserve(*args, **kwargs):
            budget.plan = TPBudgetPlan(cap, 1500, 3000, 300)
            return original_reserve(*args, **kwargs)

        monkeypatch.setattr(c, "reserve", reserve)
    try:
        report = exercise_opposite(built, monkeypatch)
    finally:
        route.close()
        c.client.send = sender
    output = capsys.readouterr().out
    final = next(
        json.loads(s)
        for s in output.splitlines()
        if s.startswith("{") and "completion_stage" in json.loads(s)
    )
    after = store.read("DEMO-test")
    d = budget.diagnostics()
    assert after.paused and report["history_and_baselines_preserved"]
    assert all(after.orders[k] == v for k, v in before.orders.items())
    assert all(after.requests[k] == v for k, v in before.requests.items())
    assert after.daily_base == before.daily_base and after.weekly_base == before.weekly_base
    assert report["new_entry_post_count"] <= 1 and report["operator_exit_post_attempts"] <= 1
    if old_allowance or downgrade:
        assert not posts(fake) and not d["admitted"] and budget.stopped
        assert d["stop_reason"] == "INCOME_PAGINATION_ALLOWANCE_INSUFFICIENT"
        assert final["validation_stage"] == (
            "RESERVE_ENTRY" if downgrade else "ENTRY_BUDGET_PRECHECK"
        )
        assert final["validation"] == "FINISHED_FLAT" and len(proofs) == 5
    elif spike:
        assert any(fake.positions.values()) and report["risk_state"] == "EMERGENCY"
        assert (
            report["flat_confirmation"] == "UNKNOWN" and report["operator_exit_post_attempts"] == 0
        )
        i = next(i for i, r in enumerate(meter.rows) if r["status"] == 429)
        assert len(meter.rows) == i + 1
    elif full:
        assert (
            final["validation"] != "FINISHED_FLAT" and report["tp_trigger_check"] == "NOT_ACCEPTED"
        )
        if any(fake.positions.values()):
            assert report["risk_state"] == "EMERGENCY"
    else:
        assert not any(fake.positions.values()) and final["validation"] == "FINISHED_FLAT"
        assert final["completion_failure"] == "NONE"
        assert all(r["status"] != 429 for r in meter.rows)
        assert len(proofs) == 5 * (FLAT_CONFIRMATION_ATTEMPTS if repeat else 1)
        assert d["cleanup_assumed_weight"] <= 24960
        assert d["income_profile_checks"] == 2
        if mode == "tp":
            assert (
                report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
            )
        if repeat:
            assert len(confirmations) == 4 and report["operator_exit_post_attempts"] == 1
    folder = os.getenv("TP_INCOME_BUDGET_REPORT_DIR")
    if folder:
        p = Path(folder)
        p.mkdir(parents=True, exist_ok=True)
        label = f"{mode}-pages{pages}-old{old_allowance}-cap{cap}-repeat{repeat}-full{full}-downgrade{downgrade}-spike{spike}"
        (p / (label + ".json")).write_text(
            json.dumps(
                {
                    "actual_venue_used": False,
                    "case": label,
                    "mode": mode,
                    "income_pages": pages,
                    "parser_max_income_pages": MAX_INCOME_PAGES,
                    "report": report,
                    "completion": final,
                    "budget": d,
                    "full_historical_flat_snapshots": len(proofs),
                    "confirmation_results": confirmations,
                    "synthetic_requests": meter.rows,
                },
                indent=2,
            )
            + "\n"
        )


@pytest.mark.parametrize("cap", [6000, 100000])
def test_original_one_page_allowances_are_rejected_before_any_order_or_resume(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
    cap,
):
    run_income_case(opposite_case, monkeypatch, capsys, old_allowance=True, cap=cap, pages=1)


@pytest.mark.parametrize("mode", ["tp", "no_tp", "partial", "unfilled"])
@pytest.mark.parametrize("pages", [1, 3, 10])
def test_larger_explicit_counterfactual_budget_handles_page_growth_with_original_proof(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
    mode,
    pages,
):
    run_income_case(opposite_case, monkeypatch, capsys, mode=mode, pages=pages)


def test_all_four_flat_observations_are_budgeted_without_new_exit_post(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
):
    run_income_case(opposite_case, monkeypatch, capsys, pages=10, repeat=True)


def test_full_tenth_page_retains_original_incomplete_income_rejection(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
):
    run_income_case(opposite_case, monkeypatch, capsys, pages=10, full=True)


def test_profile_downgrade_after_precheck_is_rejected_again_before_reservation(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
):
    run_income_case(opposite_case, monkeypatch, capsys, pages=1, downgrade=True)


def test_shared_ip_spike_still_has_no_sdk_cooldown_bypass(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,
):
    run_income_case(opposite_case, monkeypatch, capsys, pages=1, spike=True)
